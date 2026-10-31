"""The line's invite, keys and sealed messages: everything the relay must not see.

An invite is ``<relay url>/#<secret>``. The fragment never leaves the client —
browsers and HTTP libraries do not send it — so the relay only ever learns
what is derived from the secret for it:

    token   = HMAC(secret, "chamber/token")   sent to the relay as a bearer
    line id = sha256(token)[:32]                 the relay checks this, statelessly
    key     = HMAC(secret, "chamber/key")      AES-256-GCM, never sent

A message body is sealed with the envelope (line, sender, recipient, id,
reply_to) as associated data, so the relay can route a message but cannot read
it, alter it, or pass it off as from someone else. Anyone holding the invite is
on the line: share it like a password.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

AGENT = re.compile(r"[a-z0-9][a-z0-9._-]{0,31}/[a-z0-9][a-z0-9._-]{0,31}")
#: Files up to this size travel inside the (sealed) message; bigger ones go to
#: the relay's file storage, sealed the same way, wiped when the session ends.
INLINE_FILE_BYTES = 64 * 1024


class LineError(ValueError):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True)
class Invite:
    relay: str
    secret: str
    #: This member's admin-signed pass or certificate (personal invites); empty on an open relay.
    pass_: str = ""
    #: The admin's Ed25519 public key: the trust anchor member certificates and epoch
    #: distributions are verified against (v2/v3 invites; empty on v1).
    admin_pub: str = ""

    @classmethod
    def new(cls, relay: str) -> "Invite":
        return cls(relay.rstrip("/"), _b64(secrets.token_bytes(32)))

    @classmethod
    def parse(cls, text: str) -> "Invite":
        """``https://<relay>/#<secret>``, ``…#<secret>~<pass>``, or ``…#<secret>~<cert>~<admin key>``."""
        relay, _, key = text.strip().partition("#")
        secret, pass_, admin_pub = (key.split("~") + ["", ""])[:3]
        parts = urlsplit(relay)
        if parts.scheme not in ("https", "http") or not parts.netloc or len(secret) < 32:
            raise LineError("an invite looks like https://<relay>/#<secret>")
        return cls(f"{parts.scheme}://{parts.netloc}{parts.path}".rstrip("/"), secret, pass_, admin_pub)

    def __str__(self) -> str:
        out = f"{self.relay}/#{self.secret}"
        if self.pass_ or self.admin_pub:
            out += f"~{self.pass_}"
        if self.admin_pub:
            out += f"~{self.admin_pub}"
        return out

    def _derive(self, label: bytes) -> bytes:
        return hmac.new(_unb64(self.secret), b"chamber/" + label, hashlib.sha256).digest()

    @property
    def token(self) -> str:
        return _b64(self._derive(b"token"))

    @property
    def line_id(self) -> str:
        return hashlib.sha256(self.token.encode()).hexdigest()[:32]

    @property
    def ws_url(self) -> str:
        base = self.relay.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        return f"{base}/v1/lines/{self.line_id}/ws"

    def _aad(self, sender: str, to: str, message_id: str, reply_to: str | None, epoch: int = 0) -> bytes:
        # v3: the epoch joins the associated data, so a body sealed for one epoch cannot be
        # replayed as another's (the line id leads the AAD as before: never across lines).
        parts = [self.line_id, sender, to, message_id, reply_to or ""]
        if epoch:
            parts.append(f"e{epoch}")
        return "|".join(parts).encode()

    def _key(self, epoch_key: bytes | None) -> AESGCM:
        return AESGCM(epoch_key if epoch_key is not None else self._derive(b"key"))

    def seal(self, payload: dict[str, Any], *, sender: str, to: str, message_id: str, reply_to: str | None, epoch: tuple[int, bytes] | None = None) -> str:
        """Seal a payload. ``epoch=(n, key)`` seals with the per-epoch key (v3 bodies) instead of
        the shared invite key; absent/0 is the shared key (v1/v2 bodies, v3 distributions)."""
        nonce = os.urandom(12)
        data = json.dumps(payload, separators=(",", ":")).encode()
        n, key = epoch or (0, None)
        return _b64(nonce + self._key(key).encrypt(nonce, data, self._aad(sender, to, message_id, reply_to, n)))

    def seal_blob(self, data: bytes, blob_id: str, epoch: tuple[int, bytes] | None = None) -> bytes:
        nonce = os.urandom(12)
        n, key = epoch or (0, None)
        return nonce + self._key(key).encrypt(nonce, data, f"{self.line_id}|blob|{blob_id}".encode() + (f"|e{n}".encode() if n else b""))

    def open_blob(self, sealed: bytes, blob_id: str, epoch: tuple[int, bytes] | None = None) -> bytes:
        n, key = epoch or (0, None)
        try:
            return self._key(key).decrypt(sealed[:12], sealed[12:], f"{self.line_id}|blob|{blob_id}".encode() + (f"|e{n}".encode() if n else b""))
        except (InvalidTag, ValueError) as error:
            raise LineError("a file that does not open with this line's key") from error

    def blob_url(self, blob_id: str) -> str:
        return f"{self.relay}/v1/lines/{self.line_id}/blobs/{blob_id}"

    def open(self, body: str, *, sender: str, to: str, message_id: str, reply_to: str | None, epoch: tuple[int, bytes] | None = None) -> dict[str, Any]:
        n, key = epoch or (0, None)
        try:
            raw = _unb64(body)
            data = self._key(key).decrypt(raw[:12], raw[12:], self._aad(sender, to, message_id, reply_to, n))
        except (InvalidTag, ValueError) as error:
            raise LineError("a message that does not open with this line's key") from error
        return json.loads(data)


