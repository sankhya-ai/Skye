"""The team loops end to end: review send-backs, claims serialization, live discussion."""

import json
import subprocess
import sys
import threading

from skye import backlog, discuss
from skye.mission import load_mission, run_mission
from skye.policy import Policy


def _mission_file(tmp_path, steps, **extra):
    path = tmp_path / "mission.json"
    path.write_text(json.dumps({"id": "m-team", "goal": "g", "repo": str(tmp_path),
                                "steps": steps, **extra}))
    return load_mission(path)


def _policy(tmp_path, monkeypatch, **kw):
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "backlog.json"))
    monkeypatch.setenv("SKYE_ROLES", str(tmp_path / "roles.json"))
    monkeypatch.setenv("SKYE_ISSUES", str(tmp_path / "issues.json"))
    data = {"allow_unattended": [{"adapter": "root", "role": "*"}],
            "max_concurrent": 4, **kw}
    return Policy(data)


def test_review_send_back_repairs_then_re_reviews(tmp_path, monkeypatch):
    """The loop the user described: reviewer sends work back WITH feedback; the IMPLEMENTATION
    step re-runs carrying that feedback (not the reviewer re-grading itself); the second
    review passes. Every round is journaled; nothing unrelated is redone."""
    calls = {"dev": 0, "review": 0}

    def runner(spec):
        class R:
            ok = True
            returncode = 0
            job_id = ""
        if spec["role"] == "reviewer":
            calls["review"] += 1
            if calls["review"] == 1:
                R.output = 'looks close {"verdict": "request-changes", "feedback": ["add an empty-cart test"]}'
            else:
                R.output = '{"verdict": "pass"}'
            return R
        calls["dev"] += 1
        if calls["dev"] == 2:
            # The redo brief MUST carry the reviewer's feedback to the implementer.
            assert "add an empty-cart test" in spec["brief"]
            assert "Review feedback" in spec["brief"]
        R.output = "implemented"
        return R

    mission = _mission_file(tmp_path, [
        {"id": "build", "role": "developer", "adapter": "root",
         "goal": "cart checkout", "gates": ["review"]}])
    outcome = run_mission(mission, _policy(tmp_path, monkeypatch), runner=runner,
                          state_dir=tmp_path / ".skye")
    assert outcome.status == "done"
    assert calls == {"dev": 2, "review": 2}
    rounds = [e for e in (tmp_path / ".skye/missions/m-team/journal.jsonl")
              .read_text().splitlines() if '"review-round"' in e]
    assert len(rounds) == 1 and "add an empty-cart test" in rounds[0]


def test_review_rounds_are_bounded_and_the_bound_survives_a_crash(tmp_path, monkeypatch):
    """A reviewer that never stops asking eventually fails the mission - the loop is bounded,
    and the bound counts journaled rounds, so a restart cannot reset it."""
    def runner(spec):
        class R:
            ok = True
            returncode = 0
            job_id = ""
        if spec["role"] == "reviewer":
            R.output = '{"verdict": "request-changes", "feedback": ["more"]}'
        else:
            R.output = "implemented"
        return R

    mission = _mission_file(tmp_path, [
        {"id": "build", "role": "developer", "adapter": "root",
         "goal": "g", "gates": ["review"]}], max_review_rounds=2)
    first = run_mission(mission, _policy(tmp_path, monkeypatch), runner=runner,
                        state_dir=tmp_path / ".skye")
    assert first.status == "gate-failed"  # 2 rounds used, third request-changes fails
    rounds = [e for e in (tmp_path / ".skye/missions/m-team/journal.jsonl")
              .read_text().splitlines() if '"review-round"' in e]
    assert len(rounds) == 2
    # A rerun does not silently redo: the settled failure stands (existing engine posture).
    again = run_mission(mission, _policy(tmp_path, monkeypatch), runner=runner,
                        state_dir=tmp_path / ".skye")
    assert again.status == "gate-failed"


