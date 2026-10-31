"""Who is calling, and whether they may.

Local-only by default, and hardened like one: the agent can run commands on
this machine, so a web page the owner happens to have open must not be able to
drive it.

* **Host check.** Requests must name an allowed host (localhost by default),
  which defeats DNS rebinding.
* **Same-site token.** The local app sets an HttpOnly ``SameSite=Strict``
  cookie from ``/api/auth/bootstrap``; a hosted install sets it from
  ``/api/auth/login`` with a user's token. Every other API call and the
  WebSocket require it (or ``Authorization: Bearer``). A cross-site page can
  neither read the token nor get the browser to send it.
* **Origin check** on the WebSocket, which browsers do not guard with CORS.
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

from fastapi import HTTPException, Request, WebSocket

from ..tenancy import LOCAL, Server, Tenant

import ipaddress

COOKIE = "tura_code_token"
#: The Google callback is where Google's consent page sends the browser back; it is
#: authorized by its single-use ``state``, not by a cookie (``connections.google``).
PUBLIC_PATHS = ("/api/auth/bootstrap", "/api/auth/login", "/api/health", "/api/connections/google/callback")


def is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS


def load_local_token(root: Path) -> str:
    explicit = os.environ.get("TURA_CODE_TOKEN", "").strip()
    if explicit:
        return explicit
    path = root / "token"
    if path.exists():
        token = path.read_text("utf-8").strip()
        if token:
            return token
    root.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(token)
    return token


def allowed_hosts() -> set[str]:
    extra = {h.strip().lower() for h in os.environ.get("TURA_CODE_ALLOWED_HOSTS", "").split(",") if h.strip()}
    return {"localhost", "127.0.0.1", "[::1]", "::1", "testserver", *extra}


def is_private_or_local_host(name: str) -> bool:
    if name.endswith(".local") or name.endswith(".internal"):
        return True
    try:
        ip = ipaddress.ip_address(name)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False


def host_ok(host_header: str | None) -> bool:
    if not host_header:
        return False
    host = host_header.strip().lower()
    if host.startswith("["):
        name = host.split("]")[0] + "]"
    else:
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return name in allowed_hosts() or is_private_or_local_host(name)


class Auth:
    def __init__(self, server: Server) -> None:
        self.server = server
        self.local_token = load_local_token(server.home) if server.mode == "local" else ""

    def _candidates(self, cookie: str | None, authorization: str | None) -> list[str]:
        tokens = [cookie or ""]
        if authorization and authorization.lower().startswith("bearer "):
            tokens.append(authorization[7:].strip())
        return [t for t in tokens if t]

    def identify(self, cookie: str | None, authorization: str | None) -> dict | None:
        """The caller as {id, name, admin}, or None."""
        for token in self._candidates(cookie, authorization):
            if self.server.mode == "local":
                if hmac.compare_digest(token, self.local_token):
                    return {"id": LOCAL, "name": "owner", "admin": True}
            else:
                user = self.server.users.authenticate(token)
                if user is not None:
                    return user
        return None

    def tenant(self, request: Request) -> Tenant:
        user = getattr(request.state, "user", None) or self.identify(request.cookies.get(COOKIE), request.headers.get("authorization"))
        if user is None:
            raise HTTPException(401, "Not authorized. Reload the page.")
        return self.server.tenant(user["id"])

    def websocket_tenant(self, websocket: WebSocket) -> Tenant | None:
        origin = websocket.headers.get("origin")
        origin_host = origin.split("://", 1)[-1] if origin else None
        if not host_ok(websocket.headers.get("host")) or (origin_host is not None and not host_ok(origin_host)):
            return None
        user = self.identify(websocket.cookies.get(COOKIE), websocket.headers.get("authorization"))
        return self.server.tenant(user["id"]) if user else None
