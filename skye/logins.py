"""Sign the coding agents in from a chat, on a machine with no browser.

Both vendors' own CLIs already do a browser-less sign-in, so Skye drives those instead of
holding any OAuth app of its own:

* ``claude auth login`` prints a claude.com link; the person signs in on their phone, the
  page shows a code, and the CLI waits at ``Paste code here`` for it. The link is tied to
  THAT process (PKCE), so the process is kept alive between the two chat messages and the
  code is typed into it.
* ``gh auth login --web`` prints a one-time code and github.com/login/device, then polls
  GitHub until the person approves it. ``gh auth setup-git`` afterwards makes plain
  ``git clone/push`` use the same account.

Each CLI keeps its credentials in its own place under $HOME (``~/.claude``,
``~/.config/gh``), so on a VM whose $HOME is on the persistent volume the sign-in survives
sleep and redeploys. Skye never reads or stores those credentials itself.
"""
from __future__ import annotations

import json
import os
import re
import select
import shutil
import subprocess
import threading
import time
from typing import Any

ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-_]")
CLAUDE_URL = re.compile(r"https://\S*oauth/authorize\S+")
GH_CODE = re.compile(r"one-time code:\s*([A-Z0-9]{4}-[A-Z0-9]{4})")
LOGIN_TTL = 900.0  # both vendors expire an unused sign-in well within 15 minutes


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # A dumb terminal: no colour, and gh does not stop to query the terminal (OSC 11 /
    # cursor position), which would hang a pty nobody answers. BROWSER=true: nothing to open.
    env.update({"TERM": "dumb", "NO_COLOR": "1", "BROWSER": "true", "GH_PROMPT_DISABLED": "1"})
    for key in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT"):
        env.pop(key, None)
    return env


class PtyLogin:
    """One interactive CLI sign-in held open in a pseudo-terminal: its output is collected
    in the background, ``send`` types a line into it, ``wait`` blocks until it exits."""

    def __init__(self, argv: list[str]) -> None:
        import pty
        self.argv = argv
        self.started = time.monotonic()
        self._out = ""
        self._lock = threading.Lock()
        # openpty + Popen rather than pty.fork(): Skye's server is multi-threaded, and
        # subprocess forks-and-execs safely where a bare fork() could deadlock the child.
        self.fd, slave = pty.openpty()
        try:
            self.proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave,
                                         env=_env(), start_new_session=True, close_fds=True)
        finally:
            os.close(slave)
        self.pid = self.proc.pid
        self.returncode: int | None = None
        threading.Thread(target=self._pump, daemon=True, name="skye-login").start()

    def _pump(self) -> None:
        while True:
            try:
                ready, _, _ = select.select([self.fd], [], [], 0.5)
            except (OSError, ValueError):
                break
            if ready:
                try:
                    chunk = os.read(self.fd, 4096)
                except OSError:
                    chunk = b""
                if not chunk:
                    break
                with self._lock:
                    self._out += ANSI.sub("", chunk.decode("utf-8", "replace"))
                    self._out = self._out[-20000:]
            elif self._reap(block=False):
                break
            # (a pty raises EIO on read once the child has exited: handled as end of output)
        self._reap(block=True)
        try:
            os.close(self.fd)
        except OSError:
            pass

    def _reap(self, block: bool) -> bool:
        if self.returncode is not None:
            return True
        code = self.proc.wait() if block else self.proc.poll()
        if code is None:
            return False
        self.returncode = code
        return True

    @property
    def output(self) -> str:
        with self._lock:
            return self._out

    @property
    def alive(self) -> bool:
        return self.returncode is None

    def expired(self) -> bool:
        return time.monotonic() - self.started > LOGIN_TTL

    def send(self, text: str) -> None:
        os.write(self.fd, text.encode() + b"\r")

    def wait_for(self, pattern: re.Pattern, timeout: float) -> "re.Match | None":
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            match = pattern.search(self.output)
            if match or not self.alive:
                return match
            time.sleep(0.1)
        return None

    def wait(self, timeout: float) -> int | None:
        deadline = time.monotonic() + timeout
        while self.alive and time.monotonic() < deadline:
            time.sleep(0.2)
        return self.returncode

    def stop(self) -> None:
        if self.alive:
            try:
                self.proc.kill()
            except OSError:
                pass


