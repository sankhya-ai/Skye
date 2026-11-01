"""Mission-level session lifecycle, proven end-to-end against the FAKE vendor CLIs (real
stream-json / ThreadEvent wire shapes, no model, no network): streamed progress lands in the
journal, rework rounds and operator follow-ups resume the recorded vendor session, operator
cancel stops the live run and pauses honestly, and a rejected resume fails closed until the
operator explicitly redispatches fresh."""
from __future__ import annotations

import json
import os
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


def _log_lines(log: Path) -> list:
    return [json.loads(line) for line in log.read_text().splitlines()]


def _argv_lines(log: Path) -> list[list[str]]:
    # argv records are lists; {"stdin": ...} records are framed stdin messages
    return [line for line in _log_lines(log) if isinstance(line, list)]


def _stdin_frames(log: Path) -> list[str]:
    return [line["stdin"] for line in _log_lines(log) if isinstance(line, dict)]


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
    assert "--input-format" in redo  # prompts/follow-ups ride stdin frames, not argv
    prompt = _stdin_frames(fake_path)[2]
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
    assert base.cancel_live("m1:dev", grace=0.5) == "stopped"  # exit confirmed, not assumed
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
    prompt = _stdin_frames(fake_path)[1]
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


# ----- live follow-up: steered into the running process as a queued vendor message -----

