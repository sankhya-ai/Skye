"""The host's LLM proxy: runtimes reach the model without ever holding the key.

A runtime process is configured with ``<host>/llm/api/v1`` as its base URL and
a per-launch token as its "API key". The proxy swaps the token for the
person's real OpenRouter key and streams the reply back. Everything the
runtime sends to OpenRouter — chat completions, Jev's ``/alpha/decisions``, the
paid search plugin — is a path under ``/api/``, so one route covers it.

Why: an agent can read any file its runtime can, and in a hosted install the
key is often the operator's. With the proxy, the most an agent can learn is a
token that works only for its own session, only through this host, and only
until the runtime stops.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from typing import Callable

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

PREFIX = "/llm/api"
_TOTAL_TOKENS = re.compile(rb'"total_tokens"\s*:\s*(\d+)')
_FORWARD_HEADERS = ("content-type", "accept", "http-referer", "x-title", "user-agent")


@dataclass
class Restriction:
    """What a grant held by a JAILED job may do. Everything else is refused before the real key is attached.

    Bounds the damage of a leaked or abused token; it is not a dollar cap (the proxy has no prices): the worst case is
    ``max_requests`` calls at the allowed models' prices, each with up to ``max_body`` bytes in and ``max_output_tokens`` out.
    """

    models: Callable[[], set[str]]
    max_requests: int = 300
    max_body: int = 4_000_000
    max_output_tokens: int = 16_384
    concurrent: int = 2
    inflight: int = 0


#: Request fields a restricted grant may not send: paid plugins (web search), client-chosen failover lists, routing transforms.
_FORBIDDEN_FIELDS = ("plugins", "models", "route", "transforms", "web_search_options")


def restrict_request(body: bytes, rule: Restriction) -> tuple[bytes | None, str]:
    """(body to send, "") or (None, why refused). Clamps the output length rather than refusing."""
    try:
        data = json.loads(body)
    except ValueError:
        return None, "body is not JSON"
    if not isinstance(data, dict):
        return None, "body is not a JSON object"
    model = data.get("model")
    if not isinstance(model, str) or model not in rule.models():
        return None, "model is not the one configured for this session"
    for field in _FORBIDDEN_FIELDS:
        if field in data:
            return None, f"field `{field}` is not allowed"
    for field in ("max_tokens", "max_completion_tokens"):
        value = data.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            data[field] = min(int(value), rule.max_output_tokens)
        elif value is not None:
            return None, f"field `{field}` must be a number"
    if "max_tokens" not in data and "max_completion_tokens" not in data:
        data["max_tokens"] = rule.max_output_tokens
    return json.dumps(data).encode(), ""


@dataclass
class Grant:
    tenant_id: str
    session_id: str
    #: Returns (upstream base URL such as https://openrouter.ai/api/v1, key).
    resolve: Callable[[], tuple[str, str]]
    requests: int = 0
    #: Models OpenRouter falls back to, in order, when the chosen one fails or is down.
    fallbacks: Callable[[], list[str]] | None = None
    restrict: Restriction | None = None


def with_fallbacks(body: bytes, fallbacks: list[str]) -> bytes:
    """A chat request with OpenRouter's ``models`` failover list, unless it already names one."""
    try:
        data = json.loads(body)
    except ValueError:
        return body
    if not isinstance(data, dict) or "models" in data or not data.get("model"):
        return body
    chain = [data["model"], *[m for m in fallbacks if m and m != data["model"]]]
    if len(chain) < 2:
        return body
    data["models"] = chain
    return json.dumps(data).encode()


def with_reasoning_echo(body: bytes) -> bytes:
    """A chat request in which every assistant tool call carries ``reasoning_content``.

    DeepSeek in thinking mode rejects the whole history when one lacks it ("The
    `reasoning_content` in the thinking mode must be passed back"), and a step
    that reasoned nothing stores none; an empty one is accepted.
    """
    try:
        data = json.loads(body)
    except ValueError:
        return body
    messages = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(messages, list):
        return body
    changed = False
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "assistant" and message.get("tool_calls") and not any(k in message for k in ("reasoning_content", "reasoning", "reasoning_details")):
            message["reasoning_content"] = ""
            changed = True
    return json.dumps(data).encode() if changed else body


