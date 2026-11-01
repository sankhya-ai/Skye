"""The clone -> ./start -> Claude Code team path: permissions that actually let a headless
worker write, gates that do not fail projects without tests, frugal staffing, usage
capture, and handing finished work back to the owner's project."""

import json
import subprocess
from pathlib import Path

import pytest

from skye import claude_usage, workspaces
from skye.adapters import ClaudeCodeAdapter
from skye.adapters.base import Result
from skye.contract import validate
from skye.dispatcher import dispatch
from skye.mission import _handoff_brief
from skye.policy import Policy
from skye.ui import server as ui


def _contract(**over):
    base = {"goal": "fix it", "repo": "/tmp/r", "role": "developer", "adapter": "claude-code"}
    base.update(over)
    return base


class _Bridge:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


# ----- gates -----

def test_default_tests_gate_is_skipped_visibly_without_a_test_command(tmp_path):
    out = dispatch(_contract(repo=str(tmp_path)),
                   Policy({"allow_unattended": [{"adapter": "claude-code", "role": "*"}]}),
                   runner=lambda s: Result(ok=True, output="done"), bridge=_Bridge())
    assert out.status == "done"
    [gate] = out.gates
    assert gate["gate"] == "tests" and gate["ok"] and gate["skipped"]


def test_explicit_tests_gate_still_fails_closed_without_a_test_command(tmp_path):
    out = dispatch(_contract(repo=str(tmp_path), gates=["tests"]),
                   Policy({"allow_unattended": [{"adapter": "claude-code", "role": "*"}]}),
                   runner=lambda s: Result(ok=True, output="done"), bridge=_Bridge())
    assert out.status == "gate-failed" and "no test command" in out.reason


def test_policy_models_choose_the_worker_model_per_role(tmp_path):
    seen = {}

    def runner(spec):
        seen.update(spec)
        return Result(ok=True, output="ok")
    dispatch(_contract(repo=str(tmp_path), role="pm", gates=[]),
             Policy({"allow_unattended": [{"adapter": "claude-code", "role": "*"}],
                     "models": {"pm": "sonnet"}}), runner=runner, bridge=_Bridge())
    assert seen["model"] == "sonnet"


def test_policy_load_honors_skye_policy_env(tmp_path, monkeypatch):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"test_command": "make check"}))
    monkeypatch.setenv("SKYE_POLICY", str(path))
    assert Policy.load().test_command == "make check"


# ----- Claude Code protocol -----

def test_session_command_preapproves_exactly_the_role_catalogue():
    command = ClaudeCodeAdapter().build_session_command(
        validate(_contract(tools=["Read", "Edit", "Bash"])) | {"tools": ["Read", "Edit", "Bash"]},
        None)
    assert command[command.index("--allowedTools") + 1] == "Read,Edit,Bash"
    assert command[command.index("--permission-mode") + 1] == "dontAsk"


def test_stream_parsing_captures_rate_limits_and_denials():
    adapter = ClaudeCodeAdapter()
    [rl] = adapter.parse_stream_line(json.dumps({
        "type": "rate_limit_event", "session_id": "s",
        "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour",
                            "resetsAt": 1900000000, "utilization": 0.4}}))
    assert rl["kind"] == "rate-limit" and rl["info"]["utilization"] == 0.4
    [result] = adapter.parse_stream_line(json.dumps({
        "type": "result", "subtype": "success", "is_error": False, "session_id": "s",
        "num_turns": 3, "total_cost_usd": 0.02, "usage": {"input_tokens": 10, "output_tokens": 5},
        "permission_denials": [{"tool_name": "WebFetch"}]}))
    assert result["ok"] and result["usage"]["permission_denials"] == 1
    assert result["usage"]["denied_tools"] == ["WebFetch"]
    assert result["usage"]["output_tokens"] == 5


def test_session_prompt_carries_role_instructions(tmp_path, monkeypatch):
    """The stdin prompt is the FULL prompt (instructions + brief), not the bare brief."""
    sent = {}
    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(adapter, "prompt_via_stdin", lambda text: sent.setdefault("t", text) and None)
    monkeypatch.setattr(adapter, "available", lambda: True)
    monkeypatch.setattr(adapter, "build_session_command",
                        lambda c, r: ["python3", "-c", "print('')"])
    adapter.run_session({"goal": "g", "brief": "the brief", "repo": str(tmp_path),
                         "role_instructions": "You are the reviewer."}, timeout=10)
    assert sent["t"].startswith("You are the reviewer.") and "the brief" in sent["t"]


# ----- usage store -----

def test_usage_store_keeps_latest_window_and_totals(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_usage, "account", lambda force=False: {"installed": True,
                                                                      "logged_in": True})
    claude_usage.record_rate_limit({"status": "allowed", "rateLimitType": "five_hour",
                                    "resetsAt": 4102444800, "utilization": 0.1}, tmp_path)
    claude_usage.record_rate_limit({"status": "allowed_warning", "rateLimitType": "five_hour",
                                    "resetsAt": 4102444800, "utilization": 0.8}, tmp_path)
    claude_usage.record_run({"num_turns": 4, "total_cost_usd": 0.05}, role="developer",
                            state_dir=tmp_path)
    snap = claude_usage.snapshot(tmp_path)
    [window] = snap["windows"]
    assert window["status"] == "allowed_warning" and window["utilization"] == 0.8
    assert snap["skye_totals"]["runs"] == 1 and snap["skye_totals"]["turns"] == 4


# ----- staffing -----

