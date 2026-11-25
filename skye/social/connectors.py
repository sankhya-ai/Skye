"""Platform implementations. API shapes follow each platform's own docs (checked 2026-11-25):
Bluesky atproto XRPC, Mastodon REST v1, X API v2, LinkedIn Posts API (/rest/posts), Facebook
Graph Page feed. Text posts only for now; media is declared per connector, not implied."""

from __future__ import annotations

import datetime as dt
from typing import Any

from .base import Connector, SocialError, classify

LINKEDIN_VERSION = "202605"  # YYYYMM; LinkedIn sunsets old versions, bump here only
GRAPH_VERSION = "v26.0"      # matches the MIT-licensed 37Studio reference; verify at release time


def _ok(status, body, platform, good=(200, 201)):
    if status not in good:
        raise classify(status, body, platform)


def bluesky_publish(creds, post, http):
    service = (creds.get("service") or "https://bsky.social").rstrip("/")
    status, _, sess = http("POST", service + "/xrpc/com.atproto.server.createSession", {},
                           {"identifier": creds["handle"], "password": creds["app_password"]})
    _ok(status, sess, "Bluesky")
    record = {"$type": "app.bsky.feed.post", "text": post["text"],
              "createdAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")}
    status, _, out = http("POST", service + "/xrpc/com.atproto.repo.createRecord",
                          {"Authorization": "Bearer " + sess["accessJwt"]},
                          {"repo": sess["did"], "collection": "app.bsky.feed.post", "record": record})
    _ok(status, out, "Bluesky")
    rkey = out["uri"].rsplit("/", 1)[-1]
    return {"id": out["uri"], "url": f"https://bsky.app/profile/{sess['did']}/post/{rkey}"}


def bluesky_check(creds, http):
    service = (creds.get("service") or "https://bsky.social").rstrip("/")
    status, _, sess = http("POST", service + "/xrpc/com.atproto.server.createSession", {},
                           {"identifier": creds["handle"], "password": creds["app_password"]})
    _ok(status, sess, "Bluesky")
    return {"account": sess.get("handle", creds["handle"])}


def mastodon_publish(creds, post, http):
    server = creds["server"].rstrip("/")
    status, _, out = http("POST", server + "/api/v1/statuses",
                          {"Authorization": "Bearer " + creds["access_token"]}, {"status": post["text"]})
    _ok(status, out, "Mastodon")
    return {"id": str(out.get("id")), "url": out.get("url", "")}


def mastodon_check(creds, http):
    server = creds["server"].rstrip("/")
    status, _, out = http("GET", server + "/api/v1/accounts/verify_credentials",
                          {"Authorization": "Bearer " + creds["access_token"]}, None)
    _ok(status, out, "Mastodon")
    return {"account": out.get("acct", "")}


def x_publish(creds, post, http):
    status, _, out = http("POST", "https://api.x.com/2/tweets",
                          {"Authorization": "Bearer " + creds["access_token"]}, {"text": post["text"]})
    _ok(status, out, "X")
    tid = (out.get("data") or {}).get("id", "")
    return {"id": tid, "url": f"https://x.com/i/status/{tid}" if tid else ""}


def x_check(creds, http):
    status, _, out = http("GET", "https://api.x.com/2/users/me",
                          {"Authorization": "Bearer " + creds["access_token"]}, None)
    _ok(status, out, "X")
    return {"account": (out.get("data") or {}).get("username", "")}


def _li_headers(creds):
    return {"Authorization": "Bearer " + creds["access_token"],
            "X-Restli-Protocol-Version": "2.0.0", "LinkedIn-Version": LINKEDIN_VERSION}


def linkedin_publish(creds, post, http):
    body = {"author": creds["author"], "commentary": post["text"], "visibility": "PUBLIC",
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                             "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False}
    status, hdrs, out = http("POST", "https://api.linkedin.com/rest/posts", _li_headers(creds), body)
    _ok(status, out, "LinkedIn", good=(201,))
    urn = {k.lower(): v for k, v in hdrs.items()}.get("x-restli-id", "")
    return {"id": urn, "url": f"https://www.linkedin.com/feed/update/{urn}" if urn else ""}


def linkedin_check(creds, http):
    status, _, out = http("GET", "https://api.linkedin.com/v2/userinfo",
                          {"Authorization": "Bearer " + creds["access_token"]}, None)
    _ok(status, out, "LinkedIn")
    return {"account": out.get("name", "")}


def facebook_publish(creds, post, http):
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{creds['page_id']}/feed"
    status, _, out = http("POST", url, {}, {"message": post["text"], "access_token": creds["page_token"]})
    _ok(status, out, "Facebook")
    pid = out.get("id", "")
    return {"id": pid, "url": f"https://www.facebook.com/{pid}" if pid else ""}


def facebook_check(creds, http):
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{creds['page_id']}?fields=name&access_token={creds['page_token']}"
    status, _, out = http("GET", url, {}, None)
    _ok(status, out, "Facebook")
    return {"account": out.get("name", "")}


def _planned(id, name, review, notes, days=None):
    return Connector(id=id, name=name, fields=(), max_text=0, media=(), api="", token_days=days,
                     review=review, implemented=False, notes=notes)


CONNECTORS = (
    Connector("bluesky", "Bluesky", ("handle", "app_password", "service"), 300, ("text",),
              "https://bsky.social/xrpc", None, "None. App password from Bluesky settings.", True,
              bluesky_publish, bluesky_check),
    Connector("mastodon", "Mastodon", ("server", "access_token"), 500, ("text",),
              "per-server /api/v1", None, "None. Token from your server's Development page.", True,
              mastodon_publish, mastodon_check),
    Connector("x", "X", ("access_token",), 280, ("text",), "https://api.x.com/2", 1,
              "Your X developer plan must allow creating posts. Access tokens are short-lived; "
              "refresh flow is not built yet.", True, x_publish, x_check,
              "Paste a user-context token for now; OAuth sign-in and refresh are next."),
    Connector("linkedin", "LinkedIn", ("access_token", "author"), 3000, ("text",),
              f"https://api.linkedin.com/rest (LinkedIn-Version {LINKEDIN_VERSION})", 60,
              "w_member_social for profiles; company pages need LinkedIn's Community Management "
              "API approval per app. Tokens last about 60 days.", True, linkedin_publish, linkedin_check),
    Connector("facebook", "Facebook Page", ("page_id", "page_token"), 63206, ("text",),
              f"https://graph.facebook.com/{GRAPH_VERSION}", 60,
              "Meta app in Development mode works for people with a role on the app; public use "
              "needs App Review.", True, facebook_publish, facebook_check),
    _planned("instagram", "Instagram",
             "Business or Creator account linked to a Facebook Page; instagram_content_publish. "
             "API needs a public media URL, so no posting from local files without hosting.",
             "Needs media upload; planned after video posting."),
    _planned("youtube", "YouTube",
             "YouTube Data API v3 keeps uploads private until the project passes YouTube's API audit; "
             "default quota allows few uploads per day.", "Needs resumable video upload; planned."),
    _planned("tiktok", "TikTok",
             "Unaudited apps can only post private (SELF_ONLY), capped at 5 users per day. TikTok's "
             "guidelines say tools limited to your own or your team's accounts are not acceptable "
             "for audit.", "Planned as 'send to TikTok app' inbox upload first."),
    _planned("pinterest", "Pinterest", "Pinterest app review for write access.", "Needs image/video pins; planned."),
)
