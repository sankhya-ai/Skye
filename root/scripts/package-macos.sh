#!/usr/bin/env bash
# Build root.app and root-<version>-arm64.dmg.
#
#   scripts/package-macos.sh                 # ad-hoc signed: runs on this Mac
#   SIGN_IDENTITY="Developer ID Application: …" NOTARY_PROFILE=<notarytool profile> \
#     scripts/package-macos.sh               # signed, notarized, stapled: for everyone
#
# Optional: BUNDLE_ID (default ai.tura.root), BUILD (default: git commit count),
# SPARKLE_PUBLIC_KEY (default: root's own update key, `generate_keys --account root`),
# FEED_URL (default: the appcast turalearn.com serves beside the download, from what
# scripts/publish-macos.py put in R2),
# ROOT_GOOGLE_CLIENT_ID / ROOT_GOOGLE_CLIENT_SECRET (Gmail, a Google "Desktop app"
# client), ROOT_GITHUB_CLIENT_ID (GitHub sign-in), PYTHON_DIR (a relocatable
# CPython 3.11–3.13 install; default: uv's managed 3.13), SKIP_BUILD=1 (reuse the
# runtime and UI builds), SKIP_SMOKE=1.
#
# The bundle:
#   root.app/Contents/MacOS/root                the window (macos/, a Swift package)
#   root.app/Contents/Frameworks/Sparkle.framework   updates
#   root.app/Contents/Resources/python/         relocatable CPython + root's server
#   root.app/Contents/Resources/root/           bin/kimi-agent, agent/, skills/, ui/
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
out="$repo/build/macos"
app="$out/root.app"
version="$(sed -n 's/^version = "\(.*\)"/\1/p' "$repo/server/pyproject.toml")"
build="${BUILD:-$(git -C "$repo" rev-list --count HEAD 2>/dev/null || echo 1)}"
bundle_id="${BUNDLE_ID:-ai.tura.root}"
# One feed per architecture: these are separate builds, not a universal one.
feed_url="${FEED_URL:-https://www.turalearn.com/api/updates/root/stable/appcast-$(uname -m).xml}"
# Public by design: the app checks every update against it. The private half is in
# the login Keychain (Sparkle account "root") and ~/.tura-release/sparkle-root-private-key.
sparkle_public_key="${SPARKLE_PUBLIC_KEY:-Ex/OirJAJc0n1BHgIyNDTkUckx9hS/QcG9YGQEBXMXg=}"
arch="$(uname -m)"
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

[ "$(uname -s)" = Darwin ] || { echo "package-macos: run this on macOS" >&2; exit 1; }
for tool in xcrun swift codesign hdiutil ditto cargo npm; do
  command -v "$tool" >/dev/null || { echo "package-macos: $tool is required" >&2; exit 1; }
done

if [ -z "${PYTHON_DIR:-}" ]; then
  command -v uv >/dev/null || { echo "package-macos: set PYTHON_DIR or install uv (it provides a relocatable CPython)" >&2; exit 1; }
  uv python install 3.13 >/dev/null
  PYTHON_DIR="$(cd "$(dirname "$(uv python find 3.13 --managed-python)")/.." && pwd)"
fi

if [ -z "${SKIP_BUILD:-}" ]; then
  step "Building the runtime and the UI"
  cargo build --release --manifest-path "$repo/runtime/Cargo.toml" --bin kimi-agent
  (cd "$repo/ui" && npm ci --no-fund --no-audit && npm run build)
fi

step "Assembling root.app $version ($build) for $arch"
rm -rf "$app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Frameworks" "$app/Contents/Resources/root/bin"
resources="$app/Contents/Resources"
ditto "$repo/runtime/target/release/kimi-agent" "$resources/root/bin/kimi-agent"
ditto "$repo/agent" "$resources/root/agent"
ditto "$repo/runtime/kimi-agent/src/skills" "$resources/root/skills"
ditto "$repo/ui/dist" "$resources/root/ui"
ditto "$repo/runtime/LICENSE" "$resources/root/LICENSE-runtime"
ditto "$repo/runtime/NOTICE" "$resources/root/NOTICE-runtime"

sed -e "s/@BUNDLE_ID@/$bundle_id/" -e "s/@VERSION@/$version/" -e "s/@BUILD@/$build/" \
    -e "s|@FEED_URL@|$feed_url|" -e "s|@SPARKLE_PUBLIC_KEY@|$sparkle_public_key|" \
    -e "s|@GOOGLE_CLIENT_ID@|${ROOT_GOOGLE_CLIENT_ID:-}|" -e "s|@GOOGLE_CLIENT_SECRET@|${ROOT_GOOGLE_CLIENT_SECRET:-}|" \
    -e "s|@GITHUB_CLIENT_ID@|${ROOT_GITHUB_CLIENT_ID:-}|" \
    "$repo/macos/Info.plist" > "$app/Contents/Info.plist"
"$repo/macos/make-icon.sh" "$resources/AppIcon.icns"
swift build -c release --package-path "$repo/macos" --arch "$arch"
ditto "$(swift build -c release --package-path "$repo/macos" --arch "$arch" --show-bin-path)/root" "$app/Contents/MacOS/root"
ditto "$(find "$repo/macos/.build/artifacts" -path "*Sparkle.xcframework/macos-*/Sparkle.framework" -maxdepth 6 | head -1)" "$app/Contents/Frameworks/Sparkle.framework"

