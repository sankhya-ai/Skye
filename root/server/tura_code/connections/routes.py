"""Connections' API: status, GitHub (token or device flow), Gmail (OAuth)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..tenancy import Tenant
from . import github as gh
from . import google


class Token(BaseModel):
    token: str


class DevicePoll(BaseModel):
    device_code: str


def _page(title: str, message: str) -> HTMLResponse:
    body = (
        "<!doctype html><meta charset=utf-8><title>root</title>"
        "<body style='font:15px system-ui;display:grid;place-items:center;height:90vh;color:#222'>"
        f"<div style='max-width:420px;text-align:center'><h2>{title}</h2><p>{message}</p></div>"
    )
    return HTMLResponse(body)


def router(app_state) -> APIRouter:
    api = APIRouter(prefix="/api/connections")
    tenant_of = app_state.auth.tenant
    server = app_state.server
    flows = google.OAuthFlows()

    @api.get("")
    async def status(tenant: Tenant = Depends(tenant_of)) -> dict:
        store = tenant.connections
        token, source = await gh.credentials(store, local=server.mode == "local")
        github = {"connected": bool(token), "source": source, **(store.public(gh.NAME) or {})}
        github["device_flow"] = bool(gh.client_id())
        gmail = {"connected": store.get(google.NAME) is not None, "configured": google.configured(), **(store.public(google.NAME) or {})}
        return {"github": github, "gmail": gmail, "secrets": store.vault.name}

    # ----- GitHub -------------------------------------------------------------------

    async def _keep_github(tenant: Tenant, token: str, how: str) -> dict:
        try:
            user = await gh.GitHub(token).viewer()
        except gh.GitHubError as error:
            raise HTTPException(400, str(error)) from error
        tenant.connections.put(gh.NAME, secret={"token": token}, login=user.get("login", ""), how=how)
        return tenant.connections.public(gh.NAME) or {}

    @api.put("/github")
    async def github_token(body: Token, tenant: Tenant = Depends(tenant_of)) -> dict:
        if not body.token.strip():
            raise HTTPException(400, "Paste a GitHub token.")
        return await _keep_github(tenant, body.token.strip(), "token")

    @api.post("/github/device")
    async def github_device(tenant: Tenant = Depends(tenant_of)) -> dict:
        try:
            data = await gh.start_device_flow()
        except gh.GitHubError as error:
            raise HTTPException(400, str(error)) from error
        return {k: data.get(k) for k in ("device_code", "user_code", "verification_uri", "interval", "expires_in")}

    @api.post("/github/device/poll")
    async def github_device_poll(body: DevicePoll, tenant: Tenant = Depends(tenant_of)) -> dict:
        try:
            token = await gh.poll_device_flow(body.device_code)
        except gh.GitHubError as error:
            raise HTTPException(400, str(error)) from error
        if not token:
            return {"pending": True}
        return {"pending": False, **(await _keep_github(tenant, token, "device"))}

    @api.delete("/github")
    async def github_disconnect(tenant: Tenant = Depends(tenant_of)) -> dict:
        return {"removed": tenant.connections.remove(gh.NAME)}

    # ----- Gmail ----------------------------------------------------------------------

    @api.post("/google/start")
    async def google_start(request: Request, tenant: Tenant = Depends(tenant_of)) -> dict:
        redirect = str(request.url_for("google_callback"))
        try:
            return {"url": flows.start(tenant.id, redirect)}
        except google.GoogleError as error:
            raise HTTPException(400, str(error)) from error

    @api.get("/google/callback", name="google_callback")
    async def google_callback(state: str = "", code: str = "", error: str = "") -> HTMLResponse:
        flow = flows.take(state)
        if flow is None:
            return _page("That link has expired", "Start connecting Gmail again from root's settings.")
        if error or not code:
            return _page("Gmail was not connected", f"Google said: {error or 'no code'}. You can close this tab.")
        try:
            record = await google.finish(server.tenant(flow["tenant"]).connections, flow, code)
        except google.GoogleError as failure:
            return _page("Gmail was not connected", f"{failure} You can close this tab.")
        return _page("Gmail is connected", f"root can now read {record.get('email') or 'your mail'} and leave drafts. You can close this tab.")

    @api.delete("/google")
    async def google_disconnect(tenant: Tenant = Depends(tenant_of)) -> dict:
        return {"removed": tenant.connections.remove(google.NAME)}

    return api
