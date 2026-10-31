"""The real runtime talking HTTP to a local mock OpenAI-compatible provider (no paid model)."""

from __future__ import annotations

import json

from conftest import of_type, requires_runtime, run_turn
from mock_provider import MockProvider

pytestmark = requires_runtime


def test_runtime_discovers_and_uses_a_tool_through_the_http_provider(client, workspace, monkeypatch):
    (workspace / "notes.txt").write_text("alpha\n")
    replies = [
        {"tool_calls": [{"name": "find_tools", "arguments": {"query": "read a pdf document"}}]},
        {"text": "finished"},
    ]
    with MockProvider(replies) as mock:
        r = client.put("/api/settings", json={"base_url": mock.base_url, "openrouter_api_key": "sk-test-mock", "model": "mock/model", "dynamic_tools": True})
        assert r.status_code == 200, r.text
        sid = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
        with client.websocket_connect(f"/api/sessions/{sid}/ws") as ws:
            ws.receive_json()
            events = run_turn(ws, "find a tool to read a pdf", timeout=60)
    results = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(events, "ToolResult")}
    assert any("read_document" in json.dumps(v.get("output")) for v in results.values())
    assert len(mock.requests) == 2
    names = {t["function"]["name"] for t in mock.requests[0].get("tools", [])}
    # The model was offered the gateway and the coding core, not the deferred toolsets.
    assert {"find_tools", "use_tool", "Shell"} <= names
    assert not names & {"read_document", "edit_spreadsheet", "github_comment"}
    # The key reached the provider; the follow-up request carries the tool result.
    assert all(a.startswith("Bearer ") for a in mock.auth)
    assert any(m.get("role") == "tool" for m in mock.requests[1]["messages"])
