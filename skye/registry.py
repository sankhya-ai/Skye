"""The running-dispatch registry: what makes max_concurrent a real limit instead of a comment.

Each dispatch records itself in <state_dir>/running/<id>.json (pid, contract fingerprint, started
time) and removes the record when it ends. The concurrency check counts LIVE records - a record
whose pid is gone is stale (the process crashed or was killed) and is cleaned up, so a dead
dispatch never holds a slot forever. Records survive the process, which is what makes the cap
work across separate CLI invocations; liveness comes from the pid, not from the file existing.

The acquire-and-count is serialized with an flock'd lock file, so two dispatches started in the
same instant cannot both pass the check: the second one counts the first's already-registered
slot. This closes the check-then-act race for processes on this machine sharing the state dir
(flock is per-filesystem; it is not a distributed lock, and there is still no queue). A dispatch
that arrives while the cap is full comes back needs-owner, and the owner retries or raises the
cap.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # owned by someone else: alive
    except OSError:
        return False
    return True


class Registry:
    def __init__(self, state_dir: str | Path) -> None:
        self.dir = Path(state_dir) / "running"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.slot_id = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.path = self.dir / f"{self.slot_id}.json"

    def acquire(self, contract: dict[str, Any], fingerprint: str) -> None:
        self.path.write_text(json.dumps({
            "pid": os.getpid(), "started": time.time(), "fingerprint": fingerprint,
            "goal": contract.get("goal", "")[:120], "role": contract.get("role", ""),
            "adapter": contract.get("adapter", "")}) + "\n", "utf-8")

    def acquire_and_count(self, contract: dict[str, Any], fingerprint: str) -> int:
        """Register this dispatch and count the OTHER live dispatches, atomically under an flock:
        no two dispatches can interleave registration and counting, so the cap check the caller
        makes with this number actually holds. Returns the number of other live dispatches."""
        lock_path = self.dir.parent / "running.lock"
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                self.acquire(contract, fingerprint)
                return len(self.others())
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def release(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass

    def others(self) -> list[dict[str, Any]]:
        """Live records excluding this dispatch; stale records are removed as they are found."""
        live = []
        for path in self.dir.glob("*.json"):
            if path.name == self.path.name:
                continue
            try:
                record = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                path.unlink(missing_ok=True)
                continue
            if _pid_alive(int(record.get("pid") or 0)):
                live.append(record)
            else:
                path.unlink(missing_ok=True)
        return live

    def __enter__(self) -> "Registry":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.release()
