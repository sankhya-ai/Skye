#!/usr/bin/env bash
# Back up a relay that runs on your own server (relay/serve.mjs): one consistent copy of all its
# data, as a .tar.gz, keeping the newest 14. vm.sh runs it every night.
#
#   relay/backup.sh [data dir] [backups dir]      default /var/lib/chamber-relay /var/backups/chamber-relay
#
# The databases are copied with SQLite's own backup (consistent while the relay runs) when sqlite3
# is installed; the files as they are. To restore: stop the relay, empty the data dir, extract the
# backup into it, give it back to the relay's user, start the relay.
set -euo pipefail

DATA="${1:-/var/lib/chamber-relay}"
BACKUPS="${2:-/var/backups/chamber-relay}"
KEEP="${CHAMBER_BACKUPS_KEPT:-14}"
[ -d "$DATA" ] || { echo "no relay data at $DATA" >&2; exit 1; }
mkdir -p "$BACKUPS"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -a "$DATA/." "$STAGE/"
if command -v sqlite3 >/dev/null; then
  find "$STAGE" \( -name '*.sqlite-wal' -o -name '*.sqlite-shm' \) -delete
  (cd "$DATA" && find . -name '*.sqlite') | while read -r db; do
    sqlite3 "$DATA/$db" ".backup '$STAGE/$db'"
  done
fi

OUT="$BACKUPS/relay-$(date -u +%Y%m%d-%H%M%S).tar.gz"
tar -czf "$OUT" -C "$STAGE" .
ls -1t "$BACKUPS"/relay-*.tar.gz | tail -n +"$((KEEP + 1))" | while read -r old; do rm -f "$old"; done
echo "$OUT"
