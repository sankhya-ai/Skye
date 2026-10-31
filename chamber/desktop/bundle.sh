#!/usr/bin/env bash
# Build the downloadable Mac app (Chamber.app and its .dmg) with its own Node inside, so the
# person who downloads it needs nothing installed.
#
#   desktop/bundle.sh             unsigned, for trying it out
#   desktop/bundle.sh --release   Developer ID signed, notarized and stapled: what you publish
#
# --release uses the same signing identity and notary profile as Tura's Mac app
# (see Plank/docs/tura-release.md). Override with CHAMBER_SIGNING_IDENTITY / CHAMBER_NOTARY_PROFILE.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/src-tauri"

RELEASE=false; [ "${1:-}" = "--release" ] && RELEASE=true
VERSION="$(node -p 'require("./tauri.conf.json").version')"
# The updater's signing key (the app trusts only updates signed with it): kept outside the repo.
UPDATER_KEY="${CHAMBER_UPDATER_KEY:-$HOME/.chamber-release/updater.key}"
IDENTITY="${CHAMBER_SIGNING_IDENTITY:-Developer ID Application: Shreya Malaviya (986JMARB95)}"
PROFILE="${CHAMBER_NOTARY_PROFILE:-tura-notary}"
ENTITLEMENTS="$PWD/entitlements.plist"
TRIPLE="$(rustc -vV | sed -n 's/^host: //p')"

mkdir -p binaries
cp "$(command -v node)" "binaries/node-$TRIPLE"
npm --prefix ../../relay ci --silent
npm --prefix ../../ui ci --silent && npm --prefix ../../ui run build

if $RELEASE; then
  # Apple rejects a notarization if any executable inside is unsigned or lacks the hardened runtime.
  # Tauri signs the app and the node sidecar; sign everything bundled as a resource here.
  echo "Signing bundled executables…"
  find ../../relay/node_modules -type f -print0 | while IFS= read -r -d '' f; do
    if file -b "$f" | grep -q "Mach-O"; then
      codesign --force --options runtime --timestamp --entitlements "$ENTITLEMENTS" --sign "$IDENTITY" "$f"
    fi
  done
  export APPLE_SIGNING_IDENTITY="$IDENTITY"
  [ -f "$UPDATER_KEY" ] || { echo "No updater key at $UPDATER_KEY: the app could not update to this release." >&2; exit 1; }
fi
# Tauri signs the update archive (Chamber.app.tar.gz) when it has the key; without it (CI), no
# archive: it would otherwise fail the build asking for the key.
NO_UPDATER=()
if [ -f "$UPDATER_KEY" ]; then
  export TAURI_SIGNING_PRIVATE_KEY="$(cat "$UPDATER_KEY")" TAURI_SIGNING_PRIVATE_KEY_PASSWORD=""
else
  NO_UPDATER=(--config '{"bundle":{"createUpdaterArtifacts":false}}')
fi

CHAMBER_NODE="$(command -v node)" npx --yes @tauri-apps/cli@2 build --bundles app ${NO_UPDATER[@]+"${NO_UPDATER[@]}"}

# The disk image: the signed app and an Applications shortcut (hdiutil, not Tauri's Finder-scripted one).
OUT="target/release/bundle"
DMG="$OUT/dmg/chamber-darwin-arm64.dmg"
STAGE="$(mktemp -d)"
mkdir -p "$OUT/dmg"; rm -f "$DMG"
cp -R "$OUT/macos/Chamber.app" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname Chamber -srcfolder "$STAGE" -format UDZO -fs HFS+ "$DMG"
rm -rf "$STAGE"

if $RELEASE; then
  codesign --force --timestamp --sign "$IDENTITY" "$DMG"
  echo "Notarizing (a few minutes)…"
  xcrun notarytool submit "$DMG" --keychain-profile "$PROFILE" --wait
  xcrun stapler staple "$DMG"
  spctl -a -vvv -t exec "$OUT/macos/Chamber.app"
  xcrun stapler validate "$DMG"

  # The update: the notarized app (stapled too), as an archive signed for the updater, and the
  # latest.json the app checks (GitHub release v$VERSION, marked latest).
  xcrun stapler staple "$OUT/macos/Chamber.app"
  UPDATE="$OUT/update"; rm -rf "$UPDATE"; mkdir -p "$UPDATE"
  COPYFILE_DISABLE=1 tar -czf "$UPDATE/Chamber.app.tar.gz" -C "$OUT/macos" Chamber.app
  # The key is in TAURI_SIGNING_PRIVATE_KEY already (set above); the signer refuses it twice (-f too).
  npx --yes @tauri-apps/cli@2 signer sign -p "" "$UPDATE/Chamber.app.tar.gz" >/dev/null
  SIG="$(cat "$UPDATE/Chamber.app.tar.gz.sig")" VERSION="$VERSION" node -e '
    const { VERSION, SIG } = process.env;
    console.log(JSON.stringify({ version: VERSION, pub_date: new Date().toISOString(), platforms: {
      "darwin-aarch64": { signature: SIG, url: `https://github.com/tura-learn/chamber/releases/download/v${VERSION}/Chamber.app.tar.gz` } } }, null, 2));
  ' > "$UPDATE/latest.json"
  echo "Update for the app: gh release create v$VERSION $UPDATE/Chamber.app.tar.gz $UPDATE/latest.json $DMG --latest"
fi

echo "Upload to R2: chamber/releases/stable/<version>/chamber-darwin-arm64.dmg  ($PWD/$DMG)"
