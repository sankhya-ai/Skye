"""The HTTP + WebSocket API, the LLM proxy, and the static UI.

``create_app`` builds a :class:`~tura_code.tenancy.Server` (local or hosted)
and mounts the route modules on it. See ``security`` for how callers are
identified and why the API is safe to leave running on a laptop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .. import __version__, extensions, launcher, maritime, terminal
from ..pool import RuntimePool
from ..sandbox import Sandbox
from ..settings import data_dir
from ..tenancy import Server
from . import routes_core, routes_sessions, routes_workspace
from .security import COOKIE, PUBLIC_PATHS, Auth, host_ok, is_public_path

logger = logging.getLogger(__name__)


class AppState:
    def __init__(self, server: Server) -> None:
        self.server = server
        self.auth = Auth(server)


def create_app(
    root: Path | None = None,
    *,
    mode: str | None = None,
    sandbox: Sandbox | None = None,
    pool: RuntimePool | None = None,
    self_url: str | None = None,
) -> FastAPI:
    """The app. ``self_url`` (the server's own address, as runtimes reach it)
    turns on the LLM proxy; without it runtimes are given the key directly."""
    server = Server(
        root or data_dir(),
        mode=mode or os.environ.get("TURA_CODE_MODE", "local"),
        sandbox=sandbox,
        pool=pool,
    )
    server.proxy.base = self_url or os.environ.get("TURA_CODE_SELF_URL") or None
    extensions.install(server)
    state = AppState(server)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        server.start()
        tasks = [asyncio.create_task(server.gateway.warm()), asyncio.create_task(server.refresh_capabilities())]
        yield
        for task in tasks:
            task.cancel()
        await server.close()

    app = FastAPI(title="root", version=__version__, lifespan=lifespan)
    app.state.root = state

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if maritime.enabled() and request.url.path in maritime.ROUTES:
            return await call_next(request)  # Maritime's front door: the VM is not reachable otherwise
        if not host_ok(request.headers.get("host")):
            return JSONResponse({"detail": "Host not allowed."}, status_code=403)
        path = request.url.path
        request.state.user = None
        if path.startswith("/api/") and not is_public_path(path):
            user = state.auth.identify(request.cookies.get(COOKIE), request.headers.get("authorization"))
            if user is None:
                return JSONResponse({"detail": "Not authorized. Reload the page."}, status_code=401)
            request.state.user = user
        return await call_next(request)

    if maritime.enabled():
        app.include_router(maritime.router(server))
    app.include_router(routes_core.router(state))
    app.include_router(routes_sessions.router(state))
    app.include_router(routes_workspace.router(state))
    app.include_router(terminal.router(state))
    for router in extensions.routers(state):
        app.include_router(router)

    @app.api_route("/llm/api/{path:path}", methods=["GET", "POST"], include_in_schema=False)
    async def llm_proxy(path: str, request: Request):
        return await server.proxy.forward(request, path)

    @app.get("/{rest:path}", include_in_schema=False)
    async def ui(rest: str):
        if rest.startswith("api/"):
            raise HTTPException(404)
        if not launcher.ui_dist().is_dir():
            return JSONResponse(
                {"detail": "The UI is not built. Run `npm install && npm run build` in ui/, or use `npm run dev` there."},
                status_code=404,
            )
        candidate = (launcher.ui_dist() / rest).resolve()
        if rest and candidate.is_file() and candidate.is_relative_to(launcher.ui_dist().resolve()):
            return FileResponse(candidate)
        return FileResponse(launcher.ui_dist() / "index.html", headers={"Cache-Control": "no-cache"})

    return app
