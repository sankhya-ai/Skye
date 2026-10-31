"""The owner's terminal: a login shell in the session's folder, over a WebSocket.

The page runs xterm.js; this side keeps the shell behind a pseudo-terminal and
moves bytes. It is the owner's own shell, not the agent's: no approvals, and
nothing it does reaches the transcript. The page shows it as a drawer under the conversation. It sees the scrubbed environment, so
root's credentials stay out of it. Local installs only — hosted mode never
runs anything unsandboxed, so there the socket closes with 4403.

The pseudo-terminal handling is adapted from Hermes Agent's ``pty_bridge.py``
and ``win_pty_bridge.py`` (MIT, Copyright (c) 2025 Nous Research). On macOS
and Linux: ``ptyprocess`` to spawn, a non-blocking master, clamped resizes,
and closing the shell's whole process group so nothing it started outlives
the tab. On Windows: ConPTY through ``pywinpty`` running PowerShell; ConPTY
has no descriptor to wait on, so reads poll on a worker thread, and closing
stops the shell's process tree.

Protocol: the page sends JSON text frames — ``{"type": "input", "data": …}``
and ``{"type": "resize", "cols": …, "rows": …}`` — and receives the
terminal's output as binary frames.
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import struct
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import hostos, launcher

logger = logging.getLogger(__name__)

_MAX_COLS, _MAX_ROWS = 2000, 1000


def _clamp(value: object, maximum: int) -> int:
    try:
        return max(1, min(int(value), maximum))  # type: ignore[call-overload]
    except (TypeError, ValueError, OverflowError):
        return 1


class Pty:
    """One shell behind a pseudo-terminal (macOS, Linux). Owned by the socket that opened it."""

    def __init__(self, argv: list[str], *, cwd: str, env: dict[str, str], cols: int = 80, rows: int = 24) -> None:
        import ptyprocess

        self._proc = ptyprocess.PtyProcess.spawn(argv, cwd=cwd, env=env, dimensions=(rows, cols))
        self.fd: int = self._proc.fd
        os.set_blocking(self.fd, False)
        self._closed = False

    def read(self) -> bytes | None:
        """What the shell wrote since last time: ``b""`` for nothing yet, ``None`` once it is gone."""
        try:
            return os.read(self.fd, 65536) or None
        except OSError as error:
            if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                return b""
            return None  # EIO: the shell exited; EBADF: closed

    async def write(self, data: bytes, timeout: float = 10.0) -> bool:
        """Write everything without blocking the event loop (a big paste waits on the shell)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        view = memoryview(data)
        while view and not self._closed:
            try:
                view = view[os.write(self.fd, view) :]
            except OSError as error:
                if error.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                    return False
                if loop.time() > deadline:
                    return False
                await asyncio.sleep(0.01)
        return not self._closed

    def resize(self, cols: object, rows: object) -> None:
        if self._closed:
            return
        import fcntl
        import termios

        size = struct.pack("HHHH", _clamp(rows, _MAX_ROWS), _clamp(cols, _MAX_COLS), 0, 0)
        try:
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, size)
        except OSError:
            pass

    def close(self) -> None:
        """End the shell and everything it started (SIGHUP → SIGTERM → SIGKILL). Blocks briefly."""
        import signal

        if self._closed:
            return
        self._closed = True
        pid = self._proc.pid  # ptyprocess makes the shell a session (and group) leader
        for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pid, sig)
            except OSError:
                break  # the group is gone
            deadline = time.monotonic() + 0.5
            while self._proc.isalive() and time.monotonic() < deadline:
                self.read()  # an exiting shell flushes first; drain so it can
                time.sleep(0.02)
        try:
            self._proc.close(force=True)
        except Exception:  # noqa: BLE001 - closing must not fail
            pass


