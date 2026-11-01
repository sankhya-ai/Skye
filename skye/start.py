"""`skye run` / `skye stop` - the commands an owner uses every time.

`skye run` starts Skye in the background (or reuses the running one) and opens the UI in the
browser; `skye stop` shuts it down. It keeps every piece of Skye's state in ONE folder
(default: ``.skye/`` in the Skye checkout, override with SKYE_HOME), writes a sensible team policy on first run, checks that
Claude Code is installed and logged in, picks a free port, and opens the browser on the
UI. Nothing else to configure: add a project folder in the sidebar and describe the goal.

The first-run policy, stated plainly (Settings shows and edits it):
- Claude Code workers may run without a per-step approval click - pressing Start on a
  goal IS the owner's go-ahead.
- Every mission works in its own git worktree on branch ``skye/<mission-id>``; your own
  checkout is never written until you press "Bring into my project" on a finished mission.
- Each agent role has its own model (planner and reviewer on opus, implementers on sonnet,
  the explorer on haiku), changeable per role on the Agents screen.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

FIRST_RUN_POLICY = {
    "allow_unattended": [{"adapter": "claude-code", "role": "*"},
                         {"adapter": "codex", "role": "*"}],
    "max_concurrent": 2,
    "review_role": "reviewer",
    # One model per agent, changeable on the Agents screen: strong models where judgment
    # matters (planning, review), a balanced one for the many implementation turns.
    "models": {"pm": "opus", "reviewer": "opus", "developer": "sonnet",
               "senior-dev": "sonnet", "backend-dev": "sonnet", "frontend-dev": "sonnet",
               "tester": "sonnet", "artist": "sonnet", "designer": "sonnet",
               "devops": "sonnet", "explorer": "haiku"},
    "workspaces": {"enabled": True},
    "test_command": "",
}

_FILES = {
    "SKYE_STATE_DIR": "",
    "SKYE_POLICY": "policy.json",
    "SKYE_PROJECTS": "projects.json",
    "SKYE_BACKLOG": "backlog.json",
    "SKYE_APPROVALS": "approvals.json",
    "SKYE_ROLES": "roles.json",
    "SKYE_ISSUES": "issues.json",
}


def default_home() -> Path:
    """One state folder no matter where `skye run` is typed: the checkout's .skye/ for a
    source install (the clone-and-run path), else ~/.skye."""
    checkout = Path(__file__).resolve().parent.parent
    if (checkout / "pyproject.toml").exists() and os.access(checkout, os.W_OK):
        return checkout / ".skye"
    return Path.home() / ".skye"


def configure_home(home: str | Path | None = None) -> Path:
    """Point every Skye state file at one folder (env vars the owner already set win)."""
    root = Path(home or os.environ.get("SKYE_HOME", "").strip() or default_home()).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    for var, name in _FILES.items():
        if not os.environ.get(var, "").strip():
            os.environ[var] = str(root / name) if name else str(root)
    # Workers (Claude Code) call `skye say/hear` on the team line: make sure the `skye`
    # that is running now is the one they find, even from inside a virtualenv.
    bin_dir = str(Path(sys.executable).parent)
    path = os.environ.get("PATH", "")
    if bin_dir not in path.split(os.pathsep):
        os.environ["PATH"] = bin_dir + os.pathsep + path
    return root


def ensure_policy() -> tuple[Path, bool]:
    path = Path(os.environ["SKYE_POLICY"])
    if path.exists():
        return path, False
    path.write_text(json.dumps(FIRST_RUN_POLICY, indent=2) + "\n", "utf-8")
    return path, True


def preflight() -> list[str]:
    """Human-readable problems that would stop real work. Empty = ready."""
    problems = []
    if sys.version_info < (3, 10):
        problems.append(f"Python 3.10+ is required (this is {sys.version.split()[0]})")
    if not shutil.which("git"):
        problems.append("git is not installed - Skye gives each mission its own git worktree")
    from .claude_usage import account
    info = account(force=True)
    if not info.get("installed"):
        if not shutil.which("codex"):
            problems.append("Claude Code is not installed: https://claude.com/claude-code "
                            "(then run `claude` once and log in)")
    elif not info.get("logged_in"):
        problems.append("Claude Code is installed but not logged in - run `claude` once in a "
                        "terminal and log in")
    return problems


def _free_port(preferred: int) -> int:
    for port in [preferred] + list(range(preferred + 1, preferred + 20)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit(f"no free port near {preferred}; pass --port")


def _parse(args: list[str]) -> dict:
    opts = {"port": 8787, "browser": True, "home": None, "foreground": False}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--port" and i + 1 < len(args):
            opts["port"] = int(args[i + 1]); i += 2; continue
        if a == "--home" and i + 1 < len(args):
            opts["home"] = args[i + 1]; i += 2; continue
        if a == "--no-browser":
            opts["browser"] = False; i += 1; continue
        if a == "--foreground":
            opts["foreground"] = True; i += 1; continue
        raise SystemExit(f"unknown option {a!r}\nusage: skye run [--port N] [--home DIR] "
                         "[--no-browser] [--foreground]")
    return opts


def _pid_file() -> Path:
    return Path(os.environ["SKYE_STATE_DIR"]) / "server.json"


def _running() -> dict | None:
    """The live server's record (pid, port, url), or None. A stale record is removed."""
    path = _pid_file()
    try:
        record = json.loads(path.read_text("utf-8"))
        os.kill(int(record["pid"]), 0)
    except (OSError, ValueError, KeyError, TypeError):
        path.unlink(missing_ok=True)
        return None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        if sock.connect_ex(("127.0.0.1", int(record.get("port") or 0))) != 0:
            return None  # starting up, or wedged: the caller waits / reports
    return record


