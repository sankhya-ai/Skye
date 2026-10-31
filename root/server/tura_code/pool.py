"""How many runtime processes may live at once, and which one makes room.

Each runtime is a separate process (tens of megabytes). A hosted install caps
them per person and in total; when a new one needs a slot, the least recently
active *idle* runtime is stopped — its conversation is on disk and resumes on
the next prompt. Runtimes idle past the timeout are reaped as well. A runtime
in a turn or waiting on an approval is never evicted.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from .wire import WireError

if TYPE_CHECKING:
    from .sessions.runtime import SessionRuntime

logger = logging.getLogger(__name__)


class RuntimePool:
    def __init__(self, *, max_total: int = 32, max_per_tenant: int = 6, idle_seconds: float = 20 * 60) -> None:
        self.max_total = max_total
        self.max_per_tenant = max_per_tenant
        self.idle_seconds = idle_seconds
        self.live: dict[int, "SessionRuntime"] = {}
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task | None = None

    def count(self, tenant_id: str | None = None) -> int:
        return sum(1 for r in self.live.values() if tenant_id is None or r.tenant.id == tenant_id)

    async def admit(self, runtime: "SessionRuntime") -> None:
        async with self._lock:
            if id(runtime) in self.live:
                return
            while self.count(runtime.tenant.id) >= self.max_per_tenant or self.count() >= self.max_total:
                own = self.count(runtime.tenant.id) >= self.max_per_tenant
                victims = [r for r in self.live.values() if r.evictable and r is not runtime and (not own or r.tenant.id == runtime.tenant.id)]
                if not victims:
                    raise WireError("Too many sessions are working right now. Wait for one to finish, or stop one, and try again.")
                victim = min(victims, key=lambda r: r.last_activity)
                logger.info("evicting idle runtime %s to make room", victim.record.id)
                await victim._stop_process()
            self.live[id(runtime)] = runtime

    def release(self, runtime: "SessionRuntime") -> None:
        self.live.pop(id(runtime), None)

    def start(self) -> None:
        if self._reaper is None:
            self._reaper = asyncio.create_task(self._reap())

    async def _reap(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = time.time()
            for runtime in list(self.live.values()):
                if runtime.evictable and not runtime.subscribers and now - runtime.last_activity > self.idle_seconds:
                    logger.info("stopping idle runtime for session %s", runtime.record.id)
                    await runtime._stop_process()

    async def close(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