def test_steer_delivers_follow_up_to_a_live_run(fake_path, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    mission = _mission(tmp_path, _one_step())
    state_dir = tmp_path / "st"
    box: dict = {}
    thread = threading.Thread(
        target=lambda: box.__setitem__(
            "outcome", run_mission(mission, _policy(), state_dir=state_dir)),
        daemon=True)
    thread.start()
    assert _wait_for(lambda: "m1:dev" in base._LIVE), "run never registered live"
    assert base.steer_live("m1:dev", "also add logging") == "steered"
    thread.join(20)
    assert not thread.is_alive()
    assert box["outcome"].status == "done", box["outcome"].reason

    # one process, two turns: the follow-up ran INSIDE the live session, queued between
    # turns - no second process, no new session
    assert len(_argv_lines(fake_path)) == 1
    assert _stdin_frames(fake_path)[1] == "also add logging"
    progress = [e.get("text") for e in _journal(state_dir).entries()
                if e.get("event") == "worker-progress"]
    assert "Follow-up received: also add logging" in progress
    settled = next(e for e in _journal(state_dir).entries() if e.get("event") == "settled")
    assert "Follow-up received: also add logging" in settled["output"]


def test_steer_after_the_final_turn_reports_undelivered(fake_path, tmp_path):
    # No sleep: by the time steer is attempted the run is closing - the honest answer is
    # not-live/exiting, never a pretend delivery.
    mission = _mission(tmp_path, _one_step())
    outcome = run_mission(mission, _policy(), state_dir=tmp_path / "st")
    assert outcome.status == "done"
    assert base.steer_live("m1:dev", "too late") == "not-live"


def test_codex_exec_reports_live_steer_unsupported(fake_path, tmp_path, monkeypatch):
    # Grounded in codex-rs/exec (one-shot, no stdin input channel): the exec transport
    # has no live-input mechanism, so steering reports unsupported there even though
    # the app-server transport supports turn/steer.
    from skye.adapters.codex import CodexAdapter
    monkeypatch.setenv("CODEX_PROTOCOL", "exec")
    monkeypatch.setenv("FAKE_SLEEP", "5")
    adapter = CodexAdapter()
    box: dict = {}
    contract = {"repo": str(tmp_path), "goal": "work", "brief": "work"}
    thread = threading.Thread(
        target=lambda: box.__setitem__(
            "result", adapter.run_session(contract, run_key="m1:dev")), daemon=True)
    thread.start()
    assert _wait_for(lambda: "m1:dev" in base._LIVE)
    assert base.steer_live("m1:dev", "ping") == "unsupported"
    assert base.cancel_live("m1:dev", grace=0.2) == "stopped"
    thread.join(10)
    assert box["result"].cancelled is True


# ----- cancel: the whole process group dies, and death is confirmed -----

def test_cancel_reaps_descendants_and_confirms_death(fake_path, tmp_path, monkeypatch):
    child_pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("FAKE_SLEEP", "30")
    monkeypatch.setenv("FAKE_SPAWN_CHILD", "1")
    monkeypatch.setenv("FAKE_CHILD_PID", str(child_pid_file))
    mission = _mission(tmp_path, _one_step())
    box: dict = {}
    thread = threading.Thread(
        target=lambda: box.__setitem__(
            "outcome", run_mission(mission, _policy(), state_dir=tmp_path / "st")),
        daemon=True)
    thread.start()
    assert _wait_for(lambda: child_pid_file.exists()), "grandchild never spawned"
    child_pid = int(child_pid_file.read_text().strip())
    leader_pid = base._LIVE["m1:dev"]["proc"].pid
    assert base.cancel_live("m1:dev", grace=0.5) == "stopped"
    thread.join(20)
    assert not thread.is_alive()
    for pid in (leader_pid, child_pid):
        with pytest.raises(ProcessLookupError):
            import os
            os.kill(pid, 0)  # both the CLI and its descendant are verifiably gone
    assert box["outcome"].status == "needs-owner"


# ----- cancel marker: only an explicit owner action clears it -----

def test_cancel_marker_blocks_any_run_until_explicitly_cleared(fake_path, tmp_path):
    mission = _mission(tmp_path, _one_step())
    state_dir = tmp_path / "st"
    mission_dir = state_dir / "missions" / "m1"
    mission_dir.mkdir(parents=True)
    (mission_dir / "cancel-requested").write_text("{}\n", "utf-8")

    outcome = run_mission(mission, _policy(), state_dir=state_dir)
    assert outcome.status == "needs-owner" and "explicit resume" in outcome.reason
    assert (mission_dir / "cancel-requested").exists()  # NOT consumed by the refused run
    assert not fake_path.exists()  # nothing dispatched

    outcome = run_mission(mission, _policy(), state_dir=state_dir, clear_cancel=True)
    assert outcome.status == "done", outcome.reason
    assert not (mission_dir / "cancel-requested").exists()
    assert any(e.get("event") == "cancel-cleared"
               for e in _journal(state_dir).entries())


# ----- crash windows around the follow-up queue -----

def test_crash_after_intent_before_settle_pauses_without_replay(fake_path, tmp_path):
    # Window: the follow-up was journaled (intent) and possibly reached the worker, but
    # no terminal entry landed. Transcript continuation cannot prove what ran, so the
    # engine fails CLOSED: needs-owner, text preserved, no replayed dispatch.
    mission = _mission(tmp_path, _one_step())
    state_dir = tmp_path / "st"
    assert run_mission(mission, _policy(), state_dir=state_dir).status == "done"
    mission_dir = state_dir / "missions" / "m1"
    (mission_dir / "followups.jsonl").write_text(
        json.dumps({"step": "dev", "text": "also add logging", "at": 1}) + "\n", "utf-8")
    _journal(state_dir).append("dev", "follow-up", line=0, text="also add logging")

    outcome = run_mission(mission, _policy(), state_dir=state_dir)
    assert outcome.status == "needs-owner"
    assert "will NOT replay" in outcome.reason and "also add logging" not in outcome.reason
    assert len(_argv_lines(fake_path)) == 1  # the first run only - no replay
    # the preserved text stays in the journal for the operator
    assert any(e.get("event") == "follow-up" and e.get("text") == "also add logging"
               for e in _journal(state_dir).entries())

    # the documented escape: the operator redispatches the step (a terminal event), and
    # the offset then heals past the dangling line instead of replaying it
    outcome = run_mission(mission, _policy(), state_dir=state_dir, redispatch="dev")
    assert outcome.status == "done", outcome.reason
    outcome = run_mission(mission, _policy(), state_dir=state_dir)
    assert outcome.status == "done"
    assert (mission_dir / "followups.consumed").read_text().strip() == "1"
    assert len(_argv_lines(fake_path)) == 2  # redispatch ran once; nothing replayed after


def test_consumed_offset_self_heals_when_terminal_already_journaled(fake_path, tmp_path):
    # Window: follow-up ran AND settled, crash hit before the offset write. The journal
    # proves the terminal, so the offset heals forward - the follow-up is NOT re-run.
    mission = _mission(tmp_path, _one_step())
    state_dir = tmp_path / "st"
    assert run_mission(mission, _policy(), state_dir=state_dir).status == "done"
    mission_dir = state_dir / "missions" / "m1"
    (mission_dir / "followups.jsonl").write_text(
        json.dumps({"step": "dev", "text": "also add logging", "at": 1}) + "\n", "utf-8")
    journal = _journal(state_dir)
    journal.append("dev", "follow-up", line=0, text="also add logging")
    journal.append("dev", "settled", status="done", reason="", output="with logging",
                   gates=[], worker_job="", role="developer")

    outcome = run_mission(mission, _policy(), state_dir=state_dir)
    assert outcome.status == "done"
    assert len(_argv_lines(fake_path)) == 1  # settled once already - no second dispatch
    assert (mission_dir / "followups.consumed").read_text().strip() == "1"


# ----- crash-surviving worker: never a duplicate process against one session -----

def test_prior_worker_still_alive_blocks_a_duplicate(fake_path, tmp_path):
    import os
    import subprocess as sp
    mission = _mission(tmp_path, _one_step())
    state_dir = tmp_path / "st"
    mission_dir = state_dir / "missions" / "m1"
    mission_dir.mkdir(parents=True)
    # Simulate a worker that survived a server crash: live record with a REAL live pid.
    survivor = sp.Popen(["sleep", "60"])
    try:
        (mission_dir / "live-dev.json").write_text(
            json.dumps({"pid": survivor.pid, "run_key": "m1:dev"}) + "\n", "utf-8")
        outcome = run_mission(mission, _policy(), state_dir=state_dir)
        assert outcome.status == "needs-owner"
        assert "will not start a duplicate" in outcome.reason
        assert not fake_path.exists()  # nothing spawned
    finally:
        survivor.kill()
        survivor.wait()

    # Once the survivor is verifiably gone, the stale record is cleared and the run goes.
    outcome = run_mission(mission, _policy(), state_dir=state_dir)
    assert outcome.status == "done", outcome.reason
    assert len(_argv_lines(fake_path)) == 1


def test_unreadable_live_record_fails_closed(fake_path, tmp_path):
    mission = _mission(tmp_path, _one_step())
    state_dir = tmp_path / "st"
    mission_dir = state_dir / "missions" / "m1"
    mission_dir.mkdir(parents=True)
    (mission_dir / "live-dev.json").write_text("not json\n", "utf-8")
    outcome = run_mission(mission, _policy(), state_dir=state_dir)
    assert outcome.status == "needs-owner" and "unreadable" in outcome.reason
    assert not fake_path.exists()


# ----- display hygiene: session identity never leaks through UI payloads -----

def test_display_payloads_hide_session_id_and_mask_paths(ui_server, tmp_path):
    url, state_dir = ui_server
    mission_dir = _settled_mission_with_session(state_dir, tmp_path)
    journal = _journal(state_dir)
    journal.append("dev", "worker-session", adapter="claude-code",
                   session_id="/home/someone/.claude/projects/p/sess.jsonl",
                   continuity="resumed", cancelled=False, resume_rejected=False)

    status, body = _req(url, "/api/missions/m1")
    assert status == 200
    rendered = json.dumps(body)
    assert ".claude/projects" not in rendered  # path-shaped session id masked
    assert "sess-new-1" not in json.dumps(body["status"])  # status carries presence, not ids
    timeline_session = [e for e in body["journal"] if e.get("event") == "worker-session"]
    assert any("(recorded; path-shaped value masked)" == e.get("session_id")
               for e in timeline_session)

    status, graph = _req(url, "/api/missions/m1/graph")
    assert status == 200
    assert "sess-new-1" not in json.dumps(graph)
    assert "sess.jsonl" not in json.dumps(graph)


# ----- the UI endpoint steers a live run end to end -----

def test_follow_up_endpoint_steers_a_live_run(ui_server, tmp_path, monkeypatch, fake_path):
    url, state_dir = ui_server
    monkeypatch.setenv("FAKE_SLEEP", "1")
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps(
        {"allow_unattended": [{"adapter": "claude-code", "role": "*"}],
         "test_command": "true"}), "utf-8")
    monkeypatch.setenv("SKYE_POLICY", str(policy_file))
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    mission_dir = state_dir / "missions" / "m1"
    mission_dir.mkdir(parents=True)
    (mission_dir / "mission.json").write_text(json.dumps(
        {"id": "m1", "goal": "ship it", "repo": str(ws), "steps": _one_step()}), "utf-8")

    status, body = _req(url, "/api/missions/m1/resume", method="POST", body={})
    assert status == 200 and body["resumed"] is True
    assert _wait_for(lambda: "m1:dev" in base._LIVE), "run never registered live"
    status, body = _req(url, "/api/missions/m1/follow-up", method="POST",
                        body={"step": "dev", "text": "also add logging"})
    # "submitted" = handed to the live process; consumption is the provider's own
    # later event (steer-ack), never implied by the write.
    assert status == 200 and body["delivery"] == "submitted", body

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        _, detail = _req(url, "/api/missions/m1")
        if not detail["live"].get("running"):
            break
        time.sleep(0.1)
    _, detail = _req(url, "/api/missions/m1")
    assert detail["status"]["status"] == "done", detail["status"]
    follow_ups = [e for e in _journal(state_dir).entries()
                  if e.get("event") == "follow-up"]
    assert follow_ups and follow_ups[0]["delivery"] == "steered-live"
    assert follow_ups[0]["state"] == "intent" and follow_ups[0]["follow_id"]
    progress = [e.get("text") for e in _journal(state_dir).entries()
                if e.get("event") == "worker-progress"]
    assert "Follow-up received: also add logging" in progress


