"""Slack's local API: status, connect with the app's two tokens, disconnect, the app manifest."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..tenancy import Tenant
from . import service
from .api import SlackError


class Tokens(BaseModel):
    bot_token: str
    app_token: str


def router(app_state) -> APIRouter:
    api = APIRouter(prefix="/api/slack")
    tenant_of = app_state.auth.tenant
    server = app_state.server

    def local_only() -> None:
        if server.mode != "local":
            raise HTTPException(404, "Slack is for a local root.")

    @api.get("")
    async def status(tenant: Tenant = Depends(tenant_of)) -> dict:
        local_only()
        return server.slack.status()

    @api.get("/manifest")
    async def manifest(tenant: Tenant = Depends(tenant_of)) -> dict:
        """Paste into api.slack.com/apps → Create New App → From a manifest."""
        return service.manifest()

    @api.put("")
    async def connect(body: Tokens, tenant: Tenant = Depends(tenant_of)) -> dict:
        local_only()
        bot, app = body.bot_token.strip(), body.app_token.strip()
        if not bot.startswith("xoxb-") or not app.startswith("xapp-"):
            raise HTTPException(400, "Use the bot token (xoxb-…, OAuth & Permissions) and an app-level token (xapp-…, Basic Information, with connections:write).")
        try:
            return await service.connect(server, bot, app)
        except SlackError as error:
            raise HTTPException(400, f"Slack refused it: {error.error}") from error

    @api.delete("")
    async def disconnect(tenant: Tenant = Depends(tenant_of)) -> dict:
        local_only()
        tenant.connections.remove(service.NAME)
        await server.slack.close()
        return server.slack.status()

    return api
