import json
import os
import stat

import pytest

from skye import backlog
from skye.social import SocialError, get, status_rows
from skye.social import publish as pub
from skye.social.store import Store


class Fake:
    """Replays canned responses; records requests so a test can pin the wire format."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        return self.responses.pop(0)


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "social.json")
    bpath = tmp_path / "backlog.json"
    [f] = backlog.propose([{"title": "Day 1", "brief": "make a post"}], project="marketing", path=bpath)
    return store, bpath, f["id"]


def approve(bpath, fid):
    backlog.decide(fid, "approved", path=bpath)


def test_store_is_private_and_status_hides_values(tmp_path):
    store = Store(tmp_path / "s.json")
    store.save(get("mastodon"), {"server": "https://m.example", "access_token": "SECRET"})
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    rows = {r["id"]: r for r in status_rows(store)}
    assert rows["mastodon"]["connected"] and "SECRET" not in json.dumps(rows)


def test_unimplemented_platform_cannot_connect(tmp_path):
    with pytest.raises(SocialError):
        Store(tmp_path / "s.json").save(get("tiktok"), {})


def test_publish_refuses_unapproved(env):
    store, bpath, fid = env
    store.save(get("mastodon"), {"server": "https://m.example", "access_token": "t"})
    with pytest.raises(SocialError, match="approved"):
        pub.publish(fid, "mastodon", text="hi", store=store, http=Fake(), backlog_path=bpath)


def test_mastodon_wire_format_and_no_double_post(env):
    store, bpath, fid = env
    approve(bpath, fid)
    store.save(get("mastodon"), {"server": "https://m.example", "access_token": "t"})
    http = Fake((200, {}, {"id": "1", "url": "https://m.example/@a/1"}))
    out = pub.publish(fid, "mastodon", text="hello", store=store, http=http, backlog_path=bpath)
    assert out["url"].endswith("/1")
    method, url, headers, body = http.calls[0]
    assert (method, url, body) == ("POST", "https://m.example/api/v1/statuses", {"status": "hello"})
    assert headers["Authorization"] == "Bearer t"
    with pytest.raises(SocialError, match="already published"):
        pub.publish(fid, "mastodon", text="hello", store=store, http=Fake(), backlog_path=bpath)


def test_bluesky_two_step_flow(env):
    store, bpath, fid = env
    approve(bpath, fid)
    store.save(get("bluesky"), {"handle": "a.bsky.social", "app_password": "p", "service": ""})
    http = Fake((200, {}, {"accessJwt": "J", "did": "did:plc:x"}),
                (200, {}, {"uri": "at://did:plc:x/app.bsky.feed.post/abc"}))
    out = pub.publish(fid, "bluesky", text="hi", store=store, http=http, backlog_path=bpath)
    assert out["url"] == "https://bsky.app/profile/did:plc:x/post/abc"
    assert http.calls[1][3]["collection"] == "app.bsky.feed.post"
    assert http.calls[1][2]["Authorization"] == "Bearer J"


def test_linkedin_headers_and_id_from_header(env):
    store, bpath, fid = env
    approve(bpath, fid)
    store.save(get("linkedin"), {"access_token": "t", "author": "urn:li:person:1"})
    http = Fake((201, {"x-restli-id": "urn:li:share:9"}, {}))
    out = pub.publish(fid, "linkedin", text="hi", store=store, http=http, backlog_path=bpath)
    assert out["id"] == "urn:li:share:9"
    headers, body = http.calls[0][2], http.calls[0][3]
    assert headers["X-Restli-Protocol-Version"] == "2.0.0" and headers["LinkedIn-Version"]
    assert body["lifecycleState"] == "PUBLISHED" and body["author"] == "urn:li:person:1"


def test_x_limit_and_auth_error_kinds(env):
    store, bpath, fid = env
    approve(bpath, fid)
    store.save(get("x"), {"access_token": "t"})
    with pytest.raises(SocialError, match="280"):
        pub.publish(fid, "x", text="a" * 281, store=store, http=Fake(), backlog_path=bpath)
    with pytest.raises(SocialError) as e:
        pub.publish(fid, "x", text="ok", store=store, http=Fake((401, {}, {"title": "Unauthorized"})), backlog_path=bpath)
    assert e.value.kind == "auth"
    assert store.get("x")["last_check"]["ok"] is False


def test_check_probe_records_health(tmp_path):
    store = Store(tmp_path / "s.json")
    store.save(get("mastodon"), {"server": "https://m.example", "access_token": "t"})
    out = pub.check("mastodon", store=store, http=Fake((200, {}, {"acct": "ash"})))
    assert out["account"] == "ash" and store.get("mastodon")["last_check"]["ok"]