def test_crash_mid_review_loop_reseeds_the_feedback(tmp_path, monkeypatch):
    """Simulate a crash after the review-round journal entry but before the redo: resume
    re-runs the implementation step WITH the feedback, from the journal alone."""
    state_dir = tmp_path / ".skye"
    mission_dir = state_dir / "missions" / "m-team"
    mission_dir.mkdir(parents=True)
    (mission_dir / "journal.jsonl").write_text(
        json.dumps({"v": 1, "ts": 1.0, "step": "build", "event": "dispatched",
                    "fingerprint": "x"}) + "\n" +
        json.dumps({"v": 1, "ts": 2.0, "step": "build", "event": "review-round",
                    "round": 1, "feedback": "handle the empty state", "role": "developer"})
        + "\n")
    seen = {}

    def runner(spec):
        class R:
            ok = True
            returncode = 0
            job_id = ""
            output = "ok"
        if spec["role"] == "developer":
            seen["brief"] = spec["brief"]
        elif spec["role"] == "reviewer":
            R.output = '{"verdict": "pass"}'
        return R

    mission = _mission_file(tmp_path, [
        {"id": "build", "role": "developer", "adapter": "root",
         "goal": "g", "gates": ["review"]}])
    outcome = run_mission(mission, _policy(tmp_path, monkeypatch), runner=runner,
                          state_dir=state_dir)
    assert outcome.status == "done"
    assert "handle the empty state" in seen["brief"]


def test_overlapping_claims_serialize_within_a_wave(tmp_path, monkeypatch):
    """Two independent steps with intersecting write claims never run at the same time;
    non-intersecting ones may. This is prevention, not a log after the fact."""
    overlap = {"count": 0, "max": 0}
    lock = threading.Lock()

    def runner(spec):
        class R:
            ok = True
            returncode = 0
            job_id = ""
            output = "done"
        if spec["role"] == "backend-dev":
            with lock:
                overlap["count"] += 1
                overlap["max"] = max(overlap["max"], overlap["count"])
            import time as t
            t.sleep(0.05)
            with lock:
                overlap["count"] -= 1
        return R

    mission = _mission_file(tmp_path, [
        {"id": "api", "role": "backend-dev", "adapter": "root",
         "goal": "api work", "claims": ["src/api"], "gates": []},
        {"id": "api-tests", "role": "backend-dev", "adapter": "root",
         "goal": "api tests", "claims": ["src/api/routes"], "gates": []},  # intersects src/api
        {"id": "web", "role": "frontend-dev", "adapter": "root",
         "goal": "web work", "claims": ["src/web"], "gates": []}])
    outcome = run_mission(mission, _policy(tmp_path, monkeypatch), runner=runner,
                          state_dir=tmp_path / ".skye")
    assert outcome.status == "done"
    assert overlap["max"] == 1  # the colliding pair never overlapped
    held = [e for e in (tmp_path / ".skye/missions/m-team/journal.jsonl")
            .read_text().splitlines() if '"claim-held"' in e]
    assert held  # the hold was journaled, visibly


def test_claims_guard_is_atomic_across_processes(tmp_path, monkeypatch):
    """The registry's guarded acquire closes the check-then-dispatch race: a second process
    claiming an intersecting path while the first runs gets claim-held, not a collision."""
    from skye.dispatcher import dispatch
    from skye.registry import Registry
    policy = _policy(tmp_path, monkeypatch)
    state_dir = tmp_path / ".skye"
    # Simulate another process's running dispatch holding src/api.
    holder = Registry(state_dir)
    holder.acquire({"goal": "other mission api work", "role": "backend-dev",
                    "adapter": "root", "claims": ["src/api"]}, "fp-holder")
    try:
        outcome = dispatch({"goal": "g", "repo": str(tmp_path), "role": "backend-dev",
                            "adapter": "root", "claims": ["src/api/v2"], "gates": []},
                           policy, runner=lambda spec: None, state_dir=state_dir)
        assert outcome.status == "claim-held"
        assert "src/api" in outcome.reason
    finally:
        holder.release()
    # Once the holder settles (releases), the same dispatch proceeds to the policy/worker path.
    outcome = dispatch({"goal": "g", "repo": str(tmp_path), "role": "backend-dev",
                        "adapter": "root", "claims": ["src/api/v2"], "gates": []},
                       policy,
                       runner=lambda spec: type("R", (), {"ok": True, "returncode": 0,
                                                          "output": "x", "job_id": ""})(),
                       state_dir=state_dir)
    assert outcome.status == "done"


