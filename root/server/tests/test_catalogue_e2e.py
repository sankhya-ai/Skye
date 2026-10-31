"""The dynamic catalogue through the real runtime with the scripted model (no provider, no cost)."""

from __future__ import annotations

import json

from conftest import of_type, requires_runtime, run_turn, text, tool_call

from test_documents import make_pdf

pytestmark = requires_runtime


def _results(events):
    return {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(events, "ToolResult")}


def test_find_then_use_reads_a_pdf_and_direct_calls_are_refused(client, workspace, script):
    make_pdf(workspace / "invoice.pdf", ["Invoice 42\nTotal due: 99.50"])
    client.put("/api/settings", json={"dynamic_tools": True})
    script(
        [
            tool_call("f", "find_tools", {"query": "read a pdf"}),
            tool_call("u", "use_tool", {"name": "read_document", "arguments": {"path": "invoice.pdf"}}),
            tool_call("d", "read_document", {"path": "invoice.pdf"}),
            tool_call("x", "use_tool", {"name": "process", "arguments": {"action": "list"}}),
            text("ok"),
        ]
    )
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        results = _results(run_turn(ws, "read the invoice"))
    assert "read_document" in json.dumps(results["f"]["output"])
    assert "99.50" in json.dumps(results["u"]["output"])
    assert results["u"]["is_error"] is False
    assert results["d"]["is_error"] is True  # hidden toolset cannot be called directly
    assert results["x"]["is_error"] is True  # use_tool only reaches discovered (deferred) tools


def test_session_scope_refuses_even_listed_tools_and_reports_guarantee(client, workspace, script):
    script([tool_call("p", "process", {"action": "list"}), text("ok")])
    created = client.post("/api/sessions", json={"workspace": str(workspace), "tool_scope": ["ReadFile", "Grep", "Glob"]}).json()
    assert created["scope_guarantee"]["level"] == "tool-names"
    with client.websocket_connect(f"/api/sessions/{created['id']}/ws") as ws:
        ws.receive_json()
        results = _results(run_turn(ws, "list processes"))
    assert results["p"]["is_error"] is True
