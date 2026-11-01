#!/bin/sh
# Release build for the skye package. The UI server serves its screens from
# skye/ui/dist, which is a BUILD OUTPUT of skye/ui/web (vite) - a wheel built
# without this step installs a server with no screens. This script is the only
# supported order: web bundle first, then the wheel, from a clean checkout.
set -eu
cd "$(dirname "$0")/.."
echo "== building the web bundle (skye/ui/web -> skye/ui/dist)"
(cd skye/ui/web && npm ci --no-audit --no-fund && npm run build)
test -f skye/ui/dist/index.html || { echo "web build produced no dist/index.html" >&2; exit 1; }
echo "== building the wheel"
python3 -m pip wheel --no-deps -w dist-wheel .
echo "== done: wheel(s) in dist-wheel/"