# ----- identity v3: epoch read keys -------------------------------------------------------------
#
# Mirrors chamber's lib/epoch.mjs byte for byte: an epoch key is 32 random bytes (AES-256-GCM);
# the admin wraps it to each member's certified X25519 encryption key with ECIES (ephemeral
# X25519, HKDF-SHA256(salt = line id, info = "chamber/epoch-wrap/v3"), AES-256-GCM bound to
# (line, epoch, owner) as AAD) and signs the distribution with the cert-issuing admin key.
# Rotation is NOT retroactive: a revoked member keeps the epochs they already held.


def _x25519_public(raw: bytes) -> X25519PublicKey:
    return X25519PublicKey.from_public_bytes(raw)


def wrap_epoch_key(enc_pub: str, *, line: str, epoch: int, owner: str, key: bytes) -> dict[str, str]:
    """Mirrors chamber's wrapEpochKey: ECIES to the member's certified X25519 public key."""
    ephemeral = X25519PrivateKey.generate()
    shared = ephemeral.exchange(_x25519_public(_unb64(enc_pub)))
    wrap = HKDF(algorithm=hashes.SHA256(), length=32, salt=line.encode(), info=b"chamber/epoch-wrap/v3").derive(shared)
    nonce = os.urandom(12)
    sealed = nonce + AESGCM(wrap).encrypt(nonce, key, f"{line}|epoch|{epoch}|{owner}".encode())
    raw_pub = ephemeral.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"eph": _b64(raw_pub), "sealed": _b64(sealed)}


# ----- the admin side (mirrors chamber's admin.mjs; tests and future admin tooling) --------------
#
# An admin-signed body is b64url(JSON) + "." + b64url(Ed25519 signature over the body TEXT), with
# the claims serialized exactly like JSON.stringify({ v: 1, iat, ...claims }): v first, iat
# second, insertion order after, no spaces.


def new_admin_key() -> tuple[str, str]:
    """A fresh admin key: (public, private), the public half is the raw 32-byte key everyone may see."""
    private = Ed25519PrivateKey.generate()
    return (
        _b64(private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)),
        _b64(private.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())),
    )


def new_member_keypair() -> dict[str, str]:
    """A fresh member key in chamber's key-file shape: Ed25519 signing + separate X25519 encryption."""
    signing = Ed25519PrivateKey.generate()
    enc = X25519PrivateKey.generate()
    der = serialization.Encoding.DER
    pkcs8 = serialization.PrivateFormat.PKCS8
    raw = serialization.PublicFormat.Raw
    return {
        "public": _b64(signing.public_key().public_bytes(serialization.Encoding.Raw, raw)),
        "private": _b64(signing.private_bytes(der, pkcs8, serialization.NoEncryption())),
        "encPublic": _b64(enc.public_key().public_bytes(serialization.Encoding.Raw, raw)),
        "encPrivate": _b64(enc.private_bytes(der, pkcs8, serialization.NoEncryption())),
    }


