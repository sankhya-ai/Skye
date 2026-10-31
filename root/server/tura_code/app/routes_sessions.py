"""Sessions: CRUD, the event history in pages, processes, and the live socket."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .. import launcher
from ..processes import ProcessError
from ..sessions import MODES, SessionRuntime
from ..sessions.checkpoints import CheckpointError
from ..tenancy import Tenant, WorkspaceError
from ..wire import WireError

logger = logging.getLogger(__name__)


class CreateSession(BaseModel):
    workspace: str
    title: str | None = None
    #: Tool-name patterns this session is limited to (see catalogue.guarantee for what that does and does not bound).
    tool_scope: list[str] = []


class UpdateSession(BaseModel):
    title: str | None = None
    permission_mode: str | None = None


class Restore(BaseModel):
    #: A checkpoint id; empty restores the one before the latest turn.
    checkpoint: str = ""


def router(app_state) -> APIRouter:
    api = APIRouter(prefix="/api")
    auth = app_state.auth
    tenant_of = auth.tenant

    def session(tenant: Tenant, session_id: str) -> SessionRuntime:
        try:
            return tenant.sessions.get(session_id)
        except KeyError as error:
            raise HTTPException(404, "Session not found.") from error

    @api.get("/sessions")
    async def list_sessions(tenant: Tenant = Depends(tenant_of)) -> dict:
        return {"sessions": tenant.sessions.list()}

    @api.post("/sessions")
    async def create_session(body: CreateSession, tenant: Tenant = Depends(tenant_of)) -> dict:
        try:
            return tenant.sessions.create(body.workspace, body.title, tool_scope=body.tool_scope).public()
        except WorkspaceError as error:
            raise HTTPException(400, str(error)) from error

    @api.get("/sessions/{session_id}")
    async def get_session(session_id: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        return session(tenant, session_id).public()

    @api.patch("/sessions/{session_id}")
    async def update_session(session_id: str, body: UpdateSession, tenant: Tenant = Depends(tenant_of)) -> dict:
        runtime = session(tenant, session_id)
        if body.title is not None:
            runtime.record.title = body.title.strip()[:120] or runtime.record.title
            runtime.save_record()
        if body.permission_mode is not None:
            if body.permission_mode not in MODES:
                raise HTTPException(400, "Unknown permission mode.")
            await runtime.set_mode(body.permission_mode)
        return runtime.public()

    @api.delete("/sessions/{session_id}")
    async def delete_session(session_id: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        session(tenant, session_id)
        await tenant.sessions.delete(session_id)
        return {"ok": True}

    @api.get("/sessions/{session_id}/events")
    async def events(session_id: str, before: int | None = None, tenant: Tenant = Depends(tenant_of)) -> dict:
        return session(tenant, session_id).log.page(before)

    @api.get("/sessions/{session_id}/director")
    async def director(session_id: str, limit: int = 100, tenant: Tenant = Depends(tenant_of)) -> dict:
        return {"decisions": session(tenant, session_id).director_log(max(1, min(limit, 1000)))}

    @api.post("/sessions/{session_id}/grants/revoke")
    async def grant_revoke(session_id: str, body: dict[str, Any], tenant: Tenant = Depends(tenant_of)) -> dict:
        runtime = session(tenant, session_id)
        if not runtime.revoke_grant(str(body.get("key") or "")):
            raise HTTPException(404, "No such grant in this session.")
        return runtime.public()

    @api.get("/sessions/{session_id}/checkpoints")
    async def checkpoints(session_id: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        runtime = session(tenant, session_id)
        try:
            found = await runtime.checkpoints.list()
        except CheckpointError as error:
            raise HTTPException(409, str(error)) from error
        return {"checkpoints": [c.public() for c in found], "disabled": runtime.checkpoints.disabled}

    @api.get("/sessions/{session_id}/checkpoints/{checkpoint}/changes")
    async def checkpoint_changes(session_id: str, checkpoint: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        try:
            return {"changes": await session(tenant, session_id).checkpoints.changes_since(checkpoint)}
        except CheckpointError as error:
            raise HTTPException(404, str(error)) from error

    @api.post("/sessions/{session_id}/rollback")
    async def rollback(session_id: str, body: Restore, tenant: Tenant = Depends(tenant_of)) -> dict:
        try:
            return await session(tenant, session_id).rollback(body.checkpoint.strip() or None)
        except CheckpointError as error:
            raise HTTPException(409, str(error)) from error

    @api.get("/sessions/{session_id}/processes")
    async def session_processes(session_id: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        return {"processes": session(tenant, session_id).processes.list()}

    @api.get("/sessions/{session_id}/processes/{process_id}/log")
    async def process_log(session_id: str, process_id: str, offset: int | None = None, limit: int = 500, tenant: Tenant = Depends(tenant_of)) -> dict:
        try:
            return session(tenant, session_id).processes.log(process_id, offset=offset, limit=limit)
        except ProcessError as error:
            raise HTTPException(404, str(error)) from error

    @api.post("/sessions/{session_id}/processes/{process_id}/kill")
    async def process_kill(session_id: str, process_id: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        try:
            item = await session(tenant, session_id).processes.kill(process_id)
        except ProcessError as error:
            raise HTTPException(404, str(error)) from error
        return item.public()

    @api.websocket("/sessions/{session_id}/ws")
    async def session_socket(websocket: WebSocket, session_id: str) -> None:
        tenant = auth.websocket_tenant(websocket)
        if tenant is None:
            await websocket.close(code=4401)
            return
        try:
            runtime = tenant.sessions.get(session_id)
        except KeyError:
            await websocket.close(code=4404)
            return
        await websocket.accept()
        queue, snapshot = runtime.subscribe()
        await websocket.send_json({"type": "hello", **snapshot})

        async def pump() -> None:
            while True:
                event = await queue.get()
                await websocket.send_json({"type": "event", "event": event})

        sender = asyncio.create_task(pump())
        try:
            while True:
                await handle_client(runtime, await websocket.receive_json(), websocket)
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            logger.exception("websocket handler failed")
        finally:
            sender.cancel()
            runtime.unsubscribe(queue)

    return api


async def handle_client(runtime: SessionRuntime, message: dict, websocket: WebSocket) -> None:
    kind = message.get("type")
    try:
        if kind == "prompt":
            await runtime.prompt(str(message.get("text") or ""), message.get("attachments") or None)
        elif kind == "queue":
            await runtime.enqueue(str(message.get("text") or ""), message.get("attachments") or None)
        elif kind == "unqueue":
            runtime.unqueue(str(message.get("id") or ""))
        elif kind == "send_queued":
            await runtime.send_queued(str(message.get("id") or ""))
        elif kind == "cancel":
            await runtime.cancel()
        elif kind == "approval":
            runtime.resolve_approval(str(message.get("request_id") or ""), str(message.get("response") or "reject"))
        elif kind == "set_mode":
            await runtime.set_mode(str(message.get("mode") or "default"))
            await websocket.send_json({"type": "session", "session": runtime.public()})
        elif kind == "ping":
            await websocket.send_json({"type": "pong"})
        else:
            await websocket.send_json({"type": "error", "message": f"Unknown message type {kind!r}."})
    except (ValueError, WireError, launcher.RuntimeNotFound) as error:
        await websocket.send_json({"type": "error", "message": str(error)})
