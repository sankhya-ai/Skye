from __future__ import annotations

import json
import os
import sys
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


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
    # A live-file check found a still-running (or unverifiable) worker for this exact
    # step - starting another would run two processes against the same session/repo.
    duplicate_live: bool = False
    #: Which vendor protocol carried the run ("stream-json", "exec-json", "app-server",
    #: "exec-json-fallback:<reason>") - journaled on worker-session for honest reporting.
    protocol: str = ""
    usage: dict = field(default_factory=dict)


# Live session processes, so the UI server's cancel/follow-up endpoints can reach an
# in-flight run in THIS process. Keyed "<mission-id>:<step-id>". Process-group signalling
# and protocol-framed stdin writes only - the registry never touches another mission's
# processes, and steering uses the vendor's own message frame, never terminal injection.
_LIVE_LOCK = threading.Lock()
_LIVE: dict[str, dict] = {}  # run_key -> {proc, cancel, adapter, steers, closing, lock}


def cancel_live(run_key: str, *, grace: float = 5.0) -> str:
    """Stop the live run under run_key and CONFIRM it: SIGTERM to its process group,
    SIGKILL after the grace period, then wait for actual exit. Accepting the signal is
    not death, so the return value says what was verified: "stopped" (the process
    exited), "unkillable" (signals sent but it has not exited - said plainly, never
    claimed stopped), "not-live" (nothing registered - the step may already have
    settled), or "exiting" (the process was already gone)."""
    with _LIVE_LOCK:
        entry = _LIVE.get(run_key)
    if entry is None:
        return "not-live"
    proc, proc_cancel = entry["proc"], entry["cancel"]
    if proc.poll() is not None:
        return "exiting"  # registered but already gone before we could signal
    proc_cancel.set()  # the run records itself as operator-cancelled, not crashed
    _signal_group(proc, grace=grace)
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        return "unkillable"
    return "stopped"


def steer_live(run_key: str, text: str,
               expected_run_instance: "str | None" = None) -> str:
    """Deliver an operator follow-up into a LIVE run through the vendor's own input
    protocol: the CLI queues the message and starts a new turn with it (Claude Code's
    --input-format stream-json: queued user messages process sequentially between turns).
    This is never a mid-tool-call injection and never terminal input. Returns "steered",
    "not-live", "exiting" (the run ended before/while writing - the follow-up was NOT
    delivered), "stale-generation" (the registry slot is held by a DIFFERENT run
    generation than the caller bound its record to - nothing was sent), or
    "unsupported" (the adapter's protocol has no live-input channel).
    ``expected_run_instance`` binds the submission to one registry generation: the
    identity check and the write below act on the SAME entry object, so a
    re-registration after the lookup can never retarget the write at a
    different-generation process (the dead one fails the write loudly instead)."""
    with _LIVE_LOCK:
        entry = _LIVE.get(run_key)
    if entry is None:
        return "not-live"
    if expected_run_instance is not None \
            and entry.get("run_instance") != expected_run_instance:
        return "stale-generation"
    proc = entry["proc"]
    if proc.poll() is not None:
        return "exiting"
    adapter = entry["adapter"]
    if not getattr(adapter, "SUPPORTS_STEER", False):
        return "unsupported"
    # The adapter owns the wire shape and any protocol preconditions (e.g. codex
    # turn/steer's expectedTurnId). "steered" means SUBMITTED to the process; consumption
    # is a provider event (a replay echo / accepted steer response), journaled separately.
    return adapter.steer_into(entry, text)


_IDENTITY_PLATFORM = sys.platform  # where the identity story below was verified


def _pid_identity(pid: int) -> "tuple[str, int] | None":
    """(start-time token, process-group id) for a live pid - a pid alone is not an
    identity (PIDs get reused). The start-time token comes from /proc, which is
    LINUX-ONLY and the only platform this guard is verified on. Elsewhere (e.g. the
    owner's Mac) the token is "" and the pgid comes from the portable os.getpgid: the
    record then honestly cannot distinguish a live worker from a reused pid, and every
    consumer fails closed on that ("unverifiable"), never guesses. None when the process
    is gone or unreadable; the caller treats None as unverifiable, i.e. fail-closed."""
    starttime = ""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        # fields after the (comm) parens: state ppid pgrp session ... starttime(field 22)
        tail = stat[stat.rindex(")") + 2:].split()
        starttime = tail[19]
    except (OSError, ValueError, IndexError):
        pass  # no /proc (non-Linux) or pid raced away: pgid alone, honestly partial
    try:
        pgid = os.getpgid(pid)
    except OSError:
        return None  # process gone
    if not starttime and _IDENTITY_PLATFORM.startswith("linux"):
        return None  # Linux with /proc but unreadable: genuinely unverifiable
    return (starttime, pgid)