def signed_order(admin_private_der_b64: str, claims: dict[str, Any]) -> str:
    """An admin-signed body: ``b64url(JSON).b64url(sig)``, byte-compatible with chamber's signed()."""
    body = _b64(json.dumps({"v": 1, "iat": int(time.time()), **claims}, separators=(",", ":")).encode())
    private = serialization.load_der_private_key(_unb64(admin_private_der_b64), password=None)
    return f"{body}.{_b64(private.sign(body.encode()))}"


def issue_certificate(admin_private_der_b64: str, *, line: str, owner: str, pubkey: str, enc: str | None = None) -> str:
    """A v2/v3 member certificate (v3 when the encryption key rides along), chamber-compatible."""
    import secrets as _secrets

    claims: dict[str, Any] = {"line": line, "member": _b64(_secrets.token_bytes(9)), "owner": owner.lower(), "pubkey": pubkey}
    return _v_cert(admin_private_der_b64, 3 if enc else 2, claims, enc)


def _v_cert(admin_private_der_b64: str, v: int, claims: dict[str, Any], enc: str | None) -> str:
    body_claims = {"v": v, "iat": int(time.time()), **claims, **({"enc": enc} if enc else {})}
    body = _b64(json.dumps(body_claims, separators=(",", ":")).encode())
    private = serialization.load_der_private_key(_unb64(admin_private_der_b64), password=None)
    return f"{body}.{_b64(private.sign(body.encode()))}"


def sign_distribution(admin_private_der_b64: str, *, line: str, epoch: int, wraps: dict[str, dict[str, str]]) -> str:
    """The admin's signature over a distribution (rotation authority), chamber-compatible."""
    private = serialization.load_der_private_key(_unb64(admin_private_der_b64), password=None)
    return _b64(private.sign(distribution_payload(line=line, epoch=epoch, wraps=wraps).encode()))


def unwrap_epoch_key(enc_private_der: bytes, wrap: dict[str, str], *, line: str, epoch: int, owner: str) -> bytes | None:
    """The inverse of chamber's wrapEpochKey; None when the wrap doesn't open for this member."""
    try:
        # The stored key is a PKCS8 DER document (the same file chamber's identity.enrollOwner writes).
        private = serialization.load_der_private_key(enc_private_der, password=None)
        shared = private.exchange(_x25519_public(_unb64(str(wrap["eph"]))))
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=line.encode(), info=b"chamber/epoch-wrap/v3").derive(shared)
        sealed = _unb64(str(wrap["sealed"]))
        return AESGCM(key).decrypt(sealed[:12], sealed[12:], f"{line}|epoch|{epoch}|{owner}".encode())
    except (InvalidTag, ValueError, KeyError, TypeError):
        return None


def distribution_payload(*, line: str, epoch: int, wraps: dict[str, dict[str, str]]) -> str:
    """The exact bytes the admin signs over a distribution. Mirrored in lib/epoch.mjs."""
    flat = [[owner, wraps[owner]["eph"], wraps[owner]["sealed"]] for owner in sorted(wraps)]
    return "\n".join(["chamber-epoch-v3", line, str(epoch), json.dumps(flat, separators=(",", ":"))])


def verify_distribution(admin_pub: str, *, line: str, epoch: int, wraps: dict[str, dict[str, str]], sig: str) -> bool:
    """Verify a distribution against the invite's admin public key (the only trust anchor)."""
    try:
        Ed25519PublicKey.from_public_bytes(_unb64(admin_pub)).verify(_unb64(sig), distribution_payload(line=line, epoch=epoch, wraps=wraps).encode())
        return True
    except Exception:
        return False


def body_hash(body: str) -> str:
    """sha256 of the SEALED body string, hex: signatures cover the hash, not the body."""
    return hashlib.sha256(body.encode()).hexdigest()


