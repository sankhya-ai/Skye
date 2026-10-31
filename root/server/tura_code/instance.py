"""One root per data directory: two servers on one folder would share sessions,
settings and Dhee's store and overwrite each other's writes.

The server holds an exclusive lock on ``<data dir>/server.lock`` for as long as
it runs (the OS drops it when the process dies, so a crash never leaves a stale
lock) and writes who it is into the file, so a second start can say where the
running root is.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import IO

from . import hostos

LOCK = "server.lock"


class AlreadyRunning(RuntimeError):
    pass


def claim(home: Path, url: str) -> IO[str]:
    """Hold the data directory for this process; raises ``AlreadyRunning`` with the other root's details."""
    home.mkdir(parents=True, exist_ok=True)
    handle = open(home / LOCK, "a+", encoding="utf-8")
    try:
        hostos.lock(handle)
    except BlockingIOError:
        handle.seek(0)
        try:
            other = json.loads(handle.read() or "{}")
        except (OSError, ValueError):
            other = {}
        handle.close()
        where = f" at {other['url']}" if other.get("url") else ""
        who = f" (pid {other['pid']})" if other.get("pid") else ""
        raise AlreadyRunning(f"root is already running on {home}{where}{who}. Quit it first: two roots on one folder would overwrite each other.") from None
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps({"pid": os.getpid(), "url": url}))
    handle.flush()
    return handle
