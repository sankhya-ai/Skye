"""The whole stack: API → session → kimi-agent runtime → tools, with a scripted model."""

from __future__ import annotations

import json

from conftest import of_type, requires_runtime, run_turn, text, tool_call

pytestmark = requires_runtime


def _session(client, workspace) -> str:
    response = client.post("/api/sessions", json={"workspace": str(workspace)})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_multi_file_change_with_approvals(client, workspace, script):
    script(
        [
            "\n".join(
                [
                    text("Creating the module and its caller."),
                    tool_call("c1", "WriteFile", {"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"}),
                    tool_call("c2", "WriteFile", {"path": "main.py", "content": "from calc import add\nprint(add(2, 3))\n"}),
                ]
            ),
            tool_call("c3", "StrReplaceFile", {"path": "calc.py", "edit": [{"old": "a + b", "new": "int(a) + int(b)"}]}),
            tool_call("c4", "Shell", {"command": "python3 main.py"}),
            text("Done: `main.py` prints 5."),
        ]
    )
    session_id = _session(client, workspace)
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["history"] == []
        events = run_turn(ws, "Add a calc module and use it")

    finished = of_type(events, "TurnFinished")[-1]["payload"]
    assert finished["status"] == "finished", finished
    assert sorted(finished["files_touched"]) == ["calc.py", "main.py"]
    assert (workspace / "calc.py").read_text() == "def add(a, b):\n    return int(a) + int(b)\n"
    assert (workspace / "main.py").exists()

    approvals = of_type(events, "ApprovalRequest")
    assert [a["payload"]["action"] for a in approvals] == ["edit file", "edit file", "edit file", "run command"]
    assert approvals[0]["payload"]["display"][0]["type"] == "diff"
    shell_result = [
        e for e in of_type(events, "ToolResult") if e["payload"]["tool_call_id"] == "c4"
    ][0]["payload"]["return_value"]
    assert shell_result["output"].strip() == "5"

    prompt = of_type(events, "UserPrompt")[0]["payload"]
    assert prompt["text"] == "Add a calc module and use it"

    # A reloaded session replays whole messages, not streaming fragments.
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        hello = ws.receive_json()
    history = hello["history"]
    assert [e["type"] for e in history if e["type"] == "UserPrompt"] == ["UserPrompt"]
    texts = [e["payload"]["text"] for e in history if e["type"] == "ContentPart"]
    assert "Done: `main.py` prints 5." in texts
    listed = client.get("/api/sessions").json()["sessions"][0]
    assert listed["title"] == "Add a calc module and use it"
    assert listed["turns"] == 1 and listed["last_status"] == "finished"


def test_rejected_edit_stops_the_turn_and_leaves_disk_alone(client, workspace, script):
    script([tool_call("c1", "WriteFile", {"path": "nope.txt", "content": "x"}), text("unreachable")])
    session_id = _session(client, workspace)
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "write nope.txt", approve="reject")
    assert not (workspace / "nope.txt").exists()
    resolved = of_type(events, "ApprovalResolved")[0]["payload"]
    assert resolved["response"] == "reject" and resolved["auto"] is False
    assert of_type(events, "TurnFinished")[-1]["payload"]["status"] == "finished"


def test_accept_edits_mode_approves_edits_and_reads_but_not_state_changes(client, workspace, script):
    script(
        [
            tool_call("c1", "WriteFile", {"path": "a.txt", "content": "hello\n"}),
            tool_call("c2", "Shell", {"command": "cat a.txt"}),
            tool_call("c3", "Shell", {"command": "touch b.txt"}),
            text("ok"),
        ]
    )
    session_id = _session(client, workspace)
    client.patch(f"/api/sessions/{session_id}", json={"permission_mode": "accept_edits"})
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "write and show a.txt")
    auto = [e["payload"] for e in of_type(events, "ApprovalResolved") if e["payload"]["auto"]]
    asked = [e["payload"]["action"] for e in of_type(events, "ApprovalRequest")]
    assert [(a["action"], a["reason"]) for a in auto] == [("edit file", "accept edits"), ("run command", "read-only command")]
    assert asked == ["run command"]
    request = of_type(events, "ApprovalRequest")[0]["payload"]
    assert request["trust"]["key"] == "run command:touch" and request["trust"]["approvals"] == 0
    assert (workspace / "a.txt").read_text() == "hello\n"
    assert (workspace / "b.txt").exists()


