"""The running-dispatch registry: what makes max_concurrent a real limit instead of a comment.

Each dispatch records itself in <state_dir>/running/<id>.json (pid, contract fingerprint, started
time) and removes the record when it ends. The concurrency check counts LIVE records - a record
whose pid is gone is stale (the process crashed or was killed) and is cleaned up, so a dead
dispatch never holds a slot forever. Records survive the process, which is what makes the cap
work across separate CLI invocations; liveness comes from the pid, not from the file existing.

This is a cooperative bookkeeping limit, not a scheduler and not a lock: two dispatches started
in the same instant can both pass the check before either registers (check-then-act), so the cap
can be exceeded by a race; it cannot be exceeded by dispatches that start after peers registered.
There is still no queue: a dispatch that arrives while the cap is full comes back needs-owner,
and the owner retries or raises the cap.
"""

from __future__ import annotations

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