def _open(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        pass


def run(args: list[str]) -> int:
    """`skye run`: make sure Skye is running, then open it in the browser."""
    opts = _parse(args)
    root = configure_home(opts["home"])
    policy_path, created = ensure_policy()
    if created:
        print(f"skye: first run - wrote the team policy {policy_path}")
        print("      (Claude Code works unattended inside per-mission git worktrees; "
              "change it in Settings)")
    for problem in preflight():
        print(f"skye: ! {problem}")
    if opts["foreground"]:
        return serve(opts)
    record = _running()
    if record is None:
        log = root / "server.log"
        port = _free_port(opts["port"])
        with open(log, "ab") as handle:
            subprocess.Popen([sys.executable, "-m", "skye.cli", "_serve", "--port", str(port)],
                             stdout=handle, stderr=handle, stdin=subprocess.DEVNULL,
                             start_new_session=True, env=dict(os.environ))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and (record := _running()) is None:
            time.sleep(0.2)
        if record is None:
            print(f"skye: the server did not start - see {log}", file=sys.stderr)
            return 1
        print(f"skye: running (state in {root})")
    else:
        print("skye: already running")
    print(f"skye: {record['url']}")
    print("skye: stop it with `skye stop`")
    if opts["browser"]:
        _open(record["url"])
    return 0


def serve(opts: dict) -> int:
    """The server process itself (foreground). Records pid/port/url for run/stop."""
    import signal
    from .ui import server
    port = _free_port(int(opts.get("port") or 8787))
    state_dir = Path(os.environ["SKYE_STATE_DIR"])

    def _record() -> None:
        token_file = state_dir / "ui-token"
        for _ in range(100):
            if token_file.exists():
                token = token_file.read_text("utf-8").strip()
                url = f"http://127.0.0.1:{port}/?token={token}"
                _pid_file().write_text(json.dumps({"pid": os.getpid(), "port": port,
                                                   "url": url}) + "\n", "utf-8")
                os.chmod(_pid_file(), 0o600)
                if opts.get("foreground") and opts.get("browser"):
                    _open(url)
                return
            time.sleep(0.05)

    def _term(*_a) -> None:
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)
    threading.Thread(target=_record, daemon=True).start()
    try:
        server.serve(state_dir=state_dir, port=port)
    finally:
        _pid_file().unlink(missing_ok=True)
    return 0


def serve_cli(args: list[str]) -> int:
    opts = _parse(args)
    configure_home(opts["home"])
    return serve(opts)


def stop(args: list[str]) -> int:
    """`skye stop`: shut the background server down (running missions are journaled and
    resume when you start Skye again)."""
    import signal
    configure_home(_parse(args)["home"])
    record = _running()
    path = _pid_file()
    if record is None and path.exists():
        try:
            record = json.loads(path.read_text("utf-8"))
        except ValueError:
            record = None
    if record is None:
        print("skye: not running")
        return 0
    pid = int(record["pid"])
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        path.unlink(missing_ok=True)
        print("skye: not running")
        return 0
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.1)
    else:
        os.kill(pid, signal.SIGKILL)
    path.unlink(missing_ok=True)
    print("skye: stopped")
    return 0


def git_identity_args(repo: str | Path) -> list[str]:
    """`-c user.name/-c user.email` for Skye's own commits when the repo has no identity."""
    def _get(key: str) -> str:
        proc = subprocess.run(["git", "-C", str(repo), "config", key], capture_output=True,
                              text=True)
        return proc.stdout.strip()
    args = []
    if not _get("user.name"):
        args += ["-c", "user.name=Skye"]
    if not _get("user.email"):
        args += ["-c", "user.email=skye@localhost"]
    return args