def _run(argv: list[str], timeout: float = 20) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=_env())


# -- Claude Code ---------------------------------------------------------------------------

def claude_status() -> dict[str, Any]:
    if not shutil.which("claude"):
        return {"installed": False, "logged_in": False}
    try:
        data = json.loads(_run(["claude", "auth", "status", "--json"]).stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        data = {}
    return {"installed": True, "logged_in": bool(data.get("loggedIn")),
            "plan": str(data.get("subscriptionType") or ""),
            "method": str(data.get("authMethod") or "")}


def claude_begin() -> tuple[PtyLogin, str]:
    """Start ``claude auth login``; returns the held-open process and the sign-in link."""
    login = PtyLogin(["claude", "auth", "login"])
    match = login.wait_for(CLAUDE_URL, 30)
    if not match:
        tail = login.output.strip()[-300:]
        login.stop()
        raise RuntimeError(f"claude did not print a sign-in link{': ' + tail if tail else ''}")
    # The CLI prints the link as a terminal hyperlink as well; the bare URL ends where the
    # OSC 8 wrapper (already stripped) or whitespace begins - cut a doubled copy.
    url = match.group(0)
    half = url.find("https://", 8)
    return login, url[:half] if half > 0 else url


def claude_finish(login: PtyLogin, code: str, timeout: float = 60) -> dict[str, Any]:
    """Type the code the sign-in page showed into the waiting CLI and report the result."""
    code = code.strip()
    if not code or not login.alive:
        return {"ok": False, "detail": "that sign-in is no longer waiting - send /claude again"}
    login.send(code)
    login.wait(timeout)
    status = claude_status()
    if status["logged_in"]:
        login.stop()
        return {"ok": True, **status}
    tail = login.output.strip().splitlines()[-3:]
    login.stop()
    return {"ok": False, "detail": " ".join(tail)[-300:] or "the code was not accepted"}


# -- GitHub ---------------------------------------------------------------------------------

def github_status() -> dict[str, Any]:
    if not shutil.which("gh"):
        return {"installed": False, "logged_in": False}
    try:
        proc = _run(["gh", "api", "user", "--jq", ".login"])
    except (OSError, subprocess.TimeoutExpired):
        return {"installed": True, "logged_in": False}
    login = proc.stdout.strip()
    return {"installed": True, "logged_in": proc.returncode == 0 and bool(login), "login": login}


def github_begin() -> tuple[PtyLogin, str, str]:
    """Start ``gh auth login --web``; returns the process, the one-time code and the link.
    gh then polls GitHub by itself until the person approves (or the code expires)."""
    login = PtyLogin(["gh", "auth", "login", "--hostname", "github.com", "--git-protocol",
                      "https", "--web", "--skip-ssh-key", "--scopes", "repo,read:org,workflow"])
    match = login.wait_for(GH_CODE, 30)
    if not match:
        tail = login.output.strip()[-300:]
        login.stop()
        raise RuntimeError(f"gh did not print a one-time code{': ' + tail if tail else ''}")
    login.wait_for(re.compile(r"Press Enter"), 5)
    login.send("")  # "Press Enter to open github.com" - it opens nothing (BROWSER=true) and polls
    return login, match.group(1), "https://github.com/login/device"


def github_finish(login: PtyLogin, timeout: float = LOGIN_TTL) -> dict[str, Any]:
    """Block until gh's own polling ends; on success make git use the same account."""
    login.wait(timeout)
    login.stop()
    status = github_status()
    if status["logged_in"]:
        _run(["gh", "auth", "setup-git"])
    return status
