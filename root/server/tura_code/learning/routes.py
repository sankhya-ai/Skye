"""The learning API: candidates to review, and the skills root has learned."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..tenancy import Tenant


def router(app_state) -> APIRouter:
    api = APIRouter(prefix="/api")
    tenant_of = app_state.auth.tenant

    @api.get("/learning")
    async def overview(tenant: Tenant = Depends(tenant_of)) -> dict:
        service = tenant.learning
        return {"mode": service.mode(), "candidates": service.store.list(), "skills": service.skills_with_outcomes()}

    @api.post("/learning/{candidate_id}/promote")
    async def promote(candidate_id: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        try:
            return await tenant.learning.promote(candidate_id)
        except KeyError as error:
            raise HTTPException(404, "No such candidate.") from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @api.post("/learning/{candidate_id}/reject")
    async def reject(candidate_id: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        try:
            return tenant.learning.reject(candidate_id)
        except KeyError as error:
            raise HTTPException(404, "No such candidate.") from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @api.get("/skills/{name}")
    async def read_skill(name: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        try:
            return {"name": name, "text": tenant.learning.skills.read(name)}
        except (ValueError, OSError) as error:
            raise HTTPException(404, "No such skill.") from error

    @api.post("/skills/{name}/rollback")
    async def rollback_skill(name: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        try:
            if not tenant.learning.skills.rollback(name):
                raise HTTPException(404, "No earlier version.")
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return {"ok": True}

    @api.delete("/skills/{name}")
    async def forget_skill(name: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        try:
            if not tenant.learning.skills.remove(name):
                raise HTTPException(404, "No such skill.")
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return {"ok": True}

    return api
