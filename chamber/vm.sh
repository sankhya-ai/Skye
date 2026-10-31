#!/usr/bin/env bash
# Host your chamber's relay on your own Linux machine: a VM (e.g. Maritime), a server, a spare box.
# The other way to host it is your Cloudflare account (the app sets that up for you).
#
#   curl -fsSL https://raw.githubusercontent.com/tura-learn/chamber/main/vm.sh | sudo DOMAIN=relay.example.com bash
#
# With DOMAIN (its DNS pointing at this machine, ports 80 and 443 open) you get HTTPS from Let's Encrypt
# through Caddy. Without it the relay answers plain HTTP on port 8787: fine to try, but the chamber's
# access tokens would cross the network unencrypted, so use a domain for a real team.
#
# It keeps everything (messages, files, members, team memory, tasks, gates) in /var/lib/chamber-relay
# and backs it up every night to /var/backups/chamber-relay (the newest 14). To keep less, set
# CHAMBER_RETENTION_DAYS, CHAMBER_MAX_MESSAGES or CHAMBER_KEEP_FILES=0 (see relay/serve.mjs).
#
# Then in Chamber: Create a chamber → Use my own server → paste the address this prints.
# The first chamber made there claims the relay; it serves only that chamber from then on.
# Re-run anytime to update; the data in /var/lib/chamber-relay stays.
set -euo pipefail

DOMAIN="${DOMAIN:-}"
PORT="${PORT:-8787}"
SRC=/opt/chamber
DATA=/var/lib/chamber-relay
BACKUPS=/var/backups/chamber-relay
NODE_VERSION=22.20.0
REPO="${CHAMBER_REPO:-https://codeload.github.com/tura-learn/chamber/tar.gz/refs/heads/main}"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\nchamber: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || fail "run it as root (… | sudo bash)"
[ "$(uname -s)" = Linux ] || fail "this sets up a Linux server; on a Mac or Windows use: node relay/serve.mjs"
command -v systemctl >/dev/null || fail "needs systemd"
command -v curl >/dev/null || fail "needs curl"

# ----- 1. Node 22 (the relay runs on workerd, which wrangler starts with Node) --------------
say "1/5  Node.js"
if ! command -v node >/dev/null || ! node -e 'process.exit(+process.versions.node.split(".")[0] >= 22 ? 0 : 1)'; then
  case "$(uname -m)" in x86_64) ARCH=x64 ;; aarch64|arm64) ARCH=arm64 ;; *) fail "unsupported CPU $(uname -m)" ;; esac
  curl -fsSL "https://nodejs.org/dist/v$NODE_VERSION/node-v$NODE_VERSION-linux-$ARCH.tar.xz" | tar -xJ -C /usr/local --strip-components=1
fi
echo "node $(node -v)"

# ----- 2. the relay ------------------------------------------------------------------------
say "2/5  Chamber relay"
id chamber >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin chamber
mkdir -p "$DATA" && chown chamber: "$DATA"
TMP="$(mktemp -d)"
curl -fsSL "$REPO" | tar -xz -C "$TMP"
# Stop the relay while its source is replaced: it watches its files and would rebuild half-copied ones.
systemctl stop chamber-relay 2>/dev/null || true
rm -rf "$SRC" && mv "$TMP"/* "$SRC" && rm -rf "$TMP"
(cd "$SRC/relay" && npm ci --silent --no-audit --no-fund)
chown -R chamber: "$SRC" # wrangler keeps its working files in relay/.wrangler

# Behind Caddy it listens on localhost only; without a domain, on every interface.
HOST=0.0.0.0; [ -n "$DOMAIN" ] && HOST=127.0.0.1
cat > /etc/systemd/system/chamber-relay.service <<EOF
[Unit]
Description=Chamber relay
After=network-online.target
Wants=network-online.target

[Service]
User=chamber
Environment=HOST=$HOST PORT=$PORT CHAMBER_RELAY_DATA=$DATA HOME=$DATA
Environment=CHAMBER_RETENTION_DAYS=${CHAMBER_RETENTION_DAYS:-0} CHAMBER_MAX_MESSAGES=${CHAMBER_MAX_MESSAGES:-0} CHAMBER_KEEP_FILES=${CHAMBER_KEEP_FILES:-1}
ExecStart=$(command -v node) $SRC/relay/serve.mjs
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now chamber-relay >/dev/null
systemctl restart chamber-relay

# ----- 3. HTTPS ----------------------------------------------------------------------------
say "3/5  HTTPS"
if [ -n "$DOMAIN" ]; then
  if ! command -v caddy >/dev/null; then
    command -v apt-get >/dev/null || fail "install Caddy (https://caddyserver.com/docs/install), then re-run"
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https gnupg >/dev/null
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq && apt-get install -y -qq caddy >/dev/null
  fi
  cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN {
	reverse_proxy 127.0.0.1:$PORT
}
EOF
  systemctl reload caddy 2>/dev/null || systemctl restart caddy
  ADDRESS="https://$DOMAIN"
else
  echo "No DOMAIN given: plain HTTP on port $PORT."
  ADDRESS="http://$(curl -fsS https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}'):$PORT"
fi

# ----- 4. nightly backups ------------------------------------------------------------------
say "4/5  Backups"
command -v sqlite3 >/dev/null || { command -v apt-get >/dev/null && apt-get install -y -qq sqlite3 >/dev/null; } || true
command -v sqlite3 >/dev/null || echo "Without sqlite3 a backup copies the databases as they are; install it for consistent copies."
mkdir -p "$BACKUPS" && chmod 700 "$BACKUPS"
# A root-owned copy: the backup runs as root, and the relay's own user can write to $SRC.
install -m 755 -o root -g root "$SRC/relay/backup.sh" /usr/local/bin/chamber-relay-backup
cat > /etc/systemd/system/chamber-relay-backup.service <<EOF
[Unit]
Description=Back up the Chamber relay's data

[Service]
Type=oneshot
ExecStart=/usr/local/bin/chamber-relay-backup $DATA $BACKUPS
EOF
cat > /etc/systemd/system/chamber-relay-backup.timer <<EOF
[Unit]
Description=Back up the Chamber relay's data every night

[Timer]
OnCalendar=*-*-* 03:30:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now chamber-relay-backup.timer >/dev/null
echo "Every night to $BACKUPS. Now: systemctl start chamber-relay-backup"

# ----- 5. check ----------------------------------------------------------------------------
say "5/5  Checking"
for _ in $(seq 1 60); do curl -fsS "http://127.0.0.1:$PORT/v1/relay" >/dev/null 2>&1 && break; sleep 1; done
curl -fsS "http://127.0.0.1:$PORT/v1/relay" >/dev/null || fail "the relay did not start: journalctl -u chamber-relay"

say "Your relay is up: $ADDRESS"
echo "In Chamber: Create a chamber → Use my own server → paste $ADDRESS"
echo "Logs: journalctl -u chamber-relay -f    Data: $DATA    Backups: $BACKUPS"
echo "Restore: systemctl stop chamber-relay; rm -rf $DATA/*; tar -xzf <backup> -C $DATA; chown -R chamber: $DATA; systemctl start chamber-relay"
