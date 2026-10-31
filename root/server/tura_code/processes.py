"""Background processes the agent starts and manages: dev servers, watchers, long builds.

The Hermes ``process`` tool, as Chotu exposed it (``chotu_hub/process_manager.py``
and ``tool_registry._call_process``): start a command in the background, then
poll it, read its log, wait for it, write to its stdin, or kill it. The
runtime's ``Shell`` tool blocks until a command exits, so without this a coding
agent cannot run a server and test against it in the same turn.

The host owns these processes, not the runtime: they keep running when the
runtime restarts, belong to one session, and are all stopped when that session
closes or the server shuts down. Each runs in its own process group with the
same scrubbed environment as the agent's shell (no API keys), and its combined
output goes to a log file under the session directory.

Starting one is a command execution, so it goes through the same approval
policy as ``Shell`` (see ``trust``); managing a process the session already
started does not ask again.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import hostos

TOOL_NAME = "process"
ACTIONS = ("start", "list", "poll", "log", "wait", "write", "submit", "close", "kill")
MAX_PROCESSES = 12
MAX_WAIT_SECONDS = 600
TAIL_CHARS = 2_000


def tool_definition() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "Run and manage background processes (dev servers, watchers, long builds) that must keep running "
            f"while you work. `start` launches a {hostos.shell_name()} command in the background and returns its id at once; "
            "`poll` gives status and recent output; `log` pages through output lines; `wait` blocks until it "
            "exits or the timeout passes; `write`/`submit` send stdin (submit adds a newline); `close` closes "
            "stdin; `kill` stops it and its children; `list` shows this session's processes. Use `Shell` for "
            "commands that finish on their own. Kill what you start once you no longer need it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(ACTIONS)},
                "command": {"type": "string", "description": f"For start: the {hostos.shell_name()} command."},
                "cwd": {"type": "string", "description": "For start: directory relative to the workspace (default: the workspace)."},
                "label": {"type": "string", "description": "For start: a short name, e.g. `dev server`."},
                "process_id": {"type": "string", "description": "The id `start` returned (every action but start and list)."},
                "data": {"type": "string", "description": "For write/submit: text to send to stdin."},
                "timeout": {"type": "integer", "minimum": 1, "maximum": MAX_WAIT_SECONDS, "description": "For wait: seconds (default 60)."},
                "offset": {"type": "integer", "minimum": 0, "description": "For log: first line (default: the last `limit` lines)."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "description": "For log: number of lines (default 200)."},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    }


class ProcessError(RuntimeError):
    pass


@dataclass
class Managed:
    id: str
    command: str
    cwd: str
    label: str
    log_path: Path
    started_at: float = field(default_factory=time.time)
    process: asyncio.subprocess.Process | None = None
    exit_code: int | None = None
    ended_at: float | None = None
    killed: bool = False
    waiter: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self.ended_at is None

    def public(self, *, tail: int = TAIL_CHARS) -> dict[str, Any]:
        record = {
            "id": self.id,
            "label": self.label,
            "command": self.command,
            "cwd": self.cwd,
            "pid": self.process.pid if self.process else None,
            "status": "running" if self.running else ("killed" if self.killed else "exited"),
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "runtime_seconds": round((self.ended_at or time.time()) - self.started_at, 1),
        }
        if tail:
            record["output_tail"] = read_tail(self.log_path, tail)
        return record


def read_tail(path: Path, chars: int) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - chars * 4))
            return handle.read().decode("utf-8", errors="replace")[-chars:]
    except OSError:
        return ""


class ProcessManager:
    """One session's background processes."""

    def __init__(self, directory: Path, workspace: Path, environment: dict[str, str], on_change=None, wrap=None) -> None:
        self.directory = directory
        self.workspace = workspace.resolve()
        self.environment = environment
        self.on_change = on_change
        #: The sandbox's argv wrapper (identity when there is no sandbox).
        self.wrap = wrap or (lambda argv: argv)
        self.items: dict[str, Managed] = {}
        self._counter = 0

    def _cwd(self, raw: str) -> Path:
        path = (self.workspace / (raw or ".")).resolve()
        if not path.is_relative_to(self.workspace) or not path.is_dir():
            raise ProcessError(f"cwd must be a directory inside the workspace: {raw}")
        return path

    def get(self, process_id: str) -> Managed:
        item = self.items.get(str(process_id or "").strip())
        if item is None:
            raise ProcessError(f"No process `{process_id}` in this session. Use action `list`.")
        return item

    def running_count(self) -> int:
        return sum(1 for item in self.items.values() if item.running)

    async def start(self, command: str, *, cwd: str = "", label: str = "") -> Managed:
        command = str(command or "").strip()
        if not command:
            raise ProcessError("`command` is required to start a process.")
        if self.running_count() >= MAX_PROCESSES:
            raise ProcessError(f"{MAX_PROCESSES} processes are already running; kill one first.")
        directory = self._cwd(cwd)
        self.directory.mkdir(parents=True, exist_ok=True)
        # Short, sequential ids are easy for the model to carry between calls.
        self._counter += 1
        process_id = f"p{self._counter}"
        log_path = self.directory / f"{process_id}-{uuid.uuid4().hex[:8]}.log"
        item = Managed(process_id, command, str(directory), (label or command)[:80], log_path)
        log = log_path.open("wb")
        try:
            # The `cd` is for a sandbox, whose working directory is its own; Windows has none.
            line = command if hostos.WINDOWS else f"cd {shlex.quote(str(directory))} && {command}"
            argv = self.wrap(hostos.shell_argv(line))
            item.process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(directory),
                env=self.environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=log,
                stderr=asyncio.subprocess.STDOUT,
                **hostos.spawn_options(),
            )
        finally:
            log.close()
        self.items[process_id] = item
        item.waiter = asyncio.create_task(self._watch(item))
        self._changed(item)
        return item

    async def _watch(self, item: Managed) -> None:
        assert item.process is not None
        code = await item.process.wait()
        item.exit_code = code
        item.ended_at = time.time()
        self._changed(item)

    def _changed(self, item: Managed) -> None:
        if self.on_change is not None:
            with contextlib.suppress(Exception):
                self.on_change(item.public(tail=0))

    async def wait(self, process_id: str, timeout: float) -> Managed:
        item = self.get(process_id)
        if item.waiter is not None and item.running:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(item.waiter), max(1.0, min(float(timeout), MAX_WAIT_SECONDS)))
        return item

    def log(self, process_id: str, *, offset: int | None, limit: int) -> dict[str, Any]:
        item = self.get(process_id)
        try:
            lines = item.log_path.read_text("utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        limit = max(1, min(int(limit or 200), 2000))
        start = max(0, len(lines) - limit) if offset is None else max(0, int(offset))
        window = lines[start : start + limit]
        return {"id": item.id, "status": item.public(tail=0)["status"], "total_lines": len(lines), "offset": start, "lines": window}

    async def write(self, process_id: str, data: str, *, close: bool = False) -> Managed:
        item = self.get(process_id)
        if not item.running or item.process is None or item.process.stdin is None:
            raise ProcessError(f"Process `{item.id}` is not running.")
        stdin = item.process.stdin
        if data:
            stdin.write(data.encode("utf-8"))
            with contextlib.suppress(ConnectionError):
                await stdin.drain()
        if close:
            stdin.close()
        return item

    async def kill(self, process_id: str, *, grace: float = 3.0) -> Managed:
        item = self.get(process_id)
        if not item.running or item.process is None:
            return item
        item.killed = True
        pid = item.process.pid
        for force in (False, True):
            await asyncio.to_thread(hostos.stop_tree, pid, force=force)
            if item.waiter is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(item.waiter), grace)
                    break
                except asyncio.TimeoutError:
                    continue
        return item

    async def close(self) -> None:
        for item in list(self.items.values()):
            if item.running:
                await self.kill(item.id, grace=2.0)

    def list(self) -> list[dict[str, Any]]:
        return [item.public(tail=400) for item in sorted(self.items.values(), key=lambda i: i.started_at, reverse=True)]


