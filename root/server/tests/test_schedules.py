"""Schedules: the timetable language, the API, and unattended runs through the real runtime."""

from __future__ import annotations

import time
from datetime import datetime

import pytest
from conftest import of_type, requires_runtime, run_turn, text, tool_call

from tura_code.schedules.when import WhenError, next_after, to_cron


def test_plain_forms_become_cron():
    assert to_cron("every 15m") == "*/15 * * * *"
    assert to_cron("every 2h") == "0 */2 * * *"
    assert to_cron("daily 9:05") == "5 9 * * *"
    assert to_cron("Weekdays 18:30") == "30 18 * * 1-5"
    assert to_cron("@daily") == "0 9 * * *"
    for bad in ("every 7m", "daily 25:00", "* * *", "61 * * * *", "0 9 * * 7", "whenever"):
        with pytest.raises(WhenError):
            to_cron(bad)


def test_next_after_follows_cron_rules():
    friday_evening = datetime(2026, 10, 16, 19, 0)  # a Friday
    assert next_after("30 18 * * 1-5", friday_evening) == datetime(2026, 10, 19, 18, 30)
    assert next_after("*/15 * * * *", datetime(2026, 10, 16, 10, 7)) == datetime(2026, 10, 16, 10, 15)
    assert next_after("0 0 1 * *", datetime(2026, 10, 16)) == datetime(2026, 11, 1, 0, 0)
    # Day-of-month and day-of-week both set: either matches (cron's rule).
    assert next_after("0 9 13 * 5", datetime(2026, 10, 12, 10)) == datetime(2026, 10, 13, 9, 0)


def test_the_api_validates_and_manages_schedules(client, workspace):
    def create(**overrides):
        body = {"name": "nightly", "workspace": str(workspace), "prompt": "run the tests", "when": "daily 02:00", "mode": "plan", **overrides}
        return client.post("/api/schedules", json=body)

    assert create(mode="yolo").status_code == 400, "unattended runs never auto-approve everything"
    assert create(when="sometimes").status_code == 400
    assert create(workspace="/no/such/folder").status_code == 400
    item = create().json()
    assert item["cron"] == "0 2 * * *" and item["next_run"] > time.time()
    paused = client.patch(f"/api/schedules/{item['id']}", json={"enabled": False}).json()
    assert paused["next_run"] is None
    assert [s["id"] for s in client.get("/api/schedules").json()["schedules"]] == [item["id"]]
    assert client.delete(f"/api/schedules/{item['id']}").json() == {"removed": True}


def _wait_for(client, schedule_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = next(s for s in client.get("/api/schedules").json()["schedules"] if s["id"] == schedule_id)
        if not item["running"] and item["last_status"] not in ("", "running"):
            return item
        time.sleep(0.1)
    raise AssertionError("the scheduled run did not finish")


@requires_runtime
def test_an_unattended_run_declines_what_would_need_the_owner(client, workspace, script):
    script([tool_call("c1", "WriteFile", {"path": "report.md", "content": "all green\n"}), text("Could not write the report.")])
    item = client.post("/api/schedules", json={"name": "report", "workspace": str(workspace), "prompt": "write report.md", "when": "every 1h", "mode": "default"}).json()
    session_id = client.post(f"/api/schedules/{item['id']}/run").json()["session"]
    done = _wait_for(client, item["id"])
    assert done["last_session"] == session_id and done["last_status"] == "finished" and done["runs"] == 1
    assert not (workspace / "report.md").exists(), "nobody was there to approve the edit"
    session = client.get(f"/api/sessions/{session_id}").json()
    assert session["title"] == "⏰ report" and session["unattended"] and session["schedule"] == item["id"]
    events = client.get(f"/api/sessions/{session_id}/events").json()["events"]
    resolved = of_type(events, "ApprovalResolved")[0]["payload"]
    assert resolved["response"] == "reject" and resolved["auto"]


@requires_runtime
def test_accept_edits_schedules_change_files_and_the_clock_starts_due_runs(client, workspace, script):
    script([tool_call("c1", "WriteFile", {"path": "report.md", "content": "all green\n"}), text("Wrote it.")])
    item = client.post("/api/schedules", json={"name": "report", "workspace": str(workspace), "prompt": "write report.md", "when": "every 1h", "mode": "accept_edits"}).json()
    tenant = client.app.state.root.server.tenant()
    assert client.portal.call(tenant.schedules.tick, item["next_run"] + 1) == [item["id"]]
    done = _wait_for(client, item["id"])
    assert done["last_status"] == "finished" and (workspace / "report.md").read_text() == "all green\n"
    assert done["next_run"] > item["next_run"], "the next run is planned from now"


@requires_runtime
def test_the_agent_schedules_with_approval_and_runs_cannot_schedule(client, workspace, script):
    script([tool_call("c1", "schedule", {"action": "create", "name": "PR digest", "prompt": "Summarize open PRs.", "when": "weekdays 09:00"}), text("Scheduled.")])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "every weekday morning summarize open PRs")
    assert [a["payload"]["action"] for a in of_type(events, "ApprovalRequest")] == ["schedule create"]
    schedules = client.get("/api/schedules").json()["schedules"]
    assert [(s["name"], s["cron"], s["mode"], s["created_by"]) for s in schedules] == [("PR digest", "0 9 * * 1-5", "plan", "agent")]

    script([tool_call("c1", "schedule", {"action": "list"}), text("ok")])
    run = client.post(f"/api/schedules/{schedules[0]['id']}/run").json()["session"]
    _wait_for(client, schedules[0]["id"])
    events = client.get(f"/api/sessions/{run}/events").json()["events"]
    result = of_type(events, "ToolResult")[0]["payload"]["return_value"]
    assert result["is_error"] and "not found" in str(result).lower(), "a scheduled run has no schedule tool"
