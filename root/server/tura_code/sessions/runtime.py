"""One session: a conversation in one workspace, backed by one runtime process.

A session outlives its process. The runtime keeps the conversation on disk
(``--session <id>`` resumes it), so the host can stop an idle process — or
evict one when the machine is at its limit — and start a new one on the next
prompt, or restart it when a setting that shapes the process changes.

Every event is fanned out live to the session's subscribers (browser tabs)
and appended to the event log (``events``). Permission modes are enforced at
the approval boundary (``approvals``); host tools are dispatched through the
tenant's toolsets (``host_tools``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import launcher, processes
from ..events import EventLog
from ..memory import packet_envelope
from ..wire import WireError, WireProcess
from .approvals import Approvals
from .queue import Queue
from .checkpoints import CheckpointError, Rollback
from .host_tools import tool_error
from .prompting import attachment_meta, build_parts, expand_mentions, title_of
from .record import MODES, SessionRecord, TurnState

if TYPE_CHECKING:
    from ..tenancy import Tenant

logger = logging.getLogger(__name__)

EDIT_TOOLS = {"WriteFile", "StrReplaceFile"}
#: Host tools that change a document (``documents``).
DOCUMENT_EDIT_TOOLS = {"edit_spreadsheet", "edit_pdf"}


class SessionRuntime(Approvals, Rollback, Queue):
    def __init__(self, tenant: "Tenant", record: SessionRecord) -> None:
        self.tenant = tenant
        self.record = record
        self.dir = tenant.sessions_dir / record.id
        self.log = EventLog(self.dir / "events.jsonl")
        self.process: WireProcess | None = None
        self._fingerprint: tuple | None = None
        self._proxy_token = ""
        self._start_lock = asyncio.Lock()
        self.subscribers: set[asyncio.Queue] = set()
        self.pending: dict[str, tuple[asyncio.Future, dict]] = {}
        self.turn: TurnState | None = None
        self._turn_task: asyncio.Task | None = None
        self._turn_done: asyncio.Event = asyncio.Event()
        self._turn_done.set()
        self._seq = self.log.last_seq
        self.last_activity = time.time()
        self.memory_seen: set[str] = set()
        #: Messages the owner sent during a turn (``queue``), oldest first.
        self.queued: list[dict] = []
        self.runtime_state = "stopped"
        self.runtime_info: dict[str, Any] = {}
        self._processes: processes.ProcessManager | None = None
        # Token accounting
        self._token_usage: dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}
        # ContentPart delta coalescing buffer
        self._content_buffer: dict[str, dict] = {}
        self._buffer_flush_task: asyncio.Task | None = None
        #: Set while the host itself stops the process (a restart, eviction, close).
        self._stopping = False
        #: What the last finished turn answered and touched (``jobs`` reads it).
        self.last_reply = ""
        self.last_error = ""
        self.last_budget: dict[str, Any] = {}
        self.last_files: list[str] = []

    # ----- persistence and fan-out ----------------------------------------

    def save_record(self) -> None:
        self.record.save(self.dir)

    def emit(self, kind: str, payload: dict, *, persist: bool = True) -> dict:
        self._seq += 1
        # Update token usage based on event type
        if kind == "UserPrompt":
            self._token_usage["prompt"] += len(payload.get("text", "")) + sum(len(p.get("text", "")) for p in payload.get("attachments", []))
        elif kind == "TurnFinished":
            # Estimate tokens from duration and complexity
            duration = payload.get("duration", 0)
            if duration > 5:
                self._token_usage["completion"] += 2000  # estimate for complex turn
            elif duration > 2:
                self._token_usage["completion"] += 1000
            elif duration > 0.5:
                self._token_usage["completion"] += 500
            self._token_usage["total"] = self._token_usage["prompt"] + self._token_usage["completion"]
        event = {"seq": self._seq, "ts": time.time(), "turn": self.turn.id if self.turn else None, "type": kind, "payload": payload}
        if persist:
            self.log.record(event)
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("dropping a slow subscriber of %s", self.record.id)
                self.subscribers.discard(queue)
        self.last_activity = time.time()
        return event

    def snapshot(self) -> dict:
        page = self.log.page()
        return {
            "session": self.public(),
            "history": page["events"],
            "has_more": page["has_more"],
            "pending_approvals": [payload for _, payload in self.pending.values()],
        }

    def subscribe(self) -> tuple[asyncio.Queue, dict]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=10_000)
        # No await between the snapshot and the registration: an event is in
        # exactly one of the two.
        snapshot = self.snapshot()
        self.subscribers.add(queue)
        return queue, snapshot

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    def public(self) -> dict:
        data = asdict(self.record)
        data["running"] = self.turn is not None
        data["pending_approvals"] = len(self.pending)
        data["queued"] = self.queued_public()
        data["runtime_state"] = self.runtime_state
        data["runtime"] = self.runtime_info
        from .. import catalogue

        data["scope_guarantee"] = catalogue.guarantee(self.tenant, self.record)
        return data

    @property
    def scratch_dir(self) -> Path:
        """The session's own folder for throwaway work and deliverables (``$ROOT_SCRATCH``)."""
        return self.dir / "scratch"

    @property
    def processes(self) -> processes.ProcessManager:
        if self._processes is None:
            self._processes = processes.ProcessManager(
                self.dir / "processes",
                Path(self.record.workspace),
                self.tenant.command_environment(self),
                on_change=lambda record: self.emit("ProcessUpdated", record, persist=False),
                wrap=lambda argv: self.tenant.wrap_command(self, argv),
            )
        return self._processes

    # ----- the runtime process ---------------------------------------------

    async def ensure_process(self) -> WireProcess:
        async with self._start_lock:
            spec = self.tenant.launch_spec(self)
            definitions = self.tenant.tool_definitions(self)
            # The host tools are part of the process: connecting Gmail, say, restarts it.
            fingerprint = (spec.fingerprint(), tuple(sorted(d["name"] for d in definitions)))
            if self.process is not None and self.process.alive and fingerprint == self._fingerprint:
                return self.process
            if self.process is not None:
                await self._stop_process()
            if not spec.key() and not spec.endpoint and not launcher.scripted():
                raise WireError("No OpenRouter API key is set. Add one in Settings, or set OPENROUTER_API_KEY.")
            await self.tenant.pool.admit(self)
            binary = launcher.find_runtime_binary()
            if spec.endpoint:
                self._proxy_token = spec.endpoint[1]
            runtime_dir = self.dir / "runtime"
            config_file = launcher.write_runtime_config(spec, runtime_dir)
            agent_file = launcher.write_agent_overlay(spec, runtime_dir)
            env = launcher.child_environment(spec, self.dir / "kimi", binary)
            argv = launcher.runtime_argv(binary, spec, config_file, agent_file)
            self.runtime_state = "starting"
            self.emit("RuntimeStatus", {"state": "starting"}, persist=False)
            process = WireProcess(argv, cwd=spec.workspace, env=env, on_event=self._on_wire_event, on_request=self._on_wire_request, on_exit=self._on_exit)
            await process.start()
            try:
                result = await process.initialize(definitions)
            except (WireError, asyncio.TimeoutError) as error:
                detail = process.stderr_tail()
                await process.close(grace=1)
                self._release()
                self.runtime_state = "failed"
                self.emit("RuntimeStatus", {"state": "failed", "detail": detail or str(error)}, persist=False)
                raise WireError(f"The agent runtime failed to start: {detail or error}") from error
            self.process = process
            self._fingerprint = fingerprint
            self.runtime_state = "ready"
            self.runtime_info = {
                "server": result.get("server") or {},
                "slash_commands": result.get("slash_commands") or [],
                "model": "scripted-echo" if launcher.scripted() else spec.settings.model,
                "director": self.tenant.director_state(spec),
                "memory": self.tenant.memory.status()["active_mode"] if spec.memory_enabled else "off",
                "sandbox": spec.sandbox.name,
            }
            self.emit("RuntimeStatus", {"state": "ready", **self.runtime_info}, persist=False)
            return process

    def _release(self) -> None:
        if self._proxy_token:
            self.tenant.proxy.revoke(self._proxy_token)
            self._proxy_token = ""
        self.tenant.pool.release(self)

    async def _stop_process(self) -> None:
        process, self.process = self.process, None
        self._fingerprint = None
        if process is not None:
            self._stopping = True
            try:
                await process.close()
            finally:
                self._stopping = False
        self._release()
        self.runtime_state = "stopped"

    @property
    def evictable(self) -> bool:
        return self.process is not None and self.turn is None and not self.pending

    async def _on_exit(self, returncode: int | None, detail: str) -> None:
        if self._stopping:
            return  # The host stopped it: a restart or eviction, not a failure.
        if self.process is not None and not self.process.alive:
            self.process = None
            self._fingerprint = None
            self._release()
        self.runtime_state = "stopped"
        self._settle_pending("reject", "runtime exited")
        if self.turn is not None:
            self.emit("RuntimeStatus", {"state": "exited", "code": returncode, "detail": detail[-2000:]}, persist=False)

    # ----- runtime → host ----------------------------------------------------

    async def _on_wire_event(self, kind: str, payload: dict) -> None:
        if self.turn is not None:
            _observe(self.turn, kind, payload)
        # Coalesce ContentPart deltas (text/think) to reduce event count and token burn
        if kind == "ContentPart":
            ptype = payload.get("type")
            if ptype in ("text", "think"):
                key = f"ContentPart:{ptype}"
                if key not in self._content_buffer:
                    self._content_buffer[key] = {"type": "ContentPart", "payload": {"type": ptype, ptype: ""}}
                self._content_buffer[key]["payload"][ptype] += payload.get(ptype, "")
                # Schedule flush if not already scheduled
                if self._buffer_flush_task is None or self._buffer_flush_task.done():
                    self._buffer_flush_task = asyncio.create_task(self._flush_content_buffer())
                return
        # Flush any pending content before emitting other events
        await self._flush_content_buffer()
        self.emit(kind, payload)

    async def _flush_content_buffer(self) -> None:
        await asyncio.sleep(0.01)  # small delay to batch deltas
        for key, event in list(self._content_buffer.items()):
            if event["payload"].get(event["payload"]["type"]):
                self.emit("ContentPart", event["payload"])
            del self._content_buffer[key]
        self._buffer_flush_task = None

    async def _on_wire_request(self, request_id: str, kind: str, payload: dict) -> dict:
        if kind == "ApprovalRequest":
            response = await self._approve(payload)
            if response == "reject" and self.turn is not None:
                self.turn.rejections += 1
            return {"request_id": payload.get("id") or request_id, "response": response}
        if kind == "ToolCallRequest":
            return await self._external_tool(payload)
        raise WireError(f"Unknown request type {kind}")

    async def _external_tool(self, payload: dict) -> dict:
        call_id = str(payload.get("id") or "")
        name = str(payload.get("name") or "")
        try:
            arguments = json.loads(payload.get("arguments") or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError) as error:
            return {"tool_call_id": call_id, "return_value": tool_error(f"Invalid arguments: {error}", "Invalid arguments")}
        from .. import catalogue

        toolset = next((t for t in self.tenant.active_toolsets() if t.handles(name)), None)
        if toolset is not None and not catalogue.allowed(self.tenant, name, self):
            result = tool_error(f"`{name}` is outside this session's tool scope.", "Out of scope")
        elif toolset is not None and catalogue.enabled(self.tenant) and catalogue.is_deferred(toolset):
            result = tool_error(f"`{name}` is found with `{catalogue.FIND_TOOL}` and run with `{catalogue.USE_TOOL}`.", "Use the gateway")
        elif toolset is None:
            result = tool_error(f"Unknown host tool `{name}`.", "Unknown tool")
        else:
            try:
                result = await toolset.call(self, call_id, name, arguments)
            except Exception as error:  # noqa: BLE001 - a host tool fault is a tool error, not a failed turn
                logger.exception("host tool %s failed", name)
                result = tool_error(f"{name} failed: {error}")
        return {"tool_call_id": call_id, "return_value": result}

    # ----- turns --------------------------------------------------------------

    async def prompt(self, text: str, attachments: list[dict] | None = None) -> None:
        text = (text or "").strip()
        if not text and not attachments:
            raise ValueError("Empty prompt.")
        if self.turn is not None:
            raise ValueError("A turn is already running in this session.")
        if text == "/yolo":
            await self.set_mode("default" if self.record.permission_mode == "yolo" else "yolo")
            return
        if text == "/plan":
            await self.set_mode("default" if self.record.permission_mode == "plan" else "plan")
            return
        if text == "/rollback":
            try:
                await self.rollback()
            except CheckpointError as error:
                raise ValueError(str(error)) from error
            return
        if text in ("/verify", "/test", "/check"):
            text = "Run the automated verification suite and tests for this repository, inspect all outputs, and report passing or failing status."
        elif text in ("/diff", "/changes"):
            text = "Review all uncommitted changes and git diffs in this workspace, summarize modifications, and check for bugs or missing test coverage."
        workspace = Path(self.record.workspace)
        turn = TurnState(id=uuid.uuid4().hex[:12], prompt=text, mode=self.record.permission_mode)
        self.turn = turn
        self._turn_done.clear()
        try:
            process = await self.ensure_process()
        except Exception as error:
            self.emit("UserPrompt", {"text": text, "attachments": attachment_meta(attachments)})
            self.emit("TurnFinished", {"status": "error", "error": str(error)})
            self.turn = None
            self._turn_done.set()
            raise
        is_slash = text.startswith("/")
        request_text = text if is_slash else expand_mentions(text, workspace)
        memory_items: list[dict] = []
        memory = self.tenant.memory_for(self.record)
        if not is_slash and memory.enabled:
            packet = await memory.compile_packet(text, workspace, seen=self.memory_seen, first_turn=self.record.turns == 0)
            if packet is not None:
                request_text = packet_envelope(packet.text, request_text, workspace)
                memory_items = packet.items
                self.memory_seen.update(item["id"] for item in packet.items if item.get("id"))
        self.emit("UserPrompt", {"text": text, "attachments": attachment_meta(attachments), "memory": memory_items})
        user_input: str | list = build_parts(request_text, attachments) if attachments else request_text
        if self.record.turns == 0 and not is_slash and self.record.title == "New session":
            self.record.title = title_of(text)  # a title given at creation (a schedule's) is kept
        self.record.turns += 1
        self.record.updated_at = time.time()
        self.save_record()
        self.tenant.touch(self.record)
        self.emit("SessionUpdated", self.public(), persist=False)
        self._turn_task = asyncio.create_task(self._run_turn(process, turn, user_input))

    async def _run_turn(self, process: WireProcess, turn: TurnState, user_input: str | list) -> None:
        status, extra = "error", {}
        try:
            await self.checkpoint_turn(turn)
            result = await process.prompt(user_input)
            status = str(result.get("status") or "finished")
            extra = {k: v for k, v in result.items() if k != "status"}
        except WireError as error:
            extra = {"error": "No model is configured for the runtime." if error.code == -32001 else str(error)}
        except Exception as error:  # noqa: BLE001
            logger.exception("turn failed")
            extra = {"error": str(error)}
        finally:
            self._settle_pending("reject", "turn ended")
            self.log.flush()
            duration = round(time.time() - turn.started_at, 2)
            self.emit("TurnFinished", {"status": status, "duration": duration, "files_touched": turn.files_touched, "verified": turn.verified, "checks": turn.checks, "checkpoint": turn.checkpoint, "mode": turn.mode, **extra})
            self.last_reply = turn.reply()
            self.last_error = str(extra.get("error") or "")
            self.last_budget = extra.get("budget") if isinstance(extra.get("budget"), dict) else {}
            self.last_files = list(turn.files_touched)
            self.record.last_status = status
            self.record.updated_at = time.time()
            self.save_record()
            self.turn = None
            self._turn_task = None
            self._turn_done.set()
        await self.tenant.after_turn(self, turn, status)
        await self._drain_queue(status)

    async def wait_turn(self, timeout: float | None = None) -> None:
        await asyncio.wait_for(self._turn_done.wait(), timeout)

    async def cancel(self) -> None:
        self._settle_pending("reject", "cancelled")
        if self.turn is not None and self.process is not None and self.process.alive:
            await self.process.cancel()

    async def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"Unknown permission mode {mode!r}")
        if mode == self.record.permission_mode:
            return
        self.record.permission_mode = mode
        self.save_record()
        self.emit("ModeChanged", {"mode": mode})
        self._settle_for_mode(mode)

    async def close(self) -> None:
        await self.cancel()
        if self._turn_task is not None:
            try:
                await asyncio.wait_for(self._turn_task, 10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        self.log.flush()
        await self._stop_process()
        if self._processes is not None:
            await self._processes.close()

    def director_log(self, limit: int = 100) -> list[dict]:
        path = self.dir / "kimi" / "director.jsonl"
        if not path.exists():
            return []
        out = []
        for line in path.read_text("utf-8", errors="replace").splitlines()[-limit:]:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            record.pop("state", None)
            out.append(record)
        return out


def _observe(turn: TurnState, kind: str, payload: dict) -> None:
    """Keep what the host needs from a turn: its tool calls, files, and reply."""
    if kind == "ToolCall":
        call_id = str(payload.get("id") or "")
        function = payload.get("function") or {}
        turn.tool_calls[call_id] = {"name": function.get("name") or "", "arguments": function.get("arguments") or ""}
        turn.last_call_id = call_id
        turn.tool_count += 1
    elif kind == "ToolCallPart" and turn.last_call_id in turn.tool_calls:
        turn.tool_calls[turn.last_call_id]["arguments"] += payload.get("arguments_part") or ""
    elif kind == "ToolResult":
        call = turn.tool_calls.get(str(payload.get("tool_call_id") or ""))
        result = payload.get("return_value") or {}
        if call is None:
            return
        failed = bool(result.get("is_error"))
        call["status"] = "failed" if failed else "completed"
        call["brief"] = next((d.get("text", "") for d in result.get("display") or [] if d.get("type") == "brief"), "") or str(result.get("message") or "")[:200]
        turn.errors += int(failed)
        if call["name"] == "Shell":
            try:
                turn.note_command(str(json.loads(call["arguments"] or "{}").get("command") or ""))
            except (json.JSONDecodeError, AttributeError):
                pass
        elif call["name"] in ("coding_flow", "run_python"):
            turn.note_check()  # a flow runs its checks on every candidate; run_python runs code
        if call["name"] in (*EDIT_TOOLS, *DOCUMENT_EDIT_TOOLS) and not failed:
            if call["name"] in EDIT_TOOLS:
                turn.note_edit()  # a document edit re-opens what it saved: it is its own check
            try:
                arguments = json.loads(call["arguments"] or "{}")
                path = str(arguments.get("output") or arguments.get("path") or "")
            except (json.JSONDecodeError, AttributeError):
                path = ""
            if path and path not in turn.files_touched:
                turn.files_touched.append(path)
    elif kind == "ContentPart" and payload.get("type") == "text":
        turn.text.append(payload.get("text") or "")
    elif kind == "TurnBegin":
        # The runtime restarts a turn to recover from a bad generation; the
        # abandoned draft is not the answer.
        turn.text.clear()
