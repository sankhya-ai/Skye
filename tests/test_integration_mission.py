"""The recoverable team workflow, for real: a Skye mission driving the vendored Root server -
multiple contracts in a chain, journaled, with crash recovery that reattaches to the live Root
job instead of duplicating it. Scripted model, no provider, no cost. What this proves: the chain
executes, handoffs carry recorded outcomes, the journal survives, and resume does not repeat
settled work or duplicate an in-flight Root job. What it does not prove: real model behavior.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from test_integration_root import requires_runtime, root_server, text, tool_call  # noqa: F401 - shared fixture


def _api(url: str, token: str, method: str, path: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        f"{url}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json", "authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=30) as resp:
        return json.loads(resp.read())


@requires_runtime
def test_a_mission_chain_runs_real_root_jobs_with_a_journaled_handoff(root_server, tmp_path):
    from skye.events import FileBridge
    from skye.mission import Journal, load_mission, mission_status, run_mission
    from skye.policy import Policy

    url, home = root_server
    repo = tmp_path / "ws"
    repo.mkdir()
    mission_file = tmp_path / "mission.json"
    mission_file.write_text(json.dumps({
        "id": "chain", "goal": "build and verify hello", "repo": str(repo),
        "steps": [
            {"id": "dev", "role": "developer", "adapter": "root", "goal": "write hello.py",
             "gates": [], "budget": {"max_steps": 8}},
            {"id": "tester", "role": "tester", "adapter": "root", "goal": "verify hello.py",
             "needs": ["dev"], "gates": ["tests"], "budget": {"max_steps": 8}}]}))
    mission = load_mission(mission_file)
    policy = Policy({"allow_unattended": [{"adapter": "root", "role": "*"}],
                     "test_command": "python3 -c \"import pathlib; assert 'hello from skye' in pathlib.Path('hello.py').read_text()\""})
    state = tmp_path / "st"
    outcome = run_mission(mission, policy, bridge=FileBridge(tmp_path / "events.jsonl"),
                          state_dir=state)
    assert outcome.status == "done", outcome.reason
    assert "hello from skye" in (repo / "hello.py").read_text()
    # Two REAL Root jobs, one per step, both settled finished.
    jobs = sorted(p.name for p in (home / "jobs").glob("*.json"))
    assert len(jobs) == 2
    # The journal recorded both settlements with their Root job ids and the tester's gate.
    journal = Journal(state / "missions" / "chain" / "journal.jsonl")
    states = journal.step_states()
    assert states["dev"]["status"] == "done" and states["dev"]["worker_job"]
    assert states["tester"]["status"] == "done"
    assert states["tester"]["gates"][0]["ok"]
    # The handoff really happened: the tester step's dispatched contract carried dev's outcome.
    assert "dev" in json.dumps(outcome.steps) and outcome.steps["dev"]["status"] == "done"
    assert mission_status(mission, state)["status"] == "done"


@requires_runtime
def test_resume_reattaches_to_the_in_flight_root_job(root_server, tmp_path):
    """Kill-Skye simulation: a Root job started and journaled, Skye 'died' before settling. Resume
    must poll THAT job (no new POST, no duplicated work) and finish the mission."""
    from skye.events import FileBridge
    from skye.mission import Journal, load_mission, run_mission
    from skye.policy import Policy

    url, home = root_server
    token = (home / "token").read_text().strip()
    repo = tmp_path / "ws"
    repo.mkdir()
    # The step's Root job is started (as a previous Skye process would have done)...
    item = _api(url, token, "POST", "/api/jobs",
                {"goal": "write hello.py printing hello from skye", "workspace": str(repo),
                 "role": "developer", "budget": {"max_steps": 8}, "credentials": []})
    job_id = item["id"]
    # ...and the mission journal records exactly what the dispatcher had durably said.
    state = tmp_path / "st"
    journal = Journal(state / "missions" / "recovery" / "journal.jsonl")
    journal.append("dev", "dispatched", fingerprint="original")
    journal.append("dev", "worker-job", worker_job=job_id)
    mission_file = tmp_path / "mission.json"
    mission_file.write_text(json.dumps({
        "id": "recovery", "goal": "recover the interrupted job", "repo": str(repo),
        "steps": [{"id": "dev", "role": "developer", "adapter": "root", "goal": "write hello.py",
                   "gates": [], "budget": {"max_steps": 8}}]}))
    mission = load_mission(mission_file)
    outcome = run_mission(mission,
                          Policy({"allow_unattended": [{"adapter": "root", "role": "*"}]}),
                          bridge=FileBridge(tmp_path / "events.jsonl"), state_dir=state)
    assert outcome.status == "done", outcome.reason
    # The proof of no duplication: still exactly ONE job on the server, and it is THE journaled one.
    jobs = list((home / "jobs").glob("*.json"))
    assert [p.name for p in jobs] == [f"{job_id}.json"]
    events = [e["event"] for e in journal.entries()]
    assert "reattached" in events
    settled = [e for e in journal.entries() if e["event"] == "settled"]
    assert settled and settled[-1]["worker_job"] == job_id
    assert "hello from skye" in (repo / "hello.py").read_text()


@requires_runtime
def test_the_review_gate_requires_a_real_verdict_from_a_real_reviewer_job(root_server, tmp_path,
                                                                          monkeypatch):
    """The review gate over real Root jobs: the reviewer is a second Root job; only its explicit
    approve verdict passes the gate. First approve, then - with a re-scripted reviewer - reject."""
    from tura_code import launcher

    from skye.dispatcher import dispatch
    from skye.events import FileBridge
    from skye.policy import Policy

    url, home = root_server
    repo = tmp_path / "ws"
    repo.mkdir()
    policy = Policy({"allow_unattended": [{"adapter": "root", "role": "*"}]})

    (repo / "hello.py").write_text("print('hello from skye')\n")

    def run_with_verdict(verdict_line: str):
        # One script serving both jobs: a ReadFile call (allowed by both the developer's and the
        # reviewer's Root role scope) then the reply. The review job's contract_keys=["verdict"]
        # makes Root itself require the verdict block in the reply.
        script = tmp_path / "script.json"
        script.write_text(json.dumps([
            tool_call("r", "ReadFile", {"path": "hello.py"}),
            text(verdict_line)]))
        monkeypatch.setenv(launcher.SCRIPTED_ECHO_ENV, str(script))
        return dispatch(
            {"goal": "check hello.py", "repo": str(repo), "role": "developer", "adapter": "root",
             "gates": ["review"], "budget": {"max_steps": 8}},
            policy, bridge=FileBridge(tmp_path / "events.jsonl"))

    passed = run_with_verdict('Looks right. {"verdict": "pass"}')
    assert passed.status == "done", passed.reason
    assert passed.gates[0]["ok"] and passed.gates[0]["detail"] == "reviewer passed the changes"

    blocked = run_with_verdict('Not good enough. {"verdict": "block", "reasons": ["no docstring"]}')
    assert blocked.status == "gate-failed"
    assert "blocked" in blocked.reason and "no docstring" in blocked.reason


@requires_runtime
def test_parallel_wave_of_real_root_jobs_then_an_owner_gated_step(root_server, tmp_path):
    """Concurrent fan-out, for real: two Root jobs dispatched in the SAME wave (both dispatched
    before either settles, two live jobs on the server), then a step the policy did NOT
    pre-approve pauses for the owner while its siblings' results stand. After the owner approves
    the exact pending contract, resume runs only that step. Proves: real parallel overlap and an
    approval ACROSS concurrent jobs - not only unit-test barriers."""
    from skye import approvals
    from skye.events import FileBridge
    from skye.mission import Journal, load_mission, mission_status, run_mission
    from skye.policy import Policy

    url, home = root_server
    repo = tmp_path / "ws"
    repo.mkdir()
    mission_file = tmp_path / "mission.json"
    mission_file.write_text(json.dumps({
        "id": "fanout", "goal": "build api+ui in parallel, then verify with the owner's ok",
        "repo": str(repo),
        "steps": [
            {"id": "api", "role": "developer", "adapter": "root", "goal": "write api piece",
             "gates": [], "budget": {"max_steps": 8}},
            {"id": "ui", "role": "developer", "adapter": "root", "goal": "write ui piece",
             "gates": [], "budget": {"max_steps": 8}},
            {"id": "deploy", "role": "tester", "adapter": "root", "goal": "verify the build",
             "needs": ["api", "ui"], "gates": [], "budget": {"max_steps": 8}}]}))
    mission = load_mission(mission_file)
    # developers may run unattended; testers may not. Two concurrent slots.
    policy = Policy({"allow_unattended": [{"adapter": "root", "role": "developer"}],
                     "max_concurrent": 2})
    state = tmp_path / "st"
    store = tmp_path / "approvals.json"
    outcome = run_mission(mission, policy, bridge=FileBridge(tmp_path / "events.jsonl"),
                          state_dir=state, approvals_path=store)
    assert outcome.status == "needs-owner", outcome.reason
    assert "deploy" in outcome.reason

    journal = Journal(state / "missions" / "fanout" / "journal.jsonl")
    events = [(e["step"], e["event"]) for e in journal.entries()]
    api_disp = events.index(("api", "dispatched"))
    ui_disp = events.index(("ui", "dispatched"))
    first_settle = min(i for i, (s, e) in enumerate(events)
                       if e == "settled" and s in ("api", "ui"))
    assert max(api_disp, ui_disp) < first_settle  # real fan-out, not a chain
    # Two REAL Root jobs ran the wave; deploy never started without the owner.
    assert len(list((home / "jobs").glob("*.json"))) == 2

    # The owner approves EXACTLY the contract Skye wrote out for review.
    pending_path = state / "missions" / "fanout" / "pending-deploy.json"
    approvals.approve(json.loads(pending_path.read_text()), store, note="ship it")
    outcome = run_mission(mission, policy, bridge=FileBridge(tmp_path / "events.jsonl"),
                          state_dir=state, approvals_path=store)
    assert outcome.status == "done", outcome.reason
    # Resume dispatched only the deploy step: three jobs total, and api/ui were never re-run.
    assert len(list((home / "jobs").glob("*.json"))) == 3
    states = Journal(state / "missions" / "fanout" / "journal.jsonl").step_states()
    assert [states[s]["status"] for s in ("api", "ui", "deploy")] == ["done"] * 3
    assert mission_status(mission, state)["status"] == "done"