# ----- the tool ---------------------------------------------------------------


def _result(output: str, brief: str, *, error: bool = False, message: str = "") -> dict[str, Any]:
    return {"is_error": error, "output": output, "message": message, "display": [{"type": "brief", "text": brief}] if brief else []}


def _describe(item: Managed, *, tail: int = TAIL_CHARS) -> str:
    record = item.public(tail=tail)
    lines = [
        f"id: {record['id']}",
        f"command: {record['command']}",
        f"status: {record['status']}" + (f" (exit code {record['exit_code']})" if record["exit_code"] is not None else ""),
        f"pid: {record['pid']}",
        f"running for: {record['runtime_seconds']}s",
    ]
    if tail:
        lines += ["output (tail):", record.get("output_tail") or "(no output yet)"]
    return "\n".join(lines)


async def handle(manager: ProcessManager, arguments: dict[str, Any], approve) -> dict[str, Any]:
    """Run one `process` tool call. ``approve(command)`` returns True to allow a start."""
    action = str(arguments.get("action") or "").strip()
    try:
        if action == "start":
            command = str(arguments.get("command") or "").strip()
            if not command:
                raise ProcessError("`command` is required to start a process.")
            if not await approve(command):
                return _result("", "Rejected", error=True, message="The owner did not approve starting this process.")
            item = await manager.start(command, cwd=str(arguments.get("cwd") or ""), label=str(arguments.get("label") or ""))
            await asyncio.sleep(0.3)  # Let an immediate failure show up in the first reply.
            return _result(_describe(item, tail=600), f"Started {item.id}")
        if action == "list":
            items = manager.list()
            if not items:
                return _result("No background processes in this session.", "No processes")
            text = "\n".join(f"{p['id']}  {p['status']:8} {p['runtime_seconds']:>7}s  {p['command']}" for p in items)
            return _result(text, f"{len(items)} process{'es' if len(items) != 1 else ''}")
        process_id = str(arguments.get("process_id") or "")
        if action == "poll":
            item = manager.get(process_id)
            return _result(_describe(item, tail=1_000), item.public(tail=0)["status"].capitalize())
        if action == "log":
            offset = arguments.get("offset")
            page = manager.log(process_id, offset=None if offset is None else int(offset), limit=int(arguments.get("limit") or 200))
            header = f"lines {page['offset'] + 1}-{page['offset'] + len(page['lines'])} of {page['total_lines']} ({page['status']})"
            return _result(header + "\n" + "\n".join(page["lines"]), f"{len(page['lines'])} lines")
        if action == "wait":
            item = await manager.wait(process_id, float(arguments.get("timeout") or 60))
            status = item.public(tail=0)["status"]
            note = "" if not item.running else "\n(still running after the timeout)"
            return _result(_describe(item) + note, "Still running" if item.running else f"{status.capitalize()} ({item.exit_code})")
        if action in ("write", "submit", "close"):
            data = str(arguments.get("data") or "")
            if action == "submit":
                data += "\n"
            item = await manager.write(process_id, "" if action == "close" else data, close=action == "close")
            return _result(f"{'Closed stdin of' if action == 'close' else f'Sent {len(data)} bytes to'} {item.id}.", "stdin closed" if action == "close" else "Sent")
        if action == "kill":
            item = await manager.kill(process_id)
            return _result(_describe(item, tail=600), "Stopped")
        raise ProcessError(f"Unknown action `{action}`; use one of {', '.join(ACTIONS)}.")
    except ProcessError as error:
        return _result("", "Failed", error=True, message=str(error))