# ----- live-steer durability: intent without a confirmed outcome pauses the owner -----

def test_live_steer_intent_without_outcome_pauses_with_text_preserved(fake_path, tmp_path):
    # Window: the server journaled the live-steer intent, then died before recording
    # submitted/undelivered. The follow-up may or may not have reached the worker, so
    # the engine fails CLOSED: needs-owner, text preserved, no silent either-way guess.
    mission = _mission(tmp_path, _one_step())
    state_dir = tmp_path / "st"
    assert run_mission(mission, _policy(), state_dir=state_dir).status == "done"
    _journal(state_dir).append("dev", "follow-up", follow_id="f1",
                               text="also add logging", delivery="steered-live",
                               state="intent")

    outcome = run_mission(mission, _policy(), state_dir=state_dir)
    assert outcome.status == "needs-owner"
    assert "unconfirmed" in outcome.reason or "unknown" in outcome.reason
    assert "also add logging" in outcome.reason  # text preserved for the operator
    assert len(_argv_lines(fake_path)) == 1  # nothing redispatched

    # the documented escape: operator redispatch (a terminal entry) resolves the scan
    outcome = run_mission(mission, _policy(), state_dir=state_dir, redispatch="dev")
    assert outcome.status == "done", outcome.reason


def test_live_steer_submitted_without_receipt_or_settle_pauses(fake_path, tmp_path):
    # Submitted to the process, but no provider receipt (steer-ack) and no terminal
    # step record: delivery is still unconfirmed - pause, never claim consumption.
    mission = _mission(tmp_path, _one_step())
    state_dir = tmp_path / "st"
    assert run_mission(mission, _policy(), state_dir=state_dir).status == "done"
    journal = _journal(state_dir)
    journal.append("dev", "follow-up", follow_id="f1", text="tweak it",
                   delivery="steered-live", state="intent")
    journal.append("dev", "follow-up-submitted", follow_id="f1")

    outcome = run_mission(mission, _policy(), state_dir=state_dir)
    assert outcome.status == "needs-owner" and "submitted but unconfirmed" in outcome.reason

    # a bare terminal entry is NOT a receipt: settling the step afterwards must not
    # clear the unconfirmed submission
    journal.append("dev", "settled", status="done", reason="", output="tweaked",
                   gates=[], worker_job="", role="developer")
    outcome = run_mission(mission, _policy(), state_dir=state_dir)
    assert outcome.status == "needs-owner", outcome.status

    # only the provider-correlated receipt (steer-ack carrying THIS follow-up's text)
    # proves consumption
    journal.append("dev", "worker-progress", kind="steer-ack", text="tweak it")
    outcome = run_mission(mission, _policy(), state_dir=state_dir)
    assert outcome.status == "done", outcome.reason


