"""The Hermes `process` tool: background processes the agent starts and drives."""

from __future__ import annotations

import os
import time

from conftest import of_type, requires_runtime, run_turn, text, tool_call

import pytest

# POSIX commands and liveness checks; test_windows.py runs the Windows equivalents.
pytestmark = [requires_runtime, pytest.mark.skipif(os.name == "nt", reason="POSIX commands; see test_windows.py")]

INTERACTIVE = "echo ready; read line; echo got:$line; sleep 30"


def _results(events):
    return {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(events, "ToolResult")}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie is dead for our purposes.
    try:
        with open(f"/proc/{pid}/stat") as handle:
            return handle.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


def test_agent_runs_a_background_process_and_talks_to_it(client, workspace, script):
    script(
        [
            tool_call("s", "process", {"action": "start", "command": INTERACTIVE, "label": "echo server"}),
            tool_call("p", "process", {"action": "poll", "process_id": "p1"}),
            tool_call("w", "process", {"action": "submit", "process_id": "p1", "data": "hello"}),
            tool_call("t", "process", {"action": "wait", "process_id": "p1", "timeout": 1}),
            tool_call("l", "process", {"action": "log", "process_id": "p1"}),
            tool_call("k", "process", {"action": "kill", "process_id": "p1"}),
            tool_call("x", "process", {"action": "list"}),
            text("Done with the server."),
        ]
    )
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "start the echo server and send it hello")

    # Starting it was a command execution, and `sh` is not a known-safe program: it asked.
    asked = of_type(events, "ApprovalRequest")
    assert len(asked) == 1
    request = asked[0]["payload"]
    assert request["tool_call_id"] == "s" and request["display"][0]["command"] == INTERACTIVE
    assert request["trust"]["key"] == "run command:echo" or request["trust"]["tier"] == "needs_trust"

    results = _results(events)
    assert all(not r["is_error"] for r in results.values()), results
    assert "id: p1" in results["s"]["output"]
    assert "ready" in results["p"]["output"] and "status: running" in results["p"]["output"]
    assert "still running after the timeout" in results["t"]["output"]
    assert "got:hello" in results["l"]["output"]
    assert "status: killed" in results["k"]["output"]
    assert "p1" in results["x"]["output"] and "killed" in results["x"]["output"]

    listed = client.get(f"/api/sessions/{session_id}/processes").json()["processes"]
    assert listed[0]["status"] == "killed" and not _alive(listed[0]["pid"])
    log = client.get(f"/api/sessions/{session_id}/processes/p1/log").json()
    assert log["lines"][:2] == ["ready", "got:hello"]
    assert any(e["type"] == "ProcessUpdated" for e in events)


def test_rejected_start_runs_nothing(client, workspace, script):
    script([tool_call("s", "process", {"action": "start", "command": "touch started.txt"}), text("ok")])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "start it", approve="reject")
    result = _results(events)["s"]
    assert result["is_error"] and "did not approve" in result["message"]
    time.sleep(0.3)
    assert not (workspace / "started.txt").exists()
    assert client.get(f"/api/sessions/{session_id}/processes").json()["processes"] == []


def test_processes_stop_with_their_session_and_stay_in_the_workspace(client, workspace, script):
    script(
        [
            tool_call("s", "process", {"action": "start", "command": "sleep 60"}),
            tool_call("o", "process", {"action": "start", "command": "sleep 1", "cwd": "../.."}),
            tool_call("m", "process", {"action": "poll", "process_id": "p9"}),
            text("ok"),
        ]
    )
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    client.patch(f"/api/sessions/{session_id}", json={"permission_mode": "yolo"})
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "start a sleeper")
    results = _results(events)
    assert not results["s"]["is_error"]
    assert results["o"]["is_error"] and "inside the workspace" in results["o"]["message"]
    assert results["m"]["is_error"] and "No process `p9`" in results["m"]["message"]
    pid = client.get(f"/api/sessions/{session_id}/processes").json()["processes"][0]["pid"]
    assert _alive(pid)

    # The owner can stop it from the UI…
    killed = client.post(f"/api/sessions/{session_id}/processes/p1/kill").json()
    assert killed["status"] == "killed" and not _alive(pid)


def test_deleting_a_session_kills_its_processes(client, workspace, script):
    script([tool_call("s", "process", {"action": "start", "command": "sleep 60"}), text("ok")])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    client.patch(f"/api/sessions/{session_id}", json={"permission_mode": "yolo"})
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        run_turn(ws, "start a sleeper")
    pid = client.get(f"/api/sessions/{session_id}/processes").json()["processes"][0]["pid"]
    assert _alive(pid)
    assert client.delete(f"/api/sessions/{session_id}").json() == {"ok": True}
    assert not _alive(pid)


def test_cancelling_a_turn_stops_its_wait_but_not_the_process(client, workspace, script):
    import time

    script([
        tool_call("s", "process", {"action": "start", "command": "sleep 120"}),
        tool_call("w", "process", {"action": "wait", "process_id": "p1", "timeout": 600}),
        text("unreachable"),
    ])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    client.patch(f"/api/sessions/{session_id}", json={"permission_mode": "yolo"})
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "prompt", "text": "start it and wait"})
        while True:
            message = ws.receive_json()
            if message["type"] == "event" and message["event"]["type"] == "ToolCall" and message["event"]["payload"]["id"] == "w":
                time.sleep(0.5)  # the wait is under way
                started = time.monotonic()
                ws.send_json({"type": "cancel"})
            if message["type"] == "event" and message["event"]["type"] == "TurnFinished":
                break
    assert time.monotonic() - started < 15, "the cancelled turn sat out its wait"
    assert client.get(f"/api/sessions/{session_id}").json()["running"] is False
    processes = client.get(f"/api/sessions/{session_id}/processes").json()["processes"]
    assert [p["status"] for p in processes] == ["running"]  # a background process outlives the turn by design
