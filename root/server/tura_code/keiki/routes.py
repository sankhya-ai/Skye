"""Keiki's local API: status, connect with a harness API key, disconnect."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..tenancy import Tenant
from . import service
from .client import DEFAULT_BASE, KeikiError


class Key(BaseModel):
    api_key: str
    base: str = DEFAULT_BASE


def router(app_state) -> APIRouter:
    api = APIRouter(prefix="/api/keiki")
    tenant_of = app_state.auth.tenant
    server = app_state.server

    def local_only() -> None:
        if server.mode != "local":
            raise HTTPException(404, "Keiki is for a local root.")

    @api.get("")
    async def status(tenant: Tenant = Depends(tenant_of)) -> dict:
        local_only()
        return server.keiki.status()

    @api.put("")
    async def connect(body: Key, tenant: Tenant = Depends(tenant_of)) -> dict:
        local_only()
        key, base = body.api_key.strip(), body.base.strip().rstrip("/")
        if not key or not base.startswith("https://"):
            raise HTTPException(400, "Paste the agent's harness API key from Keiki (Agent → Harness → External, inbox mode).")
        try:
            return await service.connect(server, key, base)
        except KeikiError as error:
            raise HTTPException(400, str(error)) from error

    @api.delete("")
    async def disconnect(tenant: Tenant = Depends(tenant_of)) -> dict:
        local_only()
        tenant.connections.remove(service.NAME)
        await server.keiki.close()
        return server.keiki.status()

    return api
