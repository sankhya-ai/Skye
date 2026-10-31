"""The coding-flow API: what the owner reviews, applies, and rolls back."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..tenancy import Tenant
from .store import FlowError


def router(app_state) -> APIRouter:
    api = APIRouter(prefix="/api/flows")
    tenant_of = app_state.auth.tenant

    async def guarded(call):
        try:
            return await call() if callable(call) else call
        except FlowError as error:
            raise HTTPException(404 if "not found" in str(error) else 409, str(error)) from error

    @api.get("")
    async def list_flows(tenant: Tenant = Depends(tenant_of)) -> dict:
        return {"flows": tenant.flows.list()}

    @api.post("")
    async def create_flow(body: dict[str, Any], tenant: Tenant = Depends(tenant_of)) -> dict:
        task = str(body.get("task") or "").strip()
        repo = str(body.get("repo") or tenant.workspace_root or ".")
        verification_commands = body.get("verification_commands") or ["git diff --check"]
        candidate_count = int(body.get("candidate_count") or 1)
        owner_session = body.get("owner_session")
        if not owner_session:
            active_sessions = tenant.sessions.list()
            owner_session = active_sessions[0]["id"] if active_sessions else tenant.sessions.create(repo, "Flow Orchestrator").record.id
        flow = await guarded(lambda: tenant.flows.create(
            repo,
            task,
            owner_session=owner_session,
            verification_commands=verification_commands,
            candidate_count=candidate_count,
        ))
        if body.get("auto_start", True):
            try:
                await guarded(lambda: _sync(tenant.flows.start, flow["id"]))
            except Exception:
                pass
        return {"flow": flow}

    @api.get("/{flow_id}")
    async def get_flow(flow_id: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        return await guarded(lambda: _sync(tenant.flows.get, flow_id))

    @api.get("/{flow_id}/review")
    async def review(flow_id: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        return await guarded(lambda: tenant.flows.review(flow_id))

    @api.post("/{flow_id}/start")
    async def start(flow_id: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        return await guarded(lambda: _sync(tenant.flows.start, flow_id))

    @api.post("/{flow_id}/stop")
    async def stop(flow_id: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        return await guarded(lambda: tenant.flows.stop(flow_id))

    @api.post("/{flow_id}/select")
    async def select(flow_id: str, body: dict[str, Any], tenant: Tenant = Depends(tenant_of)) -> dict:
        return await guarded(lambda: _sync(tenant.flows.select, flow_id, str(body.get("candidate_id") or "")))

    @api.post("/{flow_id}/apply")
    async def apply(flow_id: str, body: dict[str, Any], tenant: Tenant = Depends(tenant_of)) -> dict:
        return await guarded(lambda: tenant.flows.apply(flow_id, str(body.get("review_sha256") or "")))

    @api.post("/{flow_id}/rollback")
    async def rollback(flow_id: str, body: dict[str, Any], tenant: Tenant = Depends(tenant_of)) -> dict:
        return await guarded(lambda: tenant.flows.rollback(flow_id, str(body.get("applied_commit") or "")))

    @api.post("/{flow_id}/cancel")
    async def cancel(flow_id: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        return await guarded(lambda: tenant.flows.cancel(flow_id))

    return api


async def _sync(fn, *args):
    return fn(*args)