def test_a_worker_posts_mid_run_and_the_next_brief_carries_it(tmp_path, monkeypatch):
    """Real cross-process team line use: a running worker shells out to `skye say` mid-dispatch;
    the dependent step's brief then carries the post and the unacked change notice."""
    state_dir = tmp_path / ".skye"
    env_script = None

    def runner(spec):
        class R:
            ok = True
            returncode = 0
            job_id = ""
        if spec["role"] == "backend-dev":
            # The worker, mid-run, posts a contract-change through the real CLI (a separate
            # process, exactly like a Claude Code Bash call would).
            proc = subprocess.run(
                [sys.executable, "-m", "skye.cli", "say", "m-team", "--as", "backend-dev",
                 "--kind", "contract-change", "--ack-needed", "frontend-dev",
                 "POST /cart/items now takes {sku, qty}"],
                capture_output=True, text=True,
                env={"PATH": "/usr/bin:/bin", "SKYE_STATE_DIR": str(state_dir),
                     "SKYE_LINE_INVITE": "", "HOME": str(tmp_path)})
            assert proc.returncode == 0, proc.stderr
            R.output = "api changed"
        else:
            # The frontend step's brief must carry both the discussion tail and the
            # unacked-change notice addressed to its role.
            assert "POST /cart/items now takes" in spec["brief"]
            assert "Unacked contract changes" in spec["brief"]
            assert "skye ack" in spec["brief"]
            R.output = "adapted"
        return R

    mission = _mission_file(tmp_path, [
        {"id": "api", "role": "backend-dev", "adapter": "root", "goal": "change the api",
         "claims": ["src/api"], "gates": []},
        {"id": "web", "role": "frontend-dev", "adapter": "root", "goal": "adapt the ui",
         "needs": ["api"], "claims": ["src/web"], "gates": []}])
    outcome = run_mission(mission, _policy(tmp_path, monkeypatch), runner=runner,
                          state_dir=state_dir)
    assert outcome.status == "done"
    # The frontend worker acks via the CLI; the change is then fully caught-up.
    proc = subprocess.run(
        [sys.executable, "-m", "skye.cli", "hear", "m-team", "--since", "0"],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "SKYE_STATE_DIR": str(state_dir), "HOME": str(tmp_path)})
    entries = json.loads(proc.stdout)["entries"]
    change_id = next(e["id"] for e in entries if e["kind"] == "contract-change")
    proc = subprocess.run(
        [sys.executable, "-m", "skye.cli", "ack", "m-team", change_id,
         "--as", "frontend-dev", "--note", "adapted in web step"],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "SKYE_STATE_DIR": str(state_dir), "HOME": str(tmp_path)})
    assert proc.returncode == 0, proc.stderr
    assert discuss.unacked(state_dir, "m-team", ["frontend-dev"]) == []


def test_mission_proposes_agents_that_wait_for_the_owner(tmp_path, monkeypatch):
    def runner(spec):
        class R:
            ok = True
            returncode = 0
            job_id = ""
            output = ('done {"propose_agents": [{"name": "security-auditor", '
                      '"instructions": "Audit for secrets.", '
                      '"catalogue": {"tools": ["Read", "Grep"]}}]}')
        return R

    mission = _mission_file(tmp_path, [
        {"id": "build", "role": "developer", "adapter": "root", "goal": "g", "gates": []}])
    outcome = run_mission(mission, _policy(tmp_path, monkeypatch), runner=runner,
                          state_dir=tmp_path / ".skye")
    assert outcome.status == "done"
    from skye import agents, roles
    assert "security-auditor" not in roles.known()  # proposed, not runnable
    agents.promote("security-auditor")
    assert "security-auditor" in roles.known()
    journal = (tmp_path / ".skye/missions/m-team/journal.jsonl").read_text()
    assert "agent-accepted" in journal


def test_new_standard_roles_bind_through_the_adapters(tmp_path, monkeypatch):
    """The new builtin roles are not badges: they resolve real catalogues and produce real
    adapter command lines (claude-code shown; codex/root consume the same contract fields)."""
    from skye import agents, gateway
    from skye.adapters.claude_code import ClaudeCodeAdapter
    for role in ("senior-dev", "backend-dev", "frontend-dev"):
        catalogue = gateway.resolve(role)
        assert catalogue["tools"] and catalogue["writable"]
        definition = agents.definition_for(role)
        assert definition["instructions"]
        adapter = ClaudeCodeAdapter()
        command = adapter.build_command({
            "goal": "g", "repo": str(tmp_path), "role": role, "adapter": "claude-code",
            "brief": "b", "tools": catalogue["tools"], "tools_writable": catalogue["writable"],
            "role_instructions": definition["instructions"]})
        assert "--tools" in command and "--disallowedTools" in command


