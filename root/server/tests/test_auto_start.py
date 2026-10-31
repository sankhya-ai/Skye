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
    rule = overrides.pop("rule", AutoStartRule(name="routine-explorer", role="explorer"))
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
    jobs.save_auto_start_policy(tenant, _policy(rule=AutoStartRule(name="r", role="explorer", max_depth=3)))
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
    rule = AutoStartRule(name="r", role="explorer", workspace_prefix="/allowed/", max_depth=0)
    jobs.save_auto_start_policy(tenant, _policy(rule=rule))
    monkeypatch.setattr(tenant.jobs, "start", lambda spec: {"id": "j", "status": "running"})
    wrong_ws = _propose(tenant)  # workspace is tmp_path/ws, not under /allowed/
    assert "auto_started" not in wrong_ws
    record = tenant.job_requests.get(wrong_ws["id"])
    record["workspace"] = "/allowed/site"
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
    jobs.save_auto_start_policy(tenant, _policy(rule=AutoStartRule(name="r", role="explorer", require_isolation="bwrap-model-only")))
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


def test_lifetime_fuse_stops_auto_start_and_a_policy_change_restarts_it(tmp_path, monkeypatch):
    tenant = _tenant(tmp_path)
    policy = _policy(max_auto_started_total=1)
    jobs.save_auto_start_policy(tenant, policy)
    monkeypatch.setattr(tenant.jobs, "start", lambda spec: {"id": f"j{time.time_ns()}", "status": "succeeded"})
    first = _propose(tenant, goal="one")
    assert "auto_started" in first
    second = _propose(tenant, goal="two")
    assert "auto_started" not in second  # fuse spent: waits for the owner
    jobs.save_auto_start_policy(tenant, _policy(max_auto_started_total=2))  # changed content = new grant
    third = _propose(tenant, goal="three")
    assert "auto_started" in third


def test_running_caps_block_until_slots_free(tmp_path, monkeypatch):
    tenant = _tenant(tmp_path)
    jobs.save_auto_start_policy(tenant, _policy(rule=AutoStartRule(name="r", role="explorer", max_concurrent=1)))
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
    body = {"enabled": True, "rules": [{"name": "routine", "role": "explorer", "max_concurrent": 1}]}
    put = client.put("/api/auto-start-policy", json=body)
    assert put.status_code == 200 and put.json()["policy"]["enabled"] is True
    assert client.get("/api/auto-start-policy").json()["policy"]["rules"][0]["role"] == "explorer"
    assert client.get("/api/auto-start-policy/log").json()["log"] == []
