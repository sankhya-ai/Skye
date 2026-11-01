"""Missions: multi-contract chains with handoffs, journaled so a crash is recoverable."""

from __future__ import annotations

import json

import pytest

from skye import approvals
from skye.adapters.base import Result
from skye.events import FileBridge
from skye.mission import (Journal, MAX_PARALLEL_STEPS, MissionError, load_mission, mission_status,
                          reset_mission,
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

def test_an_interrupted_cli_step_pauses_for_an_operator_decision(tmp_path):
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

    # No blind repeat: claude-code cannot reattach, and partial effects from the interrupted
    # attempt are the operator's call - the mission STOPS until they choose.
    paused = run_mission(mission, _policy(), runner=runner,
                         bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=state)
    assert paused.status == "interrupted" and seen == [] and "dev" in paused.reason
    # The explicit decision: re-run exactly that step; pm is still never re-run.
    outcome = run_mission(mission, _policy(), runner=runner,
                          bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=state,
                          redispatch="dev")
    assert outcome.status == "done"
    assert seen == ["developer", "tester"]
    kinds = [e["event"] for e in journal.entries()]
    assert "redispatch" in kinds  # journaled as an operator-chosen re-run, not silent recovery


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


# ----- dynamic workflows: conditions, proposals, resume of dynamic steps -----

def _dynamic_steps():
    return [{"id": "pm", "role": "pm", "adapter": "claude-code", "goal": "scope the work",
             "gates": []},
            {"id": "fix", "role": "developer", "adapter": "claude-code", "goal": "fix it",
             "needs": ["pm"], "when": {"step": "pm", "status": "done"}, "gates": []}]


def test_a_worker_can_propose_steps_that_join_the_workflow(tmp_path):
    path = _mission_file(tmp_path, _dynamic_steps())
    mission = load_mission(path)
    seen = []

    def runner(spec):
        seen.append(spec["role"])
        if spec["role"] == "pm":
            return Result(ok=True, output=(
                'Scoped. {"propose_steps": [{"id": "docs", "role": "developer", '
                '"adapter": "claude-code", "goal": "write docs", "needs": ["fix"], "gates": []}]}'))
        return Result(ok=True, output=f"{spec['role']} done")

    outcome = run_mission(mission, _policy(), runner=runner,
                          bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=tmp_path / "st")
    assert outcome.status == "done"
    assert seen == ["pm", "developer", "developer"]  # fix, then the proposed docs step
    journal = Journal(tmp_path / "st" / "missions" / "m1" / "journal.jsonl")
    accepted = journal.accepted_steps()
    assert [s["id"] for s in accepted] == ["docs"]
    assert outcome.steps["docs"]["status"] == "done"


def test_proposals_are_validated_not_executed(tmp_path):
    path = _mission_file(tmp_path, _dynamic_steps())
    mission = load_mission(path)

    def runner(spec):
        if spec["role"] == "pm":
            return Result(ok=True, output=(
                '{"propose_steps": ['
                '{"id": "pm", "role": "pm", "adapter": "codex", "goal": "dupe"}, '
                '{"id": "evil", "role": "wizard", "adapter": "codex", "goal": "bad role"}, '
                '{"id": "orphan", "role": "pm", "adapter": "codex", "goal": "x", '
                ' "needs": ["nope"]}]}'))
        return Result(ok=True, output="ok")

    outcome = run_mission(mission, _policy(), runner=runner,
                          bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=tmp_path / "st")
    assert outcome.status == "done"
    journal = Journal(tmp_path / "st" / "missions" / "m1" / "journal.jsonl")
    rejected = [e for e in journal.entries() if e["event"] == "proposal-rejected"]
    assert len(rejected) == 3 and journal.accepted_steps() == []


def test_conditional_steps_skip_and_cascade(tmp_path):
    steps = [{"id": "pm", "role": "pm", "adapter": "claude-code", "goal": "scope", "gates": []},
             {"id": "fix", "role": "developer", "adapter": "claude-code", "goal": "fix",
              "needs": ["pm"], "when": {"step": "pm", "status": "failed"}, "gates": []},
             {"id": "verify", "role": "tester", "adapter": "claude-code", "goal": "verify",
              "needs": ["fix"], "gates": []}]
    path = _mission_file(tmp_path, steps)
    mission = load_mission(path)
    seen = []

    def runner(spec):
        seen.append(spec["role"])
        return Result(ok=True, output="ok")  # pm settles done, so the failed-only fix skips

    outcome = run_mission(mission, _policy(), runner=runner,
                          bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=tmp_path / "st")
    assert outcome.status == "done"
    assert seen == ["pm"]  # fix skipped (condition unmet); verify skipped (needs a skipped step)
    assert outcome.steps["fix"]["status"] == "skipped"
    assert outcome.steps["verify"]["status"] == "skipped"


def test_resume_rebuilds_dynamic_steps_from_the_journal(tmp_path):
    path = _mission_file(tmp_path, _dynamic_steps())
    mission = load_mission(path)
    state = tmp_path / "st"
    journal = Journal(state / "missions" / "m1" / "journal.jsonl")
    # Crash story: pm settled having proposed docs; docs was accepted; then the process died.
    journal.append("pm", "dispatched", fingerprint="x")
    journal.append("pm", "settled", status="done", reason="", output="scoped", role="pm", gates=[])
    journal.append("pm", "step-accepted",
                   spec={"id": "docs", "role": "developer", "adapter": "claude-code",
                         "goal": "write docs", "needs": ["fix"], "gates": []})
    seen = []

    def runner(spec):
        seen.append(spec["goal"])
        return Result(ok=True, output="ok")

    outcome = run_mission(mission, _policy(), runner=runner,
                          bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=state)
    assert outcome.status == "done"
    assert seen == ["fix it", "write docs"]  # pm not re-run; the dynamic step survived the crash


def test_mission_validation_rejects_a_bad_when(tmp_path):
    steps = [{"id": "a", "role": "pm", "adapter": "codex", "goal": "x",
              "when": {"step": "ghost", "status": "done"}, "gates": []}]
    with pytest.raises(MissionError, match="when"):
        load_mission(_mission_file(tmp_path, steps))


# ----- parallel fan-out -----

def _parallel_policy():
    return Policy({"allow_unattended": [{"adapter": "claude-code", "role": "*"}],
                   "test_command": "true", "max_concurrent": 8})



def test_independent_steps_run_concurrently(tmp_path):
    """Two independent steps overlap in wall time: sequential execution would take ~2x as long."""
    import threading
    import time
    steps = [{"id": "a", "role": "developer", "adapter": "claude-code", "goal": "a", "gates": []},
             {"id": "b", "role": "tester", "adapter": "claude-code", "goal": "b", "gates": []}]
    mission = load_mission(_mission_file(tmp_path, steps))
    entered = threading.Barrier(2, timeout=10)

    def runner(spec):
        entered.wait()  # only passes if BOTH steps are in flight at once
        return Result(ok=True, output=f"{spec['role']} done", command=["fake"])

    started = time.monotonic()
    outcome = run_mission(mission, _parallel_policy(), runner=runner,
                          bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=tmp_path / "st")
    assert outcome.status == "done", outcome.reason
    assert time.monotonic() - started < 10


def test_dependency_order_survives_parallel_waves(tmp_path):
    """A dependent step never starts before its need settles, even with siblings in flight."""
    import time
    steps = [{"id": "base", "role": "pm", "adapter": "claude-code", "goal": "base", "gates": []},
             {"id": "sibling", "role": "developer", "adapter": "claude-code", "goal": "sib",
              "gates": []},
             {"id": "child", "role": "tester", "adapter": "claude-code", "goal": "child",
              "needs": ["base"], "gates": []}]
    mission = load_mission(_mission_file(tmp_path, steps))
    timeline = {}

    def runner(spec):
        timeline[f"{spec['goal']}:start"] = time.monotonic()
        time.sleep(0.05)
        timeline[f"{spec['goal']}:end"] = time.monotonic()
        return Result(ok=True, output="ok", command=["fake"])

    outcome = run_mission(mission, _parallel_policy(), runner=runner,
                          bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=tmp_path / "st")
    assert outcome.status == "done", outcome.reason
    assert timeline["child:start"] >= timeline["base:end"]


def test_wave_width_is_capped(tmp_path):
    """Six independent steps never exceed MAX_PARALLEL_STEPS concurrent dispatches."""
    import threading
    steps = [{"id": f"s{i}", "role": "developer", "adapter": "claude-code", "goal": f"g{i}",
              "gates": []} for i in range(6)]
    mission = load_mission(_mission_file(tmp_path, steps))
    lock = threading.Lock()
    live = {"now": 0, "peak": 0}

    def runner(spec):
        with lock:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
        try:
            import time
            time.sleep(0.05)
            return Result(ok=True, output="ok", command=["fake"])
        finally:
            with lock:
                live["now"] -= 1

    outcome = run_mission(mission, _parallel_policy(), runner=runner,
                          bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=tmp_path / "st")
    assert outcome.status == "done", outcome.reason
    assert 1 < live["peak"] <= MAX_PARALLEL_STEPS


def test_parallel_wave_failure_fails_mission_after_wave(tmp_path):
    """A failing step fails the mission; its independent sibling still completes (already
    dispatched, killing it mid-flight would strand unknown state), and the dependent never runs."""
    steps = [{"id": "bad", "role": "developer", "adapter": "claude-code", "goal": "bad",
              "gates": []},
             {"id": "good", "role": "developer", "adapter": "claude-code", "goal": "good",
              "gates": []},
             {"id": "child", "role": "tester", "adapter": "claude-code", "goal": "child",
              "needs": ["bad"], "gates": []}]
    mission = load_mission(_mission_file(tmp_path, steps))
    ran = []

    def runner(spec):
        ran.append(spec["goal"])
        if spec["goal"] == "bad":
            return Result(ok=False, output="boom", command=["fake"])
        return Result(ok=True, output="ok", command=["fake"])

    outcome = run_mission(mission, _parallel_policy(), runner=runner,
                          bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=tmp_path / "st")
    assert outcome.status == "failed"
    assert "good" in ran and "child" not in ran
    assert outcome.steps["good"]["status"] == "done"


def test_dynamic_proposal_joins_a_later_wave(tmp_path):
    """A step proposed mid-run by a parallel worker is validated, journaled and dispatched."""
    steps = [{"id": "a", "role": "developer", "adapter": "claude-code", "goal": "a", "gates": []},
             {"id": "b", "role": "tester", "adapter": "claude-code", "goal": "b", "gates": []}]
    mission = load_mission(_mission_file(tmp_path, steps))
    ran = []

    def runner(spec):
        ran.append(spec["role"])
        if spec["goal"] == "a":
            return Result(ok=True,
                          output='done {"propose_steps": [{"id": "extra", "role": "tester",'
                                 ' "adapter": "claude-code", "goal": "extra", "needs": ["b"],'
                                 ' "gates": []}]}',
                          command=["fake"])
        return Result(ok=True, output="ok", command=["fake"])

    outcome = run_mission(mission, _parallel_policy(), runner=runner,
                          bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=tmp_path / "st")
    assert outcome.status == "done", outcome.reason
    assert outcome.steps["extra"]["status"] == "done"
    journal = Journal(tmp_path / "st" / "missions" / "m1" / "journal.jsonl")
    assert [s["id"] for s in journal.accepted_steps()] == ["extra"]


def test_status_stays_needs_owner_when_a_later_dynamic_step_finished(tmp_path):
    """Parallel waves: an early paused step must keep the mission needs-owner even after a later
    independent step settles done - 'done' would be a lie."""
    steps = [{"id": "dev", "role": "pm", "adapter": "claude-code", "goal": "x", "gates": []},
             {"id": "guarded", "role": "tester", "adapter": "claude-code", "goal": "y",
              "needs": ["dev"], "gates": []}]
    mission = load_mission(_mission_file(tmp_path, steps))
    policy = Policy({"allow_unattended": [{"adapter": "claude-code", "role": "pm"}],
                     "test_command": "true", "max_concurrent": 4})
    proposer = '{"propose_steps": [{"id": "docs", "role": "pm", "adapter": "claude-code", "goal": "d", "needs": ["dev"], "gates": []}]}'
    outcome = run_mission(mission, policy,
                          runner=lambda spec: Result(ok=True, output=proposer, command=["f"]),
                          bridge=FileBridge(tmp_path / "e.jsonl"), state_dir=tmp_path / "st")
    assert outcome.status == "needs-owner"
    status = mission_status(mission, tmp_path / "st")
    assert status["status"] == "needs-owner", status


def test_settled_step_feature_proposals_land_in_the_owner_backlog(tmp_path, monkeypatch):
    """A worker may propose FEATURES; they persist as pending backlog items for the owner,
    journaled with provenance, and never start work by themselves."""
    from skye import backlog
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "skye.backlog.json"))
    steps = [{"id": "dev", "role": "developer", "adapter": "claude-code",
              "goal": "build it", "gates": []}]
    mission = load_mission(_mission_file(tmp_path, steps, project="shop"))

    def runner(spec):
        return Result(ok=True,
                      output='shipped {"propose_features": [{"title": "Search",'
                             ' "brief": "full-text search"}, {"title": "no brief"}]}',
                      command=["fake"])

    outcome = run_mission(mission, _policy(), runner=runner,
                          bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=tmp_path / "st")
    assert outcome.status == "done", outcome.reason
    features = backlog.list_features()
    assert len(features) == 1
    assert features[0]["status"] == "pending"
    assert features[0]["project"] == "shop"
    assert features[0]["proposed_by"] == "m1"
    journal = Journal(tmp_path / "st" / "missions" / "m1" / "journal.jsonl")
    events = [json.loads(line) for line in
              (tmp_path / "st" / "missions" / "m1" / "journal.jsonl").read_text().splitlines()]
    assert any(e.get("event") == "feature-proposed" for e in events)


