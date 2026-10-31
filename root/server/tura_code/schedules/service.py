"""Schedules: a prompt that runs by itself in a workspace, at the times its ``when`` names.

Each run is an ordinary session (``⏰ <name>``): visible in the sidebar, in
search, and undoable like any other. It runs **unattended**: anything that
would ask the owner is declined and the agent adapts, as a coding-flow
candidate does — so a schedule in "Ask before acting" can read, run read-only
and trusted commands, and report; "Accept edits" lets it change files too.
"Auto-approve all" is not offered for unattended runs.

A run missed while root was closed runs once on the next start, then the
schedule carries on from now: no catch-up storm.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .when import WhenError, next_after, to_cron

if TYPE_CHECKING:
    from ..tenancy import Server, Tenant

logger = logging.getLogger(__name__)

MODES = ("plan", "default", "accept_edits")
TICK_SECONDS = 30.0


class ScheduleError(ValueError):
    pass


def _next(cron: str, after: float) -> float:
    return next_after(cron, datetime.fromtimestamp(after)).timestamp()


class Schedules:
    """One person's schedules, in ``schedules.json``."""

    def __init__(self, tenant: "Tenant") -> None:
        self.tenant = tenant
        self.path = tenant.root / "schedules.json"
        self._lock = threading.Lock()
        self.running: dict[str, asyncio.Task] = {}

    # ----- records -------------------------------------------------------------------

    def _read(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return []
        return data if isinstance(data, list) else []

    def _write(self, items: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=2), "utf-8")
        os.replace(tmp, self.path)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            items = self._read()
        return [{**item, "running": item["id"] in self.running} for item in items]

    def get(self, schedule_id: str) -> dict[str, Any]:
        for item in self.list():
            if item["id"] == schedule_id:
                return item
        raise ScheduleError(f"No schedule {schedule_id!r}.")

    def _validate(self, item: dict[str, Any]) -> dict[str, Any]:
        if item.get("mode") not in MODES:
            raise ScheduleError(f"A schedule runs in {', '.join(MODES)} mode (unattended runs cannot auto-approve everything).")
        if not str(item.get("prompt") or "").strip():
            raise ScheduleError("A schedule needs a prompt.")
        try:
            item["cron"] = to_cron(item["when"])
            item["workspace"] = str(self.tenant.check_workspace(item["workspace"]))
        except (WhenError, ValueError) as error:
            raise ScheduleError(str(error)) from error
        item["next_run"] = _next(item["cron"], time.time()) if item.get("enabled", True) else None
        return item

    def create(self, *, name: str, workspace: str, prompt: str, when: str, mode: str = "plan", created_by: str = "owner") -> dict[str, Any]:
        item = self._validate(
            {
                "id": uuid.uuid4().hex[:12],
                "name": (name or prompt).strip()[:80],
                "workspace": workspace,
                "prompt": prompt.strip(),
                "when": when,
                "mode": mode,
                "enabled": True,
                "created_at": time.time(),
                "created_by": created_by,
                "last_run": None,
                "last_status": "",
                "last_session": "",
                "runs": 0,
            }
        )
        with self._lock:
            self._write([*self._read(), item])
        return item

    def update(self, schedule_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            items = self._read()
            for index, item in enumerate(items):
                if item["id"] == schedule_id:
                    merged = {**item, **{k: v for k, v in changes.items() if k in ("name", "workspace", "prompt", "when", "mode", "enabled")}}
                    items[index] = self._validate(merged)
                    self._write(items)
                    return items[index]
        raise ScheduleError(f"No schedule {schedule_id!r}.")

    def delete(self, schedule_id: str) -> bool:
        with self._lock:
            items = self._read()
            kept = [item for item in items if item["id"] != schedule_id]
            self._write(kept)
        return len(kept) != len(items)

    def _note(self, schedule_id: str, **fields: Any) -> None:
        with self._lock:
            items = self._read()
            for item in items:
                if item["id"] == schedule_id:
                    item.update(fields)
            self._write(items)

    # ----- running ---------------------------------------------------------------------

    async def tick(self, now: float | None = None) -> list[str]:
        """Start every schedule that is due; returns their ids."""
        now = time.time() if now is None else now
        started = []
        for item in self.list():
            if item.get("enabled") and item.get("next_run") and item["next_run"] <= now and item["id"] not in self.running:
                self._note(item["id"], next_run=_next(item["cron"], now))
                self.start(item)
                started.append(item["id"])
        return started

    async def run_now(self, schedule_id: str) -> str:
        """Run a schedule now, whatever its time; returns the new session's id."""
        if schedule_id in self.running:
            raise ScheduleError("That schedule is already running.")
        return self.start(self.get(schedule_id))

    def start(self, item: dict[str, Any]) -> str:
        schedule_id = item["id"]
        runtime = self.tenant.sessions.create(item["workspace"], f"⏰ {item['name']}", mode=item["mode"])
        runtime.record.unattended = True
        runtime.record.schedule = schedule_id
        runtime.save_record()
        self._note(schedule_id, last_run=time.time(), last_session=runtime.record.id, last_status="running", runs=int(item.get("runs") or 0) + 1)
        self.running[schedule_id] = asyncio.create_task(self._drive(schedule_id, runtime, item["prompt"]))
        return runtime.record.id

    async def _drive(self, schedule_id: str, runtime, prompt: str) -> None:
        try:
            await runtime.prompt(prompt)
            await runtime.wait_turn()
            self._note(schedule_id, last_status=runtime.record.last_status or "finished")
        except Exception as error:  # noqa: BLE001 - a failed run is recorded, never raised into the loop
            logger.warning("schedule %s failed: %s", schedule_id, error)
            self._note(schedule_id, last_status=f"error: {error}"[:300])
        finally:
            self.running.pop(schedule_id, None)

    async def close(self) -> None:
        for task in list(self.running.values()):
            task.cancel()


class Scheduler:
    """The server's clock: every ``TICK_SECONDS`` it starts what is due, for every tenant."""

    def __init__(self, server: "Server") -> None:
        self.server = server
        self._task: asyncio.Task | None = None

    def tenants(self) -> list["Tenant"]:
        if self.server.mode == "local":
            return [self.server.tenant()]
        found = []
        for user in self.server.users.list():
            if (self.server.home / "users" / user["id"] / "schedules.json").exists():
                found.append(self.server.tenant(user["id"]))
        return found

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            try:
                for tenant in self.tenants():
                    await tenant.schedules.tick()
            except Exception:  # noqa: BLE001
                logger.exception("scheduler tick failed")
            await asyncio.sleep(TICK_SECONDS)

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None


def describe(item: dict[str, Any]) -> str:
    when = datetime.fromtimestamp(item["next_run"]).strftime("%a %d %b %H:%M") if item.get("next_run") else "paused"
    return f"{item['id']} “{item['name']}” — {item['when']} ({item['mode']}) in {Path(item['workspace']).name}; next: {when}; last: {item.get('last_status') or 'never ran'}"