class WinPty:
    """One PowerShell behind ConPTY (Windows): the same surface as ``Pty``, without a descriptor."""

    fd = None

    def __init__(self, argv: list[str], *, cwd: str, env: dict[str, str], cols: int = 80, rows: int = 24) -> None:
        from winpty import PtyProcess  # pywinpty

        self._proc = PtyProcess.spawn(argv, cwd=cwd, env=env, dimensions=(rows, cols))
        self._closed = False

    def read(self) -> bytes | None:
        """Blocks up to a moment; ``b""`` for nothing yet, ``None`` once the shell is gone."""
        if self._closed:
            return None
        try:
            data = self._proc.read(65536)
        except EOFError:
            return None
        except Exception:  # noqa: BLE001 - a closed ConPTY raises various errors
            return None if self._closed or not self._proc.isalive() else b""
        if not data:
            if not self._proc.isalive():
                return None
            time.sleep(0.02)  # no descriptor to wait on: don't spin
            return b""
        # pywinpty decodes for us, so a multibyte character can split across reads; xterm.js copes.
        return data if isinstance(data, bytes) else data.encode("utf-8", errors="replace")

    async def write(self, data: bytes, timeout: float = 10.0) -> bool:
        if self._closed:
            return False
        try:
            await asyncio.wait_for(asyncio.to_thread(self._proc.write, data.decode("utf-8", errors="replace")), timeout)
        except Exception:  # noqa: BLE001 - a wedged ConPTY: the tab is over
            await asyncio.to_thread(self.close)
            return False
        return True

    def resize(self, cols: object, rows: object) -> None:
        if not self._closed:
            try:
                self._proc.setwinsize(_clamp(rows, _MAX_ROWS), _clamp(cols, _MAX_COLS))
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        hostos.stop_tree(self._proc.pid)
        try:
            self._proc.terminate(force=True)
        except Exception:  # noqa: BLE001 - closing must not fail
            pass


def open_shell(cwd: str, env: dict[str, str]) -> "Pty | WinPty":
    return (WinPty if hostos.WINDOWS else Pty)(hostos.login_shell(), cwd=cwd, env=env)


def router(app_state) -> APIRouter:
    api = APIRouter(prefix="/api")
    auth = app_state.auth

    @api.websocket("/sessions/{session_id}/terminal")
    async def terminal_socket(websocket: WebSocket, session_id: str) -> None:
        tenant = auth.websocket_tenant(websocket)
        if tenant is None:
            await websocket.close(code=4401)
            return
        if app_state.server.mode != "local":
            await websocket.close(code=4403, reason="The terminal is for a local install.")
            return
        try:
            runtime = tenant.sessions.get(session_id)
        except KeyError:
            await websocket.close(code=4404)
            return
        await websocket.accept()
        env = {**launcher.scrubbed_environment(), "TERM": "xterm-256color", "COLORTERM": "truecolor"}
        try:
            pty = open_shell(runtime.record.workspace, env)
        except Exception as error:  # noqa: BLE001 - say why on the page, not just in a log
            await websocket.send_bytes(f"root could not start a shell: {error}\r\n".encode())
            await websocket.close()
            return
        await serve(websocket, pty)

    return api


async def serve(websocket: WebSocket, pty: "Pty | WinPty") -> None:
    loop = asyncio.get_running_loop()
    output: asyncio.Queue[bytes | None] = asyncio.Queue()

    def readable() -> None:
        while True:
            data = pty.read()
            if data == b"":
                return
            output.put_nowait(data)
            if data is None:
                loop.remove_reader(pty.fd)
                return

    async def poll() -> None:
        """ConPTY: read on a worker thread until the shell is gone."""
        while (data := await asyncio.to_thread(pty.read)) is not None:
            if data:
                output.put_nowait(data)
        output.put_nowait(None)

    async def pump() -> None:
        while (data := await output.get()) is not None:
            await websocket.send_bytes(data)
        await websocket.close()  # the shell exited (`exit`, ^D)

    poller = None
    if pty.fd is None:
        poller = asyncio.create_task(poll())
    else:
        loop.add_reader(pty.fd, readable)
    sender = asyncio.create_task(pump())
    try:
        while True:
            message = json.loads(await websocket.receive_text())
            if message.get("type") == "input":
                await pty.write(str(message.get("data") or "").encode())
            elif message.get("type") == "resize":
                pty.resize(message.get("cols"), message.get("rows"))
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        sender.cancel()
        if pty.fd is not None:
            try:
                loop.remove_reader(pty.fd)
            except (OSError, ValueError):
                pass
        await asyncio.to_thread(pty.close)
        if poller is not None:
            poller.cancel()
