from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

# transport(method, url, headers, json_body) -> (status, response_headers, parsed_json_or_text)
Transport = Callable[[str, str, dict[str, str], Any], tuple[int, dict[str, str], Any]]


class SocialError(RuntimeError):
    """A platform refused or the owner's setup is incomplete. The message is safe to show."""

    def __init__(self, message: str, *, kind: str = "error"):
        super().__init__(message)
        self.kind = kind  # "auth" (reconnect), "limit" (retry later), "review" (app approval), "error"


class Form(dict):
    """Marks a request body as application/x-www-form-urlencoded (OAuth token endpoints)."""


def urllib_transport(method, url, headers, body):
    data = None
    headers = dict(headers)
    if isinstance(body, Form):
        import urllib.parse
        data = urllib.parse.urlencode(body).encode()
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif body is not None:
        data = json.dumps(body).encode()
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            raw = res.read().decode("utf-8", "replace")
            status, hdrs = res.status, dict(res.headers)
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        status, hdrs = error.code, dict(error.headers)
    try:
        parsed: Any = json.loads(raw) if raw else {}
    except ValueError:
        parsed = raw
    return status, hdrs, parsed


@dataclass(frozen=True)
class Connector:
    id: str
    name: str
    fields: tuple[str, ...]            # credential fields the owner supplies
    max_text: int
    media: tuple[str, ...]             # what this connector can post TODAY: "text", "image", "video"
    api: str                           # pinned base URL / version, one place to bump
    token_days: int | None             # token lifetime, None when it does not expire
    review: str                        # what the platform requires before posts go public
    implemented: bool
    publish: Callable[..., dict[str, Any]] | None = None
    check: Callable[..., dict[str, Any]] | None = None
    notes: str = ""

    def validate(self, post: dict[str, Any]) -> None:
        text = str(post.get("text") or "").strip()
        if not text:
            raise SocialError("a post needs text")
        if len(text) > self.max_text:
            raise SocialError(f"{self.name} allows {self.max_text} characters; this post has {len(text)}")
        if post.get("image") and "image" not in self.media:
            raise SocialError(f"{self.name} cannot post images from Skye yet")
        if post.get("video") and "video" not in self.media:
            raise SocialError(f"{self.name} cannot post video from Skye yet")


def classify(status: int, body: Any, platform: str) -> SocialError:
    detail = ""
    if isinstance(body, dict):
        detail = str(body.get("message") or body.get("error_description") or body.get("detail")
                     or body.get("error") or body.get("title") or "")[:200]
    elif isinstance(body, str):
        detail = body[:200]
    if status in (401, 403):
        return SocialError(f"{platform} rejected the login ({status}); reconnect the account. {detail}".strip(), kind="auth")
    if status == 429:
        return SocialError(f"{platform} rate limit reached; try again later. {detail}".strip(), kind="limit")
    return SocialError(f"{platform} returned {status}. {detail}".strip())