def test_concurrent_workers_talk_mid_run(tmp_path, monkeypatch):
    """Live discussion during parallel work: two workers running AT THE SAME TIME, where the
    listener polls the team line mid-dispatch and reacts to the poster's contract-change
    BEFORE either finishes. Not next-brief delivery: receipt and reaction inside the run."""
    import subprocess, sys, threading, time
    state_dir = tmp_path / ".skye"
    reacted = threading.Event()
    overlapped = threading.Event()

    def runner(spec):
        class R:
            ok = True
            returncode = 0
            job_id = ""
            output = "ok"
        env = {"PATH": "/usr/bin:/bin", "SKYE_STATE_DIR": str(state_dir),
               "HOME": str(tmp_path)}
        if spec["role"] == "backend-dev":
            overlapped.set()  # the listener is already running by the time we post
            proc = subprocess.run(
                [sys.executable, "-m", "skye.cli", "say", "m-team", "--as", "backend-dev",
                 "--kind", "contract-change", "--ack-needed", "frontend-dev",
                 "field total_cents is now integer cents"],
                capture_output=True, text=True, env=env)
            assert proc.returncode == 0, proc.stderr
            time.sleep(0.3)  # stay in-flight while the listener reacts
            R.output = "api changed"
        else:
            # Poll mid-run (the honest CLI-adapter delivery): react the moment the change lands.
            deadline = time.time() + 15
            heard = None
            while time.time() < deadline:
                proc = subprocess.run(
                    [sys.executable, "-m", "skye.cli", "hear", "m-team", "--since", "0"],
                    capture_output=True, text=True, env=env)
                entries = json.loads(proc.stdout)["entries"]
                heard = next((e for e in entries if e["kind"] == "contract-change"), None)
                if heard:
                    break
                time.sleep(0.05)
            assert heard, "the running listener never received the contract-change mid-run"
            ack = subprocess.run(
                [sys.executable, "-m", "skye.cli", "ack", "m-team", heard["id"],
                 "--as", "frontend-dev", "--note", "adapting now, mid-run"],
                capture_output=True, text=True, env=env)
            assert ack.returncode == 0, ack.stderr
            assert overlapped.is_set(), "the poster had already finished - no real overlap"
            reacted.set()
            R.output = "adapted mid-run"
        return R

    mission = _mission_file(tmp_path, [
        {"id": "api", "role": "backend-dev", "adapter": "root", "goal": "change the api",
         "claims": ["src/api"], "gates": []},
        {"id": "web", "role": "frontend-dev", "adapter": "root", "goal": "build the ui",
         "claims": ["src/web"], "gates": []}])
    outcome = run_mission(mission, _policy(tmp_path, monkeypatch), runner=runner,
                          state_dir=state_dir)
    assert outcome.status == "done"
    assert reacted.is_set()
    entries = discuss.read(state_dir, "m-team", since=0)["entries"]
    assert [e["kind"] for e in entries] == ["contract-change", "ack"]


def test_proposed_agents_with_out_of_universe_tools_never_reach_the_registry(tmp_path, monkeypatch):
    """Failure scenario: a worker proposing a specialist with tools outside the known universe
    (widening its own reach) is rejected and journaled; the registry stays clean."""
    def runner(spec):
        class R:
            ok = True
            returncode = 0
            job_id = ""
            output = ('done {"propose_agents": [{"name": "rooter", '
                      '"instructions": "i do everything", '
                      '"catalogue": {"tools": ["Read", "Sudo", "Network"]}}]}')
        return R

    mission = _mission_file(tmp_path, [
        {"id": "build", "role": "developer", "adapter": "root", "goal": "g", "gates": []}])
    outcome = run_mission(mission, _policy(tmp_path, monkeypatch), runner=runner,
                          state_dir=tmp_path / ".skye")
    assert outcome.status == "done"
    from skye import roles
    assert "rooter" not in roles.known()
    assert roles._read()["roles"] == []  # nothing landed
    journal = (tmp_path / ".skye/missions/m-team/journal.jsonl").read_text()
    assert "agent-rejected" in journal and "outside the known universe" in journal


def test_editing_role_instructions_invalidates_prior_approval(tmp_path, monkeypatch):
    """Snapshot/approval binding: the approved text IS the dispatched text. Editing the
    registry's instructions after an approval changes the fingerprint - the old approval
    cannot silently retarget to the new instructions."""
    from skye import approvals, roles
    from skye.contract import validate
    monkeypatch.setenv("SKYE_ROLES", str(tmp_path / "roles.json"))
    monkeypatch.setenv("SKYE_APPROVALS", str(tmp_path / "approvals.json"))
    roles.add({"name": "pm", "instructions": "scope tightly"})
    contract = validate({"goal": "g", "repo": str(tmp_path), "role": "pm",
                         "adapter": "root", "gates": []})
    assert contract["role_instructions"] == "scope tightly"
    entry = approvals.approve(contract)
    assert approvals.peek(contract) is not None
    # The owner edits the role's instructions; the previously approved contract no longer matches.
    roles.add({"name": "pm", "instructions": "scope BROADLY"})
    contract2 = validate({"goal": "g", "repo": str(tmp_path), "role": "pm",
                          "adapter": "root", "gates": []})
    assert contract2["role_instructions"] == "scope BROADLY"
    assert approvals.peek(contract2) is None  # the old approval does not cover the new text