def test_plan_mode_removes_edit_tools(client, workspace, script):
    script([tool_call("c1", "WriteFile", {"path": "plan.txt", "content": "x"}), text("Here is the plan.")])
    session_id = _session(client, workspace)
    client.patch(f"/api/sessions/{session_id}", json={"permission_mode": "plan"})
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "plan a change")
    result = of_type(events, "ToolResult")[0]["payload"]["return_value"]
    assert result["is_error"] is True
    assert "not found" in (result["message"] + result["output"]).lower()
    assert not (workspace / "plan.txt").exists()
    assert of_type(events, "ApprovalRequest") == []


def test_memory_tools_round_trip_through_dhee(client, workspace, script):
    script(
        [
            tool_call("m1", "memory_remember", {"content": "Run the test suite with `make check`.", "kind": "command"}),
            tool_call("m2", "memory_recall", {"query": "how do I run the test suite"}),
            text("Remembered."),
        ]
    )
    session_id = _session(client, workspace)
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "remember how tests run")
    results = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(events, "ToolResult")}
    assert results["m1"]["is_error"] is False, results["m1"]
    assert "Stored in project memory" in results["m1"]["output"]
    assert "make check" in results["m2"]["output"]

    # The next session in the same workspace gets it back without asking.
    script([text("Using `make check`.")])
    second = _session(client, workspace)
    with client.websocket_connect(f"/api/sessions/{second}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "how should I run the test suite here?")
    memory = of_type(events, "UserPrompt")[0]["payload"]["memory"]
    assert any("make check" in item["memory"] for item in memory)

    listed = client.get("/api/memory", params={"path": str(workspace)}).json()
    assert any("make check" in item["memory"] for item in listed["items"])


def test_cancel_ends_a_turn_waiting_for_approval(client, workspace, script):
    script([tool_call("c1", "Shell", {"command": "touch hi.txt"}), text("unreachable")])
    session_id = _session(client, workspace)
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "prompt", "text": "say hi"})
        while True:
            message = ws.receive_json()
            if message["type"] == "event" and message["event"]["type"] == "ApprovalRequest":
                ws.send_json({"type": "cancel"})
            if message["type"] == "event" and message["event"]["type"] == "TurnFinished":
                status = message["event"]["payload"]["status"]
                break
    assert status in ("cancelled", "finished")
    session = client.get(f"/api/sessions/{session_id}").json()
    assert session["running"] is False


def test_changes_endpoint_reports_agent_edits(client, workspace, script):
    import subprocess

    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(["git", "-C", str(workspace), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        check=True,
    )
    script([tool_call("c1", "StrReplaceFile", {"path": "README.md", "edit": {"old": "# demo", "new": "# demo app"}}), text("ok")])
    session_id = _session(client, workspace)
    client.patch(f"/api/sessions/{session_id}", json={"permission_mode": "yolo"})
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        run_turn(ws, "rename the heading")
    changes = client.get("/api/workspace/changes", params={"path": str(workspace)}).json()
    assert changes["git"] is True
    assert changes["files"] == [{"path": "README.md", "status": "M"}]
    assert "+# demo app" in changes["diff"]


def test_missing_key_is_a_clear_error(client, workspace, monkeypatch):
    from tura_code import launcher

    monkeypatch.delenv(launcher.SCRIPTED_ECHO_ENV, raising=False)
    session_id = _session(client, workspace)
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "prompt", "text": "hello"})
        messages = [ws.receive_json() for _ in range(3)]
    errors = [m for m in messages if m["type"] == "error"]
    assert errors and "OpenRouter API key" in errors[0]["message"]


def test_slash_commands_are_passed_to_the_runtime(client, workspace, script):
    script([])
    session_id = _session(client, workspace)
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "/clear")
    parts = [e["payload"].get("text", "") for e in of_type(events, "ContentPart")]
    assert any("cleared" in p for p in parts)
    runtime = [e for e in events if e["type"] == "TurnFinished"][0]["payload"]
    assert runtime["status"] == "finished"
    assert json.dumps(events)  # serialisable


