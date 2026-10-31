"""One person's sessions: create, list, load, delete."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from .record import SessionRecord
from .runtime import SessionRuntime

if TYPE_CHECKING:
    from ..tenancy import Tenant

logger = logging.getLogger(__name__)
_ID = re.compile(r"[0-9a-f]{8,64}")


class SessionManager:
    def __init__(self, tenant: "Tenant") -> None:
        self.tenant = tenant
        self.dir = tenant.sessions_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.runtimes: dict[str, SessionRuntime] = {}

    def list(self, *, include_candidates: bool = False) -> list[dict]:
        records = []
        for path in self.dir.glob("*/session.json"):
            try:
                record = SessionRecord.load(path)
            except (OSError, ValueError, TypeError):
                continue
            if record.kind == "candidate" and not include_candidates:
                continue
            runtime = self.runtimes.get(record.id)
            records.append(runtime.public() if runtime else {**asdict(record), "running": False, "runtime_state": "stopped", "runtime": {}})
        records.sort(key=lambda item: item["updated_at"], reverse=True)
        return records

    def create(self, workspace: str | Path, title: str | None = None, *, kind: str = "session", parent: str = "", mode: str | None = None, role: str = "", tool_scope: list[str] | None = None) -> SessionRuntime:
        path = self.tenant.check_workspace(workspace)
        settings = self.tenant.settings.get()
        record = SessionRecord(
            id=uuid.uuid4().hex,
            workspace=str(path),
            title=title or "New session",
            permission_mode=mode or settings.default_permission_mode,
            kind=kind,
            parent=parent,
            role=role,
            tool_scope=list(tool_scope or []),
        )
        runtime = SessionRuntime(self.tenant, record)
        runtime.save_record()
        self.runtimes[record.id] = runtime
        if kind == "session":
            self.tenant.settings.remember_workspace(str(path))
        return runtime

    def get(self, session_id: str) -> SessionRuntime:
        runtime = self.runtimes.get(session_id)
        if runtime is not None:
            return runtime
        if not _ID.fullmatch(session_id or ""):
            raise KeyError(session_id)
        path = self.dir / session_id / "session.json"
        if not path.exists():
            raise KeyError(session_id)
        runtime = SessionRuntime(self.tenant, SessionRecord.load(path))
        self.runtimes[session_id] = runtime
        return runtime

    async def delete(self, session_id: str) -> None:
        runtime = self.get(session_id)
        await runtime.close()
        self.runtimes.pop(session_id, None)
        await asyncio.to_thread(_remove_tree, runtime.dir)

    async def close(self) -> None:
        for runtime in list(self.runtimes.values()):
            try:
                await runtime.close()
            except Exception:  # noqa: BLE001
                logger.exception("closing session %s", runtime.record.id)


def _remove_tree(path: Path) -> None:
    """Delete a session's folder. Windows frees a stopped process's files a moment
    after it exits, and refuses to delete an open file, so try for a few seconds."""
    for _ in range(50):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        time.sleep(0.1)
    logger.warning("could not fully delete %s", path)
