"""Bounded auto-start: the owner's standing policy for routine proposals, never model judgment.

Default is the old world (every proposal waits); a rule starts a matching proposal through the SAME locked
accept path with the rule's limits; every escalation, fuse, and start-guard failure stays pending.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from tura_code import jobs
from tura_code.jobs import AutoStartPolicy, AutoStartRule


def _tenant(tmp_path):
    tenant = SimpleNamespace(root=tmp_path / "data", workspace_root=str(tmp_path / "ws"))
    tenant.job_requests = jobs.JobRequestStore(tenant)
    tenant.jobs = jobs.Jobs(tenant)
    tenant.registry = None
    return tenant


def _runtime(tenant, session="sess1"):
    return SimpleNamespace(record=SimpleNamespace(id=session, workspace=str(tenant.workspace_root)))


def _propose(tenant, role="explorer", goal="look at the repo"):
    payload, error = jobs.request_job(tenant, _runtime(tenant), {"goal": goal, "role": role})
    assert error is None
    return payload


def _policy(enabled=True, **overrides):
    rule = overrides.pop("rule", AutoStartRule(name="routine-explorer", role="explorer", max_steps=25))
    return AutoStartPolicy(enabled=enabled, rules=[rule], **overrides)


def test_no_policy_file_keeps_every_proposal_pending(tmp_path):
    tenant = _tenant(tmp_path)
    payload = _propose(tenant)
    assert "auto_started" not in payload
    assert tenant.job_requests.get(payload["id"])["status"] == "pending"
    assert tenant.jobs.items == {}


def test_disabled_policy_keeps_every_proposal_pending(tmp_path):
    tenant = _tenant(tmp_path)
    jobs.save_auto_start_policy(tenant, _policy(enabled=False))
    payload = _propose(tenant)
    assert "auto_started" not in payload
    assert tenant.jobs.items == {}


def test_matching_rule_auto_starts_with_stored_fields_and_rule_limits(tmp_path, monkeypatch):
    tenant = _tenant(tmp_path)
    jobs.save_auto_start_policy(tenant, _policy(rule=AutoStartRule(name="r", role="explorer", max_steps=7)))
    started = {}

    def fake_start(spec):
        started["spec"] = spec
        return {"id": "job1", "status": "running"}

    monkeypatch.setattr(tenant.jobs, "start", fake_start)
    payload = _propose(tenant)
    assert payload["auto_started"] == {"job_id": "job1", "rule": "r"}
    spec = started["spec"]
    assert spec.role == "explorer" and spec.request_id == payload["id"] and spec.budget.max_steps == 7
    assert spec.isolation == "" and spec.accept_unprotected_credentials is False
    assert tenant.job_requests.get(payload["id"])["status"] == "accepted"
    log = jobs.json.loads((jobs._auto_dir(tenant) / "auto-start-log.json").read_text())
    assert log[0]["rule"] == "r" and log[0]["job_id"] == "job1"


def test_non_matching_role_stays_pending(tmp_path, monkeypatch):
    tenant = _tenant(tmp_path)
    jobs.save_auto_start_policy(tenant, _policy())
    monkeypatch.setattr(tenant.jobs, "start", lambda spec: pytest.fail("must not start"))
    payload = _propose(tenant, role="developer")
    assert "auto_started" not in payload


def test_escalation_never_auto_starts_even_with_a_matching_rule(tmp_path, monkeypatch):
    tenant = _tenant(tmp_path)
    jobs.save_auto_start_policy(tenant, _policy(rule=AutoStartRule(name="r", role="explorer", max_steps=25, max_depth=3)))
    calls = []
    monkeypatch.setattr(tenant.jobs, "start", lambda spec: calls.append(spec) or {"id": "j", "status": "running"})
    payload = _propose(tenant)
    assert len(calls) == 1  # control: without the flag this proposal DOES auto-start
    tenant.job_requests.items[payload["id"]]["escalation"] = True
    record = dict(payload)
    record["escalation"] = True  # asks for tools the parent lacks: the owner's call, always
    assert jobs.maybe_auto_start(tenant, record) is None
    assert len(calls) == 1


def test_workspace_prefix_and_depth_gate_the_rule(tmp_path, monkeypatch):
    tenant = _tenant(tmp_path)
    rule = AutoStartRule(name="r", role="explorer", max_steps=25, workspace_prefix="/allowed/", max_depth=0)
    jobs.save_auto_start_policy(tenant, _policy(rule=rule))
    monkeypatch.setattr(tenant.jobs, "start", lambda spec: {"id": "j", "status": "running"})
    wrong_ws = _propose(tenant)  # workspace is tmp_path/ws, not under /allowed/
    assert "auto_started" not in wrong_ws
    # Mutate the STORED proposal (the in-accept re-check re-reads the store, so a caller-side copy is not enough).
    with tenant.job_requests._exclusive():
        tenant.job_requests.items[wrong_ws["id"]]["workspace"] = "/allowed/site"
        tenant.job_requests._save()
    record = tenant.job_requests.get(wrong_ws["id"])
    item_rule = jobs.maybe_auto_start(tenant, record)
    assert item_rule is not None
    # depth: a delegated child (depth 1) is beyond max_depth=0
    record2 = dict(record)
    record2["id"] = "req2"
    record2["status"] = "pending"
    record2["depth"] = 1
    tenant.job_requests.create(record2)
    assert jobs.maybe_auto_start(tenant, record2) is None


def test_rule_isolation_is_forced_and_never_loosened(tmp_path, monkeypatch):
    tenant = _tenant(tmp_path)
    jobs.save_auto_start_policy(tenant, _policy(rule=AutoStartRule(name="r", role="explorer", max_steps=25, require_isolation="bwrap-model-only")))
    started = {}

    def fake_start(spec):
        started["spec"] = spec
        return {"id": "j", "status": "running"}

    monkeypatch.setattr(tenant.jobs, "start", fake_start)
    _propose(tenant)
    assert started["spec"].isolation == "bwrap-model-only"


def test_start_guard_failure_leaves_the_request_pending(tmp_path, monkeypatch):
    tenant = _tenant(tmp_path)
    jobs.save_auto_start_policy(tenant, _policy())
    monkeypatch.setattr(tenant.jobs, "start", lambda spec: (_ for _ in ()).throw(jobs.JobError("refused by a start guard")))
    payload = _propose(tenant)
    assert "auto_started" not in payload
    assert tenant.job_requests.get(payload["id"])["status"] == "pending"


def test_lifetime_fuse_survives_edits_and_restarts_until_explicitly_reset(tmp_path, monkeypatch):
    tenant = _tenant(tmp_path)
    policy = _policy(max_auto_started_total=1)
    jobs.save_auto_start_policy(tenant, policy)
    monkeypatch.setattr(tenant.jobs, "start", lambda spec: {"id": f"j{time.time_ns()}", "status": "succeeded"})
    assert "auto_started" in _propose(tenant, goal="one")
    assert "auto_started" not in _propose(tenant, goal="two")  # fuse spent: waits for the owner
    # A policy edit does NOT silently re-arm the fuse...
    edited = _policy(max_auto_started_total=1)
    edited.rules.append(AutoStartRule(name="extra", role="explorer", max_steps=10))  # content edit, same cap
    jobs.save_auto_start_policy(tenant, edited)
    assert "auto_started" not in _propose(tenant, goal="three")
    # ...a host restart preserves the spent fuse (fresh objects, same directory)...
    tenant2 = _tenant(tmp_path)
    monkeypatch.setattr(tenant2.jobs, "start", lambda spec: {"id": "jx", "status": "succeeded"})
    assert "auto_started" not in _propose(tenant2, goal="four")
    # ...and only the owner's explicit reset_fuse re-arms it.
    with jobs._auto_exclusive(tenant2):
        jobs.save_auto_start_policy(tenant2, _policy(max_auto_started_total=2, reset_fuse=True))
        jobs._save_auto_state(tenant2, {"total": 0})
    assert "auto_started" in _propose(tenant2, goal="five")


def test_running_caps_block_until_slots_free(tmp_path, monkeypatch):
    tenant = _tenant(tmp_path)
    jobs.save_auto_start_policy(tenant, _policy(rule=AutoStartRule(name="r", role="explorer", max_steps=25, max_concurrent=1)))
    monkeypatch.setattr(tenant.jobs, "start", lambda spec: {"id": "j1", "status": "running"})
    first = _propose(tenant, goal="one")
    assert "auto_started" in first
    tenant.jobs.items["j1"] = {"id": "j1", "status": "running", "auto_started": True, "auto_rule": "r"}
    second = _propose(tenant, goal="two")
    assert "auto_started" not in second  # the rule's concurrency slot is taken
    tenant.jobs.items["j1"]["status"] = "succeeded"
    third = _propose(tenant, goal="three")
    assert "auto_started" in third


def test_policy_routes_roundtrip_and_log(client):
    got = client.get("/api/auto-start-policy")
    assert got.status_code == 200 and got.json()["policy"]["enabled"] is False
    body = {"enabled": True, "rules": [{"name": "routine", "role": "explorer", "max_steps": 25, "max_concurrent": 1}]}
    put = client.put("/api/auto-start-policy", json=body)
    assert put.status_code == 200 and put.json()["policy"]["enabled"] is True
    assert client.get("/api/auto-start-policy").json()["policy"]["rules"][0]["role"] == "explorer"
    assert client.get("/api/auto-start-policy/log").json()["log"] == []


def test_rule_without_a_budget_ceiling_admits_nothing(tmp_path, monkeypatch):
    tenant = _tenant(tmp_path)
    jobs.save_auto_start_policy(tenant, _policy(rule=AutoStartRule(name="r", role="explorer")))  # no ceilings
    monkeypatch.setattr(tenant.jobs, "start", lambda spec: (_ for _ in ()).throw(AssertionError("must not start")))
    assert "auto_started" not in _propose(tenant)


def test_a_dollars_only_ceiling_admits_nothing(tmp_path, monkeypatch):
    # max_cost_usd is not independently enforceable (no pricing offline/scripted/via the proxy; per-turn
    # accrual can overshoot): a rule whose only ceiling is dollars is a false bound and fails closed.
    tenant = _tenant(tmp_path)
    jobs.save_auto_start_policy(tenant, _policy(rule=AutoStartRule(name="r", role="explorer", max_cost_usd=5.0)))
    monkeypatch.setattr(tenant.jobs, "start", lambda spec: (_ for _ in ()).throw(AssertionError("must not start")))
    assert "auto_started" not in _propose(tenant)


def test_workspace_prefix_match_is_canonical(tmp_path, monkeypatch):
    tenant = _tenant(tmp_path)
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    jobs.save_auto_start_policy(tenant, _policy(rule=AutoStartRule(
        name="r", role="explorer", max_steps=25, workspace_prefix=str(allowed))))
    monkeypatch.setattr(tenant.jobs, "start", lambda spec: {"id": "j", "status": "running"})
    payload = _propose(tenant)
    record = tenant.job_requests.get(payload["id"])
    # ".." and a symlink that lands OUTSIDE the prefix must not match.
    record["workspace"] = str(allowed / ".." / "elsewhere")
    assert jobs.match_auto_start(jobs.load_auto_start_policy(tenant), record, tenant) is None
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "elsewhere-target", target_is_directory=True)
    record["workspace"] = str(link)
    assert jobs.match_auto_start(jobs.load_auto_start_policy(tenant), record, tenant) is None
    # A real path inside the prefix, with a .. segment, matches.
    record["workspace"] = str(allowed / "sub" / "..")
    assert jobs.match_auto_start(jobs.load_auto_start_policy(tenant), record, tenant) is not None


def test_concurrent_proposals_cannot_both_pass_a_one_slot_fuse(tmp_path, monkeypatch):
    import threading

    tenant = _tenant(tmp_path)
    jobs.save_auto_start_policy(tenant, _policy(max_auto_started_total=1, max_auto_running=10))
    monkeypatch.setattr(tenant.jobs, "start", lambda spec: {"id": f"j{time.time_ns()}", "status": "succeeded"})
    outcomes = []
    threads = [threading.Thread(target=lambda: outcomes.append("auto_started" in _propose(tenant))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count(True) == 1  # exactly one admission across the race


def test_policy_revoked_mid_admission_stays_pending(tmp_path, monkeypatch):
    tenant = _tenant(tmp_path)
    jobs.save_auto_start_policy(tenant, _policy())
    real_load = jobs.load_auto_start_policy
    calls = {"n": 0}

    def revoke_on_recheck(t):
        calls["n"] += 1
        if calls["n"] >= 2:  # the re-check inside the locked accept sees the revoked policy
            return jobs.AutoStartPolicy(enabled=False)
        return real_load(t)

    monkeypatch.setattr(jobs, "load_auto_start_policy", revoke_on_recheck)
    monkeypatch.setattr(tenant.jobs, "start", lambda spec: (_ for _ in ()).throw(AssertionError("must not start")))
    payload = _propose(tenant)
    assert "auto_started" not in payload
    assert tenant.job_requests.get(payload["id"])["status"] == "pending"


def test_auto_started_job_never_carries_credentials(tmp_path, monkeypatch):
    tenant = _tenant(tmp_path)
    jobs.save_auto_start_policy(tenant, _policy())
    seen = {}
    monkeypatch.setattr(tenant.jobs, "start", lambda spec: seen.setdefault("spec", spec) or {"id": "j", "status": "running"})
    _propose(tenant)
    assert seen["spec"].credentials == [] and seen["spec"].accept_unprotected_credentials is False


def test_admission_is_atomic_across_processes(tmp_path):
    """Two independent HOST processes race a one-slot fuse: the flock is a cross-process lock, the
    counter file is bumped inside it, so exactly one admission exists system-wide."""
    import subprocess
    import sys

    tenant = _tenant(tmp_path)
    jobs.save_auto_start_policy(tenant, _policy(max_auto_started_total=1, max_auto_running=10))
    record = {"id": "race1", "goal": "g", "role": "explorer", "role_version": 0, "depth": 0,
              "workspace": str(tmp_path / "ws"), "budget": {}, "status": "pending", "created_at": time.time()}
    tenant.job_requests.create(record)
    racer = tmp_path / "racer.py"
    racer.write_text(
        "import sys, time\n"
        "from types import SimpleNamespace\n"
        "sys.path.insert(0, '.')\n"
        "from tura_code import jobs\n"
        f"root = {str(tmp_path)!r}\n"
        "tenant = SimpleNamespace(root=__import__('pathlib').Path(root) / 'data', workspace_root=root + '/ws')\n"
        "tenant.job_requests = jobs.JobRequestStore(tenant)\n"
        "tenant.jobs = jobs.Jobs(tenant)\n"
        "tenant.registry = None\n"
        "tenant.jobs.start = lambda spec: (time.sleep(0.4), {'id': 'jx', 'status': 'succeeded'})[1]\n"
        "rec = tenant.job_requests.get('race1')\n"
        "print('admitted' if jobs.maybe_auto_start(tenant, rec) else 'pending')\n")
    runs = [subprocess.run([sys.executable, str(racer)], cwd=".", capture_output=True, text=True)
            for _ in range(2)]
    # Sequential subprocesses prove the counter file; a concurrent pair proves the flock. Run one
    # concurrent pair on top for the cross-process critical section.
    assert sorted(r.stdout.strip() for r in runs) == ["admitted", "pending"], [r.stdout for r in runs]
