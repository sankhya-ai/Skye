"""What differs between the operating systems root runs on: macOS, Linux and Windows.

Everything else in the host is portable Python; these are the places it is
not, kept in one module so the rest never asks which system it is on. The
Windows side follows Hermes Agent's native Windows port (MIT, Nous Research):

* the data directory lock is ``msvcrt.locking`` (``fcntl.flock`` elsewhere);
* a command starts in its own process group (``CREATE_NEW_PROCESS_GROUP``,
  hidden), and stopping it stops its whole tree with ``taskkill /T /F`` —
  Windows has no process-group signal, and ``os.kill`` there is not one;
* host commands (background processes, flow checks) run in PowerShell, the
  shell the runtime's own ``Shell`` tool uses on Windows, so the agent writes
  one dialect: ``pwsh`` (PowerShell 7, which has ``&&``) when it is installed,
  else Windows PowerShell.

Never ``os.kill(pid, 0)`` to ask whether a process lives: on Windows that
sends Ctrl+C to the target's whole console group (bpo-14484).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
from pathlib import Path
from typing import IO, Any

WINDOWS = os.name == "nt"

_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000
#: Windows locks byte ranges, and a locked byte cannot even be read by another
#: process: lock one far past what the file holds, so the holder's details stay readable.
_LOCK_OFFSET = 1 << 30


def lock(handle: IO[str]) -> None:
    """Hold an exclusive lock on an open file; ``BlockingIOError`` when another process has it."""
    if WINDOWS:
        import msvcrt

        handle.seek(_LOCK_OFFSET)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        except OSError as error:
            raise BlockingIOError(str(error)) from error
        finally:
            handle.seek(0)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def spawn_options() -> dict[str, Any]:
    """``subprocess``/``asyncio`` keyword arguments that start a command as the root of its own process group."""
    if WINDOWS:
        return {"creationflags": _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW}
    return {"start_new_session": True}


def stop_tree(pid: int, *, force: bool = True) -> None:
    """Stop a process started with ``spawn_options`` and everything it started.

    POSIX signals the group, SIGKILL when ``force`` else SIGTERM. Windows has
    one strength: ``taskkill /T /F``.
    """
    if WINDOWS:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                [_system32("taskkill.exe"), "/T", "/F", "/PID", str(pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                creationflags=_CREATE_NO_WINDOW,
                check=False,
            )
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL if force else signal.SIGTERM)


def _system32(name: str) -> str:
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / name)


def powershell() -> str:
    """PowerShell 7 when it is installed, else the Windows PowerShell every Windows has."""
    return shutil.which("pwsh") or shutil.which("powershell") or _system32(r"WindowsPowerShell\v1.0\powershell.exe")


def shell_argv(command: str) -> list[str]:
    """argv that runs one shell command line, in this system's shell."""
    if WINDOWS:
        return [powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command]
    return ["/bin/sh", "-c", command]


def shell_name() -> str:
    return "PowerShell" if WINDOWS else "sh"


def venv_bin(venv: Path) -> Path:
    """A virtual environment's scripts folder: ``Scripts`` on Windows, ``bin`` elsewhere."""
    return venv / ("Scripts" if WINDOWS else "bin")


def venv_python(venv: Path) -> Path:
    return venv_bin(venv) / ("python.exe" if WINDOWS else "python")


def executable(name: str) -> str:
    """A program's file name here: ``kimi-agent.exe`` on Windows."""
    return f"{name}.exe" if WINDOWS and not name.lower().endswith(".exe") else name


def configure_console() -> None:
    """Windows: UTF-8 for this console and every child Python (its code page cannot print most text)."""
    if not WINDOWS:
        return
    import sys

    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    with contextlib.suppress(Exception):
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)  # type: ignore[attr-defined]
        ctypes.windll.kernel32.SetConsoleCP(65001)  # type: ignore[attr-defined]
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def login_shell() -> list[str]:
    """The owner's interactive shell for the terminal drawer."""
    if WINDOWS:
        return [powershell(), "-NoLogo"]
    import pwd

    shell = os.environ.get("SHELL") or pwd.getpwuid(os.getuid()).pw_shell
    return [shell if shell and os.path.exists(shell) else "/bin/sh", "-l"]
