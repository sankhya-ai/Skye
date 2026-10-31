"""Gmail: Google's installed-app OAuth (PKCE, loopback redirect) and a small Gmail client.

root asks for two scopes, as Chotu did: read mail, and compose drafts. It
never sends: a draft waits in the owner's Gmail for them to send.

The OAuth client is the app's (``TURA_CODE_GOOGLE_CLIENT_ID`` and, for a
Google "Desktop app" client, its non-confidential ``…_CLIENT_SECRET``). The
consent page sends the browser back to root's own
``/api/connections/google/callback``, authorized by a single-use ``state``.
"""

from __future__ import annotations

import base64
import hashlib
import html
import os
import re
import secrets
import time
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlencode

import httpx

from .store import ConnectionStore

NAME = "gmail"
SCOPES = ("https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.compose")
CLIENT_ID_ENV = "TURA_CODE_GOOGLE_CLIENT_ID"
CLIENT_SECRET_ENV = "TURA_CODE_GOOGLE_CLIENT_SECRET"
STATE_TTL = 600
TIMEOUT = 20.0


class GoogleError(RuntimeError):
    pass


def _env(name: str, default: str) -> str:
    return os.environ.get(name, "").strip().rstrip("/") or default


def auth_url_base() -> str:
    return _env("TURA_CODE_GOOGLE_AUTH", "https://accounts.google.com/o/oauth2/v2/auth")


def token_url() -> str:
    return _env("TURA_CODE_GOOGLE_TOKEN", "https://oauth2.googleapis.com/token")


def gmail_api() -> str:
    return _env("TURA_CODE_GMAIL_API", "https://gmail.googleapis.com")


def configured() -> bool:
    return bool(os.environ.get(CLIENT_ID_ENV, "").strip())


class OAuthFlows:
    """Consent requests in flight: ``state`` → who asked, the PKCE verifier, the redirect."""

    def __init__(self) -> None:
        self._pending: dict[str, dict[str, Any]] = {}

    def start(self, tenant_id: str, redirect_uri: str) -> str:
        if not configured():
            raise GoogleError(f"Gmail needs a Google OAuth client: set {CLIENT_ID_ENV} (and {CLIENT_SECRET_ENV} for a Desktop client).")
        now = time.time()
        self._pending = {k: v for k, v in self._pending.items() if now - v["at"] < STATE_TTL}
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self._pending[state] = {"tenant": tenant_id, "verifier": verifier, "redirect_uri": redirect_uri, "at": now}
        query = {
            "client_id": os.environ[CLIENT_ID_ENV].strip(),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{auth_url_base()}?{urlencode(query)}"

    def take(self, state: str) -> dict[str, Any] | None:
        entry = self._pending.pop(state, None)
        if entry is None or time.time() - entry["at"] > STATE_TTL:
            return None
        return entry


async def _token_request(data: dict[str, str]) -> dict[str, Any]:
    data = {**data, "client_id": os.environ.get(CLIENT_ID_ENV, "").strip()}
    secret = os.environ.get(CLIENT_SECRET_ENV, "").strip()
    if secret:
        data["client_secret"] = secret
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            response = await client.post(token_url(), data=data)
        except httpx.HTTPError as error:
            raise GoogleError(f"Could not reach Google: {error}") from error
    body = response.json() if response.content else {}
    if response.status_code >= 400 or "access_token" not in body:
        raise GoogleError(f"Google refused: {body.get('error_description') or body.get('error') or response.text[:200]}")
    return body


async def finish(store: ConnectionStore, flow: dict[str, Any], code: str) -> dict[str, Any]:
    """Exchange the consent code; keep the refresh token. Returns the public record."""
    token = await _token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": flow["redirect_uri"], "code_verifier": flow["verifier"]})
    if not token.get("refresh_token"):
        raise GoogleError("Google returned no refresh token; disconnect root in your Google account settings and try again.")
    secret = {"refresh_token": token["refresh_token"], "access_token": token["access_token"], "expires_at": time.time() + float(token.get("expires_in") or 3600)}
    profile = await Gmail(store, secret).request("GET", "/gmail/v1/users/me/profile")
    store.put(NAME, secret=secret, email=profile.get("emailAddress", ""), scopes=str(token.get("scope") or " ".join(SCOPES)).split())
    return store.public(NAME) or {}


class Gmail:
    def __init__(self, store: ConnectionStore, secret: dict[str, Any] | None = None) -> None:
        self.store = store
        self.secret = secret if secret is not None else (store.get(NAME) or {}).get("secret") or {}

    async def _access_token(self) -> str:
        if not self.secret.get("refresh_token"):
            raise GoogleError("Gmail is not connected: the owner can connect it in Settings → Connections.")
        if self.secret.get("access_token") and float(self.secret.get("expires_at") or 0) > time.time() + 60:
            return self.secret["access_token"]
        token = await _token_request({"grant_type": "refresh_token", "refresh_token": self.secret["refresh_token"]})
        self.secret = {**self.secret, "access_token": token["access_token"], "expires_at": time.time() + float(token.get("expires_in") or 3600)}
        self.store.update_secret(NAME, self.secret)
        return self.secret["access_token"]

    async def request(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None) -> Any:
        headers = {"Authorization": f"Bearer {await self._access_token()}"}
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            try:
                response = await client.request(method, f"{gmail_api()}{path}", params=params, json=body, headers=headers)
            except httpx.HTTPError as error:
                raise GoogleError(f"Could not reach Gmail: {error}") from error
        if response.status_code == 401:
            raise GoogleError("Gmail rejected root's access: reconnect Gmail in Settings → Connections.")
        if response.status_code >= 400:
            raise GoogleError(f"Gmail said {response.status_code}: {response.text[:300]}")
        return response.json() if response.content else {}


# ----- messages ----------------------------------------------------------------------


def headers_of(message: dict) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in (message.get("payload") or {}).get("headers") or []}


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def body_of(part: dict) -> tuple[str, list[str]]:
    """The message's text (plain preferred over HTML) and its attachment names."""
    plain, rich, attachments = [], [], []

    def walk(node: dict) -> None:
        mime = node.get("mimeType", "")
        if node.get("filename"):
            attachments.append(node["filename"])
        data = (node.get("body") or {}).get("data")
        if data and mime == "text/plain":
            plain.append(_decode(data))
        elif data and mime == "text/html":
            rich.append(_decode(data))
        for child in node.get("parts") or []:
            walk(child)

    walk(part)
    if plain:
        return "\n".join(plain).strip(), attachments
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", "\n".join(rich), flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<br\s*/?>|</p>", "\n", text, flags=re.I)))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip(), attachments


def draft_raw(*, to: str, subject: str, body: str, cc: str = "", reply_headers: dict[str, str] | None = None) -> str:
    message = EmailMessage()
    message["To"] = to
    if cc:
        message["Cc"] = cc
    message["Subject"] = subject
    for name, value in (reply_headers or {}).items():
        message[name] = value
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode()
