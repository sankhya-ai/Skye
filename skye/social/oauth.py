"""OAuth sign-in and token refresh for X, LinkedIn and Facebook Pages.

The owner registers their own developer app per platform (no shared keys) and saves its client
id/secret once. `begin` returns the platform's sign-in URL; the browser comes back to the local
callback with a single-use `state`; `finish` trades the code for tokens and stores them (0600).
Endpoints and parameters follow each platform's docs and the MIT 37Studio reference
(github.com/Shaitrish/37studio, internal/social); verify against the docs when bumping versions.
All HTTP goes through the injected transport."""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
import urllib.parse
from typing import Any

from .base import Form, SocialError, classify, urllib_transport
from .connectors import GRAPH_VERSION
from .store import Store

X_AUTH = "https://x.com/i/oauth2/authorize"
X_TOKEN = "https://api.x.com/2/oauth2/token"
X_SCOPES = "tweet.read tweet.write users.read offline.access"
LI_AUTH = "https://www.linkedin.com/oauth/v2/authorization"
LI_TOKEN = "https://www.linkedin.com/oauth/v2/accessToken"
LI_SCOPES = "openid profile w_member_social"
META_SCOPES = "pages_show_list,pages_read_engagement,pages_manage_posts,business_management"
GRAPH = "https://graph.facebook.com/" + GRAPH_VERSION
STATE_TTL = 600
REFRESH_MARGIN = 300

_pending: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _app(store: Store, platform: str) -> dict[str, str]:
    app = store.app(platform)
    if not app:
        raise SocialError(f"save your {platform} developer app (client id and secret) first")
    return app


def _basic(app) -> dict[str, str]:
    raw = f"{app['client_id']}:{app['client_secret']}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}


