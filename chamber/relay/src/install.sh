#!/bin/sh
# chamber installer, served by your relay:
#   curl -fsSL __RELAY__/install | sh -s -- <secret> [your-name]
# Installs the `chamber` command, joins the chamber, and puts the project you are
# in (if it is a git repository) on it.
set -e
RELAY="__RELAY__"
MODE="__MODE__"
if [ "$MODE" = "new" ]; then
  SECRET=""
  NAME="$1"
else
  SECRET="$1"
  NAME="$2"
fi
if [ "$MODE" = "join" ] && [ -z "$SECRET" ]; then
  echo "usage: curl -fsSL $RELAY/install | sh -s -- <secret> [your-name]" >&2
  exit 1
fi
if ! command -v node >/dev/null 2>&1; then
  echo "chamber needs Node.js 22.4 or newer: https://nodejs.org" >&2
  exit 1
fi
if ! node -e 'const [a, b] = process.versions.node.split(".").map(Number); process.exit(a > 22 || (a === 22 && b >= 4) ? 0 : 1)'; then
  echo "chamber needs Node.js 22.4 or newer (you have $(node -v))" >&2
  exit 1
fi

APP="${CHAMBER_HOME:-$HOME/.chamber}/app"
for file in __FILES__; do
  mkdir -p "$(dirname "$APP/$file")"
  curl -fsSL "$RELAY/client/$file" -o "$APP/$file"
done
chmod +x "$APP/bin/chamber.mjs"

BIN="${CHAMBER_BIN:-$HOME/.local/bin}"
mkdir -p "$BIN"
printf '#!/bin/sh\nexec node "%s/bin/chamber.mjs" "$@"\n' "$APP" > "$BIN/chamber"
chmod +x "$BIN/chamber"

# Your name is your address in the chamber (@name). Optional: without one, chamber picks your
# git first name or your login (never an account name like root); change it anytime: chamber name <new>.
if [ -z "$NAME" ]; then
  for GUESS in "$(git config --global user.name 2>/dev/null | awk '{print $1}')" "${USER:-}"; do
    GUESS="$(printf '%s' "$GUESS" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9._-')"
    case "$GUESS" in ""|root|admin|administrator|user|ubuntu|ec2-user|dev|developer) continue ;; esac
    NAME="$GUESS"
    break
  done
  [ -n "$NAME" ] || NAME="member-$(od -An -N2 -tx1 /dev/urandom | tr -d ' \n')"
fi
if [ "$MODE" = "new" ]; then
  node "$APP/bin/chamber.mjs" new --relay "$RELAY" --as "$NAME" || exit 1
else
  node "$APP/bin/chamber.mjs" join "$RELAY/#$SECRET" --as "$NAME"
fi
if PROJECT="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  node "$APP/bin/chamber.mjs" setup "$PROJECT"
else
  echo "Next: run 'chamber start' inside each project whose coding agents should be in the chamber."
fi
# The chamber command, from any new terminal: add its folder to PATH in your shell's startup file.
case ":$PATH:" in
  *":$BIN:"*) ;;
  *)
    case "$(basename "${SHELL:-sh}")" in
      zsh) RC="$HOME/.zshrc" ;;
      bash) RC="$HOME/.bashrc" ;;
      *) RC="$HOME/.profile" ;;
    esac
    if ! grep -qs "# added by chamber" "$RC"; then
      printf '\nexport PATH="%s:$PATH"  # added by chamber\n' "$BIN" >> "$RC"
    fi
    echo "Added $BIN to your PATH in $RC (open a new terminal to use it)."
    ;;
esac
echo
echo "You're in. From now on, to come back any day, run this inside your project:"
echo "  chamber start     (updates chamber, connects the project, opens the board)"
