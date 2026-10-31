"""The workspace (files, changes, folders), creating workspaces, and memory."""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from .. import workspace
from ..tenancy import Tenant, WorkspaceError

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_GIT_URL = re.compile(r"^https://[\w.-]+(:\d+)?/[\w./~-]+$")


def router(app_state) -> APIRouter:
    api = APIRouter(prefix="/api")
    tenant_of = app_state.auth.tenant

    def root_of(tenant: Tenant, path: str) -> Path:
        try:
            return tenant.check_workspace(path)
        except WorkspaceError as error:
            raise HTTPException(400, str(error)) from error

    @api.get("/workspace/files")
    async def files(path: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        return await asyncio.to_thread(workspace.list_files, root_of(tenant, path))

    @api.get("/workspace/file")
    async def file(path: str, file: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        try:
            return await asyncio.to_thread(workspace.read_file, root_of(tenant, path), file)
        except (workspace.WorkspaceError, OSError) as error:
            raise HTTPException(400, str(error)) from error

    @api.get("/workspace/changes")
    async def changes(path: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        return await asyncio.to_thread(workspace.git_changes, root_of(tenant, path))

    @api.get("/fs/dirs")
    async def dirs(path: str | None = None, tenant: Tenant = Depends(tenant_of)) -> dict:
        try:
            return await asyncio.to_thread(workspace.list_dirs, path, tenant.workspace_root)
        except (workspace.WorkspaceError, OSError) as error:
            raise HTTPException(400, str(error)) from error

    @api.post("/fs/pick")
    async def pick(body: dict[str, Any], tenant: Tenant = Depends(tenant_of)) -> dict:
        """The native folder dialog on the owner's own machine (local mode, macOS)."""
        if tenant.server.mode != "local" or not workspace.folder_picker():
            raise HTTPException(404, "No folder dialog here.")
        return {"path": await workspace.pick_folder(str(body.get("start") or "") or None)}

    @api.get("/workspaces")
    async def list_workspaces(tenant: Tenant = Depends(tenant_of)) -> dict:
        personal = []
        if tenant.workspace_root and tenant.workspace_root.is_dir():
            personal = [{"name": d.name, "path": str(d), "scope": "personal"} for d in sorted(tenant.workspace_root.iterdir()) if d.is_dir() and not d.name.startswith(".")]
        shared = []
        if tenant.server.shared_workspaces and tenant.server.shared_workspaces.is_dir():
            shared = [{"name": d.name, "path": str(d), "scope": "shared"} for d in sorted(tenant.server.shared_workspaces.iterdir()) if d.is_dir() and not d.name.startswith(".")]
        return {"workspaces": personal + shared, "personal": personal, "shared": shared}

    @api.post("/workspaces")
    async def create_workspace(body: dict[str, Any], tenant: Tenant = Depends(tenant_of)) -> dict:
        """A new folder in the personal or shared workspace folder, empty or cloned from an https Git URL."""
        scope = str(body.get("scope") or "personal").strip()
        base_dir = tenant.server.shared_workspaces if scope == "shared" else tenant.workspace_root
        if base_dir is None:
            raise HTTPException(400, "Open a folder on this machine instead.")
        name = str(body.get("name") or "").strip()
        url = str(body.get("git_url") or "").strip()
        if not name and url:
            name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        if not _NAME.match(name):
            raise HTTPException(400, "Use a name of letters, digits, `.`, `-` or `_`.")
        target = base_dir / name
        if target.exists():
            raise HTTPException(409, "A workspace with that name exists.")
        if not url:
            target.mkdir(parents=True)
            return {"path": str(target), "scope": scope, "name": name}
        if not _GIT_URL.match(url):
            raise HTTPException(400, "Clone from an https:// Git URL.")
        argv = tenant.sandbox.wrap(
            ["git", "clone", "--depth", "50", "--", url, str(target)],
            _mounts(tenant),
        )
        process = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=tenant.sandbox.environment({"PATH": "/usr/bin:/bin", "GIT_TERMINAL_PROMPT": "0"}, _mounts(tenant)))
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 300)
        except asyncio.TimeoutError:
            process.kill()
            shutil.rmtree(target, ignore_errors=True)
            raise HTTPException(504, "The clone took too long.")
        if process.returncode != 0:
            shutil.rmtree(target, ignore_errors=True)
            raise HTTPException(400, f"Clone failed: {output.decode(errors='replace')[-500:]}")
        return {"path": str(target)}

    @api.delete("/workspaces")
    async def delete_workspace(path: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        tenant.settings.forget_workspace(path)
        for s in tenant.sessions.list():
            if s.get("workspace") == path:
                try:
                    await tenant.sessions.delete(s["id"])
                except Exception:
                    pass
        return {"ok": True}

    @api.get("/memory")
    async def memories(path: str, q: str | None = None, tenant: Tenant = Depends(tenant_of)) -> dict:
        root = root_of(tenant, path)
        try:
            items = await (tenant.memory.recall(q, root, limit=20) if q else tenant.memory.list_memories(root))
        except Exception as error:  # noqa: BLE001
            return {"items": [], "status": tenant.memory.status(), "error": str(error)}
        return {"items": items, "status": tenant.memory.status()}

    @api.delete("/memory/{memory_id}")
    async def forget(memory_id: str, path: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        try:
            forgotten = await tenant.memory.forget(memory_id, root_of(tenant, path))
        except Exception as error:  # noqa: BLE001
            raise HTTPException(400, str(error)) from error
        if not forgotten:
            raise HTTPException(404, "No such memory.")
        return {"ok": True}

    return api


def _mounts(tenant: Tenant):
    from ..sandbox import Mounts

    return Mounts(workspace=tenant.workspace_root, home=tenant.home)
