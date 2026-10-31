#!/usr/bin/env bash
# Make AppIcon.icns from the Tura mark (ui/src/components/TuraBrand.tsx) with macOS tools only.
#   macos/make-icon.sh <out.icns>
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
out="${1:?usage: make-icon.sh <out.icns>}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mark="$(sed -n 's/^const MARK = "\(.*\)";$/\1/p' "$here/../ui/src/components/TuraBrand.tsx")"
[ -n "$mark" ] || { echo "make-icon: could not read the Tura mark" >&2; exit 1; }
# A dark rounded square, the mark in paper white, its dot in Tura blue; the mark's
# viewBox is 304×357 at (129.75, 178.31), scaled to 560 px tall and centred.
cat > "$work/icon.svg" <<SVG
<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
  <rect x="80" y="80" width="864" height="864" rx="196" fill="#15171a"/>
  <g transform="translate(512 512) scale(1.5676) translate(-281.80 -356.93)">
    <path d="$mark" fill="#f4f1ea"/>
    <circle cx="183.50" cy="441.00" r="39.75" fill="#4e83b0"/>
  </g>
</svg>
SVG
png="$work/icon.png"
xcrun swift "$here/render-svg.swift" "$work/icon.svg" "$png" 1024
[ -f "$png" ] || { echo "make-icon: could not render the icon" >&2; exit 1; }
set_dir="$work/AppIcon.iconset"
mkdir "$set_dir"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$png" --out "$set_dir/icon_${size}x${size}.png" >/dev/null
  sips -z $((size * 2)) $((size * 2)) "$png" --out "$set_dir/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$set_dir" -o "$out"
