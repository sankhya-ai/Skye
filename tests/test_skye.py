"""Skye core: contract, policy, gateway, dispatcher - plain code, no LLM, no network."""

from __future__ import annotations

import json

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
    assert command[:3] == ["claude", "-p", "do it"]
    assert "--tools" in command and "--allowedTools" not in command
    assert "--disallowedTools" in command and "mcp__*" in command


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


PASS = 'Reviewed. {"verdict": "pass"}'
BLOCK = 'Problems. {"verdict": "block", "reasons": ["no error handling"]}'


def test_review_gate_dispatches_a_second_worker_not_self_review():
    seen = []

    def runner(spec):
        seen.append(spec["role"])
        return Result(ok=True, output=PASS if spec["role"] == "reviewer" else "ok")

    outcome = dispatch(_contract(repo="/tmp", gates=["review"]), _policy(), runner=runner)
    assert outcome.status == "done" and seen == ["developer", "reviewer"]
    assert outcome.gates[0]["ok"] and outcome.gates[0]["detail"] == "reviewer passed the changes"


def test_review_gate_fails_when_the_reviewer_says_block():
    # The audit's correctness hole, fixed: a cleanly finished reviewer whose verdict is "block"
    # must NOT pass the gate.
    def runner(spec):
        return Result(ok=True, output=BLOCK if spec["role"] == "reviewer" else "ok")

    outcome = dispatch(_contract(repo="/tmp", gates=["review"]), _policy(), runner=runner)
    assert outcome.status == "gate-failed"
    assert "blocked" in outcome.reason and "no error handling" in outcome.reason


def test_review_gate_fails_without_a_verdict():
    # A reviewer that finishes but never says approve is not an approval.
    outcome = dispatch(_contract(repo="/tmp", gates=["review"]), _policy(),
                       runner=lambda spec: Result(ok=True, output="looks fine to me"))
    assert outcome.status == "gate-failed" and "without a parseable verdict" in outcome.reason


def test_review_gate_uses_the_policy_review_role():
    seen = []

    def runner(spec):
        seen.append(spec["role"])
        return Result(ok=True, output=PASS if spec["role"] == "designer" else "ok")

    outcome = dispatch(_contract(repo="/tmp", gates=["review"]), _policy(review_role="designer"),
                       runner=runner)
    assert outcome.status == "done" and seen == ["developer", "designer"]


def test_review_gate_fails_on_an_unknown_verdict():
    # "lgtm" is not a verdict; fail closed.
    outcome = dispatch(_contract(repo="/tmp", gates=["review"]), _policy(),
                       runner=lambda spec: Result(ok=True, output='sure {"verdict": "lgtm"}'))
    assert outcome.status == "gate-failed" and "without a parseable verdict" in outcome.reason


def test_review_gate_fails_on_a_malformed_verdict():
    # Unquoted value is not the JSON verdict shape; fail closed.
    outcome = dispatch(_contract(repo="/tmp", gates=["review"]), _policy(),
                       runner=lambda spec: Result(ok=True, output='{"verdict": pass}'))
    assert outcome.status == "gate-failed" and "without a parseable verdict" in outcome.reason


def test_request_changes_without_feedback_fails_closed_no_redo(tmp_path, monkeypatch):
    # A send-back with nothing actionable must not spawn a blind redo: gate fails, no redo edge.
    from skye.mission import load_mission, run_mission
    calls = {"dev": 0}

    def runner(spec):
        if spec["role"] == "reviewer":
            return Result(ok=True, output='{"verdict": "request-changes"}')  # no feedback at all
        calls["dev"] += 1
        return Result(ok=True, output="implemented")

    path = tmp_path / "m.json"
    path.write_text(json.dumps({"id": "m-rc", "goal": "g", "repo": str(tmp_path),
                                "steps": [{"id": "build", "role": "developer", "adapter": "root",
                                           "goal": "g", "gates": ["review"]}]}))
    monkeypatch.setenv("SKYE_ROLES", str(tmp_path / "roles.json"))
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "backlog.json"))
    monkeypatch.setenv("SKYE_ISSUES", str(tmp_path / "issues.json"))
    outcome = run_mission(load_mission(path), _policy(), runner=runner, state_dir=tmp_path / ".skye")
    assert outcome.status == "gate-failed"
    assert calls["dev"] == 1  # never redispatched
    assert "without usable feedback" in outcome.reason
    journal = (tmp_path / ".skye/missions/m-rc/journal.jsonl").read_text()
    assert "review-round" not in journal  # no redo edge journaled


