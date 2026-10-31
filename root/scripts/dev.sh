#!/usr/bin/env bash
# Run the host (with auto-reload of nothing but the UI) and Vite together.
# The UI on http://localhost:5173 proxies /api to the host on :8787.
set -euo pipefail
cd "$(dirname "$0")/.."

server/.venv/bin/tura-code --port 8787 &
host=$!
trap 'kill $host 2>/dev/null || true' EXIT

(cd ui && [ -d node_modules ] || npm install --no-fund --no-audit)
cd ui && npm run dev