class LLMProxy:
    def __init__(self) -> None:
        self._grants: dict[str, Grant] = {}
        self._client: httpx.AsyncClient | None = None
        #: Set by the server once it knows its own address; None disables the proxy.
        self.base: str | None = None
        #: Per session: provider-reported tokens used, and the cap (None = none). See ``budget``.
        self.tokens: dict[str, int] = {}
        self.caps: dict[str, int] = {}

    @property
    def enabled(self) -> bool:
        return self.base is not None

    def issue(self, tenant_id: str, session_id: str, resolve: Callable[[], tuple[str, str]], fallbacks: Callable[[], list[str]] | None = None, restrict: Restriction | None = None) -> tuple[str, str]:
        """A (base URL, token) pair for one runtime launch. A ``restrict``ed grant is for code in a jail (see Restriction)."""
        assert self.base is not None
        token = f"root-{secrets.token_urlsafe(24)}"
        self._grants[token] = Grant(tenant_id, session_id, resolve, fallbacks=fallbacks, restrict=restrict)
        return f"{self.base}{PREFIX}/v1", token

    def budget(self, session_id: str, max_tokens: int | None) -> None:
        """Cap the provider-reported tokens (prompt + completion) a session may use through this proxy.

        Counts what the provider says it used, so it is exact for providers that report usage and blind to ones
        that do not. Checked before each request, so a session can overshoot by one call. Tokens only: a dollar
        cap would need prices, which the proxy does not have.
        """
        self.tokens.setdefault(session_id, 0)
        if max_tokens:
            self.caps[session_id] = max_tokens
        else:
            self.caps.pop(session_id, None)

    def used(self, session_id: str) -> int:
        return self.tokens.get(session_id, 0)

    def exhausted(self, session_id: str) -> bool:
        cap = self.caps.get(session_id)
        return cap is not None and self.used(session_id) >= cap

    def revoke(self, token: str) -> None:
        self._grants.pop(token, None)

    def usage(self, tenant_id: str) -> int:
        return sum(g.requests for g in self._grants.values() if g.tenant_id == tenant_id)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(connect=20, read=600, write=60, pool=20))
        return self._client

    async def _counted(self, response: httpx.Response, session_id: str):
        """The body unchanged, adding the last ``total_tokens`` the provider reported to the session's tally."""
        tail, last = b"", None
        async for chunk in response.aiter_bytes():
            found = _TOTAL_TOKENS.findall(tail + chunk)
            if found:
                last = int(found[-1])
            tail = (tail + chunk)[-48:]
            yield chunk
        if last:
            self.tokens[session_id] = self.tokens.get(session_id, 0) + last

    async def forward(self, request: Request, path: str) -> Response:
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        grant = self._grants.get(token)
        if grant is None:
            return JSONResponse({"error": {"message": "Unknown or expired runtime token."}}, status_code=401)
        if self.exhausted(grant.session_id):
            return JSONResponse({"error": {"message": "This session's token budget is used up."}}, status_code=402)
        rule = grant.restrict
        if rule is not None:
            refusal = await self._refuse(request, path, grant, rule)
            if refusal is not None:
                return refusal
        base, key = grant.resolve()
        if not key:
            return JSONResponse({"error": {"message": "No OpenRouter API key is configured."}}, status_code=401)
        root = base.rstrip("/").rsplit("/v1", 1)[0]
        headers = {k: v for k, v in request.headers.items() if k.lower() in _FORWARD_HEADERS}
        headers["authorization"] = f"Bearer {key}"
        grant.requests += 1
        body = await request.body()
        if rule is not None:
            body, why = restrict_request(body, rule)
            if body is None:
                grant.requests -= 1
                return JSONResponse({"error": {"message": f"Refused for a jailed session: {why}."}}, status_code=403)
            rule.inflight += 1
        if request.method == "POST" and path.endswith("chat/completions"):
            if "openrouter.ai" not in root:
                body = with_reasoning_echo(body)
            elif grant.fallbacks is not None:
                body = with_fallbacks(body, grant.fallbacks())
        upstream = self._http().build_request(
            request.method,
            f"{root}/{path}",
            params=request.query_params,
            headers=headers,
            content=body,
        )
        try:
            response = await self._http().send(upstream, stream=True)
        except BaseException:
            if rule is not None:
                rule.inflight -= 1
            raise
        passthrough = {k: v for k, v in response.headers.items() if k.lower() in ("content-type", "cache-control")}
        return StreamingResponse(
            # Decoded: content-encoding is not forwarded, so neither may compressed bytes be.
            self._counted(response, grant.session_id),
            status_code=response.status_code,
            headers=passthrough,
            # Close the upstream response once the client has the whole body.
            background=BackgroundTask(self._finish, response, rule),
        )

    async def _finish(self, response: httpx.Response, rule: Restriction | None) -> None:
        await response.aclose()
        if rule is not None:
            rule.inflight -= 1

    async def _refuse(self, request: Request, path: str, grant: Grant, rule: Restriction) -> Response | None:
        """Why a jailed session's request is not forwarded, before the body is read or the key is resolved."""
        def no(message: str, code: int) -> Response:
            return JSONResponse({"error": {"message": f"Refused for a jailed session: {message}."}}, status_code=code)

        if request.method != "POST" or path != "v1/chat/completions" or request.url.query:
            return no("only POST chat completions is allowed (no provider key, account, credit, model-list or other routes)", 403)
        if grant.session_id not in self.caps:
            return no("it has no token cap, and the proxy cannot bound spend without one", 402)
        if grant.requests >= rule.max_requests:
            return no(f"its {rule.max_requests}-request limit is used up", 429)
        if rule.inflight >= rule.concurrent:
            return no("too many requests in flight", 429)
        length = request.headers.get("content-length", "")
        if not length.isdigit() or int(length) > rule.max_body:
            return no(f"the body must declare a Content-Length of at most {rule.max_body} bytes", 413)
        return None
