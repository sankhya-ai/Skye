"""Job-scoped credentials: a job may USE a named credential without the model ever holding it.

The owner registers a credential once (write-only: ``PUT /api/credentials/{name}`` with the value and the hosts it may
be sent to). A job lists the names it may use. Its session then gets one host tool, ``credentialed_request``, which
adds the credential to an outgoing HTTP request itself, so the secret is never in the prompt, a tool argument or a reply.

What is enforced, and what is not:
* Enforced: the tool exists only in a session whose job listed the credential; the request goes only to a host, method
  (default GET only) and path prefix the owner registered (exact host, https; http only for localhost); redirects are never followed; the response is capped and has
  the secret (and its common encodings) replaced before the model or any handoff sees it; job replies, errors, contract
  data and handoffs are passed through the same redaction.
* NOT enforced: the store is a 0600 file under the data directory, which stops other users, not this one. A role that has
  Shell, process or run_python runs as the same user and can read that file (and the owner token beside it, and so call
  the owner API); a job must say ``accept_unprotected_credentials`` to give credentials to such a role. For other roles
  the file tools refuse symlinks out of the workspace and a workspace overlapping the data directory is refused, but this
  is a checked path rule, not isolation. Authority to attach credentials or ``serve`` to a job is the authenticated owner
  API; a job cannot do it for itself unless its code can read the owner token. The job reports ``credential_guarantee``.
  This is not an exfiltration boundary: the role can send any request the grant allows, and the host side effects of an
  allowed POST are real. Redaction matches known values and encodings; a
  secret the page or API transforms some other way is not caught.
* Not provided: rotation, expiry, per-request approval, audit beyond the job's own tool log.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .sessions.host_tools import tool_error

if TYPE_CHECKING:
    from .sessions.runtime import SessionRuntime

TOOL_NAME = "credentialed_request"
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,48}$")
MAX_BODY = 20000
MAX_REQUEST_BODY = 20000
TIMEOUT = 20


class BrokerError(ValueError):
    pass


class Broker:
    def __init__(self, root: Path) -> None:
        self.path = Path(root) / "credentials.json"

    def _read(self) -> dict[str, dict[str, Any]]:
        try:
            return json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    def put(self, name: str, value: str, hosts: list[str], header: str = "Authorization", scheme: str = "Bearer", methods: list[str] | None = None, paths: list[str] | None = None) -> None:
        if not NAME_RE.match(name):
            raise BrokerError("Credential names are 1-48 letters, digits, dots, dashes or underscores.")
        hosts = [h.strip().lower() for h in hosts if h.strip()]
        if not value or not hosts:
            raise BrokerError("A credential needs a value and at least one host.")
        if any("/" in h or "*" in h or "@" in h for h in hosts):
            raise BrokerError("Hosts are exact host names (optionally host:port), no paths or wildcards.")
        data = self._read()
        methods = [m.upper() for m in (methods or ["GET"])]
        paths = paths or ["/"]
        if any(m not in ("GET", "POST", "PUT", "PATCH", "DELETE") for m in methods) or any(not p.startswith("/") for p in paths):
            raise BrokerError("methods are HTTP verbs; paths are prefixes starting with /.")
        data[name] = {"value": value, "hosts": hosts, "header": header, "scheme": scheme, "methods": methods, "paths": paths}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(data))
        tmp.replace(self.path)

    def delete(self, name: str) -> None:
        data = self._read()
        if data.pop(name, None) is not None:
            self.path.write_text(json.dumps(data))
            self.path.chmod(0o600)

    def names(self) -> list[dict[str, Any]]:
        """What is registered, never the values."""
        return [{"name": n, "hosts": c["hosts"], "methods": c.get("methods", ["GET"]), "paths": c.get("paths", ["/"])} for n, c in sorted(self._read().items())]

    def has(self, name: str) -> bool:
        return name in self._read()

    def variants(self, names: list[str] | None = None) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for name, cred in self._read().items():
            if names is not None and name not in names:
                continue
            v = cred["value"]
            forms = {v, urllib.parse.quote(v, safe=""), urllib.parse.quote_plus(v), base64.b64encode(v.encode()).decode(), base64.urlsafe_b64encode(v.encode()).decode().rstrip("="), base64.b64encode(v.encode()).decode().rstrip("=")}
            out += [(form, name) for form in forms if len(form) >= 6]
        return sorted(out, key=lambda p: -len(p[0]))

    def redact(self, text: str, names: list[str] | None = None) -> str:
        """Replace every registered value (and URL/base64 forms) with ``[credential:name]``. ``names=None``: all of them."""
        for form, name in self.variants(names):
            text = text.replace(form, f"[credential:{name}]")
        return text

    def redact_json(self, value: Any) -> Any:
        return json.loads(self.redact(json.dumps(value)))

    def request(self, name: str, method: str, url: str, body: str = "") -> dict[str, Any]:
        cred = self._read().get(name)
        if cred is None:
            raise BrokerError("No such credential.")
        method = method.upper()
        if method not in cred.get("methods", ["GET"]):
            raise BrokerError(f"This credential is registered for {', '.join(cred.get('methods', ['GET']))} only.")
        parsed = urllib.parse.urlparse(url)
        host = (parsed.netloc or "").lower()
        local = (parsed.hostname or "") in ("localhost", "127.0.0.1", "::1")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
            raise BrokerError("Only https URLs (http only for localhost).")
        if "@" in parsed.netloc or host not in cred["hosts"]:
            raise BrokerError(f"This credential is not registered for host `{host}`. Registered: {', '.join(cred['hosts'])}.")
        normal = urllib.parse.unquote(parsed.path or "/")
        if ".." in normal.split("/") or not any(normal.startswith(p) for p in cred.get("paths", ["/"])):
            raise BrokerError(f"This credential is registered for paths under {', '.join(cred.get('paths', ['/']))} only.")
        data = body.encode()[:MAX_REQUEST_BODY] if body else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header(cred["header"], f"{cred['scheme']} {cred['value']}".strip())
        if data:
            req.add_header("Content-Type", "application/json" if body.lstrip().startswith(("{", "[")) else "text/plain")

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):  # a redirect could carry the header to another host
                return None

        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(req, timeout=TIMEOUT) as r:
                status, headers, raw = r.status, dict(r.headers), r.read(MAX_BODY + 1)
        except urllib.error.HTTPError as error:
            status, headers, raw = error.code, dict(error.headers), error.read(MAX_BODY + 1)
        except (urllib.error.URLError, OSError) as error:
            raise BrokerError(f"Request failed: {error}") from error
        text = raw[:MAX_BODY].decode("utf-8", errors="replace")
        return {"status": status, "location": headers.get("Location", ""), "truncated": len(raw) > MAX_BODY, "body": self.redact(text, None)}


def definition() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "Send an HTTP request that carries one of this job's credentials. You never see the credential: it is added to the "
            "request for you, only to the hosts the owner registered, and removed from the response. Redirects are not followed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "credential": {"type": "string", "description": "One of the credential names this job was given."},
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                "url": {"type": "string"},
                "body": {"type": "string", "description": "Optional request body (JSON text)."},
            },
            "required": ["credential", "url"],
            "additionalProperties": False,
        },
    }


class CredentialToolset:
    """Present only in a session whose job listed credentials; answers only for those."""

    def definitions(self, runtime: "SessionRuntime") -> list[dict[str, Any]]:
        return [definition()] if getattr(runtime.record, "credentials", None) else []

    def handles(self, name: str) -> bool:
        return name == TOOL_NAME

    async def call(self, runtime: "SessionRuntime", call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        import asyncio

        granted = list(getattr(runtime.record, "credentials", None) or [])
        wanted = str(arguments.get("credential") or "")
        if wanted not in granted:
            return tool_error(f"This job was not given a credential named `{wanted}`. It has: {', '.join(granted) or 'none'}.", "Not allowed")
        broker: Broker = runtime.tenant.broker
        try:
            result = await asyncio.to_thread(broker.request, wanted, str(arguments.get("method") or "GET"), str(arguments.get("url") or ""), str(arguments.get("body") or ""))
        except BrokerError as error:
            return tool_error(broker.redact(str(error)), "Refused")
        return {"is_error": False, "output": json.dumps(result), "message": "", "display": [{"type": "brief", "text": f"{result['status']} {arguments.get('url')}"}]}


def install(server) -> None:
    def extend(tenant) -> None:
        tenant.broker = Broker(Path(tenant.root))
        tenant.toolsets.append(CredentialToolset())

    server.extensions.append(extend)


from pydantic import BaseModel  # noqa: E402


class Put(BaseModel):
    value: str
    hosts: list[str]
    header: str = "Authorization"
    scheme: str = "Bearer"
    methods: list[str] = ["GET"]
    paths: list[str] = ["/"]


def router(app_state):
    from fastapi import APIRouter, Depends, HTTPException
    from .tenancy import Tenant

    api = APIRouter(prefix="/api")

    @api.get("/credentials")
    async def listing(tenant: Tenant = Depends(app_state.auth.tenant)) -> dict:
        return {"credentials": tenant.broker.names()}  # names and hosts only: values are write-only

    @api.put("/credentials/{name}")
    async def put(name: str, body: Put, tenant: Tenant = Depends(app_state.auth.tenant)) -> dict:
        try:
            tenant.broker.put(name, body.value, body.hosts, body.header, body.scheme, body.methods, body.paths)
        except BrokerError as error:
            raise HTTPException(400, str(error)) from error
        return {"name": name, "hosts": body.hosts}

    @api.delete("/credentials/{name}")
    async def delete(name: str, tenant: Tenant = Depends(app_state.auth.tenant)) -> dict:
        tenant.broker.delete(name)
        return {"deleted": name}

    return api
