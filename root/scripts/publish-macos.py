"""Publish a notarized root.app disk image to R2, the way Tura ships (Plank docs/tura-release.md).

    uv run --with boto3 python scripts/publish-macos.py --version 0.1.0

Uploads to the releases bucket Tura uses (``chotu-bucket``), under
``root/releases/<channel>/<version>/``:

    root-darwin-<arch>.dmg      what people download
    root-darwin-<arch>.json     version, build, size, sha256 and Sparkle's EdDSA
                                signature — uploaded last, so it never names a disk
                                image that is not there yet. turalearn.com turns it into
                                the appcast "Check for Updates…" reads

with Tura's upload key from ``~/.tura-release/r2-upload.env`` (mode 600):
``TURA_R2_ACCESS_KEY_ID`` / ``TURA_R2_SECRET_ACCESS_KEY``, an R2 API token with
Object Read & Write on the bucket. The bucket is private: the website hands out
short-lived signed links, as it does for Tura. Refuses a disk image that is not
stapled with a notarization ticket, and signs it with root's Sparkle key (the login
Keychain's "root" account, from ``generate_keys --account root``): an installed root
takes no update whose signature does not match.

Which release people get, as a download and as an update, is the website's
``ROOT_R2_RELEASE_PREFIX``; publishing alone changes nothing they see.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import platform
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UPLOAD_ENV = Path(os.environ.get("TURA_R2_UPLOAD_ENV", Path.home() / ".tura-release" / "r2-upload.env"))
SPARKLE_BIN = REPO / "macos" / ".build" / "artifacts" / "sparkle" / "Sparkle" / "bin"
DEFAULTS = {"TURA_R2_ACCOUNT_ID": "7bde7e018e7fa9eb5d03b9df2e66387b", "TURA_R2_BUCKET": "chotu-bucket"}


def settings() -> dict[str, str]:
    values = dict(DEFAULTS)
    if UPLOAD_ENV.is_file():
        for line in UPLOAD_ENV.read_text("utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip():
                values[key.strip().removeprefix("export ").strip()] = value.strip().strip("\"'")
    for key in ("TURA_R2_ACCESS_KEY_ID", "TURA_R2_SECRET_ACCESS_KEY", *DEFAULTS):
        if os.environ.get(key):
            values[key] = os.environ[key].strip()
    missing = [k for k in ("TURA_R2_ACCESS_KEY_ID", "TURA_R2_SECRET_ACCESS_KEY") if not values.get(k)]
    if missing:
        sys.exit(f"R2 upload key missing ({', '.join(missing)}): put it in {UPLOAD_ENV} (mode 600).")
    return values


def sparkle_signature(dmg: Path) -> str:
    """Sparkle's EdDSA signature over the disk image, with root's key from the Keychain."""
    tool = SPARKLE_BIN / "sign_update"
    if not tool.is_file():
        sys.exit(f"No {tool}: build the app once (scripts/package-macos.sh) so Swift fetches Sparkle.")
    out = subprocess.run([str(tool), "--account", "root", str(dmg)], capture_output=True, text=True)
    found = re.search(r'sparkle:edSignature="([^"]+)"', out.stdout)
    if out.returncode != 0 or not found:
        sys.exit(f"sign_update failed: is root's Sparkle key in the login Keychain?\n{out.stdout}{out.stderr}")
    return found.group(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", required=True)
    parser.add_argument("--channel", default="stable")
    parser.add_argument("--arch", default=platform.machine())
    parser.add_argument("--dmg", help="Default: build/macos/root-<version>-<arch>.dmg")
    parser.add_argument("--app", help="The root.app the disk image holds. Default: build/macos/root.app")
    parser.add_argument("--notes", default="", help="What changed, shown in the update window (plain text or HTML)")
    args = parser.parse_args()

    dmg = Path(args.dmg) if args.dmg else REPO / "build" / "macos" / f"root-{args.version}-{args.arch}.dmg"
    if not dmg.is_file():
        sys.exit(f"No disk image at {dmg}: build it with scripts/package-macos.sh.")
    stapled = subprocess.run(["xcrun", "stapler", "validate", str(dmg)], capture_output=True, text=True)
    if stapled.returncode != 0:
        sys.exit(f"{dmg.name} has no notarization ticket stapled: build with SIGN_IDENTITY and NOTARY_PROFILE.\n{stapled.stdout}{stapled.stderr}")

    info = plistlib.loads((Path(args.app or REPO / "build" / "macos" / "root.app") / "Contents" / "Info.plist").read_bytes())
    if info.get("CFBundleShortVersionString") != args.version:
        sys.exit(f"root.app is {info.get('CFBundleShortVersionString')}, not {args.version}: pass --app, or rebuild.")
    signature = sparkle_signature(dmg)

    import boto3
    from botocore.config import Config

    config = settings()
    client = boto3.client(
        "s3",
        endpoint_url=f"https://{config['TURA_R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=config["TURA_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=config["TURA_R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(signature_version="s3v4", retries={"max_attempts": 5}),
    )
    bucket = config["TURA_R2_BUCKET"]
    prefix = f"root/releases/{args.channel}/{args.version}"
    name = f"root-darwin-{args.arch}"
    digest = hashlib.sha256(dmg.read_bytes()).hexdigest()
    manifest = {
        "product": "root",
        "version": args.version,
        "channel": args.channel,
        "platform": f"darwin-{args.arch}",
        "dmg": f"{prefix}/{name}.dmg",
        "build": str(info["CFBundleVersion"]),
        "minimum_system_version": str(info.get("LSMinimumSystemVersion", "13.0")),
        "size": dmg.stat().st_size,
        "sha256": digest,
        "ed_signature": signature,
        "notes": args.notes,
        "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    for key, body, content_type in (
        (f"{prefix}/{name}.dmg", dmg, "application/x-apple-diskimage"),
        (f"{prefix}/{name}.json", json.dumps(manifest, indent=2).encode(), "application/json"),
    ):
        if isinstance(body, Path):
            client.upload_file(str(body), bucket, key, ExtraArgs={"ContentType": content_type})
            expected = body.stat().st_size
        else:
            client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)
            expected = len(body)
        remote = int(client.head_object(Bucket=bucket, Key=key)["ContentLength"])
        if remote != expected:
            sys.exit(f"{key}: uploaded {expected} bytes but R2 reports {remote}.")
        print(f"uploaded {key} ({remote:,} bytes)")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