def test_solo_mission_is_one_developer_step_and_no_planner(tmp_path):
    mission_id = ui.create_mission(tmp_path / "st", {"goal": "fix the typo", "solo": True,
                                                     "repo": str(tmp_path)})
    snap = json.loads((tmp_path / "st" / "missions" / mission_id / "mission.json").read_text())
    [step] = snap["steps"]
    assert step["role"] == "developer" and step["id"] == "develop"


def test_mission_without_a_project_is_refused(tmp_path):
    with pytest.raises(Exception, match="project folder"):
        ui.create_mission(tmp_path / "st", {"goal": "anything", "plan": True})


def test_planner_brief_asks_for_the_smallest_team(tmp_path):
    mission_id = ui.create_mission(tmp_path / "st", {"goal": "add dark mode", "plan": True,
                                                     "repo": str(tmp_path),
                                                     "adapter": "claude-code"})
    snap = json.loads((tmp_path / "st" / "missions" / mission_id / "mission.json").read_text())
    brief = snap["steps"][0]["brief"]
    assert "exactly ONE step" in brief and '"adapter": "claude-code"' in brief
    assert "{{" not in brief


def test_lone_worker_brief_has_no_team_line_instructions():
    step = {"id": "d", "role": "developer", "goal": "g"}
    assert "skye say" not in _handoff_brief(step, {}, mission_id="m", teammates=False)
    assert "skye say" in _handoff_brief(step, {}, mission_id="m", teammates=True)


# ----- handing work back -----

def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                           *args], capture_output=True, text=True, check=True)


def test_bring_into_project_commits_and_merges_the_mission_branch(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("one\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    mission_dir = tmp_path / "st" / "missions" / "m1"
    mission_dir.mkdir(parents=True)
    record = workspaces.prepare({"id": "m1", "repo": str(repo), "steps": []},
                                state_dir=tmp_path / "st",
                                policy=Policy({"workspaces": {"enabled": True}}))
    worktree = Path(record["worktree"])
    (worktree / "a.txt").write_text("two\n")  # the worker's uncommitted change
    (worktree / "__pycache__").mkdir()
    (worktree / "__pycache__" / "a.cpython-313.pyc").write_bytes(b"junk")  # test-run droppings
    status = workspaces.delivery_status(mission_dir)
    assert status["repos"]["default"]["uncommitted_files"] == 1
    result = workspaces.deliver_to_project(mission_dir, message="change a")
    assert result["ok"] and result["repos"]["default"]["merged"]
    assert (repo / "a.txt").read_text() == "two\n"
    assert not (repo / "__pycache__").exists()
    again = workspaces.deliver_to_project(mission_dir, message="change a")
    assert again["ok"] and not again["repos"]["default"]["merged"]


def test_bring_into_project_never_clobbers_owner_edits(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("one\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    mission_dir = tmp_path / "st" / "missions" / "m2"
    mission_dir.mkdir(parents=True)
    record = workspaces.prepare({"id": "m2", "repo": str(repo), "steps": []},
                                state_dir=tmp_path / "st",
                                policy=Policy({"workspaces": {"enabled": True}}))
    (Path(record["worktree"]) / "a.txt").write_text("team\n")
    (repo / "a.txt").write_text("mine, uncommitted\n")
    result = workspaces.deliver_to_project(mission_dir, message="team edit")
    assert not result["ok"]
    assert (repo / "a.txt").read_text() == "mine, uncommitted\n"


# ----- per-agent models -----

def test_owner_picks_a_model_per_agent_through_the_ui(tmp_path, monkeypatch):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"test_command": "true", "models": {"pm": "sonnet"}}))
    monkeypatch.setenv("SKYE_POLICY", str(policy))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), ui.UiHandler)
    httpd.state_dir = tmp_path
    httpd.token = "tok"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(path, body=None):
        req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Skye-Token": "tok",
                                              "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())
    try:
        assert call("/api/policy/models", {"role": "pm", "model": "opus"})[0] == 200
        assert call("/api/policy/models", {"role": "developer", "model": "sonnet"})[0] == 200
        assert call("/api/policy/models", {"role": "reviewer", "model": "opus"})[0] == 200
        saved = json.loads(policy.read_text())
        assert saved["models"] == {"pm": "opus", "developer": "sonnet", "reviewer": "opus"}
        assert saved["test_command"] == "true"  # nothing else in the policy changes
        _, roles = call("/api/roles")
        by_name = {r["name"]: r for r in roles["roles"]}
        assert by_name["pm"]["model"] == "opus" and by_name["tester"]["model"] == ""
        assert any(c["value"] == "haiku" for c in roles["model_choices"])
        assert call("/api/policy/models", {"role": "pm", "model": ""})[0] == 200  # back to default
        assert "pm" not in json.loads(policy.read_text())["models"]
        assert call("/api/policy/models", {"role": "wizard", "model": "opus"})[0] == 400
        assert call("/api/policy/models", {"role": "pm", "model": "opus; rm -rf /"})[0] == 400
    finally:
        httpd.shutdown()


def test_each_dispatched_role_gets_its_own_model(tmp_path):
    seen = {}

    def runner(spec):
        seen[spec["role"]] = spec.get("model")
        return Result(ok=True, output='{"verdict": "pass"}')
    policy = Policy({"allow_unattended": [{"adapter": "claude-code", "role": "*"}],
                     "models": {"pm": "opus", "developer": "sonnet", "reviewer": "opus"}})
    for role in ("pm", "developer", "reviewer", "tester"):
        dispatch(_contract(repo=str(tmp_path), role=role, gates=[]), policy, runner=runner,
                 bridge=_Bridge())
    assert seen == {"pm": "opus", "developer": "sonnet", "reviewer": "opus", "tester": None}
