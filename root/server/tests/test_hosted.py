"""Hosted mode: people are kept apart, keys stay out of runtimes, the sandbox holds."""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import of_type, requires_runtime, run_turn, text, tool_call
from fastapi.testclient import TestClient

from tura_code.app import create_app
from tura_code.pool import RuntimePool
from tura_code.sandbox import Bubblewrap, Sandbox
from tura_code.users import UserStore

bwrap_ok = not Bubblewrap().problem()


def _hosted(home, **kw):
    store = UserStore(home / "users.json")
    tokens = {name: store.add(name) for name in ("alice", "bob")}
    app = create_app(home, mode="hosted", **kw)
    return app, tokens


def _auth(token):
    return {"authorization": f"Bearer {token}"}


def test_people_are_kept_apart(home, tmp_path):
    app, tokens = _hosted(home, sandbox=Sandbox())
    with TestClient(app) as client:
        assert client.post("/api/auth/bootstrap").status_code == 403
        assert client.get("/api/sessions").status_code == 401
        assert client.post("/api/auth/login", json={"token": "rt_nope"}).status_code == 401
        assert client.post("/api/auth/login", json={"token": tokens["alice"]}).json()["user"]["id"] == "alice"
        alice, bob = _auth(tokens["alice"]), _auth(tokens["bob"])
        assert client.get("/api/me", headers=alice).json() == {"mode": "hosted", "user": {"id": "alice", "name": "alice", "admin": False}, "sandbox": "none"}

        created = client.post("/api/workspaces", json={"name": "app"}, headers=alice).json()
        assert created["path"] == str(home / "users" / "alice" / "workspaces" / "app")
        assert client.post("/api/workspaces", json={"name": "../escape"}, headers=alice).status_code == 400
        outside = tmp_path / "outside"
        outside.mkdir()
        assert client.post("/api/sessions", json={"workspace": str(outside)}, headers=alice).status_code == 400
        session = client.post("/api/sessions", json={"workspace": "app"}, headers=alice).json()
        assert session["workspace"] == created["path"]

        # Bob sees none of it, and cannot reach it by id or path.
        assert client.get("/api/sessions", headers=bob).json()["sessions"] == []
        assert client.get(f"/api/sessions/{session['id']}", headers=bob).status_code == 404
        assert client.post("/api/sessions", json={"workspace": created["path"]}, headers=bob).status_code == 400
        assert client.get("/api/workspace/files", params={"path": created["path"]}, headers=bob).status_code == 400
        listing = client.get("/api/fs/dirs", params={"path": str(home)}, headers=bob).json()
        assert listing["path"] == str(home / "users" / "bob" / "workspaces") and listing["parent"] is None

        # Settings, and so keys and models, are per person.
        client.put("/api/settings", json={"model": "x/alice-model", "openrouter_api_key": "sk-or-alice-1234"}, headers=alice)
        assert client.get("/api/settings", headers=bob).json()["model"] != "x/alice-model"
        assert client.get("/api/settings", headers=alice).json()["openrouter_api_key"] == ""