def _seed_interrupted_journal(state_dir, worker_job=None):
    mission_dir = state_dir / "missions" / "m-team"
    mission_dir.mkdir(parents=True)
    entries = [{"v": 1, "ts": 1.0, "step": "build", "event": "dispatched", "fingerprint": "x"}]
    if worker_job:
        entries.append({"v": 1, "ts": 2.0, "step": "build", "event": "worker-job",
                        "worker_job": worker_job})
    (mission_dir / "journal.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in entries))


def test_operator_redispatch_beats_reattach_for_a_stale_prior_job(tmp_path, monkeypatch):
    """A stale prior job id is on record, but the operator chose `mission redispatch`: the
    decision must journal as a redispatch (with the superseded job), never as a reattach, and
    the fresh dispatch must NOT resume the stale job."""
    import skye.mission as mission_mod
    state_dir = tmp_path / ".skye"
    _seed_interrupted_journal(state_dir, worker_job="stale-job-1")
    seen = {}
    real_dispatch = mission_mod.dispatch

    def capturing(contract, *a, **kw):
        seen.update(kw)
        return real_dispatch(contract, *a, **kw)

    monkeypatch.setattr(mission_mod, "dispatch", capturing)

    def runner(spec):
        class R:
            ok = True
            returncode = 0
            job_id = "job-new"
            output = "fresh run"
        return R

    mission = _mission_file(tmp_path, [
        {"id": "build", "role": "developer", "adapter": "root", "goal": "g", "gates": []}])
    outcome = run_mission(mission, _policy(tmp_path, monkeypatch), runner=runner,
                          state_dir=state_dir, redispatch="build")
    assert outcome.status == "done"
    assert not seen.get("resume_job_id"), "the stale job must not be resumed"
    entries = [json.loads(l) for l in (state_dir / "missions/m-team/journal.jsonl").read_text().splitlines()]
    kinds = [e["event"] for e in entries]
    assert "redispatch" in kinds and "reattached" not in kinds
    assert entries[kinds.index("redispatch")].get("superseded_job") == "stale-job-1"


def test_reattach_without_operator_decision_resumes_the_prior_job(tmp_path, monkeypatch):
    """No operator redispatch: the interrupted root step reattaches to its journaled job -
    the fresh dispatch receives the prior job id to poll, never duplicates the work."""
    import skye.mission as mission_mod
    state_dir = tmp_path / ".skye"
    _seed_interrupted_journal(state_dir, worker_job="job-live-1")
    seen = {}
    real_dispatch = mission_mod.dispatch

    def capturing(contract, *a, **kw):
        seen.update(kw)
        return real_dispatch(contract, *a, **kw)

    monkeypatch.setattr(mission_mod, "dispatch", capturing)

    def runner(spec):
        class R:
            ok = True
            returncode = 0
            job_id = "job-live-1"
            output = "ok"
        return R

    mission = _mission_file(tmp_path, [
        {"id": "build", "role": "developer", "adapter": "root", "goal": "g", "gates": []}])
    outcome = run_mission(mission, _policy(tmp_path, monkeypatch), runner=runner,
                          state_dir=state_dir)
    assert outcome.status == "done"
    assert seen.get("resume_job_id") == "job-live-1"
    journal = (state_dir / "missions/m-team/journal.jsonl").read_text()
    assert "reattached" in journal and "redispatch" not in journal


def test_interrupted_step_without_a_job_id_pauses_for_the_operator(tmp_path, monkeypatch):
    """Interrupted before any worker job id reached the journal: no silent re-run - the
    mission stops and names the operator's choices."""
    state_dir = tmp_path / ".skye"
    _seed_interrupted_journal(state_dir, worker_job=None)
    dispatched = []

    def runner(spec):
        dispatched.append(spec["role"])
        class R:
            ok = True
            returncode = 0
            job_id = ""
            output = "ok"
        return R

    mission = _mission_file(tmp_path, [
        {"id": "build", "role": "developer", "adapter": "root", "goal": "g", "gates": []}])
    outcome = run_mission(mission, _policy(tmp_path, monkeypatch), runner=runner,
                          state_dir=state_dir)
    assert outcome.status == "interrupted"
    assert "mission redispatch" in outcome.reason and "mission reset" in outcome.reason
    assert not dispatched  # nothing re-ran
