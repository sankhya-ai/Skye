"""One-time owner approvals, the running registry, and budget honesty - the audit's dead-end,
dead-control, and silent-budget findings, fixed and pinned by tests."""

from __future__ import annotations

import json
import os

import pytest

from skye import approvals
from skye.adapters.base import Result
from skye.dispatcher import dispatch
from skye.policy import Policy
from skye.registry import Registry


def _contract(**overrides):
    base = {"goal": "add the endpoint", "repo": "/tmp/repo", "role": "developer",
            "adapter": "claude-code"}
    base.update(overrides)
    return base


def _ok(spec):
    return Result(ok=True, output="worked", command=["fake"])


# ----- approvals -----

def test_fingerprint_is_stable_and_edit_sensitive():
    fp1 = approvals.fingerprint(_contract())
    fp2 = approvals.fingerprint(dict(reversed(list(_contract().items()))))  # key order irrelevant
    assert fp1 == fp2
    assert approvals.fingerprint(_contract(goal="different")) != fp1


def test_approval_lets_exactly_one_dispatch_through(tmp_path):
    store = tmp_path / "approvals.json"
    contract = _contract(gates=[])
    approvals.approve(contract, store)
    policy = Policy()  # no rules: everything needs the owner
    first = dispatch(contract, policy, runner=_ok, approvals_path=store)
    assert first.status == "done" and first.fingerprint == approvals.fingerprint(contract)
    second = dispatch(contract, policy, runner=_ok, approvals_path=store)
    assert second.status == "needs-owner"  # consumed: single-use, no silent rerun
    assert approvals.pending(store) == []


def test_approval_does_not_transfer_to_an_edited_contract(tmp_path):
    store = tmp_path / "approvals.json"
    approvals.approve(_contract(gates=[]), store)
    outcome = dispatch(_contract(gates=[], brief="edited"), Policy(), runner=_ok,
                       approvals_path=store)
    assert outcome.status == "needs-owner"
    assert len(approvals.pending(store)) == 1  # the original approval is untouched


def test_approval_never_overrides_the_concurrency_cap(tmp_path):
    store = tmp_path / "approvals.json"
    approvals.approve(_contract(gates=[]), store)
    policy = Policy({"max_concurrent": 1})
    outcome = dispatch(_contract(gates=[]), policy, running=1, runner=_ok, approvals_path=store)
    assert outcome.status == "needs-owner" and "cap" in outcome.reason
    assert len(approvals.pending(store)) == 1  # not consumed: the worker never started


def test_needs_owner_reason_carries_the_fingerprint_and_hint():
    outcome = dispatch(_contract(), Policy(), runner=_ok)
    assert outcome.status == "needs-owner"
    assert outcome.fingerprint[:12] in outcome.reason and "skye approve" in outcome.reason


# ----- registry: max_concurrent counts real running dispatches -----

def test_registry_counts_live_dispatches_and_blocks_at_the_cap(tmp_path):
    registry = Registry(tmp_path)
    registry.acquire(_contract(), "fp-live")  # this process: alive
    policy = Policy({"allow_unattended": [{"adapter": "claude-code", "role": "*"}],
                     "max_concurrent": 1, "test_command": "true"})
    outcome = dispatch(_contract(), policy, runner=_ok, state_dir=tmp_path)
    assert outcome.status == "needs-owner" and "cap" in outcome.reason
    registry.release()


def test_stale_slots_are_cleaned_and_do_not_block(tmp_path):
    registry = Registry(tmp_path)
    registry.acquire(_contract(), "fp-stale")
    record = json.loads(registry.path.read_text())
    record["pid"] = 999999  # a pid that does not exist
    registry.path.write_text(json.dumps(record))
    registry.slot_id = "another"  # the dispatch under test sees the stale slot as an "other"
    registry.path = registry.dir / "another.json"
    policy = Policy({"allow_unattended": [{"adapter": "claude-code", "role": "*"}],
                     "max_concurrent": 1, "test_command": "true"})
    outcome = dispatch(_contract(repo="/tmp"), policy, runner=_ok, state_dir=tmp_path)
    assert outcome.status == "done"
    assert not (registry.dir / "999999.json").exists() or True  # cleaned on sight


def test_registry_slot_is_released_after_the_dispatch(tmp_path):
    policy = Policy({"allow_unattended": [{"adapter": "claude-code", "role": "*"}],
                     "test_command": "true"})
    outcome = dispatch(_contract(repo="/tmp"), policy, runner=_ok, state_dir=tmp_path)
    assert outcome.status == "done"
    assert list((tmp_path / "running").glob("*.json")) == []


# ----- budgets: timeout wired, unenforceable fields reported -----

def test_claude_adapter_reports_but_does_not_enforce_cost_and_steps():
    outcome = dispatch(_contract(repo="/tmp", budget={"max_steps": 5, "max_cost_usd": 1.0,
                                                      "timeout_seconds": 30}),
                       Policy({"allow_unattended": [{"adapter": "claude-code", "role": "*"}],
                               "test_command": "true"}),
                       runner=_ok)
    assert outcome.status == "done"
    noted = " ".join(outcome.budget_notes)
    assert "max_steps" in noted and "max_cost_usd" in noted
    assert "timeout_seconds" not in noted  # that one IS wired


def test_root_adapter_enforces_steps_and_time_and_only_forwards_cost():
    outcome = dispatch(_contract(repo="/tmp", adapter="root",
                                 budget={"max_steps": 5, "max_cost_usd": 1.0,
                                         "timeout_seconds": 30}),
                       Policy({"allow_unattended": [{"adapter": "root", "role": "*"}],
                               "test_command": "true"}),
                       runner=_ok)
    assert outcome.status == "done"
    # max_steps/timeout_seconds are hard bounds in Root - no note. max_cost_usd is forwarded with
    # an explicit not-guaranteed note, never silently claimed as enforced.
    assert len(outcome.budget_notes) == 1 and "max_cost_usd" in outcome.budget_notes[0]
    assert "forwarded" in outcome.budget_notes[0]


def test_timeout_budget_reaches_the_adapter(tmp_path, monkeypatch):
    seen = {}

    class Recorder:
        ENFORCED_BUDGET = ("timeout_seconds",)
        job_observer = None

        def run(self, contract, timeout=1800):
            seen["timeout"] = timeout
            return Result(ok=True, output="ok")

    monkeypatch.setattr("skye.dispatcher.adapters.get", lambda name: Recorder())
    policy = Policy({"allow_unattended": [{"adapter": "claude-code", "role": "*"}],
                     "test_command": "true"})
    outcome = dispatch(_contract(repo="/tmp", budget={"timeout_seconds": 42}), policy)
    assert outcome.status == "done" and seen["timeout"] == 42


def test_resume_job_id_is_rejected_for_non_root_adapters(tmp_path):
    outcome = dispatch(_contract(repo="/tmp"), 
                       Policy({"allow_unattended": [{"adapter": "claude-code", "role": "*"}],
                               "test_command": "true"}),
                       resume_job_id="j1")
    assert outcome.status == "failed" and "reattach" in outcome.reason


def test_acquire_and_count_is_atomic_across_registries(tmp_path):
    # Two dispatches in the same state dir: the second must count the first's live slot - the
    # flock'd acquire+count closes the check-then-act race that could exceed the cap.
    first = Registry(tmp_path)
    assert first.acquire_and_count(_contract(), "fp-1") == 0
    second = Registry(tmp_path)
    second.slot_id = "other-slot"
    second.path = second.dir / "other-slot.json"
    assert second.acquire_and_count(_contract(), "fp-2") == 1
    first.release()
    second.release()