def test_the_proxy_swaps_the_runtime_token_for_the_key(home, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-operator")
    app, tokens = _hosted(home, sandbox=Sandbox(), self_url="http://127.0.0.1:9")
    server = app.state.root.server
    seen = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    server.proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    with TestClient(app) as client:
        tenant = server.tenant("alice")
        base, token = server.proxy.issue("alice", "s1", lambda: ("https://openrouter.ai/api/v1", tenant.api_key()))
        assert base == "http://127.0.0.1:9/llm/api/v1" and "sk-or" not in token
        reply = client.post("/llm/api/v1/chat/completions", json={"model": "m"}, headers=_auth(token))
        assert reply.json()["choices"][0]["message"]["content"] == "hi"
        assert seen == {"url": "https://openrouter.ai/api/v1/chat/completions", "auth": "Bearer sk-or-operator"}
        # Jev's decisions route is a sibling of /v1, and goes through too.
        client.post("/llm/api/alpha/decisions", json={}, headers=_auth(token))
        assert seen["url"] == "https://openrouter.ai/api/alpha/decisions"
        # A person's own key wins over the operator's.
        client.put("/api/settings", json={"openrouter_api_key": "sk-or-alice-own"}, headers=_auth(tokens["alice"]))
        client.post("/llm/api/v1/chat/completions", json={}, headers=_auth(token))
        assert seen["auth"] == "Bearer sk-or-alice-own"
        server.proxy.revoke(token)
        assert client.post("/llm/api/v1/chat/completions", json={}, headers=_auth(token)).status_code == 401


@requires_runtime
def test_runtime_config_holds_a_token_not_the_key(home, monkeypatch, script):
    script([text("hello")])
    monkeypatch.delenv("TURA_CODE_SCRIPTED_ECHO", raising=False)
    from tura_code import launcher
    from tura_code.tenancy import Server

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-operator-secret")
    server = Server(home, mode="hosted")
    server.proxy.base = "http://127.0.0.1:9"
    tenant = server.tenant("alice")
    (tenant.workspace_root / "w").mkdir()
    runtime = tenant.sessions.create("w")
    spec = tenant.launch_spec(runtime)
    config = launcher.runtime_config(spec)
    provider = config["providers"]["openrouter"]
    assert provider["base_url"] == "http://127.0.0.1:9/llm/api/v1"
    assert provider["api_key"].startswith("root-") and "sk-or" not in json.dumps(config)
    assert tenant.director_state(spec) == "on"


@requires_runtime
@pytest.mark.skipif(not bwrap_ok, reason="bubblewrap cannot run here")
def test_the_sandbox_hides_everything_but_the_workspace(home, script, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-operator-secret")
    app, tokens = _hosted(home, sandbox=Bubblewrap())
    bob_dir = home / "users" / "bob"
    script(
        [
            tool_call("a", "Shell", {"command": f"ls {bob_dir} 2>&1; cat {home / 'users.json'} 2>&1; echo done"}),
            tool_call("b", "Shell", {"command": "env | grep -c API_KEY; true"}),
            tool_call("c", "Shell", {"command": "touch made.txt && pwd"}),
            tool_call("d", "process", {"action": "start", "command": "ls /home/user/root/server 2>&1; echo $HOME"}),
            tool_call("e", "process", {"action": "wait", "process_id": "p1", "timeout": 5}),
            text("checked"),
        ]
    )
    with TestClient(app) as client:
        alice = _auth(tokens["alice"])
        bob = _auth(tokens["bob"])
        client.get("/api/sessions", headers=bob)  # bob's tenant exists alongside
        workspace = client.post("/api/workspaces", json={"name": "proj"}, headers=alice).json()["path"]
        session = client.post("/api/sessions", json={"workspace": workspace}, headers=alice).json()["id"]
        client.patch(f"/api/sessions/{session}", json={"permission_mode": "yolo"}, headers=alice)
        with client.websocket_connect(f"/api/sessions/{session}/ws", headers=alice) as ws:
            ws.receive_json()
            events = run_turn(ws, "look around", timeout=60)
    results = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(events, "ToolResult")}
    out = {k: (v["output"] if isinstance(v["output"], str) else "") for k, v in results.items()}
    assert "No such file" in out["a"] and "token_sha256" not in out["a"] and "done" in out["a"]
    assert out["b"].strip() == "0"
    assert out["c"].strip() == workspace and (home / "users" / "alice" / "workspaces" / "proj" / "made.txt").exists()
    assert "No such file" in out["e"] and str(home / "users" / "alice" / "home") in out["e"]
    ready = [e for e in events if e["type"] == "RuntimeStatus" and e["payload"].get("state") == "ready"]
    assert ready and ready[0]["payload"]["sandbox"] == "bwrap"


@requires_runtime
def test_the_pool_evicts_an_idle_runtime_to_make_room(client, workspace, script, monkeypatch):
    server = client.app.state.root.server
    server.pool.max_per_tenant = 1
    script([text("one"), text("two"), text("three")])
    first = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    second = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{first}/ws") as ws:
        ws.receive_json()
        run_turn(ws, "first")
    tenant = server.tenant()
    assert tenant.sessions.get(first).process is not None
    with client.websocket_connect(f"/api/sessions/{second}/ws") as ws:
        ws.receive_json()
        assert of_type(run_turn(ws, "second"), "TurnFinished")[0]["payload"]["status"] == "finished"
    assert tenant.sessions.get(first).process is None and server.pool.count("local") == 1
    # The evicted session resumes on its next prompt.
    with client.websocket_connect(f"/api/sessions/{first}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "again")
    assert of_type(events, "TurnFinished")[0]["payload"]["status"] == "finished"


def test_hosted_never_opens_a_folder_dialog_on_the_server(home, monkeypatch):
    from tura_code import workspace

    monkeypatch.setattr(workspace, "folder_picker", lambda: True)
    app, tokens = _hosted(home, sandbox=Sandbox())
    with TestClient(app) as client:
        assert client.get("/api/health", headers=_auth(tokens["alice"])).json()["folder_picker"] is False
        assert client.post("/api/fs/pick", json={}, headers=_auth(tokens["alice"])).status_code == 404
