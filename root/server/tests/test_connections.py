"""Connections: GitHub and Gmail through the real runtime against fake services; the Keychain vault."""

from __future__ import annotations

import base64
import email
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest
from conftest import of_type, requires_runtime, run_turn, text, tool_call
from fake_services import Fakes, Running

from tura_code.connections import github as gh


@pytest.fixture
def fakes(monkeypatch):
    found = Fakes()
    with Running(found) as running:
        for name in ("TURA_CODE_GITHUB_API", "TURA_CODE_GITHUB_WEB"):
            monkeypatch.setenv(name, running.url)
        monkeypatch.setenv("TURA_CODE_GOOGLE_TOKEN", f"{running.url}/token")
        monkeypatch.setenv("TURA_CODE_GMAIL_API", running.url)
        monkeypatch.setenv("TURA_CODE_GOOGLE_CLIENT_ID", "client-1")
        monkeypatch.setenv("TURA_CODE_GOOGLE_CLIENT_SECRET", "not-secret")
        monkeypatch.setenv("TURA_CODE_GITHUB_CLIENT_ID", "gh-client")
        # Never the developer's own GitHub login.
        monkeypatch.setattr(gh, "_gh_cache", (float("inf"), ""))
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        yield found


@pytest.fixture
def repo(workspace):
    git = workspace / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/feature\n")
    (git / "config").write_text('[core]\n\tbare = false\n[remote "origin"]\n\turl = git@github.com:o/r.git\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n')
    return workspace


def test_remote_parsing():
    assert gh.parse_remote("https://github.com/o/r.git") == "o/r"
    assert gh.parse_remote("git@github.com:o/r.git") == "o/r"
    assert gh.parse_remote("https://ghe.corp/team/app") == "team/app"


def test_github_device_flow_and_disconnect(client, fakes):
    assert client.get("/api/connections").json()["github"]["connected"] is False
    started = client.post("/api/connections/github/device").json()
    assert started["user_code"] == "ABCD-1234"
    assert client.post("/api/connections/github/device/poll", json={"device_code": "dev-1"}).json() == {"pending": True}
    done = client.post("/api/connections/github/device/poll", json={"device_code": "dev-1"}).json()
    assert done["pending"] is False and done["login"] == "ada"
    status = client.get("/api/connections").json()["github"]
    assert status["connected"] and status["source"] == "connected" and "secret" not in status and "token" not in str(status)
    assert client.delete("/api/connections/github").json() == {"removed": True}
    assert client.put("/api/connections/github", json={"token": "wrong"}).status_code == 400


@requires_runtime
def test_the_agent_reads_an_issue_comments_and_opens_a_pr(client, fakes, repo, script):
    assert client.put("/api/connections/github", json={"token": "gh-token"}).json()["login"] == "ada"
    script(
        [
            tool_call("c1", "github", {"action": "get_issue", "number": 1}),
            tool_call("c2", "github", {"action": "comment", "number": 1, "body": "Fixed in #7."}),
            tool_call("c3", "github", {"action": "create_pr", "title": "Fix login", "body": "Closes #1"}),
            tool_call("c4", "github", {"action": "get_pr", "number": 7}),
            text("Opened #7."),
        ]
    )
    session_id = client.post("/api/sessions", json={"workspace": str(repo)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "fix issue 1 and open a PR")
    results = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(events, "ToolResult")}
    assert "Login fails" in results["c1"]["output"] and "Same here." in results["c1"]["output"]
    assert "Opened #7 (feature → main)" in results["c3"]["output"]
    assert "success: tests" in results["c4"]["output"] and "modified  app.py" in results["c4"]["output"]
    asked = [e["payload"]["action"] for e in of_type(events, "ApprovalRequest")]
    assert asked == ["github comment", "github create_pr"], "reads are free, writes ask"
    assert ("POST", "comment", {"body": "Fixed in #7."}) in fakes.calls
    pr = next(body for method, what, body in fakes.calls if what == "pulls")
    assert pr == {"title": "Fix login", "body": "Closes #1", "head": "feature", "base": "main", "draft": False}


