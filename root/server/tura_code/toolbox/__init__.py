"""Toolbox: root's own Python, packages installed on demand, and the files work produces.

``env`` (the per-person virtual environment), ``tool`` (``run_python``). Each
session also has a scratch folder (``$ROOT_SCRATCH``, ``<session>/scratch``)
for throwaway work and deliverables; the owner opens or downloads what lands
there — or anywhere in the workspace — through the routes below.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from .env import Toolbox, ToolboxError
from .tool import RunPythonToolset, changed_files

__all__ = ["Toolbox", "ToolboxError", "install"]

INLINE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf", "text/plain", "text/csv"}


def toolbox_root(tenant) -> Path:
    # Hosted: inside the person's sandbox home, the one place besides the workspace it may write.
    return (tenant.home / ".root-toolbox") if tenant.home is not None else tenant.root / "toolbox"


def install(server) -> None:
    def extend(tenant) -> None:
        tenant.toolbox = Toolbox(toolbox_root(tenant))
        tenant.toolsets.append(RunPythonToolset())

    server.extensions.append(extend)


def router(app_state):
    from fastapi import APIRouter, Depends, HTTPException
    from fastapi.responses import FileResponse

    from ..tenancy import Tenant

    api = APIRouter(prefix="/api")

    def scope_root(tenant: Tenant, session_id: str, scope: str) -> Path:
        try:
            runtime = tenant.sessions.get(session_id)
        except KeyError as error:
            raise HTTPException(404, "Session not found.") from error
        if scope == "scratch":
            return runtime.scratch_dir
        if scope == "workspace":
            return Path(runtime.record.workspace)
        raise HTTPException(400, "scope is `scratch` or `workspace`.")

    @api.get("/sessions/{session_id}/outputs")
    async def outputs(session_id: str, tenant: Tenant = Depends(app_state.auth.tenant)) -> dict:
        """What is in the session's scratch folder, newest first."""
        root = scope_root(tenant, session_id, "scratch")
        files = [f for f in changed_files(root, 0, limit=200) if not f["path"].startswith(".root-runs")] if root.is_dir() else []
        return {"files": files, "path": str(root)}

    @api.get("/sessions/{session_id}/file")
    async def file(session_id: str, path: str, scope: str = "scratch", download: bool = False, tenant: Tenant = Depends(app_state.auth.tenant)):
        root = scope_root(tenant, session_id, scope).resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(404, "No such file.")
        media = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        # Shown in the browser only as types that cannot run script as this origin (no HTML, no SVG),
        # and even then in a CSP sandbox; anything else is a download.
        inline_ok = media in INLINE_TYPES
        return FileResponse(
            target,
            media_type=media if inline_ok else "application/octet-stream",
            content_disposition_type="inline" if inline_ok and not download else "attachment",
            filename=target.name,
            headers={"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"},
        )

    return api
