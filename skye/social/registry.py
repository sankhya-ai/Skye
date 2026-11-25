from __future__ import annotations

from .connectors import CONNECTORS
from .store import Store

PLATFORMS = {c.id: c for c in CONNECTORS}
OAUTH_PLATFORMS = ("x", "linkedin", "facebook")


def get(platform: str):
    from .base import SocialError
    try:
        return PLATFORMS[platform]
    except KeyError:
        raise SocialError(f"unknown platform {platform!r}") from None


def status_rows(store: Store | None = None) -> list[dict]:
    """Safe-to-show rows: never includes a credential value."""
    store = store or Store()
    rows = []
    for c in CONNECTORS:
        rec = store.get(c.id)
        rows.append({"id": c.id, "name": c.name, "implemented": c.implemented,
                     "connected": bool(rec), "oauth": c.id in OAUTH_PLATFORMS,
                     "refreshable": bool((rec or {}).get("refresh_token")),
                     "app_configured": bool(store.app(c.id)),
                     "pages": [{"id": p["id"], "name": p["name"]} for p in (rec or {}).get("pages", [])],
                     "page_id": ((rec or {}).get("credentials") or {}).get("page_id"), "fields": list(c.fields), "media": list(c.media),
                     "max_text": c.max_text, "review": c.review, "notes": c.notes,
                     "saved_at": (rec or {}).get("saved_at"),
                     "expires_at": (rec or {}).get("expires_at"),
                     "last_check": (rec or {}).get("last_check")})
    return rows