def test_one_receipt_never_clears_two_concurrent_submits(fake_path, tmp_path):
    mission = _mission(tmp_path, _one_step())
    state_dir = tmp_path / "st"
    assert run_mission(mission, _policy(), state_dir=state_dir).status == "done"
    journal = _journal(state_dir)
    journal.append("dev", "follow-up", follow_id="f1", text="same words",
                   delivery="steered-live", state="intent")
    journal.append("dev", "follow-up-submitted", follow_id="f1")
    journal.append("dev", "follow-up", follow_id="f2", text="same words",
                   delivery="steered-live", state="intent")
    journal.append("dev", "follow-up-submitted", follow_id="f2")
    # ONE provider receipt for that text: it clears the FIRST submission only.
    journal.append("dev", "worker-progress", kind="steer-ack", text="same words")
    outcome = run_mission(mission, _policy(), state_dir=state_dir)
    assert outcome.status == "needs-owner" and "unconfirmed" in outcome.reason
    # the second receipt clears the second
    journal.append("dev", "worker-progress", kind="steer-ack", text="same words")
    assert run_mission(mission, _policy(), state_dir=state_dir).status == "done"


def test_unrelated_steer_ack_does_not_resolve_a_submission(fake_path, tmp_path):
    mission = _mission(tmp_path, _one_step())
    state_dir = tmp_path / "st"
    assert run_mission(mission, _policy(), state_dir=state_dir).status == "done"
    journal = _journal(state_dir)
    journal.append("dev", "follow-up", follow_id="f1", text="add logging",
                   delivery="steered-live", state="intent")
    journal.append("dev", "follow-up-submitted", follow_id="f1")
    # an ack for DIFFERENT text (unrelated provider activity) must not resolve f1
    journal.append("dev", "worker-progress", kind="steer-ack",
                   text="some other echoed message")
    outcome = run_mission(mission, _policy(), state_dir=state_dir)
    assert outcome.status == "needs-owner" and "add logging" in outcome.reason