@requires_runtime
def test_a_rejected_github_write_does_not_happen(client, fakes, repo, script):
    client.put("/api/connections/github", json={"token": "gh-token"})
    script([tool_call("c1", "github", {"action": "comment", "number": 1, "body": "spam"}), text("ok")])
    session_id = client.post("/api/sessions", json={"workspace": str(repo)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "comment", approve="reject")
    assert of_type(events, "ToolResult")[0]["payload"]["return_value"]["is_error"]
    assert not [c for c in fakes.calls if c[1] == "comment"]


def _connect_gmail(client, fakes) -> None:
    url = client.post("/api/connections/google/start").json()["url"]
    query = parse_qs(urlparse(url).query)
    assert query["code_challenge_method"] == ["S256"] and "gmail.compose" in query["scope"][0]
    fakes.challenges["code-1"] = query["code_challenge"][0]
    page = client.get("/api/connections/google/callback", params={"state": query["state"][0], "code": "code-1"})
    assert "Gmail is connected" in page.text, page.text
    replay = client.get("/api/connections/google/callback", params={"state": query["state"][0], "code": "code-1"})
    assert "expired" in replay.text, "a state works once"


def test_gmail_oauth_rejects_a_forged_state_and_a_wrong_verifier(client, fakes):
    assert "expired" in client.get("/api/connections/google/callback", params={"state": "forged", "code": "x"}).text
    url = client.post("/api/connections/google/start").json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    fakes.challenges["code-2"] = base64.urlsafe_b64encode(hashlib.sha256(b"someone else").digest()).rstrip(b"=").decode()
    page = client.get("/api/connections/google/callback", params={"state": state, "code": "code-2"})
    assert "not connected" in page.text and client.get("/api/connections").json()["gmail"]["connected"] is False


@requires_runtime
def test_the_agent_reads_mail_and_leaves_a_reply_draft(client, fakes, workspace, script):
    _connect_gmail(client, fakes)
    status = client.get("/api/connections").json()["gmail"]
    assert status["connected"] and status["email"] == "ada@example.com"
    script(
        [
            tool_call("c1", "gmail", {"action": "search", "query": "from:bob newer_than:7d"}),
            tool_call("c2", "gmail", {"action": "read", "id": "m1"}),
            tool_call("c3", "gmail", {"action": "draft", "reply_to": "m1", "body": "Thanks Bob, fixed in 1.2.3."}),
            text("Drafted a reply."),
        ]
    )
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "answer bob's crash report")
    results = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(events, "ToolResult")}
    assert "subject: Crash on login" in results["c1"]["output"]
    assert "It crashes when I log in with SSO." in results["c2"]["output"] and "attachments: trace.pdf" in results["c2"]["output"]
    assert [e["payload"]["action"] for e in of_type(events, "ApprovalRequest")] == ["gmail draft"]
    draft = next(body for method, what, body in fakes.calls if what == "draft")
    assert draft["message"]["threadId"] == "t1"
    sent = email.message_from_bytes(base64.urlsafe_b64decode(draft["message"]["raw"]))
    assert sent["To"] == "Bob <bob@example.com>" and sent["Subject"] == "Re: Crash on login"
    assert sent["In-Reply-To"] == "<m1@example.com>" and "fixed in 1.2.3" in sent.get_payload()
    assert fakes.refreshes >= 1, "the short-lived access token was refreshed with the refresh token"


def test_auth_bypass_paths_are_closed(client):
    from fastapi.testclient import TestClient

    from tura_code.app import create_app

    with TestClient(create_app()) as anonymous:
        for path in ("/api/sessions/public", "/api/settings/public", "/api/i/anything"):
            assert anonymous.get(path).status_code == 401, path
        assert anonymous.get("/api/sessions").status_code == 401


def test_keychain_vault_holds_secrets_instead_of_files(tmp_path, monkeypatch):
    import json

    import keyring
    from keyring.backend import KeyringBackend

    class Memory(KeyringBackend):
        priority = 1
        items: dict = {}

        def get_password(self, service, username):
            return self.items.get((service, username))

        def set_password(self, service, username, password):
            self.items[(service, username)] = password

        def delete_password(self, service, username):
            from keyring.errors import PasswordDeleteError

            if self.items.pop((service, username), None) is None:
                raise PasswordDeleteError(username)

    previous = keyring.get_keyring()
    keyring.set_keyring(Memory())
    try:
        root = tmp_path / "data"
        root.mkdir()
        (root / "settings.json").write_text(json.dumps({"openrouter_api_key": "sk-or-old", "model": "m"}))
        monkeypatch.setenv("TURA_CODE_SECRETS", "keychain")
        from tura_code.connections.store import ConnectionStore
        from tura_code.settings import SettingsStore

        store = SettingsStore(root)
        assert store.get().openrouter_api_key == "sk-or-old", "migrated, still in effect"
        assert "sk-or-old" not in (root / "settings.json").read_text(), "the file no longer holds it"
        store.update({"openrouter_api_key": "sk-or-new"})
        assert "sk-or-new" not in (root / "settings.json").read_text()
        assert SettingsStore(root).get().openrouter_api_key == "sk-or-new"
        # A root started without the app's setting (`make run`) follows the move.
        monkeypatch.delenv("TURA_CODE_SECRETS")
        assert json.loads((root / "settings.json").read_text())["secrets"] == "keychain"
        assert SettingsStore(root).get().openrouter_api_key == "sk-or-new"
        connections = ConnectionStore(root)
        connections.put("github", secret={"token": "ghp_x"}, login="ada")
        assert "ghp_x" not in (root / "connections.json").read_text()
        assert connections.get("github")["secret"] == {"token": "ghp_x"} and connections.public("github")["login"] == "ada"
        connections.remove("github")
        assert not [k for k in Memory.items if k[1] == "connection:github"]
    finally:
        keyring.set_keyring(previous)
