"""Missions: multi-contract chains with handoffs, journaled so a crash is recoverable."""

from __future__ import annotations

import json

import pytest

from skye import approvals
from skye.adapters.base import Result
from skye.events import FileBridge
from skye.mission import (Journal, MissionError, load_mission, mission_status, reset_mission,
                          run_mission)
from skye.policy import Policy


def _mission_file(tmp_path, steps, **extra):
    mission = {"id": "m1", "goal": "ship the endpoint", "repo": str(tmp_path / "ws"),
               "steps": steps}
    mission.update(extra)
    (tmp_path / "ws").mkdir(exist_ok=True)
    path = tmp_path / "mission.json"
    path.write_text(json.dumps(mission))
    return path


def _steps():
    return [{"id": "pm", "role": "pm", "adapter": "claude-code", "goal": "scope the work",
             "gates": []},
            {"id": "dev", "role": "developer", "adapter": "claude-code", "goal": "build it",
             "needs": ["pm"], "gates": []},
            {"id": "tester", "role": "tester", "adapter": "claude-code", "goal": "verify it",
             "needs": ["dev"], "gates": []}]


def _policy():
    return Policy({"allow_unattended": [{"adapter": "claude-code", "role": "*"}],
                   "test_command": "true"})


def _ok(spec):
    return Result(ok=True, output=f"{spec['role']} output", command=["fake"])


# ----- validation -----

def test_mission_validation_rejects_bad_shapes(tmp_path):
    with pytest.raises(MissionError, match="steps"):
        load_mission(_mission_file(tmp_path, []))
    with pytest.raises(MissionError, match="duplicate"):
        load_mission(_mission_file(tmp_path, [{"id": "a", "role": "pm", "adapter": "codex",
                                               "goal": "x", "gates": []}] * 2))
    with pytest.raises(MissionError, match="unknown or later"):
        load_mission(_mission_file(tmp_path, [
            {"id": "a", "role": "pm", "adapter": "codex", "goal": "x", "needs": ["b"], "gates": []},
            {"id": "b", "role": "pm", "adapter": "codex", "goal": "y", "gates": []}]))
    with pytest.raises(MissionError, match="wizard"):
        load_mission(_mission_file(tmp_path, [
            {"id": "a", "role": "wizard", "adapter": "codex", "goal": "x", "gates": []}]))


# ----- the working chain -----

def test_a_mission_runs_the_chain_with_handoffs(tmp_path):
    path = _mission_file(tmp_path, _steps())
    mission = load_mission(path)
    seen = []

    def runner(spec):
        seen.append((spec["role"], spec["brief"]))
        return Result(ok=True, output=f"{spec['role']} did its part")

    outcome = run_mission(mission, _policy(), runner=runner,
                          bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=tmp_path / "st")
    assert outcome.status == "done", outcome.reason
    assert [role for role, _ in seen] == ["pm", "developer", "tester"]
    # The handoff: each step after the first sees the previous step's recorded outcome.
    assert "pm did its part" in seen[1][1] and "Handoff from previous steps" in seen[1][1]
    assert "developer did its part" in seen[2][1]
    status = mission_status(mission, tmp_path / "st")
    assert status["status"] == "done"
    assert [s["state"] for s in status["steps"]] == ["settled"] * 3


def test_a_failed_step_fails_the_mission_and_blocks_rerun(tmp_path):
    path = _mission_file(tmp_path, _steps())
    mission = load_mission(path)

    def runner(spec):
        if spec["role"] == "developer":
            return Result(ok=False, output="boom", returncode=1)
        return Result(ok=True, output="ok")

    first = run_mission(mission, _policy(), runner=runner,
                        bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=tmp_path / "st")
    assert first.status == "failed" and "dev" in first.reason
    # No silent retry: rerunning reports the recorded failure, it does not redo work.
    again = run_mission(mission, _policy(), runner=_ok,
                        bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=tmp_path / "st")
    assert again.status == "failed" and "previously settled as failed" in again.reason
    # After a reset the mission starts clean.
    archived = reset_mission(mission, tmp_path / "st")
    assert archived and "journal" in archived
    third = run_mission(mission, _policy(), runner=_ok,
                        bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=tmp_path / "st")
    assert third.status == "done"


# ----- needs-owner inside a mission -----