def test_request_changes_with_an_empty_feedback_list_fails_closed(tmp_path):
    # {"verdict": "request-changes", "feedback": []} is the same blind redo; fail closed.
    def runner(spec):
        return Result(ok=True, output='{"verdict": "request-changes", "feedback": []}'
                      if spec["role"] == "reviewer" else "ok")
    outcome = dispatch(_contract(repo=str(tmp_path), gates=["review"]), _policy(), runner=runner)
    assert outcome.status == "gate-failed"
    assert "without usable feedback" in outcome.gates[0]["detail"]
    assert not outcome.gates[0].get("changes_requested")


def test_request_changes_on_a_review_step_fails_closed_invalid_target(tmp_path):
    # The redo targets the step the gate is attached to; when that step IS the reviewer,
    # the send-back would route the review to itself. Fail closed, no redo edge.
    def runner(spec):
        return Result(ok=True, output='{"verdict": "request-changes", "feedback": ["redo it"]}'
                      if spec["role"] == "reviewer" else "ok")
    outcome = dispatch(_contract(repo=str(tmp_path), gates=["review"], role="reviewer"),
                       _policy(), runner=runner)
    assert outcome.status == "gate-failed"
    assert "cannot be sent back to itself" in outcome.gates[0]["detail"]
    assert not outcome.gates[0].get("changes_requested")


def test_request_changes_parses_through_the_root_reply_envelope(tmp_path):
    # Adapter-path coverage: the Root adapter's JSON envelope (model reply as a string field)
    # must parse the same as raw stdout from the CLI adapters.
    def runner(spec):
        if spec["role"] == "reviewer":
            return Result(ok=True, output=json.dumps(
                {"reply": 'done {"verdict": "request-changes", "feedback": ["fix the off-by-one"]}',
                 "status": "finished"}))
        return Result(ok=True, output="ok")
    outcome = dispatch(_contract(repo=str(tmp_path), gates=["review"]), _policy(), runner=runner)
    assert outcome.status == "gate-failed"
    gate = outcome.gates[0]
    assert gate.get("changes_requested") and "off-by-one" in gate.get("feedback", "")


def test_fail_closed_review_terminal_state_survives_resume(tmp_path, monkeypatch):
    # Recovery path: re-running the mission after a fail-closed review must NOT invent a redo.
    from skye.mission import load_mission, run_mission
    calls = {"dev": 0}

    def runner(spec):
        if spec["role"] == "reviewer":
            return Result(ok=True, output='{"verdict": "request-changes"}')
        calls["dev"] += 1
        return Result(ok=True, output="implemented")

    path = tmp_path / "m.json"
    path.write_text(json.dumps({"id": "m-rc", "goal": "g", "repo": str(tmp_path),
                                "steps": [{"id": "build", "role": "developer", "adapter": "root",
                                           "goal": "g", "gates": ["review"]}]}))
    monkeypatch.setenv("SKYE_ROLES", str(tmp_path / "roles.json"))
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "backlog.json"))
    monkeypatch.setenv("SKYE_ISSUES", str(tmp_path / "issues.json"))
    state_dir = tmp_path / ".skye"
    first = run_mission(load_mission(path), _policy(), runner=runner, state_dir=state_dir)
    assert first.status == "gate-failed" and calls["dev"] == 1
    resumed = run_mission(load_mission(path), _policy(), runner=runner, state_dir=state_dir)
    assert resumed.status == "gate-failed"
    assert calls["dev"] == 1  # the settled failure replays; no phantom redo