step "Bundling Python ($PYTHON_DIR) and the server"
ditto "$PYTHON_DIR" "$resources/python"
python="$resources/python/bin/python3"
# uv marks its Pythons as externally managed; this copy is the app's own.
find "$resources/python/lib" -maxdepth 2 -name EXTERNALLY-MANAGED -delete
"$python" -m ensurepip --upgrade >/dev/null 2>&1 || true
# A non-editable install from a copy, so the repo's server/build is untouched.
staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT
ditto "$repo/server" "$staging/server"
rm -rf "$staging/server/.venv" "$staging/server/build" "$staging/server/tests"
PIP_DISABLE_PIP_VERSION_CHECK=1 "$python" -m pip install --quiet --no-compile "$staging/server[memory,keychain]"
"$python" -m pip uninstall --quiet --yes pip setuptools wheel >/dev/null 2>&1 || true
# Not used by root: Tk, IDLE, the stdlib's own tests. (ensurepip stays: the toolbox's venv needs it.)
stdlib="$(echo "$resources"/python/lib/python3.*)"
rm -rf "$stdlib"/{test,idlelib,tkinter,turtledemo,turtle.py} "$stdlib"/lib-dynload/_tkinter* \
       "$resources"/python/lib/{tcl9*,tk9*,itcl*,thread*,libtcl*,libtk*} "$resources"/python/include
find "$resources/python" -name "__pycache__" -type d -prune -exec rm -rf {} +
find "$resources/python" -type d \( -name "test" -o -name "tests" \) -path "*/site-packages/*" -prune -exec rm -rf {} +
# Compiled once here: the signed bundle must never be written to at run time.
"$python" -m compileall -q -j 0 "$resources/python/lib" >/dev/null || true

step "Signing"
entitlements="$repo/macos/root.entitlements"
if [ -n "${SIGN_IDENTITY:-}" ]; then
  sign=(codesign --force --timestamp --options runtime --entitlements "$entitlements" --sign "$SIGN_IDENTITY")
else
  sign=(codesign --force --sign -)
  echo "No SIGN_IDENTITY: ad-hoc signing (runs on this Mac; others need a Developer ID build)."
fi
sign_quietly() { "$@" >/dev/null 2>&1 || { echo "codesign failed: ${*: -1}" >&2; "$@"; exit 1; }; }
# Every Mach-O inside first, deepest first (Sparkle apart), then Sparkle as its docs order it, then the app.
while IFS= read -r file; do
  if file -b "$file" | grep -q "Mach-O"; then sign_quietly "${sign[@]}" "$file"; fi
done < <(find "$app/Contents" -type f \( -perm -u+x -o -name "*.so" -o -name "*.dylib" \) -not -path "*/Sparkle.framework/*" | awk '{ print length($0) "\t" $0 }' | sort -rn | cut -f2-)
sparkle="$app/Contents/Frameworks/Sparkle.framework/Versions/B"
if [ -n "${SIGN_IDENTITY:-}" ]; then
  plain=(codesign --force --timestamp --options runtime --sign "$SIGN_IDENTITY")
else
  plain=(codesign --force --sign -)
fi
sign_quietly "${plain[@]}" "$sparkle/XPCServices/Installer.xpc"
sign_quietly "${plain[@]}" --preserve-metadata=entitlements "$sparkle/XPCServices/Downloader.xpc"
sign_quietly "${plain[@]}" "$sparkle/Autoupdate"
sign_quietly "${plain[@]}" "$sparkle/Updater.app"
sign_quietly "${plain[@]}" "$app/Contents/Frameworks/Sparkle.framework"
sign_quietly "${sign[@]}" "$app"
codesign --verify --deep --strict "$app"
echo "signature ok"

if [ -z "${SKIP_SMOKE:-}" ]; then
  step "Smoke test: the bundled server answers and finds its runtime"
  home="$(mktemp -d)"
  port="$("$python" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
  TURA_CODE_HOME="$home" TURA_CODE_RESOURCES="$resources/root" TURA_CODE_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 \
    "$python" -m tura_code serve --port "$port" >"$home/server.log" 2>&1 &
  pid=$!
  ok=""
  for _ in $(seq 1 120); do
    health="$(curl -fsS "http://127.0.0.1:$port/api/health" 2>/dev/null || true)"
    if [ -n "$health" ]; then ok=1; break; fi
    sleep 0.5
  done
  index="$(curl -fsS "http://127.0.0.1:$port/" 2>/dev/null || true)"
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  if [ -z "$ok" ]; then cat "$home/server.log" >&2; echo "smoke test: the server did not start" >&2; exit 1; fi
  echo "$health" | "$python" -c 'import json, sys; h = json.load(sys.stdin); r = h["runtime"]; assert not r.get("error"), r; print("runtime:", r.get("binary") or r); print("memory:", h["memory"])'
  echo "$index" | grep -q "<title>root</title>" || { echo "smoke test: the UI is not served" >&2; exit 1; }
  codesign --verify --deep --strict "$app" || { echo "smoke test: running the server changed the signed bundle" >&2; exit 1; }
  rm -rf "$home"
  echo "smoke ok"
fi

step "Disk image"
dmg="$out/root-$version-$arch.dmg"
rm -f "$dmg"
image="$(mktemp -d)"
ditto "$app" "$image/root.app"
ln -s /Applications "$image/Applications"
hdiutil create -quiet -volname "root $version" -srcfolder "$image" -ov -format UDZO "$dmg"
rm -rf "$image"
[ -n "${SIGN_IDENTITY:-}" ] && codesign --force --timestamp --sign "$SIGN_IDENTITY" "$dmg"

if [ -n "${NOTARY_PROFILE:-}" ]; then
  [ -n "${SIGN_IDENTITY:-}" ] || { echo "notarizing needs SIGN_IDENTITY" >&2; exit 1; }
  step "Notarizing (uploads the disk image to Apple)"
  xcrun notarytool submit "$dmg" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$dmg"
  xcrun stapler staple "$app"
fi

du -sh "$app" "$dmg"
echo "Built $app and $dmg"