def _identity_platform_note() -> str:
    if _IDENTITY_PLATFORM.startswith("linux"):
        return ""
    return (f" (process start-time is not verifiable on {_IDENTITY_PLATFORM} - "
            f"Skye is conservatively treating the live pid as possibly its own worker; "
            f"the Linux-verified identity check does not apply here)")


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness check, fail-CLOSED: anything but a definitive 'no such
    process' is treated as alive (an unverifiable pid must block a duplicate spawn)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


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


def live_claim(live_file: "str | Path | None", command: list, run_key: "str | None",
               continuity: str) -> "SessionResult | None":
    """The pre-spawn duplicate guard (see run_session): a still-running or unverifiable
    prior worker blocks with duplicate_live. On success the step is CLAIMED (O_EXCL
    record) and None returns. Shared by protocol-specific session runners."""
    if live_file is None:
        return None
    live_path = Path(live_file)
    live_path.parent.mkdir(parents=True, exist_ok=True)
    if live_path.exists():
        try:
            prior = json.loads(live_path.read_text("utf-8"))
            prior_pid = int(prior.get("pid") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            prior, prior_pid = None, 0
        if prior is None:
            reason = f"the live record {live_path.name} is unreadable"
        elif prior_pid <= 0:
            reason = ("a prior run claimed this step but crashed before recording its "
                      "worker pid - that worker's fate is unknown")
        elif not _pid_alive(prior_pid):
            reason = ""
        else:
            identity = _pid_identity(prior_pid)
            if identity is None:
                reason = f"pid {prior_pid} is alive but its identity cannot be verified"
            elif not prior.get("starttime") or not prior.get("pgid"):
                reason = (f"pid {prior_pid} is alive but the live record carries no "
                          "start-time/process-group identity to check against"
                          + _identity_platform_note())
            elif identity == (str(prior.get("starttime")), int(prior.get("pgid"))):
                reason = f"worker pid {prior_pid} is still running"
            else:
                reason = ""  # pid reused: the recorded worker is verifiably gone
        if reason:
            return SessionResult(
                ok=False, duplicate_live=True, command=command, returncode=-1,
                continuity=continuity,
                output=(f"a previous worker for this step: {reason} - Skye will not "
                        "start a duplicate against the same session. Stop the old "
                        "process (or confirm it is gone), then resume."))
        try:
            live_path.unlink()
        except OSError:
            pass
    try:
        fd = os.open(live_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps({"claim": True, "claimer": os.getpid(),
                                     "run_key": run_key or "",
                                     "started": time.time()}) + "\n")
    except FileExistsError:
        return SessionResult(
            ok=False, duplicate_live=True, command=command, returncode=-1,
            continuity=continuity,
            output=("another run claimed this step between the check and the spawn - "
                    "refusing to race it"))
    return None


def live_record(live_file: "str | Path | None", proc: "subprocess.Popen",
                run_key: "str | None", adapter_name: str) -> None:
    """Fill the claim with the spawned worker's verified identity (pid + start-time +
    process group)."""
    if live_file is None:
        return
    try:
        identity = _pid_identity(proc.pid) or ("", -1)
        Path(live_file).write_text(json.dumps(
            {"pid": proc.pid, "starttime": identity[0], "pgid": identity[1],
             "run_key": run_key or "", "adapter": adapter_name,
             "identity_platform": _IDENTITY_PLATFORM,
             "started": time.time()}) + "\n", "utf-8")
    except OSError:
        pass


def live_release(live_file: "str | Path | None", proc: "subprocess.Popen") -> None:
    """Remove only OUR record (pid match) - never a successor's claim."""
    if live_file is None:
        return
    try:
        if json.loads(Path(live_file).read_text("utf-8")).get("pid") == proc.pid:
            Path(live_file).unlink()
    except (OSError, ValueError):
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
    #: Whether the vendor protocol accepts follow-up input while a run is live.
    SUPPORTS_STEER = False
    #: seconds of grace between SIGTERM and SIGKILL on cancel/timeout
    CANCEL_GRACE = 5.0

    def build_session_command(self, contract: dict, resume_session_id: str | None) -> list[str]:
        raise NotImplementedError

    def prompt_via_stdin(self, prompt: str) -> "str | None":
        """The framed initial prompt when the protocol takes input on stdin (Claude
        stream-json input), newline-terminated; None when the prompt rides argv."""
        return None

    def steer_frame(self, text: str) -> "str | None":
        """One framed follow-up message for a live process's stdin, newline-terminated;
        None when the protocol has no live-input channel."""
        return None

    def steer_into(self, entry: dict, text: str) -> str:
        """Submit a follow-up into the live run. Default: write steer_frame() to stdin
        under the closing lock; adapters with a request/response protocol override this.
        Returns "steered" (submitted - consumption is a separate provider event),
        "exiting", "no-active-turn", or "unsupported"."""
        frame = self.steer_frame(text)
        if frame is None:
            return "unsupported"
        proc = entry["proc"]
        with entry["lock"]:
            # closing means the final turn's result already landed: a write now would sit
            # in a pipe nobody consumes - report it undelivered instead of pretending.
            if entry["closing"] or proc.poll() is not None:
                return "exiting"
            # The pre-write checks above are the ONLY known-non-delivery signal.
            # Once a write is ATTEMPTED, an exception leaves delivery UNKNOWN: a
            # buffered write can be partially flushed before failing, and a failed
            # flush can follow a fully accepted one. Never infer a partial write
            # delivered zero bytes - propagate so the caller retains its claim and
            # blocks retries instead of doubling the frame.
            assert proc.stdin is not None
            proc.stdin.write(frame)
            proc.stdin.flush()
            entry["steers"] += 1
        return "steered"

    def parse_stream_line(self, line: str) -> list[dict]:
        """One NDJSON line -> normalized events (possibly several, possibly none).
        Normalized kinds: session (session_id), message (text), tool (name),
        result (ok, usage, session_id), error (text)."""
        raise NotImplementedError

    def run_session(self, contract: dict, *, timeout: int = 1800,
                    resume_session_id: str | None = None,
                    run_key: str | None = None,
                    on_event=None,
                    cancel_event: "threading.Event | None" = None,
                    live_file: "str | Path | None" = None,
                    run_instance: "str | None" = None) -> SessionResult:
        if not self.available():
            raise AdapterError(
                f"{self.name}: the '{self.binary}' tool is not installed on this machine. "
                "Install it yourself - Skye uses YOUR coding agent, it does not download one.")
        command = self.build_session_command(contract, resume_session_id)
        prompt_frame = self.prompt_via_stdin(str(contract.get("brief") or contract["goal"]))
        live_path = Path(live_file) if live_file else None
        if live_path is not None:
            live_path.parent.mkdir(parents=True, exist_ok=True)
            if live_path.exists():
                # Crash-consistency guard: a prior run's worker may STILL be alive (the
                # worker is its own process group - it survives a server crash). Identity
                # is pid + start-time + process group, never the pid alone (PIDs are
                # reused). Anything unverifiable blocks: unknowns pause.
                try:
                    prior = json.loads(live_path.read_text("utf-8"))
                    prior_pid = int(prior.get("pid") or 0)
                except (OSError, ValueError, TypeError, AttributeError):
                    prior, prior_pid = None, 0
                if prior is None:
                    reason = f"the live record {live_path.name} is unreadable"
                elif prior_pid <= 0:
                    reason = ("a prior run claimed this step but crashed before recording "
                              "its worker pid - that worker's fate is unknown")
                elif not _pid_alive(prior_pid):
                    reason = ""  # verifiably dead: stale record, safe to replace
                else:
                    identity = _pid_identity(prior_pid)
                    if identity is None:
                        reason = (f"pid {prior_pid} is alive but its identity cannot be "
                                  "verified")
                    elif not prior.get("starttime") or not prior.get("pgid"):
                        # An older/foreign record without identity fields: a live pid with
                        # an unverifiable identity - fail closed, never gamble on reuse.
                        reason = (f"pid {prior_pid} is alive but the live record carries "
                                  "no start-time/process-group identity to check against"
                                  + _identity_platform_note())
                    elif identity == (str(prior.get("starttime")), int(prior.get("pgid"))):
                        reason = f"worker pid {prior_pid} is still running"
                    elif not identity[0]:
                        # Non-Linux record and reading: pgid alone cannot prove reuse -
                        # fail closed (alive pid, unverifiable identity).
                        reason = (f"pid {prior_pid} is alive but cannot be distinguished "
                                  "from a reused pid" + _identity_platform_note())
                    else:
                        reason = ""  # pid reused: the recorded worker is verifiably gone
                if reason:
                    return SessionResult(
                        ok=False, duplicate_live=True, command=command, returncode=-1,
                        continuity="resumed" if resume_session_id else "fresh",
                        output=(f"a previous worker for this step: {reason} - Skye will "
                                "not start a duplicate against the same session. Stop the "
                                "old process (or confirm it is gone), then resume."))
                try:
                    live_path.unlink()  # stale: that process is verifiably gone
                except OSError:
                    pass
            # Claim BEFORE spawning (O_EXCL): two runners racing this step cannot both
            # pass, and a crash in the spawn window leaves a claim-only record the next
            # run pauses on instead of gambling.
            try:
                fd = os.open(live_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w") as handle:
                    handle.write(json.dumps({"claim": True, "claimer": os.getpid(),
                                             "run_key": run_key or "",
                                             "started": time.time()}) + "\n")
            except FileExistsError:
                return SessionResult(
                    ok=False, duplicate_live=True, command=command, returncode=-1,
                    continuity="resumed" if resume_session_id else "fresh",
                    output=("another run claimed this step between the check and the "
                            "spawn - refusing to race it"))
        proc = subprocess.Popen(
            command, cwd=contract["repo"],
            stdin=subprocess.PIPE if prompt_frame is not None or self.SUPPORTS_STEER
            else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
            start_new_session=True)  # its own process group: cancel/timeout signal the tree
        if live_path is not None:
            try:
                identity = _pid_identity(proc.pid) or ("", -1)
                live_path.write_text(json.dumps(
                    {"pid": proc.pid, "starttime": identity[0], "pgid": identity[1],
                     "run_key": run_key or "", "adapter": self.name,
                     "identity_platform": _IDENTITY_PLATFORM,
                     "started": time.time()}) + "\n", "utf-8")
            except OSError:
                pass
        if cancel_event is None:
            cancel_event = threading.Event()
        entry = None
        if run_key:
            entry = {"proc": proc, "cancel": cancel_event, "adapter": self,
                     "steers": 0, "closing": False, "lock": threading.Lock(),
                     "run_instance": run_instance}
            with _LIVE_LOCK:
                _LIVE[run_key] = entry
        if prompt_frame is not None:
            try:
                assert proc.stdin is not None
                proc.stdin.write(prompt_frame)
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass  # the CLI exits on its own; the read loop sees EOF
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
                        # Turn finished. A steer written during it queues a NEW turn
                        # (documented stream-json behavior), so one steered message means
                        # one more result to wait for; with none pending, closing stdin
                        # ends the run. The closing flag makes a steer racing this point
                        # report undelivered instead of vanishing into a closing pipe.
                        finish_turn = True
                        if entry is not None:
                            with entry["lock"]:
                                if entry["steers"] > 0:
                                    entry["steers"] -= 1
                                    finish_turn = False
                                else:
                                    entry["closing"] = True
                        if finish_turn and proc.stdin is not None:
                            try:
                                proc.stdin.close()  # EOF: the CLI exits after this turn
                            except OSError:
                                pass
                    if on_event is not None:
                        on_event(event)
        finally:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            returncode = proc.wait()
            watcher.join(timeout=self.CANCEL_GRACE + 2)
            if run_key:
                with _LIVE_LOCK:
                    _LIVE.pop(run_key, None)
            if live_path is not None:
                try:
                    if json.loads(live_path.read_text("utf-8")).get("pid") == proc.pid:
                        live_path.unlink()
                except (OSError, ValueError):
                    pass
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
