import json
import os
import subprocess
import threading
import time

from .base import (AdapterError, SessionAdapter, SessionResult, _LIVE, _LIVE_LOCK,
                   _signal_group, live_claim, live_record, live_release)


class CodexAdapter(SessionAdapter):
    """The user's own Codex CLI install. Non-interactive ``codex exec`` mode, which runs read-only
    by default; the sandbox widens to workspace-write only when the role's gateway catalogue grants
    writes (verified against the current Codex non-interactive docs; ``--full-auto`` is deprecated
    and never used). The sandbox is Codex's own mechanism, not an OS boundary Skye provides.
    ``codex exec`` refuses to run outside a git repository unless told otherwise - Skye leaves that
    vendor safety check in place.
    """
    name = "codex"
    binary = "codex"
    # Only the wall-clock timeout is enforceable here; max_steps/max_cost_usd surface as notes.
    ENFORCED_BUDGET = ("timeout_seconds",)
    # Live steering: the app-server session mode (run_session below) supports it via
    # `turn/steer`; the exec fallback does not. steer_into() answers per RUN MODE from
    # the registry entry, so an exec-mode run reports "unsupported" honestly.
    SUPPORTS_STEER = True

    def _prompt(self, contract: dict) -> str:
        prompt = contract.get("brief") or contract["goal"]
        if contract.get("role_instructions"):
            prompt = contract["role_instructions"] + "\n\n" + prompt
        if contract.get("reference_docs"):
            refs = "\n\n".join(
                f"--- {d['path']} (owner-supplied project reference) ---\n{d['content']}"
                for d in contract["reference_docs"])
            prompt = "Project references:\n" + refs + "\n\n" + prompt
        return prompt

    def _sandbox(self, contract: dict) -> str:
        return "workspace-write" if bool(contract.get("tools_writable")) else "read-only"

    def build_command(self, contract: dict) -> list[str]:
        return [self.binary, "exec", "--sandbox", self._sandbox(contract),
                self._prompt(contract)]

    # -- Session protocol (--json events) ------------------------------------------
    # Grounded in the Codex CLI source (codex-rs/exec): `codex exec --json` emits one
    # JSON ThreadEvent per line - thread.started carries the thread_id "used to resume
    # the thread later" (exec_events.rs) - and `codex exec resume <SESSION_ID> [prompt]`
    # re-enters a recorded session (cli.rs ResumeArgs). Options precede the subcommand,
    # matching the clap layout (global exec options, then resume/fork/review).

    def build_session_command(self, contract: dict, resume_session_id: str | None) -> list[str]:
        base = [self.binary, "exec", "--json", "--sandbox", self._sandbox(contract)]
        if resume_session_id:
            # The role's sandbox still applies on a resumed session: continuity never
            # widens what the worker may touch.
            return base + ["resume", resume_session_id, self._prompt(contract)]
        return base + [self._prompt(contract)]

    def parse_stream_line(self, line: str) -> list[dict]:
        try:
            data = json.loads(line)
        except ValueError:
            return []
        kind = data.get("type")
        if kind == "thread.started":
            return [{"kind": "session", "session_id": str(data.get("thread_id") or "")}]
        if kind == "item.completed":
            item = data.get("item") or {}
            item_type = item.get("type")
            if item_type == "agent_message" and item.get("text"):
                return [{"kind": "message", "text": str(item["text"])}]
            if item_type == "command_execution":
                return [{"kind": "tool", "name": str(item.get("command") or "")[:120]}]
            return []
        if kind == "turn.completed":
            usage = data.get("usage") or {}
            return [{"kind": "result", "ok": True, "session_id": "",
                     "usage": {k: usage[k] for k in
                               ("input_tokens", "output_tokens") if k in usage}}]
        if kind == "turn.failed":
            return [{"kind": "result", "ok": False, "session_id": "",
                     "usage": {}},
                    {"kind": "error", "text": str((data.get("error") or {})
                                                  .get("message") or "turn failed")}]
        if kind == "error":
            return [{"kind": "error", "text": str(data.get("message") or "stream error")}]
        return []


    # -- App-server session protocol ------------------------------------------------
    # Grounded in codex-rs source (app-server-protocol + app-server-transport): newline-
    # delimited JSON-RPC 2.0 over stdio (`codex app-server`; transport reads stdin line
    # by line). Flow: initialize {clientInfo} -> {userAgent, codexHome, ...}; thread/start
    # {cwd, sandbox} or thread/resume {threadId}; turn/start {threadId, input:[{type:
    # "text", text}]}; stream turn/started, item/completed (agentMessage text,
    # commandExecution), turn/completed. Live follow-up: turn/steer {threadId, input,
    # expectedTurnId} - the METHOD is a stable (non-experimental) v2 request with a
    # provider-enforced active-turn precondition; only some optional FIELDS are marked
    # experimental, and Skye sends none of them. We do NOT opt into the experimentalApi
    # capability. Version guard: any unexpected handshake shape BEFORE work starts falls
    # back to exec; a protocol break mid-turn fails closed (no silent exec re-run after
    # side effects may have happened).

    #: handshake fields that must be present and correctly typed (version guard)
    _APP_SERVER_GUARD = {"userAgent": str, "codexHome": str, "platformFamily": str}

    def run_session(self, contract: dict, *, timeout: int = 1800,
                    resume_session_id: "str | None" = None,
                    run_key: "str | None" = None,
                    on_event=None,
                    cancel_event: "threading.Event | None" = None,
                    live_file: "str | None" = None,
                    run_instance: "str | None" = None) -> SessionResult:
        # CODEX_PROTOCOL=exec pins the older exec-JSONL transport for codex builds
        # that predate the app-server; anything else uses the app-server and falls
        # back to exec (loudly, via the protocol field) if the handshake cannot
        # complete.
        if os.environ.get("CODEX_PROTOCOL", "").strip().lower() == "exec":
            return super().run_session(
                contract, timeout=timeout, resume_session_id=resume_session_id,
                run_key=run_key, on_event=on_event, cancel_event=cancel_event,
                live_file=live_file, run_instance=run_instance)
        if not self.available():
            raise AdapterError(
                f"{self.name}: the '{self.binary}' tool is not installed on this machine. "
                "Install it yourself - Skye uses YOUR coding agent, it does not download one.")
        continuity = "resumed" if resume_session_id else "fresh"
        command = [self.binary, "app-server"]
        guard = live_claim(live_file, command, run_key, continuity)
        if guard is not None:
            return guard
        proc = subprocess.Popen(
            command, cwd=contract["repo"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
            start_new_session=True)  # its own process group: cancel/timeout signal the tree
        live_record(live_file, proc, run_key, self.name)
        if cancel_event is None:
            cancel_event = threading.Event()
        entry = None
        if run_key:
            entry = {"proc": proc, "cancel": cancel_event, "adapter": self,
                     "steers": 0, "closing": False, "lock": threading.Lock(),
                     "run_instance": run_instance,
                     "rpc": {"thread_id": "", "turn_id": "", "seq": 0}}
            with _LIVE_LOCK:
                _LIVE[run_key] = entry
        state = {"cancelled": False, "timed_out": False}
        deadline = time.monotonic() + timeout

        def _watchdog() -> None:
            while proc.poll() is None:
                if cancel_event.is_set():
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
        try:
            try:
                result = self._app_server_turn(proc, contract, resume_session_id,
                                               continuity, command, on_event, entry,
                                               cancel_event)
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
                live_release(live_file, proc)
        except _FallbackToExec as error:
            # Handshake failed BEFORE any work: no side effects, so the proven exec
            # session path is a safe, journaled fallback (never after the turn started).
            if cancel_event.is_set():
                return SessionResult(ok=False, cancelled=True, command=command,
                                     returncode=-1, continuity=continuity,
                                     output="cancelled before the run started",
                                     protocol="app-server-jsonrpc;guard=shape-only")
            if on_event is not None:
                on_event({"kind": "error",
                          "text": f"app-server unusable ({error}); running one-shot exec "
                                  "- live follow-ups unsupported this run"})
            result = super().run_session(
                contract, timeout=timeout, resume_session_id=resume_session_id,
                run_key=run_key, on_event=on_event, cancel_event=cancel_event,
                live_file=live_file, run_instance=run_instance)
            result.protocol = f"exec-json-fallback: {str(error)[:160]}"
            return result
        if state["cancelled"] or cancel_event.is_set():
            result.ok = False
            result.cancelled = True
            result.returncode = returncode
            if "cancelled by the operator" not in result.output:
                result.output = ("cancelled by the operator"
                                 + (f"\n{result.output}" if result.output else ""))
        if state["timed_out"]:
            result.ok = False
            result.timed_out = True
            result.returncode = 124
            result.output = f"timed out after {timeout}s"
        return result

    def steer_into(self, entry: dict, text: str) -> str:
        rpc = entry.get("rpc")
        if rpc is None:
            return "unsupported"  # an exec-mode run has no live-input channel
        proc = entry["proc"]
        with entry["lock"]:
            if entry["closing"] or proc.poll() is not None:
                return "exiting"
            if not rpc.get("turn_id"):
                return "no-active-turn"  # provider requires the active turn id
            rpc["seq"] += 1
            rid = f"steer-{rpc['seq']}"
            # Remember WHICH text this request carries: the provider's response only
            # echoes a turn id, so the receipt must be correlated back to the exact
            # follow-up through this map (receipts are per-request, never positional
            # guesses against any later activity).
            rpc.setdefault("steer_texts", {})[rid] = text
            frame = json.dumps({
                "jsonrpc": "2.0", "id": rid, "method": "turn/steer",
                "params": {"threadId": rpc["thread_id"],
                           "input": [{"type": "text", "text": text}],
                           "expectedTurnId": rpc["turn_id"]}}) + "\n"
            # Same boundary as the base adapter: the pre-write checks are the only
            # known-non-delivery signal; a write/flush exception means delivery is
            # UNKNOWN (the frame may be partially or fully in the process) - propagate
            # it so the caller retains its claim instead of retrying into a duplicate.
            proc.stdin.write(frame)
            proc.stdin.flush()
        return "steered"  # SUBMITTED; the provider's steer response is the receipt

    @staticmethod
    def _rpc_reply(proc, request_id, result=None, error=None) -> None:
        frame = {"jsonrpc": "2.0", "id": request_id}
        frame["error" if error else "result"] = error if error else result
        try:
            proc.stdin.write(json.dumps(frame) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def _app_server_turn(self, proc, contract, resume_session_id, continuity, command,
                         on_event, entry, cancel_event=None) -> SessionResult:
        """The full app-server exchange. Protocol-shape failures BEFORE the turn starts
        raise _FallbackToExec (no side effects yet); after turn start they fail closed."""
        rpc = (entry or {}).get("rpc", {"thread_id": "", "turn_id": "", "seq": 0})
        responses: dict = {}
        texts: list[str] = []
        session_id = ""
        usage: dict = {}
        turn_started = False
        turn_done = False
        effect_sent = False  # thread/start or thread/resume went out: effects may exist
        result_ok: "bool | None" = None
        protocol_error = ""

        def _send(method: str, params: dict, req_id: str) -> None:
            try:
                proc.stdin.write(json.dumps(
                    {"jsonrpc": "2.0", "id": req_id, "method": method,
                     "params": params}) + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as error:
                # The app-server went away. Before any effect-bearing request this is a
                # transport failure (fall back to exec); after, it fails closed below.
                if not effect_sent and not turn_started:
                    raise _FallbackToExec(f"app-server closed its input ({error})") from error
                raise

        _send("initialize", {"clientInfo": {"name": "skye", "title": None,
                                            "version": "1"},
                             "capabilities": {"experimentalApi": False}}, "init")
        # One pass over stdout drives the whole exchange: requests advance when their
        # response arrives; notifications become normalized events as they stream.
        phase = "initialize"
        assert proc.stdout is not None
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                responses[msg["id"]] = msg
                rid = msg["id"]
                if rid == "init":
                    result = msg.get("result") or {}
                    if (msg.get("error") is not None
                            or any(not isinstance(result.get(f), t)
                                   for f, t in self._APP_SERVER_GUARD.items())):
                        # Version guard: the server does not speak the protocol shape we
                        # grounded against - fall back BEFORE any side effect.
                        raise _FallbackToExec(f"unexpected initialize response: "
                                              f"{str(msg)[:200]}")
                    _send_notification(proc, "initialized")
                    sandbox = "workspace-write" if contract.get("tools_writable") else "read-only"
                    if resume_session_id:
                        _send("thread/resume", {"threadId": resume_session_id}, "thread")
                    else:
                        _send("thread/start", {"cwd": contract["repo"],
                                               "sandbox": sandbox}, "thread")
                    effect_sent = True
                elif rid == "thread":
                    if msg.get("error") is not None and not resume_session_id:
                        # thread/start failed AFTER the request went out: an explicit
                        # error is not proof no thread was created - classify
                        # conservatively as unknown-effect and fail closed.
                        protocol_error = (f"thread/start returned an error after the "
                                          f"request was sent; thread state unknown - "
                                          f"failing closed: {str(msg.get('error'))[:200]}")
                        break
                    if msg.get("error") is not None:
                        if resume_session_id:
                            # Rejected resume: fail CLOSED, never a silent fresh thread.
                            return SessionResult(
                                ok=False, resume_rejected=True, command=command,
                                returncode=1, continuity=continuity,
                                protocol="app-server-jsonrpc;guard=shape-only",
                                session_id=resume_session_id,
                                output=f"thread/resume rejected: "
                                       f"{str(msg.get('error'))[:300]}")
                    thread = (msg.get("result") or {}).get("thread") or {}
                    session_id = str(thread.get("id") or "")
                    if not session_id:
                        # Ambiguous: the server claimed success but gave no thread id -
                        # a thread may exist. No exec fallback (duplicate risk).
                        protocol_error = ("thread/start succeeded without a thread id; "
                                          "thread state unknown - failing closed")
                        break
                    rpc["thread_id"] = session_id
                    if on_event is not None:
                        on_event({"kind": "session", "session_id": session_id})
                    _send("turn/start",
                          {"threadId": session_id,
                           "input": [{"type": "text",
                                      "text": self._prompt(contract)}]}, "turn")
                elif rid == "turn":
                    if msg.get("error") is not None:
                        protocol_error = f"turn/start failed: {str(msg.get('error'))[:300]}"
                        break
                    turn = (msg.get("result") or {}).get("turn") or {}
                    rpc["turn_id"] = str(turn.get("id") or "")
                    turn_started = True
                elif str(rid).startswith("steer-"):
                    steered_text = rpc.get("steer_texts", {}).pop(str(rid), "")
                    if msg.get("error") is not None:
                        if on_event is not None:
                            on_event({"kind": "steer-rejected",
                                      "text": steered_text,
                                      "detail": str(msg.get("error"))[:300]})
                    else:
                        # ACCEPTED is not CONSUMED: remember it; the receipt is the
                        # provider echoing the steered input as a userMessage item
                        # inside the turn (same parity as Claude's replay echo).
                        rpc.setdefault("steer_accepted", []).append(
                            {"text": steered_text,
                             "turn_id": str((msg.get("result") or {})
                                            .get("turnId", ""))})
                        if on_event is not None:
                            on_event({"kind": "steer-accepted",
                                      "text": steered_text})
                continue
            method = msg.get("method")
            params = msg.get("params") or {}
            if method is not None and "id" in msg:
                # A SERVER->CLIENT request. Skye never approves escalations: known
                # approval methods get an explicit DECLINE (grounded: v2/item.rs
                # CommandExecutionApprovalDecision/FileChangeApprovalDecision "decline").
                # The sandbox field is the CLI-CONFIGURED execution scope per upstream
                # policy semantics (read-only denies writes per the CLI's config); its
                # enforcement strength on any real platform is the CLI's claim, not one
                # Skye has verified - Skye's own guarantee is only that no escalation
                # past the configured scope is ever approved here.
                # requests for interactive input or dynamic tools cannot be served and
                # unknown methods fail closed - all with a JSON-RPC error, all journaled.
                # (approvalPolicy params on thread/turn start are experimental-gated, so
                # enforcement lives here at the decision point, not in a sent policy.)
                if method in ("item/commandExecution/requestApproval",
                              "item/fileChange/requestApproval",
                              "item/permissions/requestApproval"):
                    self._rpc_reply(proc, msg["id"],
                                    result={"decision": "decline"})
                    if on_event is not None:
                        on_event({"kind": "approval-declined",
                                  "text": f"{method}: declined (Skye never "
                                          "auto-approves)"})
                elif method in ("item/tool/requestUserInput",
                                "mcpServer/elicitation/request",
                                "item/tool/call"):
                    self._rpc_reply(proc, msg["id"],
                                    error={"code": -32601,
                                           "message": "skye serves no interactive "
                                                      "requests"})
                    if on_event is not None:
                        on_event({"kind": "server-request-refused",
                                  "text": f"{method}: cannot be served by an "
                                          "unattended run"})
                else:
                    self._rpc_reply(proc, msg["id"],
                                    error={"code": -32601,
                                           "message": f"unknown server request: "
                                                      f"{method}"})
                    if on_event is not None:
                        on_event({"kind": "server-request-unknown",
                                  "text": f"{method}: unrecognized - refused"})
                continue
            if method == "turn/started":
                turn = params.get("turn") or {}
                if turn.get("id"):
                    rpc["turn_id"] = str(turn["id"])
                turn_started = True
            elif method == "item/completed":
                item = params.get("item") or {}
                itype = item.get("type")
                if itype == "userMessage":
                    # The provider surfaced steered input as a turn item: THAT is the
                    # consumption receipt for the matching accepted steer.
                    echoed = " ".join(str(part.get("text") or "")
                                      for part in item.get("content") or []
                                      if isinstance(part, dict)).strip()
                    for accepted in list(rpc.get("steer_accepted", [])):
                        if accepted["text"].strip() and (
                                echoed == accepted["text"].strip()):
                            rpc["steer_accepted"].remove(accepted)
                            if on_event is not None:
                                on_event({"kind": "steer-ack",
                                          "text": accepted["text"],
                                          "turn_id": accepted["turn_id"]})
                            break
                if itype == "agentMessage" and item.get("text"):
                    texts.append(str(item["text"]))
                    if on_event is not None:
                        on_event({"kind": "message", "text": str(item["text"])})
                elif itype == "commandExecution":
                    if on_event is not None:
                        on_event({"kind": "tool",
                                  "name": str(item.get("command") or "")[:120]})
            elif method == "turn/completed":
                # Turn over: any steer the server ACCEPTED but never surfaced as a
                # userMessage item was NOT applied - say so, never let the acceptance
                # masquerade as consumption.
                for accepted in rpc.get("steer_accepted", []):
                    if on_event is not None:
                        on_event({"kind": "steer-unapplied",
                                  "text": accepted["text"]})
                rpc["steer_accepted"] = []
                turn = params.get("turn") or {}
                status = str(turn.get("status") or "")
                result_ok = status in ("completed", "")
                turn_done = True
                if entry is not None:
                    with entry["lock"]:
                        entry["closing"] = True
                break  # one process per turn: close stdin in the finally, server exits
        if not turn_started and not turn_done and not protocol_error and proc.poll() not in (None, 0):
            if cancel_event is not None and cancel_event.is_set():
                protocol_error = "cancelled by the operator"
            elif not effect_sent:
                # Died during the handshake, before any thread/turn effect: the proven
                # exec path is a safe, journaled fallback.
                raise _FallbackToExec(f"app-server exited (exit {proc.poll()}) during "
                                      "the handshake")
            else:
                # Effects may exist server-side: retrying under exec could duplicate
                # them. Fail closed with the truthful state.
                protocol_error = (f"app-server exited (exit {proc.poll()}) after "
                                  "thread/turn effects were sent; thread/turn state "
                                  "unknown - failing closed")
        ok = bool(turn_done and result_ok and not protocol_error)
        body = "\n".join(texts)[-8000:]
        if protocol_error:
            # the protocol state is the story - never bury it under partial text
            body = f"{protocol_error}" + (f"\npartial output:\n{body}" if body else "")
        return SessionResult(ok=ok, output=body, command=command,
                             returncode=0 if ok else 1,
                             session_id=session_id, continuity=continuity,
                             protocol="app-server-jsonrpc;guard=shape-only",
                             resume_rejected=False, usage=usage)


class _FallbackToExec(Exception):
    """The app-server handshake failed before any work: run the proven exec path."""


def _send_notification(proc, method: str) -> None:
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
    proc.stdin.flush()