def begin(platform: str, redirect_uri: str, *, store: Store | None = None) -> dict[str, str]:
    store = store or Store()
    app = _app(store, platform)
    state = secrets.token_urlsafe(24)
    entry: dict[str, Any] = {"platform": platform, "redirect": redirect_uri, "at": time.time()}
    if platform == "x":
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        entry["verifier"] = verifier
        query = {"response_type": "code", "client_id": app["client_id"], "redirect_uri": redirect_uri,
                 "scope": X_SCOPES, "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
        url = X_AUTH + "?" + urllib.parse.urlencode(query)
    elif platform == "linkedin":
        query = {"response_type": "code", "client_id": app["client_id"], "redirect_uri": redirect_uri,
                 "state": state, "scope": LI_SCOPES}
        url = LI_AUTH + "?" + urllib.parse.urlencode(query)
    elif platform == "facebook":
        query = {"client_id": app["client_id"], "redirect_uri": redirect_uri, "state": state,
                 "scope": META_SCOPES, "response_type": "code"}
        url = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth?" + urllib.parse.urlencode(query)
    else:
        raise SocialError(f"{platform} has no sign-in flow")
    with _lock:
        for k in [k for k, v in _pending.items() if time.time() - v["at"] > STATE_TTL]:
            _pending.pop(k)
        _pending[state] = entry
    return {"url": url, "state": state}


def finish(state: str, code: str, *, store: Store | None = None, http=urllib_transport) -> str:
    """Complete a sign-in. Returns the platform id. State is single-use."""
    store = store or Store()
    with _lock:
        entry = _pending.pop(state, None)
    if not entry or time.time() - entry["at"] > STATE_TTL:
        raise SocialError("this sign-in link expired; start again", kind="auth")
    platform, redirect = entry["platform"], entry["redirect"]
    app = _app(store, platform)
    if platform == "x":
        status, _, tok = http("POST", X_TOKEN, _basic(app), Form(
            grant_type="authorization_code", code=code, redirect_uri=redirect,
            code_verifier=entry["verifier"], client_id=app["client_id"]))
        _need(status, tok, "X")
        store.save_oauth("x", {"access_token": tok["access_token"]}, expires_in=tok.get("expires_in"),
                         refresh_token=tok.get("refresh_token", ""))
    elif platform == "linkedin":
        status, _, tok = http("POST", LI_TOKEN, {}, Form(
            grant_type="authorization_code", code=code, redirect_uri=redirect,
            client_id=app["client_id"], client_secret=app["client_secret"]))
        _need(status, tok, "LinkedIn")
        status, _, me = http("GET", "https://api.linkedin.com/v2/userinfo",
                             {"Authorization": "Bearer " + tok["access_token"]}, None)
        _need(status, me, "LinkedIn")
        store.save_oauth("linkedin", {"access_token": tok["access_token"], "author": "urn:li:person:" + me["sub"]},
                         expires_in=tok.get("expires_in"), refresh_token=tok.get("refresh_token", ""))
    else:
        q = urllib.parse.urlencode({"client_id": app["client_id"], "client_secret": app["client_secret"],
                                    "redirect_uri": redirect, "code": code})
        status, _, tok = http("GET", GRAPH + "/oauth/access_token?" + q, {}, None)
        _need(status, tok, "Facebook")
        q = urllib.parse.urlencode({"grant_type": "fb_exchange_token", "client_id": app["client_id"],
                                    "client_secret": app["client_secret"], "fb_exchange_token": tok["access_token"]})
        status, _, long_tok = http("GET", GRAPH + "/oauth/access_token?" + q, {}, None)
        _need(status, long_tok, "Facebook")
        q = urllib.parse.urlencode({"fields": "id,name,access_token", "limit": "100"})
        status, _, pages = http("GET", GRAPH + "/me/accounts?" + q,
                                {"Authorization": "Bearer " + long_tok["access_token"]}, None)
        _need(status, pages, "Facebook")
        rows = [{"id": p["id"], "name": p["name"], "token": p["access_token"]} for p in pages.get("data", [])]
        if not rows:
            raise SocialError("no Facebook Pages came back; the login needs a Page you manage")
        # Page tokens fetched with a long-lived user token do not expire (37Studio notes the same).
        store.save_oauth("facebook", {"page_id": rows[0]["id"], "page_token": rows[0]["token"]},
                         extra={"pages": rows})
    return platform


def select_page(page_id: str, *, store: Store | None = None) -> None:
    store = store or Store()
    rec = store.raw("facebook")
    for page in rec.get("pages", []):
        if page["id"] == page_id:
            store.save_oauth("facebook", {"page_id": page["id"], "page_token": page["token"]},
                             extra={"pages": rec["pages"]})
            return
    raise SocialError("unknown page")


def _need(status: int, body: Any, platform: str) -> None:
    if status != 200 or not isinstance(body, dict):
        raise classify(status, body, platform)
    if "access_token" not in body and "sub" not in body and "data" not in body:
        raise SocialError(f"{platform} sign-in returned no token")


def ensure_fresh(platform: str, *, store: Store | None = None, http=urllib_transport) -> None:
    """Refresh a token that is expired or about to be. A platform without a refresh token
    (LinkedIn for most apps) raises an auth error telling the owner to sign in again."""
    store = store or Store()
    rec = store.raw(platform)
    exp = rec.get("expires_at")
    if not rec or not exp or exp - time.time() > REFRESH_MARGIN:
        return
    refresh = rec.get("refresh_token") or ""
    if platform not in ("x", "linkedin") or not refresh:
        if exp < time.time():
            raise SocialError(f"the {platform} login expired; sign in again", kind="auth")
        return
    app = _app(store, platform)
    if platform == "x":
        status, _, tok = http("POST", X_TOKEN, _basic(app), Form(
            grant_type="refresh_token", refresh_token=refresh, client_id=app["client_id"]))
        label = "X"
    else:
        status, _, tok = http("POST", LI_TOKEN, {}, Form(
            grant_type="refresh_token", refresh_token=refresh,
            client_id=app["client_id"], client_secret=app["client_secret"]))
        label = "LinkedIn"
    if status != 200 or "access_token" not in (tok if isinstance(tok, dict) else {}):
        raise SocialError(f"{label} could not renew the login; sign in again", kind="auth")
    # X rotates refresh tokens: keep the new one, or the old one if none came back.
    store.update_tokens(platform, tok["access_token"], expires_in=tok.get("expires_in"),
                        refresh_token=tok.get("refresh_token", ""))
