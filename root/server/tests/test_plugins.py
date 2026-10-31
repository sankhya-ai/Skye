"""Plugins: the catalog, switching one off (its tools are gone), and the owner's MCP servers."""

from __future__ import annotations

import json
import os

from conftest import of_type, requires_runtime, run_turn, text, tool_call


def test_the_catalog_lists_what_root_can_do(client):
    data = client.get("/api/plugins").json()
    ids = [p["id"] for p in data["plugins"]]
    assert {"documents", "toolbox", "github", "gmail", "slack", "keiki", "driver", "inspect", "guides", "history", "schedules", "flows", "web", "memory"} == set(ids)
    assert all(p["enabled"] for p in data["plugins"])
    assert data["mcp"] == []


def test_settings_backed_plugins_switch_their_setting(client):
    assert client.put("/api/plugins/web", json={"enabled": False}).json() == {"id": "web", "enabled": False}
    assert client.get("/api/settings").json()["web_search"] is False
    client.put("/api/plugins/memory", json={"enabled": False})
    assert client.get("/api/settings").json()["memory_mode"] == "off"
    assert client.put("/api/plugins/nope", json={"enabled": False}).status_code == 404


@requires_runtime
def test_a_plugin_that_is_off_gives_the_agent_none_of_its_tools(client, workspace, script):
    (workspace / "a.csv").write_text("x,y\n1,2\n")
    client.put("/api/plugins/documents", json={"enabled": False})
    script([tool_call("c1", "read_document", {"path": "a.csv"}), text("ok")])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "read it")
    result = of_type(events, "ToolResult")[0]["payload"]["return_value"]
    assert result["is_error"] and "not found" in json.dumps(result).lower()

    client.put("/api/plugins/documents", json={"enabled": True})
    script([tool_call("c1", "read_document", {"path": "a.csv"}), text("ok")])
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "read it again")
    assert "1 | 2" in of_type(events, "ToolResult")[0]["payload"]["return_value"]["output"]


def test_mcp_servers_are_added_switched_and_removed(client, home):
    added = client.put("/api/plugins/mcp/linear", json={"command": "npx", "args": ["-y", "linear-mcp"], "env": {"LINEAR_TOKEN": "lin_secret"}})
    assert added.status_code == 200, added.text
    assert added.json()["mcp"] == [{"name": "linear", "enabled": True, "command": "npx", "args": ["-y", "linear-mcp"], "url": "", "env": ["LINEAR_TOKEN"]}]
    assert "lin_secret" not in json.dumps(client.get("/api/plugins").json()), "env values never go back to the UI"
    stored = json.loads((home / "mcp.json").read_text())
    assert stored["mcpServers"]["linear"]["env"] == {"LINEAR_TOKEN": "lin_secret"}
    if os.name != "nt":  # Windows has no mode bits; the profile folder's ACL keeps it private
        assert (home / "mcp.json").stat().st_mode & 0o777 == 0o600

    # Switching it off keeps its token; the runtime's file no longer lists it.
    client.put("/api/plugins/mcp/linear", json={"command": "npx", "args": ["-y", "linear-mcp"], "env": {"LINEAR_TOKEN": ""}, "enabled": False})
    assert not (home / "mcp.json").exists()
    assert json.loads((home / "mcp.disabled.json").read_text())["mcpServers"]["linear"]["env"] == {"LINEAR_TOKEN": "lin_secret"}

    assert client.put("/api/plugins/mcp/both", json={"command": "x", "url": "https://x"}).status_code == 400
    assert client.put("/api/plugins/mcp/bad name", json={"command": "x"}).status_code == 400
    assert client.delete("/api/plugins/mcp/linear").json()["mcp"] == []
