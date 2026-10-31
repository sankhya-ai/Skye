"""Auth, health, settings, models and the trust ledger."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .. import __version__, launcher, workspace
from ..tenancy import Tenant
from ..trust import PROMOTE_AFTER, classify_command
from .security import COOKIE


def router(app_state) -> APIRouter:
    api = APIRouter(prefix="/api")
    server, auth = app_state.server, app_state.auth
    tenant_of = auth.tenant

    @api.post("/auth/bootstrap")
    async def bootstrap(response: Response) -> dict:
        # A root in the cloud (TURA_CODE_BOOTSTRAP=0) hands its token to no one: sign in with TURA_CODE_TOKEN.
        if server.mode != "local" or os.environ.get("TURA_CODE_BOOTSTRAP", "1") == "0":
            raise HTTPException(403, "Sign in with your token.")
        response.set_cookie(COOKIE, auth.local_token, httponly=True, samesite="strict", path="/")
        return {"ok": True}

    @api.post("/auth/login")
    async def login(body: dict[str, Any], response: Response) -> dict:
        token = str(body.get("token") or "").strip()
        user = auth.identify(token, None)
        if user is None:
            raise HTTPException(401, "That token is not valid.")
        # Hosted installs are served over HTTPS; TURA_CODE_SECURE_COOKIE=0 is for a local trial.
        secure = server.mode == "hosted" and os.environ.get("TURA_CODE_SECURE_COOKIE", "1") != "0"
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", path="/", secure=secure)
        return {"ok": True, "user": user}

    @api.post("/auth/logout")
    async def logout(response: Response) -> dict:
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @api.get("/me")
    async def me(request: Request) -> dict:
        return {"mode": server.mode, "user": request.state.user, "sandbox": server.sandbox.name}

    @api.get("/health")
    async def health(request: Request) -> dict:
        try:
            binary, runtime_error = str(launcher.find_runtime_binary()), ""
        except launcher.RuntimeNotFound as error:
            binary, runtime_error = "", str(error)
        user = auth.identify(request.cookies.get(COOKIE), request.headers.get("authorization"))
        key_set = bool(server.tenant(user["id"]).api_key()) if user else False
        return {
            "version": __version__,
            "mode": server.mode,
            "signed_in": user is not None,
            "runtime": {"binary": binary, "error": runtime_error},
            "memory": server.gateway.status(),
            "api_key_set": key_set,
            "scripted": launcher.scripted(),
            # Where root keeps everything (local only: a hosted server's layout is the operator's).
            "data_dir": str(server.home) if server.mode == "local" and user else "",
            # The host can show the native folder dialog (local mode on macOS).
            "folder_picker": server.mode == "local" and workspace.folder_picker(),
        }

    @api.get("/settings")
    async def get_settings(tenant: Tenant = Depends(tenant_of)) -> dict:
        data = tenant.settings.get().public()
        data["api_key_set"] = bool(tenant.api_key())
        return data

    @api.put("/settings")
    async def put_settings(changes: dict[str, Any], tenant: Tenant = Depends(tenant_of)) -> dict:
        try:
            updated = tenant.settings.update(changes)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        if "model" in changes or "base_url" in changes:
            asyncio.create_task(server.refresh_capabilities())
            context = server.catalog.context_length(updated.model)
            if context and "max_context_size" not in changes:
                updated = tenant.settings.update({"max_context_size": max(8_000, min(int(context), 4_000_000))})
        if server.mode == "local" and {"memory_mode", "memory_llm_model", "memory_embedding_model", "memory_embedding_dims"} & changes.keys():
            asyncio.create_task(server.gateway.warm())
        data = updated.public()
        data["api_key_set"] = bool(tenant.api_key())
        return data

    @api.get("/models")
    async def models(refresh: bool = False, tenant: Tenant = Depends(tenant_of)) -> dict:
        items = await server.catalog.models(tenant.settings.get().base_url, refresh=refresh)
        server.model_capabilities.update({m["id"]: m["capabilities"] for m in items})
        return {"models": items, "error": server.catalog.error}

    @api.get("/trust")
    async def trust_ledger(tenant: Tenant = Depends(tenant_of)) -> dict:
        return {"entries": tenant.trust.snapshot(), "promote_after": PROMOTE_AFTER}

    @api.post("/trust/revoke")
    async def trust_revoke(body: dict[str, Any], tenant: Tenant = Depends(tenant_of)) -> dict:
        if not tenant.trust.revoke(str(body.get("key") or "")):
            raise HTTPException(404, "No trust entry for that action.")
        return {"ok": True}

    @api.post("/classify")
    async def classify(body: dict[str, Any], tenant: Tenant = Depends(tenant_of)) -> dict:
        return classify_command(str(body.get("command") or "")).as_dict()

    return api