def test_done_mission_delivers_its_approved_feature(tmp_path, monkeypatch):
    """A mission started from an approved backlog feature marks it delivered on done."""
    from skye import backlog
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "skye.backlog.json"))
    feature = backlog.propose([{"title": "Cart", "brief": "shopping cart"}],
                              project="shop", mission="seed")[0]
    backlog.decide(feature["id"], "approved")
    backlog.mark_started(feature["id"], "m1")
    steps = [{"id": "dev", "role": "developer", "adapter": "claude-code",
              "goal": "build it", "gates": []}]
    mission = load_mission(_mission_file(tmp_path, steps))

    def runner(spec):
        return Result(ok=True, output="ok", command=["fake"])

    outcome = run_mission(mission, _policy(), runner=runner,
                          bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=tmp_path / "st")
    assert outcome.status == "done", outcome.reason
    # no journey gate ran, so done work waits for verification instead of claiming delivery
    assert backlog.list_features()[0]["status"] == "awaiting-verification"


def test_done_mission_with_a_passed_journey_gate_delivers(tmp_path, monkeypatch):
    """The delivery bar: a passed user-journey replay gate in the workflow delivers the
    feature with the replay evidence recorded."""
    from skye import backlog
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "skye.backlog.json"))
    feature = backlog.propose([{"title": "Cart", "brief": "shopping cart"}],
                              project="shop", mission="seed")[0]
    backlog.decide(feature["id"], "approved")
    backlog.mark_started(feature["id"], "m1")
    steps = [{"id": "dev", "role": "developer", "adapter": "claude-code",
              "goal": "build it", "gates": ["journey"]}]
    mission = load_mission(_mission_file(tmp_path, steps))

    def runner(spec):
        return Result(ok=True, output="ok", command=["fake"])

    policy = _policy()
    policy.journey_command = "echo 'journey: add to cart -> checkout OK'"
    outcome = run_mission(mission, policy, runner=runner,
                          bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=tmp_path / "st")
    assert outcome.status == "done", outcome.reason
    stored = backlog.list_features()[0]
    assert stored["status"] == "delivered"
    assert stored["verification"]["journey_passed"] is True
    assert "checkout OK" in stored["verification"]["evidence"]


def test_a_failed_journey_gate_blocks_the_step(tmp_path, monkeypatch):
    """A journey replay failure fails the step honestly - no silent pass."""
    steps = [{"id": "dev", "role": "developer", "adapter": "claude-code",
              "goal": "build it", "gates": ["journey"]}]
    mission = load_mission(_mission_file(tmp_path, steps))

    def runner(spec):
        return Result(ok=True, output="ok", command=["fake"])

    policy = _policy()
    policy.journey_command = "echo 'checkout 500' >&2; exit 1"
    outcome = run_mission(mission, policy, runner=runner,
                          bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=tmp_path / "st")
    assert outcome.status == "gate-failed"
    assert "journey replay failed" in outcome.reason