def envelope_payload(*, line: str, from_: str, to: str, message_id: str, reply_to: str | None, ts: int, hash_: str, epoch: int = 0) -> str:
    """The exact bytes a v2/v3 message signature covers. Mirrored in chamber's lib/identity.mjs:
    v3 signatures also commit to the epoch, so ciphertext cannot be re-labeled across epochs."""
    if epoch:
        return "\n".join(["chamber-msg-v3", line, from_, to, message_id, reply_to or "", str(ts), str(epoch), hash_])
    return "\n".join(["chamber-msg-v2", line, from_, to, message_id, reply_to or "", str(ts), hash_])


def sign_envelope(member_private_der: bytes, **fields: Any) -> str:
    """Sign a message envelope with the member's private key (which never leaves this machine)."""
    return _b64(serialization.load_der_private_key(member_private_der, password=None).sign(envelope_payload(**fields).encode()))


def load_member_key(home: Path, line: str, owner: str) -> dict[str, Any] | None:
    """This machine's member key for a line (the file chamber's identity.enrollOwner wrote, 0600)."""
    try:
        key = json.loads((home / "keys" / f"{line}-{owner}.json").read_text("utf-8"))
        if isinstance(key, dict) and key.get("v") == 2 and key.get("private"):
            return key
    except (OSError, ValueError):
        pass
    return None


class EpochStore:
    """~/.chamber/keys/<line>-<owner>-epochs.json (0600): every epoch key this member received."""

    def __init__(self, path: Path):
        self.path = path
        self.current = 0
        self.keys: dict[str, str] = {}
        try:
            data = json.loads(path.read_text("utf-8"))
            if isinstance(data, dict) and isinstance(data.get("keys"), dict):
                self.current = int(data.get("current") or 0)
                self.keys = {str(k): str(v) for k, v in data["keys"].items()}
        except (OSError, ValueError, TypeError):
            pass

    def key_for(self, epoch: int) -> bytes | None:
        raw = self.keys.get(str(epoch))
        return _unb64(raw) if raw else None

    def save(self, epoch: int, key: bytes) -> None:
        self.keys[str(epoch)] = _b64(key)
        self.current = max(self.current, int(epoch))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        self.path.write_text(json.dumps({"current": self.current, "keys": self.keys}))
        self.path.chmod(0o600)


@dataclass
class Message:
    """One message as an agent sees it: opened, with its files decoded."""

    id: str
    sender: str
    to: str
    text: str
    reply_to: str | None = None
    seq: int = 0
    ts: int = 0
    files: list[dict[str, Any]] = field(default_factory=list)  # {"name", "data": bytes}
    #: The conversation it belongs to, and that conversation's title (set by the sender).
    thread: str | None = None
    title: str = ""
    #: People @mentioned (a channel message reaches their agents through these).
    mentions: list[str] = field(default_factory=list)


_MENTION = re.compile(r"(?:^|[^\w@])@([a-z0-9][a-z0-9._-]{0,31})", re.I)


def mentions_in(text: str) -> list[str]:
    """People @mentioned in a text: the relay routes on these (it cannot read the text). Same rule as chamber."""
    out: list[str] = []
    for match in _MENTION.finditer(text or ""):
        name = match.group(1).lower().rstrip(".")
        if name and name not in out:
            out.append(name)
    return out


def encode_file(name: str, data: bytes) -> dict[str, str]:
    return {"name": name, "data": base64.b64encode(data).decode()}


def decode_file(item: Any) -> dict[str, Any] | None:
    """An inline file, or ``{"name", "blob", "size"}`` for one in the relay's storage."""
    if not isinstance(item, dict):
        return None
    name = Path(str(item.get("name") or "file")).name or "file"
    blob = str(item.get("blob") or "")
    if blob:
        return {"name": name, "blob": blob, "size": int(item.get("size") or 0)} if re.fullmatch(r"[0-9a-f]{32}", blob) else None
    try:
        return {"name": name, "data": base64.b64decode(str(item.get("data") or ""))}
    except ValueError:
        return None
