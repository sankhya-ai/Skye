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
    assert "not on the team's unattended list" in reason and "approval" in reason
    assert "claude-code/developer" in reason  # the decision truth: which pair is blocked


def test_policy_rule_allows_named_pair_only():
    policy = Policy({"allow_unattended": [{"adapter": "codex", "role": "developer"}]})
    assert policy.unattended_ok(_contract(adapter="codex")) == ""
    assert "not on the team's unattended list" in policy.unattended_ok(_contract(adapter="codex", role="reviewer"))
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
    # Headless mode has nobody to answer a permission prompt: the catalogue is both the
    # available set (--tools) and the pre-approved set (--allowedTools), and dontAsk denies
    # everything else instead of prompting.
    assert command[command.index("--tools") + 1] == "Read,Grep"
    assert command[command.index("--allowedTools") + 1] == "Read,Grep"
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert "--disallowedTools" in command and "mcp__*" in command
    # The owner's interactive hooks and MCP servers never run inside a worker: a blocking
    # SessionStart hook in the repo's .claude settings once stalled a step before turn one.
    assert json.loads(command[command.index("--settings") + 1]) == {"disableAllHooks": True}
    assert "--strict-mcp-config" in command
    session = adapters.ClaudeCodeAdapter().build_session_command(_contract(tools=["Read"]), None)
    assert "--strict-mcp-config" in session and "--settings" in session


def test_claude_adapter_drops_tools_claude_code_does_not_have():
    command = adapters.ClaudeCodeAdapter().build_command(
        _contract(tools=["Read", "inspect_page"], brief="look", model="sonnet"))
    assert command[command.index("--tools") + 1] == "Read"
    assert command[command.index("--model") + 1] == "sonnet"


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
    assert outcome.status == "needs-owner" and "needs your approval" in outcome.reason


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


def test_change_record_ignores_gitignored_output(tmp_path):
    # An `npm install` / build inside a mission worktree must not flood the change record:
    # ignored folders are not the team's changes and used to push it past its file limit.
    import subprocess
    from skye import journey
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("node_modules/\ndist/\n")
    (tmp_path / "app.js").write_text("one")
    start, _ = journey.repo_manifest(str(tmp_path))
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "index.js").write_text("dep")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "out.js").write_text("built")
    (tmp_path / "app.js").write_text("two")
    (tmp_path / "new.css").write_text("x")
    end, truncated = journey.repo_manifest(str(tmp_path))
    diff = journey.diff_manifests(start, end)
    assert not truncated
    assert diff == {"added": ["new.css"], "modified": ["app.js"], "deleted": []}


def test_preview_detects_how_a_result_runs(tmp_path):
    import json as _json
    from skye import preview
    (tmp_path / "package.json").write_text(_json.dumps(
        {"scripts": {"dev": "vite"}, "devDependencies": {"vite": "^6"}}))
    info = preview.detect(tmp_path)
    assert info["kind"] == "node" and info["script"] == "dev" and info["framework"] == "vite"
    assert preview._command(info, 5301)[-5:] == ["--port", "5301", "--strictPort", "--host", "127.0.0.1"]
    site = tmp_path / "plain"
    site.mkdir()
    (site / "index.html").write_text("<h1>hi</h1>")
    assert preview.detect(site)["kind"] == "static"
    empty = tmp_path / "empty"
    empty.mkdir()
    assert preview.detect(empty)["kind"] == "none"


def test_preview_rejects_unsafe_task_ids(tmp_path):
    import pytest as _pytest
    from skye import preview
    for bad in ("../etc", "a/b", ""):
        with _pytest.raises(KeyError):
            preview.tree_for(tmp_path, bad)


def _preview_task(state_dir, mission_id, record=None, repo=""):
    import json as _json
    mission_dir = state_dir / "missions" / mission_id
    mission_dir.mkdir(parents=True)
    (mission_dir / "mission.json").write_text(_json.dumps({"id": mission_id, "repo": repo}))
    if record is not None:
        from skye import workspaces
        workspaces._record_path(mission_dir).write_text(_json.dumps(record))
    return mission_dir


def test_preview_says_which_folder_it_runs_in(tmp_path):
    """A task's own worktree is isolated; a task without one runs in the project folder,
    and the preview must say so. A cleaned-up worktree is an error, never a quiet switch
    to the owner's project folder."""
    import pytest as _pytest
    from skye import preview
    project = tmp_path / "project"
    project.mkdir()
    (project / "index.html").write_text("<h1>project</h1>")
    copy = tmp_path / "copy"
    copy.mkdir()
    (copy / "index.html").write_text("<h1>copy</h1>")
    state = tmp_path / "state"
    _preview_task(state, "iso", {"worktree": str(copy), "repo": str(project)})
    _preview_task(state, "direct", {"worktree": "", "repo": str(project)})
    _preview_task(state, "gone", {"worktree": str(tmp_path / "missing"), "repo": str(project)})
    assert preview.status(state, "iso")["isolated"] is True
    assert preview.tree_for(state, "iso") == copy
    assert preview.status(state, "direct")["isolated"] is False
    with _pytest.raises(ValueError, match="cleaned up"):
        preview.status(state, "gone")
    assert preview.stop(state, "gone")["status"] == "stopped"


def test_preview_starts_and_stops_a_static_site(tmp_path):
    """End to end on a static site: the preview listens, then Stop leaves no process."""
    import time as _time
    import urllib.request as _req
    from skye import preview
    copy = tmp_path / "copy"
    copy.mkdir()
    (copy / "index.html").write_text("<h1>hello preview</h1>")
    state = tmp_path / "state"
    _preview_task(state, "site", {"worktree": str(copy), "repo": str(tmp_path)})
    preview.start(state, "site")
    deadline = _time.monotonic() + 20
    current = preview.status(state, "site")
    while current["status"] == "starting" and _time.monotonic() < deadline:
        _time.sleep(0.2)
        current = preview.status(state, "site")
    try:
        assert current["status"] == "ready", current
        assert b"hello preview" in _req.urlopen(current["url"], timeout=5).read()
        proc = preview._PREVIEWS["site"]["proc"]
    finally:
        stopped = preview.stop(state, "site")
    assert stopped["status"] == "stopped"
    assert proc.poll() is not None  # the server process is gone, not orphaned
    assert "site" not in preview._PREVIEWS