import os

import pytest


@pytest.mark.skipif(not os.environ.get("TURA_CODE_LIVE_SEARCH"), reason="set TURA_CODE_LIVE_SEARCH=1 to search the real web")
def test_live_keyless_web_search(client, workspace, script):
    """The runtime's SearchWeb against the real keyless backends: no key anywhere."""
    script([tool_call("w", "SearchWeb", {"query": "tokio rust async runtime", "limit": 3}), text("found it")])
    session_id = _session(client, workspace)
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "search the web", timeout=90)
    result = of_type(events, "ToolResult")[0]["payload"]["return_value"]
    assert result["is_error"] is False, result
    assert "tokio" in result["output"].lower() and "URL: https://" in result["output"]
    assert any(d.get("text", "").startswith("Searched via") for d in result["display"])


def test_a_restart_for_a_new_mode_is_not_reported_as_an_exit(client, workspace, script):
    script([text("first"), text("second")])
    session_id = _session(client, workspace)
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        run_turn(ws, "one")
        ws.send_json({"type": "set_mode", "mode": "plan"})  # plan mode reshapes the process
        events = run_turn(ws, "two")
    statuses = [e["payload"].get("state") for e in events if e["type"] == "RuntimeStatus"]
    assert "starting" in statuses and "ready" in statuses and "exited" not in statuses


def _until(ws, kind: str, timeout_events: int = 500) -> list[dict]:
    seen: list[dict] = []
    for _ in range(timeout_events):
        message = ws.receive_json()
        if message["type"] == "error":
            raise AssertionError(message["message"])
        if message["type"] == "event":
            seen.append(message["event"])
            if message["event"]["type"] == kind:
                return seen
    raise AssertionError(f"no {kind}")


def test_a_message_sent_during_a_turn_runs_when_it_finishes(client, workspace, script):
    script([tool_call("c1", "Shell", {"command": "touch hi.txt"}), text("one"), text("two")])
    session_id = _session(client, workspace)
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "prompt", "text": "first"})
        request = _until(ws, "ApprovalRequest")[-1]
        ws.send_json({"type": "queue", "text": "second"})
        queued = _until(ws, "QueueUpdated")[-1]["payload"]["queued"]
        assert [q["text"] for q in queued] == ["second"]
        ws.send_json({"type": "approval", "request_id": request["payload"]["id"], "response": "approve"})
        events = _until(ws, "TurnFinished")
        events += _until(ws, "TurnFinished")
    prompts = [e["payload"]["text"] for e in of_type(events, "UserPrompt")]
    assert prompts == ["second"]
    assert of_type(events, "QueueUpdated")[-1]["payload"]["queued"] == []
    assert [e["payload"]["status"] for e in of_type(events, "TurnFinished")] == ["finished", "finished"]
    assert client.get(f"/api/sessions/{session_id}").json()["queued"] == []


def test_a_cancelled_turn_pauses_the_queue_until_the_owner_sends_it(client, workspace, script):
    script([tool_call("c1", "Shell", {"command": "touch hi.txt"}), text("later")])
    session_id = _session(client, workspace)
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "prompt", "text": "first"})
        _until(ws, "ApprovalRequest")
        ws.send_json({"type": "queue", "text": "next"})
        _until(ws, "QueueUpdated")
        ws.send_json({"type": "cancel"})
        finished = _until(ws, "TurnFinished")[-1]["payload"]
        assert finished["status"] in ("cancelled", "finished")
        waiting = client.get(f"/api/sessions/{session_id}").json()
        assert waiting["running"] is False and [q["text"] for q in waiting["queued"]] == ["next"]
        ws.send_json({"type": "send_queued", "id": waiting["queued"][0]["id"]})
        events = _until(ws, "TurnFinished")
    assert [e["payload"]["text"] for e in of_type(events, "UserPrompt")] == ["next"]
    assert client.get(f"/api/sessions/{session_id}").json()["queued"] == []
