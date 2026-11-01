"""Mission-level session lifecycle, proven end-to-end against the FAKE vendor CLIs (real
stream-json / ThreadEvent wire shapes, no model, no network): streamed progress lands in the
journal, rework rounds and operator follow-ups resume the recorded vendor session, operator
cancel stops the live run and pauses honestly, and a rejected resume fails closed until the
operator explicitly redispatches fresh."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from skye.adapters import base
from skye.mission import Journal, load_mission, mission_status, run_mission
from skye.policy import Policy
from skye.ui import server as ui

FAKES = Path(__file__).parent / "fakes"


@pytest.fixture
def fake_path(monkeypatch, tmp_path):
    log = tmp_path / "invocations.jsonl"
    import os
    monkeypatch.setenv("PATH", f"{FAKES}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(log))
    for var in ("FAKE_SLEEP", "FAKE_MARK", "FAKE_VERDICT_SEQUENCE", "FAKE_COUNTER"):
        monkeypatch.delenv(var, raising=False)
    return log


def _argv_lines(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def _policy():
    return Policy({"allow_unattended": [{"adapter": "claude-code", "role": "*"}],
                   "test_command": "true"})


def _mission(tmp_path, steps, **extra):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    mission = {"id": "m1", "goal": "ship the endpoint", "repo": str(ws), "steps": steps}
    mission.update(extra)
    return mission


def _one_step(**step_extra):
    step = {"id": "dev", "role": "developer", "adapter": "claude-code",
            "goal": "build it", "gates": []}
    step.update(step_extra)
    return [step]


def _journal(state_dir) -> Journal:
    return Journal(state_dir / "missions" / "m1" / "journal.jsonl")


def _wait_for(predicate, timeout=15, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ----- streaming + session capture land in the journal and status -----

def test_streamed_progress_and_session_reach_journal_and_status(fake_path, tmp_path):
    mission = _mission(tmp_path, _one_step())
    state_dir = tmp_path / "st"
    outcome = run_mission(mission, _policy(), state_dir=state_dir)
    assert outcome.status == "done", outcome.reason

    entries = _journal(state_dir).entries()
    progress = [(e.get("kind"), e.get("text")) for e in entries
                if e.get("event") == "worker-progress"]
    assert ("message", "I worked on the task.") in progress
    assert ("tool", "Read") in progress
    session = next(e for e in entries if e.get("event") == "worker-session")
    assert session["session_id"] == "sess-new-1" and session["continuity"] == "fresh"

    status = mission_status(mission, state_dir=state_dir)
    step = status["steps"][0]
    assert step["continuity"] == "fresh" and step["has_session"] is True
    assert step["followupable"] is True
    assert step["progress"] == "Read"  # the LATEST streamed event (tool use follows the reply)


# ----- a review send-back RESUMES the worker's own session with the feedback -----

def test_review_sendback_resumes_the_recorded_session(fake_path, tmp_path, monkeypatch):
    counter = tmp_path / "verdict-counter"
    monkeypatch.setenv("FAKE_VERDICT_SEQUENCE", "request-changes,pass")
    monkeypatch.setenv("FAKE_COUNTER", str(counter))
    mission = _mission(tmp_path, _one_step(gates=["review"]))
    state_dir = tmp_path / "st"
    outcome = run_mission(mission, _policy(), state_dir=state_dir)
    assert outcome.status == "done", outcome.reason

    calls = _argv_lines(fake_path)
    assert len(calls) == 4  # worker, reviewer(changes), worker redo, reviewer(pass)
    redo = calls[2]
    assert "--resume" in redo and redo[redo.index("--resume") + 1] == "sess-new-1"
    prompt = redo[redo.index("-p") + 1]
    assert "tighten the parser" in prompt  # the reviewer's feedback reaches the redo

    entries = _journal(state_dir).entries()
    assert any(e.get("event") == "review-round" for e in entries)
    sessions = [e for e in entries if e.get("event") == "worker-session"]
    assert [s["continuity"] for s in sessions] == ["fresh", "resumed"]
    status = mission_status(mission, state_dir=state_dir)
    assert status["steps"][0]["continuity"] == "resumed"


# ----- operator cancel: the live run is signalled, the step pauses, nothing is settled -----

def test_cancel_signals_the_live_run_and_pauses_needs_owner(fake_path, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "30")
    monkeypatch.setenv("FAKE_MARK", str(tmp_path / "completed-marker"))
    mission = _mission(tmp_path, _one_step())
    state_dir = tmp_path / "st"
    outcome_box: dict = {}

    def _run():
        outcome_box["outcome"] = run_mission(mission, _policy(), state_dir=state_dir)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    assert _wait_for(lambda: "m1:dev" in base._LIVE), "run never registered live"
    assert base.cancel_live("m1:dev", grace=0.5) == "signalled"
    thread.join(20)
    assert not thread.is_alive()

    outcome = outcome_box["outcome"]
    assert outcome.status == "needs-owner" and "cancelled" in outcome.reason
    assert not (tmp_path / "completed-marker").exists()  # the run was stopped mid-sleep

    entries = _journal(state_dir).entries()
    assert any(e.get("event") == "cancelled" for e in entries)
    assert not any(e.get("event") == "settled" for e in entries)
    status = mission_status(mission, state_dir=state_dir)
    step = status["steps"][0]
    assert step["state"] == "cancelled" and step["followupable"] is False
    assert status["status"] == "needs-owner"


# ----- a rejected resume fails CLOSED; only an explicit fresh redispatch recovers -----

def test_rejected_resume_fails_closed_until_fresh_redispatch(fake_path, tmp_path):
    mission = _mission(tmp_path, _one_step())
    state_dir = tmp_path / "st"
    mission_dir = state_dir / "missions" / "m1"
    mission_dir.mkdir(parents=True)
    journal = _journal(state_dir)
    journal.append("dev", "dispatched", fingerprint="fp0")
    journal.append("dev", "worker-session", adapter="claude-code",
                   session_id="bad-session", continuity="fresh",
                   cancelled=True, resume_rejected=False)
    journal.append("dev", "cancelled", reason="cancelled by the operator", role="developer")

    outcome = run_mission(mission, _policy(), state_dir=state_dir, redispatch="dev")
    assert outcome.status == "needs-owner"
    assert "did NOT start a fresh session" in outcome.reason
    calls = _argv_lines(fake_path)
    assert len(calls) == 1  # exactly one attempt, the resume - no silent fresh fallback
    assert calls[0][calls[0].index("--resume") + 1] == "bad-session"
    assert any(e.get("event") == "resume-rejected" for e in _journal(state_dir).entries())
    assert mission_status(mission, state_dir=state_dir)["status"] == "needs-owner"

    outcome = run_mission(mission, _policy(), state_dir=state_dir,
                          redispatch="dev", redispatch_fresh=True)
    assert outcome.status == "done", outcome.reason
    calls = _argv_lines(fake_path)
    assert len(calls) == 2 and "--resume" not in calls[1]  # the owner chose fresh


# ----- an operator follow-up continues the SAME session -----

def test_follow_up_continues_the_recorded_session(fake_path, tmp_path):
    mission = _mission(tmp_path, _one_step())
    state_dir = tmp_path / "st"
    outcome = run_mission(mission, _policy(), state_dir=state_dir)
    assert outcome.status == "done", outcome.reason

    mission_dir = state_dir / "missions" / "m1"
    (mission_dir / "followups.jsonl").write_text(
        json.dumps({"step": "dev", "text": "also add logging", "at": time.time()}) + "\n",
        "utf-8")
    outcome = run_mission(mission, _policy(), state_dir=state_dir)
    assert outcome.status == "done", outcome.reason

    calls = _argv_lines(fake_path)
    assert len(calls) == 2
    follow = calls[1]
    assert follow[follow.index("--resume") + 1] == "sess-new-1"
    prompt = follow[follow.index("-p") + 1]
    assert "Operator follow-up" in prompt and "also add logging" in prompt
    assert any(e.get("event") == "follow-up" for e in _journal(state_dir).entries())

    # The consumed offset holds: a third run does NOT replay the follow-up.
    outcome = run_mission(mission, _policy(), state_dir=state_dir)
    assert outcome.status == "done"
    assert len(_argv_lines(fake_path)) == 2


# ----- UI endpoints: validation and passthrough, same functions the CLI drives -----

@pytest.fixture()
def ui_server(tmp_path):
    state_dir = tmp_path / "st"
    state_dir.mkdir()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), ui.UiHandler)
    httpd.state_dir = state_dir
    httpd.token = "test-token-abc"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", state_dir
    httpd.shutdown()
    thread.join(5)


def _req(url, path, method="GET", body=None):
    request = urllib.request.Request(
        f"{url}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Skye-Token": "test-token-abc", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def _settled_mission_with_session(state_dir, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    mission_dir = state_dir / "missions" / "m1"
    mission_dir.mkdir(parents=True)
    (mission_dir / "mission.json").write_text(json.dumps(
        {"id": "m1", "goal": "ship the endpoint", "repo": str(ws),
         "steps": _one_step()}), "utf-8")
    journal = _journal(state_dir)
    journal.append("dev", "dispatched", fingerprint="fp0")
    journal.append("dev", "worker-session", adapter="claude-code",
                   session_id="sess-new-1", continuity="fresh",
                   cancelled=False, resume_rejected=False)
    journal.append("dev", "settled", status="done", reason="", output="built",
                   gates=[], worker_job="", role="developer")
    return mission_dir


def test_follow_up_endpoint_validates_then_queues(ui_server, tmp_path, monkeypatch):
    url, state_dir = ui_server
    _settled_mission_with_session(state_dir, tmp_path)
    resumed = []
    monkeypatch.setattr(ui, "resume_in_thread",
                        lambda *a, **kw: resumed.append({"args": a, "kwargs": kw}))

    status, body = _req(url, "/api/missions/m1/follow-up", method="POST",
                        body={"step": "dev"})
    assert status == 400
    status, body = _req(url, "/api/missions/m1/follow-up", method="POST",
                        body={"step": "ghost", "text": "hi"})
    assert status == 404

    status, body = _req(url, "/api/missions/m1/follow-up", method="POST",
                        body={"step": "dev", "text": "also add logging"})
    assert status == 200 and body["accepted"] is True and body["continuity"] == "resumed"
    queued = (state_dir / "missions" / "m1" / "followups.jsonl").read_text().strip()
    assert json.loads(queued)["text"] == "also add logging"
    assert len(resumed) == 1  # the queued follow-up triggers exactly one resumed run


def test_follow_up_endpoint_rejects_a_step_without_a_session(ui_server, tmp_path):
    url, state_dir = ui_server
    mission_dir = _settled_mission_with_session(state_dir, tmp_path)
    # Re-write the journal with no session record: settled but nothing to continue.
    journal = _journal(state_dir)
    (mission_dir / "journal.jsonl").write_text("", "utf-8")
    journal.append("dev", "dispatched", fingerprint="fp0")
    journal.append("dev", "settled", status="done", reason="", output="built",
                   gates=[], worker_job="", role="developer")
    status, body = _req(url, "/api/missions/m1/follow-up", method="POST",
                        body={"step": "dev", "text": "hi"})
    assert status == 409 and "no settled vendor session" in body["error"]


def test_cancel_endpoint_409_when_nothing_is_live(ui_server, tmp_path):
    url, state_dir = ui_server
    _settled_mission_with_session(state_dir, tmp_path)
    status, body = _req(url, "/api/missions/m1/cancel", method="POST", body={})
    assert status == 409 and "not running" in body["error"]


def test_redispatch_fresh_passthrough(ui_server, tmp_path, monkeypatch):
    url, state_dir = ui_server
    _settled_mission_with_session(state_dir, tmp_path)
    calls = []
    monkeypatch.setattr(ui, "resume_in_thread",
                        lambda *a, **kw: calls.append(kw))
    status, body = _req(url, "/api/missions/m1/redispatch", method="POST",
                        body={"step": "dev"})
    assert status == 200 and body["continuity"] == "resumed"
    assert calls[-1] == {"redispatch": "dev", "redispatch_fresh": False}
    status, body = _req(url, "/api/missions/m1/redispatch", method="POST",
                        body={"step": "dev", "fresh": True})
    assert status == 200 and body["continuity"] == "fresh"
    assert calls[-1] == {"redispatch": "dev", "redispatch_fresh": True}


def test_mission_graph_carries_continuity(ui_server, tmp_path):
    url, state_dir = ui_server
    _settled_mission_with_session(state_dir, tmp_path)
    status, body = _req(url, "/api/missions/m1/graph")
    assert status == 200
    node = next(n for n in body["nodes"] if n["id"] == "dev")
    assert node["continuity"] == "fresh" and node["has_session"] is True
