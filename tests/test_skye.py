"""Skye core: contract, policy, gateway, dispatcher - plain code, no LLM, no network."""

from __future__ import annotations

import pytest

from skye import adapters, gateway
from skye.adapters.base import AdapterError, Result
from skye.contract import ContractError, validate
from skye.dispatcher import dispatch
from skye.policy import Policy


def _contract(**overrides):
    base = {"goal": "add the endpoint", "repo": "/tmp/repo", "role": "developer", "adapter": "claude-code"}
    base.update(overrides)
    return base


def _policy(**overrides):
    data = {"allow_unattended": [{"adapter": "claude-code", "role": "*"},
                                 {"adapter": "root", "role": "*"},
                                 {"adapter": "codex", "role": "*"}],
            "test_command": "true"}
    data.update(overrides)
    return Policy(data)


def _ok_runner(spec):
    return Result(ok=True, output="worked", command=["fake"])


# ----- contract -----

def test_contract_requires_goal_repo_adapter_and_known_role():
    for bad in ({}, {"goal": "x"}, _contract(role="wizard"), _contract(adapter="emacs"), _contract(gates=["magic"])):
        with pytest.raises(ContractError):
            validate(bad)
    assert validate(_contract())["role"] == "developer"


# ----- policy -----

def test_default_policy_blocks_everything_unattended():
    reason = Policy().unattended_ok(_contract())
    assert "no policy rule" in reason


def test_policy_rule_allows_named_pair_only():
    policy = Policy({"allow_unattended": [{"adapter": "codex", "role": "developer"}]})
    assert policy.unattended_ok(_contract(adapter="codex")) == ""
    assert "no policy rule" in policy.unattended_ok(_contract(adapter="codex", role="reviewer"))
    assert "cap" in policy.unattended_ok(_contract(adapter="codex"), running=5)


# ----- gateway -----

def test_gateway_resolves_builtin_and_team_catalogue(tmp_path):
    assert gateway.resolve("reviewer")["writable"] is False
    (tmp_path / "reviewer.json").write_text('{"tools": ["Read"], "writable": false}')
    resolved = gateway.resolve("reviewer", tmp_path)
    assert resolved["source"].endswith("reviewer.json") and resolved["tools"] == ["Read"]
    with pytest.raises(KeyError):
        gateway.resolve("wizard", tmp_path)


# ----- adapters -----

def test_claude_adapter_carries_the_tool_catalogue():
    command = adapters.ClaudeCodeAdapter().build_command(_contract(tools=["Read", "Grep"], brief="do it"))
    assert command[:3] == ["claude", "-p", "do it"] and "--allowedTools" in command


def test_codex_adapter_sandbox_follows_writable():
    assert "read-only" in adapters.CodexAdapter().build_command(_contract(tools_writable=False))
    assert "workspace-write" in adapters.CodexAdapter().build_command(_contract(tools_writable=True))


def test_missing_worker_tool_fails_loudly(tmp_path):
    adapter = adapters.ClaudeCodeAdapter()
    adapter.binary = "definitely-not-installed-skye-test"
    with pytest.raises(AdapterError, match="not installed"):
        adapter.run(_contract(repo=str(tmp_path)))


# ----- dispatcher -----

def test_dispatch_blocks_unattended_without_a_rule():
    outcome = dispatch(_contract(), Policy())
    assert outcome.status == "needs-owner" and "no policy rule" in outcome.reason


def test_dispatch_runs_worker_and_test_gate(tmp_path):
    outcome = dispatch(_contract(repo=str(tmp_path)), _policy(), runner=_ok_runner)
    assert outcome.status == "done"
    assert outcome.gates == [{"gate": "tests", "ok": True, "detail": "tests passed", "command": "true"}]


def test_dispatch_gate_uses_the_policy_command_never_the_contract(tmp_path):
    # A contract cannot smuggle a command into the tests gate.
    outcome = dispatch(_contract(repo=str(tmp_path), test_command="evil"), _policy(test_command="false"), runner=_ok_runner)
    assert outcome.status == "gate-failed" and "tests failed" in outcome.reason


def test_dispatch_worker_failure_stops_before_gates(tmp_path):
    outcome = dispatch(_contract(repo=str(tmp_path)), _policy(),
                       runner=lambda spec: Result(ok=False, output="boom", returncode=1))
    assert outcome.status == "failed" and outcome.gates == []


def test_review_gate_dispatches_a_second_worker_not_self_review():
    seen = []

    def runner(spec):
        seen.append(spec["role"])
        return Result(ok=True, output="ok")

    outcome = dispatch(_contract(repo="/tmp", gates=["review"]), _policy(), runner=runner)
    assert outcome.status == "done" and seen == ["developer", "reviewer"]
