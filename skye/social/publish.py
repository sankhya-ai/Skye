"""The only way a post leaves Skye: an APPROVED backlog item, an owner action, one platform.

Workers draft; they never hold credentials. A publish is refused unless the backlog item is
approved, and each success is recorded on the item so it cannot go out twice by accident."""

from __future__ import annotations

from typing import Any

from .. import backlog
from .base import SocialError, urllib_transport
from .registry import get
from .oauth import ensure_fresh
from .store import Store


def publish(feature_id: str, platform: str, *, text: str, store: Store | None = None,
            http=urllib_transport, backlog_path=None, force: bool = False) -> dict[str, Any]:
    store = store or Store()
    connector = get(platform)
    if not connector.implemented:
        raise SocialError(f"{connector.name} is not available yet")
    feature = backlog.get(feature_id, backlog_path)
    if feature.get("status") != "approved":
        raise SocialError("only an approved item can be published")
    prior = [t for t in feature.get("thread", [])
             if t.get("kind") == "status" and t.get("body", "").startswith(f"published:{platform}:")]
    if prior and not force:
        raise SocialError(f"already published to {connector.name}; pass force to post again")
    # The brief is an instruction to the team, not a post. The owner supplies the final words.
    post = {"text": text}
    connector.validate(post)
    try:
        ensure_fresh(platform, store=store, http=http)
        creds = store.creds(platform)
        result = connector.publish(creds, post, http)
    except SocialError as error:
        store.note_check(platform, False, str(error))
        raise
    store.note_check(platform, True, "published")
    backlog.add_thread(feature_id, actor="skye", kind="status",
                       body=f"published:{platform}:{result.get('url') or result.get('id')}",
                       path=backlog_path)
    return result


def check(platform: str, *, store: Store | None = None, http=urllib_transport) -> dict[str, Any]:
    """Health probe: logs in / reads the account without posting anything."""
    store = store or Store()
    connector = get(platform)
    if not connector.implemented or not connector.check:
        raise SocialError(f"{connector.name} is not available yet")
    try:
        ensure_fresh(platform, store=store, http=http)
        out = connector.check(store.creds(platform), http)
    except SocialError as error:
        store.note_check(platform, False, str(error))
        raise
    store.note_check(platform, True, out.get("account", ""))
    return out
