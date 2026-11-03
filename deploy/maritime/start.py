"""Start Skye on a VM: everything persistent under /data, the UI on the platform's $PORT,
the Telegram bot when SKYE_TELEGRAM_TOKEN is set.

SKYE_ROLE picks what the machine is for:
* unset or ``worker`` - one person's Skye (``worker`` also answers Maritime's POST /chat, which
  the front desk uses; see skye/chat.py);
* ``front-desk`` - the desk that gives everyone who messages Skye their own worker
  (skye/frontdesk.py).

/data survives sleep and redeploys, so $HOME lives there too: Claude Code keeps its sign-in
in ~/.claude and the GitHub CLI in ~/.config/gh, and both stay signed in across restarts.
"""
import os
import subprocess
import sys

DATA = os.environ.get("SKYE_DATA", "/data")
home = os.path.join(DATA, "home")
for path in (home, os.path.join(DATA, "skye"), os.path.join(DATA, "work")):
    os.makedirs(path, exist_ok=True)
os.environ["HOME"] = home
os.environ.setdefault("SKYE_HOME", os.path.join(DATA, "skye"))
os.environ.setdefault("SKYE_WORK_DIR", os.path.join(DATA, "work"))
# The VM is reached only through the platform's front door, under a host name Skye cannot
# know in advance; Skye's own session token still guards every API call.
os.environ.setdefault("SKYE_UI_HOST", "0.0.0.0")

# Commits on a fresh machine: a neutral identity until the owner sets their own.
for key, value in (("user.name", "Skye"), ("user.email", "skye@users.noreply.github.com"),
                   ("init.defaultBranch", "main")):
    if subprocess.run(["git", "config", "--global", key], capture_output=True).returncode != 0:
        subprocess.run(["git", "config", "--global", key, value], check=False)

port = os.environ.get("PORT", "8080")
if os.environ.get("SKYE_ROLE", "").strip().lower() == "front-desk":
    os.environ.setdefault("SKYE_DESK_HOME", os.path.join(DATA, "desk"))
    os.execvp(sys.executable, [sys.executable, "-m", "skye.frontdesk"])
os.execvp(sys.executable, [sys.executable, "-m", "skye.cli", "run", "--foreground",
                           "--no-browser", "--port", port])