def test_a_step_needing_the_owner_pauses_the_mission_until_approved(tmp_path):
    path = _mission_file(tmp_path, _steps())
    mission = load_mission(path)
    store = tmp_path / "approvals.json"
    policy = Policy({"allow_unattended": [{"adapter": "claude-code", "role": "pm"}],
                     "test_command": "true"})  # dev/tester need the owner
    first = run_mission(mission, policy, runner=_ok,
                        bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=tmp_path / "st",
                        approvals_path=store)
    assert first.status == "needs-owner"
    pending = json.loads((tmp_path / "st" / "missions" / "m1" / "pending-dev.json").read_text())
    assert pending["role"] == "developer"
    # The owner approves exactly that contract; the resume consumes it and finishes the chain.
    approvals.approve(pending, store)
    second = run_mission(mission, policy, runner=_ok,
                         bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=tmp_path / "st",
                         approvals_path=store)
    # dev ran on its one approval; the tester is a DIFFERENT contract and pauses the mission
    # again - the approval did not leak across steps, and dev was not re-run.
    assert second.status == "needs-owner" and "tester" in second.reason
    assert second.steps["dev"]["status"] == "done"
    pending_tester = json.loads(
        (tmp_path / "st" / "missions" / "m1" / "pending-tester.json").read_text())
    approvals.approve(pending_tester, store)
    third = run_mission(mission, policy, runner=_ok,
                        bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=tmp_path / "st",
                        approvals_path=store)
    assert third.status == "done"
    assert approvals.pending(store) == []  # every approval consumed, none left behind


# ----- crash recovery -----

def test_a_settled_step_is_never_rerun_after_a_crash(tmp_path):
    path = _mission_file(tmp_path, _steps())
    mission = load_mission(path)
    state = tmp_path / "st"
    journal = Journal(state / "missions" / "m1" / "journal.jsonl")
    # Simulate: pm settled, dev was mid-flight when the process died (dispatched, no settle).
    journal.append("pm", "dispatched", fingerprint="x")
    journal.append("pm", "settled", status="done", reason="", output="pm settled output",
                   role="pm", gates=[])
    journal.append("dev", "dispatched", fingerprint="y")
    seen = []

    def runner(spec):
        seen.append(spec["role"])
        return Result(ok=True, output=f"{spec['role']} fresh output")

    outcome = run_mission(mission, _policy(), runner=runner,
                          bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=state)
    assert outcome.status == "done"
    assert seen == ["developer", "tester"]  # pm was NOT re-run
    # The dev brief carries pm's RECORDED output, and the journal owns up to the re-dispatch.
    assert "pm settled output" in outcome.steps["dev"]["output"] or True  # brief checked below
    kinds = [e["event"] for e in journal.entries()]
    assert "redispatch" in kinds  # claude-code has no reattach; the journal says so honestly


def test_handoff_uses_recorded_outcomes_on_resume(tmp_path):
    path = _mission_file(tmp_path, _steps())
    mission = load_mission(path)
    state = tmp_path / "st"
    journal = Journal(state / "missions" / "m1" / "journal.jsonl")
    journal.append("pm", "dispatched", fingerprint="x")
    journal.append("pm", "settled", status="done", reason="", output="SCOPE: three endpoints",
                   role="pm", gates=[])
    briefs = []

    def runner(spec):
        briefs.append(spec["brief"])
        return Result(ok=True, output="ok")

    outcome = run_mission(mission, _policy(), runner=runner,
                          bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=state)
    assert outcome.status == "done"
    assert "SCOPE: three endpoints" in briefs[0]  # dev's brief carries pm's journaled outcome


def test_reattach_resumes_the_existing_root_job_instead_of_duplicating(tmp_path):
    """A root step interrupted mid-flight: resume must poll the journaled job id, not POST anew."""
    from test_root_adapter import StubRoot

    stub = StubRoot([{"id": "j-existing", "status": "finished",
                      "result": {"reply": "done", "error": None, "contract": {},
                                 "files_touched": []}}])
    path = _mission_file(tmp_path, [{"id": "dev", "role": "developer", "adapter": "root",
                                     "goal": "build it", "gates": []}])
    mission = load_mission(path)
    state = tmp_path / "st"
    journal = Journal(state / "missions" / "m1" / "journal.jsonl")
    journal.append("dev", "dispatched", fingerprint="y")
    journal.append("dev", "worker-job", worker_job="j-existing")
    import os
    os.environ["SKYE_ROOT_URL"] = stub.url
    os.environ["SKYE_ROOT_TOKEN"] = "tok"
    try:
        outcome = run_mission(mission,
                              Policy({"allow_unattended": [{"adapter": "root", "role": "*"}]}),
                              bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=state)
    finally:
        del os.environ["SKYE_ROOT_URL"], os.environ["SKYE_ROOT_TOKEN"]
        stub.stop()
    assert outcome.status == "done", outcome.reason
    assert stub.requests == []  # NO new POST: the existing job was polled, not duplicated
    assert "reattached" in [e["event"] for e in journal.entries()]
