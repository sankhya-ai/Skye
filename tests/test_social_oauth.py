import time
import urllib.parse

import pytest

from skye.social import SocialError, get
from skye.social import oauth
from skye.social import publish as pub
from skye.social.base import Form
from skye.social.store import Store
from test_social import Fake


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "s.json")
    for p in ("x", "linkedin", "facebook"):
        s.save_app(p, "cid", "csecret")
    return s


def query(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))


def test_begin_requires_app_and_builds_x_pkce_url(tmp_path, store):
    with pytest.raises(SocialError, match="developer app"):
        oauth.begin("x", "http://127.0.0.1:8787/oauth/callback", store=Store(tmp_path / "empty.json"))
    out = oauth.begin("x", "http://127.0.0.1:8787/oauth/callback", store=store)
    q = query(out["url"])
    assert out["url"].startswith("https://x.com/i/oauth2/authorize?")
    assert q["code_challenge_method"] == "S256" and q["state"] == out["state"]
    assert "offline.access" in q["scope"] and "tweet.write" in q["scope"]


def test_x_finish_then_refresh_rotates_tokens(store):
    out = oauth.begin("x", "http://127.0.0.1:8787/oauth/callback", store=store)
    http = Fake((200, {}, {"access_token": "A1", "refresh_token": "R1", "expires_in": 7200}))
    assert oauth.finish(out["state"], "CODE", store=store, http=http) == "x"
    method, url, headers, body = http.calls[0]
    assert url == "https://api.x.com/2/oauth2/token" and headers["Authorization"].startswith("Basic ")
    assert isinstance(body, Form) and body["code_verifier"] and body["grant_type"] == "authorization_code"
    assert store.creds("x")["access_token"] == "A1"
    # state is single use
    with pytest.raises(SocialError, match="expired"):
        oauth.finish(out["state"], "CODE", store=store, http=Fake())
    # expire it, publish refreshes first
    data = store._read()
    data["x"]["expires_at"] = time.time() + 10
    store._write(data)
    http = Fake((200, {}, {"access_token": "A2", "refresh_token": "R2", "expires_in": 7200}),
                (200, {}, {"data": {"username": "ash"}}))
    out = pub.check("x", store=store, http=http)
    assert out["account"] == "ash"
    assert http.calls[0][3]["grant_type"] == "refresh_token" and http.calls[0][3]["refresh_token"] == "R1"
    assert http.calls[1][2]["Authorization"] == "Bearer A2"
    assert store.raw("x")["refresh_token"] == "R2"


def test_linkedin_sets_author_and_expired_without_refresh_asks_to_reconnect(store):
    out = oauth.begin("linkedin", "http://127.0.0.1:8787/oauth/callback", store=store)
    http = Fake((200, {}, {"access_token": "L1", "expires_in": 5184000}), (200, {}, {"sub": "abc", "name": "Ash"}))
    oauth.finish(out["state"], "CODE", store=store, http=http)
    assert store.creds("linkedin")["author"] == "urn:li:person:abc"
    data = store._read()
    data["linkedin"]["expires_at"] = time.time() - 5
    store._write(data)
    with pytest.raises(SocialError) as e:
        pub.check("linkedin", store=store, http=Fake())
    assert e.value.kind == "auth"


def test_facebook_long_lived_exchange_and_page_selection(store):
    out = oauth.begin("facebook", "http://127.0.0.1:8787/oauth/callback", store=store)
    http = Fake((200, {}, {"access_token": "short"}), (200, {}, {"access_token": "long"}),
                (200, {}, {"data": [{"id": "1", "name": "Shop", "access_token": "pt1"},
                                    {"id": "2", "name": "Cafe", "access_token": "pt2"}]}))
    oauth.finish(out["state"], "CODE", store=store, http=http)
    assert "fb_exchange_token=short" in http.calls[1][1]
    assert http.calls[2][2]["Authorization"] == "Bearer long"
    assert store.creds("facebook") == {"page_id": "1", "page_token": "pt1"}
    oauth.select_page("2", store=store)
    assert store.creds("facebook")["page_token"] == "pt2"
    from skye.social import status_rows
    row = {r["id"]: r for r in status_rows(store)}["facebook"]
    assert [p["name"] for p in row["pages"]] == ["Shop", "Cafe"]
    assert "pt1" not in str(row) and "pt2" not in str(row)
