from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field


class AdapterError(RuntimeError):
    """An adapter failure. status_code carries the HTTP status when one was involved, so
    callers can tell "definitively gone" (404) from "cannot reach" (connection error)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class Result:
    ok: bool
    output: str
    command: list[str] = field(default_factory=list)
    returncode: int = 0
    job_id: str = ""  # the worker's own job/run id when the adapter has one (Root does)


@dataclass
class SessionResult(Result):
    """A run through the worker CLI's session protocol. session_id is the vendor's own
    conversation/thread id, captured from the stream - it is what makes a later run RESUME
    the same session instead of starting blind. continuity is "fresh" (new session) or
    "resumed" (the run continued a recorded session via the vendor's resume mechanism).
    resume_rejected means the vendor refused that resume: Skye then fails the run and does
    NOT silently fall back to a fresh session - the owner decides. cancelled means the
    operator's cancel request stopped the run (partial work honestly reported)."""
    session_id: str = ""
    continuity: str = "fresh"  # "fresh" | "resumed"
    cancelled: bool = False
    timed_out: bool = False
    resume_rejected: bool = False
    usage: dict = field(default_factory=dict)


# Live session processes, so the UI server's cancel endpoint can signal an in-flight run
# in THIS process. Keyed "<mission-id>:<step-id>". Process-group signalling only - the
# registry never touches another mission's processes.
_LIVE_LOCK = threading.Lock()
_LIVE: dict[str, tuple] = {}  # run_key -> (Popen, cancel threading.Event)


def cancel_live(run_key: str, *, grace: float = 5.0) -> str:
    """Signal the live run under run_key: SIGTERM to its process group, SIGKILL after the
    grace period. Returns "signalled", "not-live" (nothing registered - the step may
    already have settled), or "exiting" (the process was already gone)."""
    with _LIVE_LOCK:
        entry = _LIVE.get(run_key)
    if entry is None:
        return "not-live"
    proc, proc_cancel = entry
    if proc.poll() is not None:
        return "exiting"  # registered but already gone before we could signal
    proc_cancel.set()  # the run records itself as operator-cancelled, not crashed
    _signal_group(proc, grace=grace)
    return "signalled"


def _signal_group(proc: "subprocess.Popen", *, grace: float = 5.0) -> None:
    """SIGTERM the run's whole process group (the CLI may have spawned children), then
    SIGKILL what survives the grace period. The process was started with
    start_new_session=True, so the group is exactly this run's tree."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.05)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


class Adapter:
    """One coding agent, driven through its own CLI/API. The worker's model is the worker's own
    subscription or key - Skye never proxies or pays for it."""

    name = "base"
    binary = ""
    #: Budget fields this adapter enforces as a hard bound (timeout_seconds is honored by the
    #: shared run() below). Fields in FORWARDED_BUDGET are passed into the worker's own budget
    #: machinery, whose enforcement may depend on its configuration - forwarded, not guaranteed.
    #: Anything else in a contract's budget is reported as a note, never silently ignored.
    ENFORCED_BUDGET: tuple = ("timeout_seconds",)
    FORWARDED_BUDGET: tuple = ()
    #: Optional callable the dispatcher sets to learn the worker's job id the moment it exists
    #: (used to journal it for crash recovery before the worker settles).
    job_observer = None

    def available(self) -> bool:
        return bool(self.binary) and shutil.which(self.binary) is not None

    def build_command(self, contract: dict) -> list[str]:
        raise NotImplementedError

    def run(self, contract: dict, timeout: int = 1800) -> Result:
        if not self.available():
            raise AdapterError(
                f"{self.name}: the '{self.binary}' tool is not installed on this machine. "
                "Install it yourself - Skye uses YOUR coding agent, it does not download one.")
        command = self.build_command(contract)
        try:
            proc = subprocess.run(command, cwd=contract["repo"], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return Result(ok=False, output=f"timed out after {timeout}s", command=command, returncode=124)
        return Result(ok=proc.returncode == 0, output=(proc.stdout + proc.stderr)[-8000:],
                      command=command, returncode=proc.returncode)


class SessionAdapter(Adapter):
    """An adapter whose CLI speaks a streaming session protocol (Claude Code's
    stream-json, Codex's --json events). run_session streams parsed events to on_event as
    they arrive, captures the vendor session id, supports resuming a recorded session, and
    honors operator cancellation with SIGTERM-then-SIGKILL on the process group.

    Model: ONE PROCESS PER TURN. Skye never holds a CLI process open between steps;
    continuity is the vendor's own session store, re-entered with the resume mechanism.
    A follow-up or rework therefore runs a NEW process against the SAME session id - the
    conversation context is preserved by the CLI, not by a lingering process."""

    SUPPORTS_SESSIONS = True
    #: seconds of grace between SIGTERM and SIGKILL on cancel/timeout
    CANCEL_GRACE = 5.0

    def build_session_command(self, contract: dict, resume_session_id: str | None) -> list[str]:
        raise NotImplementedError

    def parse_stream_line(self, line: str) -> list[dict]:
        """One NDJSON line -> normalized events (possibly several, possibly none).
        Normalized kinds: session (session_id), message (text), tool (name),
        result (ok, usage, session_id), error (text)."""
        raise NotImplementedError

    def run_session(self, contract: dict, *, timeout: int = 1800,
                    resume_session_id: str | None = None,
                    run_key: str | None = None,
                    on_event=None,
                    cancel_event: "threading.Event | None" = None) -> SessionResult:
        if not self.available():
            raise AdapterError(
                f"{self.name}: the '{self.binary}' tool is not installed on this machine. "
                "Install it yourself - Skye uses YOUR coding agent, it does not download one.")
        command = self.build_session_command(contract, resume_session_id)
        proc = subprocess.Popen(
            command, cwd=contract["repo"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
            start_new_session=True)  # its own process group: cancel/timeout signal the tree
        if cancel_event is None:
            cancel_event = threading.Event()
        if run_key:
            with _LIVE_LOCK:
                _LIVE[run_key] = (proc, cancel_event)
        state = {"cancelled": False, "timed_out": False}
        deadline = time.monotonic() + timeout

        def _watchdog() -> None:
            while proc.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    state["cancelled"] = True
                    _signal_group(proc, grace=self.CANCEL_GRACE)
                    return
                if time.monotonic() > deadline:
                    state["timed_out"] = True
                    _signal_group(proc, grace=self.CANCEL_GRACE)
                    return
                time.sleep(0.1)

        watcher = threading.Thread(target=_watchdog, daemon=True)
        watcher.start()
        texts: list[str] = []
        session_id = ""
        usage: dict = {}
        result_ok: bool | None = None
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                for event in self.parse_stream_line(line):
                    if event["kind"] == "session":
                        session_id = event["session_id"]
                    elif event["kind"] == "message":
                        texts.append(event["text"])
                    elif event["kind"] == "result":
                        result_ok = bool(event.get("ok"))
                        if event.get("session_id"):
                            session_id = event["session_id"]
                        if event.get("usage"):
                            usage = event["usage"]
                    if on_event is not None:
                        on_event(event)
        finally:
            returncode = proc.wait()
            watcher.join(timeout=self.CANCEL_GRACE + 2)
            if run_key:
                with _LIVE_LOCK:
                    _LIVE.pop(run_key, None)
        stderr_tail = ""
        try:
            stderr_tail = (proc.stderr.read() if proc.stderr else "")[-2000:]
        except OSError:
            pass
        if cancel_event.is_set():
            state["cancelled"] = True
        if state["cancelled"]:
            return SessionResult(ok=False, output="\n".join(texts)[-8000:],
                                 command=command, returncode=returncode,
                                 session_id=session_id, cancelled=True,
                                 continuity="resumed" if resume_session_id else "fresh",
                                 usage=usage)
        if state["timed_out"]:
            return SessionResult(ok=False, output=f"timed out after {timeout}s",
                                 command=command, returncode=124,
                                 session_id=session_id, timed_out=True,
                                 continuity="resumed" if resume_session_id else "fresh",
                                 usage=usage)
        # A resume that failed is NEVER retried fresh here: the caller sees
        # resume_rejected and fails closed, so work never silently loses the session's
        # context. (Any nonzero exit on a resume run is treated as a rejected resume -
        # Skye does not pattern-match vendor error text it cannot verify.)
        rejected = bool(resume_session_id) and returncode != 0
        ok = returncode == 0 and result_ok is not False
        output = "\n".join(texts)[-8000:]
        if stderr_tail and not ok:
            output = (output + "\n" + stderr_tail).strip()[-8000:]
        return SessionResult(ok=ok, output=output, command=command, returncode=returncode,
                             session_id=session_id,
                             continuity="resumed" if resume_session_id else "fresh",
                             resume_rejected=rejected, usage=usage)
