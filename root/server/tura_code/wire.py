"""An asyncio client for kimi-agent's JSON-RPC wire protocol over stdio.

One ``WireProcess`` is one long-lived runtime process. It is spoken to with
three requests — ``initialize`` (once), ``prompt`` (one turn at a time) and
``cancel`` — and it speaks back with:

* ``event`` notifications: the turn's stream (TurnBegin, StepBegin,
  ContentPart, ToolCall, ToolResult, StatusUpdate, TurnEnd, …);
* ``request`` messages that need an answer: ``ApprovalRequest`` (may this tool
  run?) and ``ToolCallRequest`` (run this host-owned tool);
* the response to ``prompt`` when the turn is over, carrying its status.

The protocol itself is documented by the runtime in ``src/wire/``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import hostos

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "1.1"
#: Tool results can carry whole files and base64 images on one line.
LINE_LIMIT = 100 * 1024 * 1024

EventHandler = Callable[[str, dict], Awaitable[None]]
RequestHandler = Callable[[str, str, dict], Awaitable[dict]]
ExitHandler = Callable[[int | None, str], Awaitable[None]]


class WireError(RuntimeError):
    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class WireProcess:
    def __init__(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        on_event: EventHandler,
        on_request: RequestHandler,
        on_exit: ExitHandler | None = None,
    ) -> None:
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self.on_event = on_event
        self.on_request = on_request
        self.on_exit = on_exit
        self.process: asyncio.subprocess.Process | None = None
        self.initialize_result: dict = {}
        self._pending: dict[str, asyncio.Future] = {}
        #: Answers in flight to the runtime's requests (an approval, a host tool call).
        self._tasks: set[asyncio.Task] = set()
        self._stderr: deque[str] = deque(maxlen=200)
        self._write_lock = asyncio.Lock()
        self._reader: asyncio.Task | None = None
        self._stderr_reader: asyncio.Task | None = None
        self._closed = False

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.returncode is None and not self._closed

    def stderr_tail(self, lines: int = 40) -> str:
        return "\n".join(list(self._stderr)[-lines:])

    async def start(self) -> None:
        self.process = await asyncio.create_subprocess_exec(
            *self.argv,
            cwd=str(self.cwd),
            env=self.env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=LINE_LIMIT,
            **hostos.spawn_options(),
        )
        self._reader = asyncio.create_task(self._read_stdout())
        self._stderr_reader = asyncio.create_task(self._read_stderr())

    async def initialize(self, external_tools: list[dict] | None = None, timeout: float = 60) -> dict:
        params: dict[str, Any] = {
            "protocol_version": PROTOCOL_VERSION,
            "client": {"name": "tura-code", "version": "0.1.0"},
        }
        if external_tools:
            params["external_tools"] = external_tools
        result = await self.call("initialize", params, timeout=timeout)
        self.initialize_result = result
        rejected = (result.get("external_tools") or {}).get("rejected") or []
        for item in rejected:
            logger.warning("runtime rejected external tool %s", item)
        return result

    async def prompt(self, user_input: str | list) -> dict:
        """Run one turn. Returns the final result (``status`` et al.)."""
        return await self.call("prompt", {"user_input": user_input}, timeout=None)

    async def cancel(self) -> None:
        try:
            await self.call("cancel", {}, timeout=10)
        except WireError as error:
            # "No agent turn is in progress" is a race with the turn ending.
            logger.debug("cancel: %s", error)
        # The turn is gone: a host tool still working for it (a `process` wait,
        # `run_python`) stops too, instead of running on for nobody.
        for task in list(self._tasks):
            task.cancel()

    async def call(self, method: str, params: dict, timeout: float | None = 60) -> dict:
        if not self.alive:
            raise WireError("The agent runtime is not running.")
        request_id = f"{method}-{uuid.uuid4().hex[:12]}"
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        try:
            if timeout is None:
                return await future
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(request_id, None)

    async def _send(self, message: dict) -> None:
        if self.process is None or self.process.stdin is None:
            raise WireError("The agent runtime is not running.")
        line = json.dumps(message, ensure_ascii=False) + "\n"
        async with self._write_lock:
            try:
                self.process.stdin.write(line.encode("utf-8"))
                await self.process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as error:
                raise WireError(f"The agent runtime closed its input: {error}") from error

    async def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        stdout = self.process.stdout
        try:
            while True:
                raw = await stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("runtime wrote a non-JSON line: %.200s", line)
                    continue
                await self._dispatch(message)
        except Exception:  # noqa: BLE001 - the reader must always reach the exit path
            logger.exception("wire reader failed")
        finally:
            await self._finish()

    async def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        while True:
            raw = await self.process.stderr.readline()
            if not raw:
                return
            text = raw.decode("utf-8", errors="replace").rstrip()
            if text:
                self._stderr.append(text)
                logger.debug("runtime stderr: %s", text)

    async def _dispatch(self, message: dict) -> None:
        method = message.get("method")
        if method == "event":
            params = message.get("params") or {}
            await self._safe(self.on_event(str(params.get("type") or ""), params.get("payload") or {}))
            return
        if method == "request":
            params = message.get("params") or {}
            request_id = str(message.get("id") or "")
            self._spawn(self._answer(request_id, str(params.get("type") or ""), params.get("payload") or {}))
            return
        request_id = message.get("id")
        future = self._pending.get(str(request_id)) if request_id is not None else None
        if future is None:
            if message.get("error"):
                logger.warning("runtime error without a request: %s", message["error"])
            return
        if future.done():
            return
        if "error" in message and message["error"] is not None:
            error = message["error"]
            future.set_exception(WireError(str(error.get("message") or "runtime error"), error.get("code")))
        else:
            future.set_result(message.get("result") or {})

    async def _answer(self, request_id: str, kind: str, payload: dict) -> None:
        try:
            result = await self.on_request(request_id, kind, payload)
            await self._send({"jsonrpc": "2.0", "id": request_id, "result": result})
        except asyncio.CancelledError:
            # Cancelled with its turn: still answer, so the runtime lets go of it.
            try:
                await self._send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": "Cancelled with the turn."}})
            except WireError:
                pass
            raise
        except Exception as error:  # noqa: BLE001 - every request gets an answer
            logger.exception("host failed to answer %s", kind)
            try:
                await self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32603, "message": f"Host error: {error}"},
                    }
                )
            except WireError:
                pass

    async def _safe(self, awaitable: Awaitable[None]) -> None:
        try:
            await awaitable
        except Exception:  # noqa: BLE001 - one bad event must not stop the stream
            logger.exception("event handler failed")

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _finish(self) -> None:
        if self._closed:
            return
        self._closed = True
        returncode = None
        if self.process is not None:
            try:
                returncode = await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                returncode = None
        if self._stderr_reader is not None:
            try:
                await asyncio.wait_for(self._stderr_reader, 2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        detail = self.stderr_tail()
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(
                    WireError(
                        "The agent runtime exited"
                        + (f" with code {returncode}" if returncode is not None else "")
                        + (f": {detail.splitlines()[-1]}" if detail else ".")
                    )
                )
        if self.on_exit is not None:
            await self._safe(self.on_exit(returncode, detail))

    async def close(self, grace: float = 3.0) -> None:
        process = self.process
        if process is None:
            return
        if process.returncode is None:
            try:
                if process.stdin is not None and not process.stdin.is_closing():
                    process.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                await asyncio.wait_for(process.wait(), grace)
            except asyncio.TimeoutError:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), grace)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
        if self._reader is not None:
            try:
                await asyncio.wait_for(self._reader, grace)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