def test_accepted_but_unapplied_steer_pauses_as_not_consumed(fake_path, tmp_path):
    # The provider ACCEPTED the steer (turn id returned) but the turn ended before the
    # input was applied: not consumed - pause with the exact reason.
    mission = _mission(tmp_path, _one_step())
    state_dir = tmp_path / "st"
    assert run_mission(mission, _policy(), state_dir=state_dir).status == "done"
    journal = _journal(state_dir)
    journal.append("dev", "follow-up", follow_id="f1", text="add logging",
                   delivery="steered-live", state="intent")
    journal.append("dev", "follow-up-submitted", follow_id="f1")
    journal.append("dev", "worker-progress", kind="steer-accepted", text="add logging")
    journal.append("dev", "worker-progress", kind="steer-unapplied", text="add logging")
    journal.append("dev", "settled", status="done", reason="", output="x",
                   gates=[], worker_job="", role="developer")
    outcome = run_mission(mission, _policy(), state_dir=state_dir)
    assert outcome.status == "needs-owner"
    assert "did NOT consume" in outcome.reason and "add logging" in outcome.reason


def test_concurrent_cancel_calls_never_crash_and_agree_on_death(fake_path, tmp_path,
                                                                monkeypatch):
    # Two owners hit cancel at once: both calls must return a verified state (stopped /
    # exiting / not-live / unkillable) - no exception, no pretend confirmation.
    from skye.adapters import base
    from skye.adapters.claude_code import ClaudeCodeAdapter
    monkeypatch.setenv("FAKE_SLEEP", "30")
    adapter = ClaudeCodeAdapter()
    box: dict = {}
    thread = threading.Thread(target=lambda: box.__setitem__(
        "result", adapter.run_session(
            {"repo": str(tmp_path), "goal": "work", "brief": "work"},
            run_key="m1:dev")),
        daemon=True)
    thread.start()
    assert _wait_for(lambda: "m1:dev" in base._LIVE)
    results: list = []
    threads = [threading.Thread(target=lambda: results.append(
        base.cancel_live("m1:dev"))) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    thread.join(timeout=10)
    assert len(results) == 2
    assert all(r in ("stopped", "exiting", "not-live", "unkillable") for r in results)
    assert "stopped" in results or "exiting" in results or "not-live" in results
    # a third cancel after death is honest about it
    assert base.cancel_live("m1:dev") in ("not-live", "exiting")


def test_resume_endpoint_names_interrupted_steps_and_supported_actions(ui_server,
                                                                       tmp_path,
                                                                       monkeypatch):
    # After an operator cancel the step's journal state is "cancelled": a generic
    # resume clears the marker but cannot restart that step's CLI. The endpoint must
    # say so and name the supported per-step redispatch actions - no silent pretend
    # continuation.
    url, state_dir = ui_server
    _settled_mission_with_session(state_dir, tmp_path)
    journal = _journal(state_dir)
    (state_dir / "missions" / "m1" / "journal.jsonl").write_text("", "utf-8")
    journal.append("dev", "dispatched", fingerprint="fp0")
    journal.append("dev", "cancelled", reason="operator stop", role="developer")
    resumed = []
    monkeypatch.setattr(ui, "resume_in_thread",
                        lambda *a, **kw: resumed.append(True))

    status, body = _req(url, "/api/missions/m1/resume", method="POST", body={})
    assert status == 200 and body["resumed"] is True
    assert body["interrupted"] == ["dev"]
    actions = {(a["action"], a["step"], a["fresh"]) for a in body["supported_actions"]}
    assert actions == {("redispatch", "dev", False), ("redispatch", "dev", True)}
    assert len(resumed) == 1

    # no interrupted steps: the plain response, no noise
    (state_dir / "missions" / "m1" / "journal.jsonl").write_text("", "utf-8")
    _journal(state_dir).append("dev", "settled", status="done", reason="", output="x",
                               gates=[], worker_job="", role="developer")
    status, body = _req(url, "/api/missions/m1/resume", method="POST", body={})
    assert status == 200 and "interrupted" not in body


def test_stale_or_early_receipt_never_resolves_a_submission(fake_path, tmp_path):
    # An ack positioned BEFORE the submission (a stale echo from an earlier journey)
    # must not resolve it: receipts only count after their own submitted entry.
    mission = _mission(tmp_path, _one_step())
    state_dir = tmp_path / "st"
    assert run_mission(mission, _policy(), state_dir=state_dir).status == "done"
    journal = _journal(state_dir)
    journal.append("dev", "worker-progress", kind="steer-ack", text="add logging")
    journal.append("dev", "follow-up", follow_id="f1", text="add logging",
                   delivery="steered-live", state="intent")
    journal.append("dev", "follow-up-submitted", follow_id="f1")
    outcome = run_mission(mission, _policy(), state_dir=state_dir)
    assert outcome.status == "needs-owner" and "unconfirmed" in outcome.reason
    # and the REAL receipt, arriving after, resolves exactly it
    journal.append("dev", "worker-progress", kind="steer-ack", text="add logging")
    assert run_mission(mission, _policy(), state_dir=state_dir).status == "done"


def test_follow_up_endpoint_serializes_live_steers(ui_server, tmp_path, monkeypatch):
    # A second live steer while one awaits its provider receipt is REJECTED (409):
    # identical texts could not be told apart by echo correlation, so ambiguity is
    # refused up front rather than given a false exact-consumption guarantee.
    url, state_dir = ui_server
    _settled_mission_with_session(state_dir, tmp_path)
    journal = _journal(state_dir)
    journal.append("dev", "follow-up", follow_id="f1", text="add logging",
                   delivery="steered-live", state="intent")
    journal.append("dev", "follow-up-submitted", follow_id="f1")
    monkeypatch.setattr(ui, "_live_missions", {"m1": {"running": True}})
    status, body = _req(url, "/api/missions/m1/follow-up", method="POST",
                        body={"step": "dev", "text": "add logging"})
    assert status == 409 and "awaiting its provider receipt" in body["error"], body
    # once the receipt lands, the next steer is accepted
    journal.append("dev", "worker-progress", kind="steer-ack", text="add logging")
    monkeypatch.setattr("skye.adapters.base.steer_live", lambda *a: "steered")
    status, body = _req(url, "/api/missions/m1/follow-up", method="POST",
                        body={"step": "dev", "text": "add logging"})
    assert status == 200 and body["delivery"] == "submitted", body


def test_two_simultaneous_live_steers_exactly_one_accepted(ui_server, tmp_path,
                                                           monkeypatch):
    # Concurrency guard, not button-disable: two HTTP submits racing the same running
    # step get exactly ONE acceptance; the loser gets 409 with its input preserved.
    url, state_dir = ui_server
    _settled_mission_with_session(state_dir, tmp_path)
    monkeypatch.setattr(ui, "_live_missions", {"m1": {"running": True}})
    barrier = threading.Barrier(2)

    def _slow_steer(*_a):
        time.sleep(0.2)  # widen the overlap window: both requests are in flight
        return "steered"

    monkeypatch.setattr("skye.adapters.base.steer_live", _slow_steer)
    outcomes: list = []

    def _post():
        barrier.wait(timeout=10)  # release both HTTP submits at the same instant
        outcomes.append(_req(url, "/api/missions/m1/follow-up", method="POST",
                             body={"step": "dev", "text": "also add logging"}))

    threads = [threading.Thread(target=_post), threading.Thread(target=_post)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    codes = sorted(status for status, _ in outcomes)
    assert codes == [200, 409], outcomes
    loser = next(body for status, body in outcomes if status == 409)
    assert loser["preserved_text"] == "also add logging"


def test_marker_takeover_refused_while_owner_alive(ui_server, tmp_path, monkeypatch):
    # No age-only takeover: a claim with no journaled intent and a LIVE owner blocks
    # indefinitely (fail closed) - only a proven-dead owner releases it.
    url, state_dir = ui_server
    _settled_mission_with_session(state_dir, tmp_path)
    monkeypatch.setattr(ui, "_live_missions", {"m1": {"running": True}})
    marker = state_dir / "missions" / "m1" / "steer-pending-dev"
    marker.write_text(json.dumps({"follow_id": "f-old", "owner_pid": os.getpid(),
                                  "run_key": "m1:dev", "created": time.time() - 9999}))
    status, body = _req(url, "/api/missions/m1/follow-up", method="POST",
                        body={"step": "dev", "text": "also add logging"})
    assert status == 409 and "in flight" in body["error"]
    assert body["preserved_text"] == "also add logging"
    assert marker.exists()  # untouched

    # a DEAD owner cannot journal or send later: takeover is safe
    marker.write_text(json.dumps({"follow_id": "f-old", "owner_pid": 2**22 + 424242,
                                  "run_key": "m1:dev", "created": time.time() - 9999}))
    monkeypatch.setattr("skye.adapters.base.steer_live", lambda *a: "steered")
    status, body = _req(url, "/api/missions/m1/follow-up", method="POST",
                        body={"step": "dev", "text": "also add logging"})
    assert status == 200 and body["delivery"] == "submitted", body
    claim = json.loads(marker.read_text())
    assert claim["follow_id"] != "f-old" and claim["owner_pid"] == os.getpid()


def test_unrelated_step_activity_never_releases_a_marker(ui_server, tmp_path,
                                                         monkeypatch):
    url, state_dir = ui_server
    _settled_mission_with_session(state_dir, tmp_path)
    monkeypatch.setattr(ui, "_live_missions", {"m1": {"running": True}})
    journal = _journal(state_dir)
    journal.append("dev", "follow-up", follow_id="f1", text="add logging",
                   delivery="steered-live", state="intent")
    journal.append("dev", "follow-up-submitted", follow_id="f1")
    (state_dir / "missions" / "m1" / "steer-pending-dev").write_text(
        json.dumps({"follow_id": "f1", "owner_pid": os.getpid(),
                    "run_key": "m1:dev", "created": time.time()}))
    # another step's terminal + ack, plus a dev ack for DIFFERENT text: none resolve f1
    journal.append("qa", "settled", status="done", reason="", output="x",
                   gates=[], worker_job="", role="reviewer")
    journal.append("qa", "worker-progress", kind="steer-ack", text="add logging")
    journal.append("dev", "worker-progress", kind="steer-ack", text="different text")
    status, body = _req(url, "/api/missions/m1/follow-up", method="POST",
                        body={"step": "dev", "text": "more work"})
    assert status == 409 and "awaiting its provider receipt" in body["error"]


def test_failed_steer_never_deletes_a_reclaimed_marker(ui_server, tmp_path,
                                                       monkeypatch):
    # The losing handler's cleanup is conditional: it must not remove a marker that a
    # NEW owner reclaimed mid-flight.
    url, state_dir = ui_server
    _settled_mission_with_session(state_dir, tmp_path)
    monkeypatch.setattr(ui, "_live_missions", {"m1": {"running": True}})
    marker = state_dir / "missions" / "m1" / "steer-pending-dev"

    def _steal_then_fail(*_a):
        marker.write_text(json.dumps({"follow_id": "f-new-owner",
                                      "owner_pid": os.getpid(),
                                      "run_key": "m1:dev",
                                      "created": time.time()}))
        return "exiting"

    monkeypatch.setattr("skye.adapters.base.steer_live", _steal_then_fail)
    status, body = _req(url, "/api/missions/m1/follow-up", method="POST",
                        body={"step": "dev", "text": "also add logging"})
    assert status != 200
    assert json.loads(marker.read_text())["follow_id"] == "f-new-owner"


def test_delayed_prior_generation_receipt_never_releases_claim(ui_server, tmp_path,
                                                                monkeypatch):
    # A same-text ack journaled AFTER a newer dispatch belongs to the old process's
    # generation: it must not release the new claim.
    url, state_dir = ui_server
    _settled_mission_with_session(state_dir, tmp_path)
    monkeypatch.setattr(ui, "_live_missions", {"m1": {"running": True}})
    journal = _journal(state_dir)
    journal.append("dev", "follow-up", follow_id="f1", text="add logging",
                   delivery="steered-live", state="intent")
    journal.append("dev", "follow-up-submitted", follow_id="f1")
    journal.append("dev", "dispatched", fingerprint="fp1")  # a NEWER generation began
    journal.append("dev", "worker-progress", kind="steer-ack", text="add logging")
    (state_dir / "missions" / "m1" / "steer-pending-dev").write_text(
        json.dumps({"follow_id": "f1", "owner_pid": os.getpid(),
                    "run_key": "m1:dev", "created": time.time()}))
    status, body = _req(url, "/api/missions/m1/follow-up", method="POST",
                        body={"step": "dev", "text": "add logging"})
    assert status == 409, body
    # engine side: same boundary - the scan must still call f1 unconfirmed
    mission = _mission(tmp_path, _one_step())
    outcome = run_mission(mission, _policy(), state_dir=state_dir)
    assert outcome.status == "needs-owner", outcome.status


def test_unsubmitted_intent_blocks_new_posts(ui_server, tmp_path, monkeypatch):
    # Intent journaled but submission never recorded: the follow-up may have reached
    # the worker. New sends fail closed until the operator resolves it.
    url, state_dir = ui_server
    _settled_mission_with_session(state_dir, tmp_path)
    monkeypatch.setattr(ui, "_live_missions", {"m1": {"running": True}})
    _journal(state_dir).append("dev", "follow-up", follow_id="f1", text="risky",
                               delivery="steered-live", state="intent")
    status, body = _req(url, "/api/missions/m1/follow-up", method="POST",
                        body={"step": "dev", "text": "more"})
    assert status == 409 and "UNKNOWN" in body["error"]
    assert body["preserved_text"] == "more"


def test_post_intent_exception_preserves_claim_and_blocks_retry(ui_server, tmp_path,
                                                                monkeypatch):
    # A crash AFTER the intent journaled (here: the submitted-record write fails)
    # keeps the claim and the intent: no cleanup-and-retry into a possible duplicate.
    url, state_dir = ui_server
    _settled_mission_with_session(state_dir, tmp_path)
    monkeypatch.setattr(ui, "_live_missions", {"m1": {"running": True}})
    marker = state_dir / "missions" / "m1" / "steer-pending-dev"
    from skye import mission as _m
    original = _m.Journal.append
    calls = {"n": 0}

    def _flaky(self, step, event, **fields):
        calls["n"] += 1
        if event == "follow-up-submitted":
            raise OSError("disk full")
        return original(self, step, event, **fields)

    monkeypatch.setattr("skye.adapters.base.steer_live", lambda *a: "steered")
    monkeypatch.setattr(_m.Journal, "append", _flaky)
    import http.client
    import urllib.error
    try:
        _req(url, "/api/missions/m1/follow-up", method="POST",
             body={"step": "dev", "text": "also add logging"})
        assert False, "the handler must surface the failure, not swallow it"
    except (urllib.error.URLError, urllib.error.HTTPError,
            http.client.RemoteDisconnected, ConnectionError, OSError):
        pass  # the write failure surfaced as a dropped/failed response - not a 200
    monkeypatch.setattr(_m.Journal, "append", original)
    # claim + intent preserved; a retry fails CLOSED (unknown submission state)
    assert marker.exists()
    status, body = _req(url, "/api/missions/m1/follow-up", method="POST",
                        body={"step": "dev", "text": "also add logging"})
    assert status == 409, body


def test_other_followups_undelivered_does_not_clear_claim(ui_server, tmp_path,
                                                          monkeypatch):
    url, state_dir = ui_server
    _settled_mission_with_session(state_dir, tmp_path)
    monkeypatch.setattr(ui, "_live_missions", {"m1": {"running": True}})
    journal = _journal(state_dir)
    journal.append("dev", "follow-up", follow_id="f1", text="add logging",
                   delivery="steered-live", state="intent")
    journal.append("dev", "follow-up-submitted", follow_id="f1")
    journal.append("dev", "follow-up-undelivered", follow_id="f-OTHER",
                   reason="exiting")
    (state_dir / "missions" / "m1" / "steer-pending-dev").write_text(
        json.dumps({"follow_id": "f1", "owner_pid": os.getpid(),
                    "run_key": "m1:dev", "created": time.time()}))
    status, body = _req(url, "/api/missions/m1/follow-up", method="POST",
                        body={"step": "dev", "text": "more"})
    assert status == 409 and "awaiting its provider receipt" in body["error"]
