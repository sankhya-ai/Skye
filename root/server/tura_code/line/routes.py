"""The line's local API: status and roster, and where root answers from."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..tenancy import Tenant
from . import config as line_config


class Answer(BaseModel):
    workspace: str = ""


def router(app_state) -> APIRouter:
    api = APIRouter(prefix="/api")
    tenant_of = app_state.auth.tenant
    server = app_state.server

    def local_only() -> None:
        if server.mode != "local":
            raise HTTPException(404, "The line is for a local root.")

    @api.get("/line")
    async def status(tenant: Tenant = Depends(tenant_of)) -> dict:
        local_only()
        return server.line.status()

    @api.put("/line")
    async def answer_from(body: Answer, tenant: Tenant = Depends(tenant_of)) -> dict:
        """Answer from this workspace (empty: stop answering, stay on the line)."""
        local_only()
        try:
            workspace = str(tenant.check_workspace(body.workspace)) if body.workspace else ""
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        line_config.save_workspace(server.home, workspace)
        await server.line.restart()
        return server.line.status()

    return api
