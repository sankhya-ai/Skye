"""The composer accepts owner-supplied repos maps, validated exactly like a mission file."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from skye.mission import MissionError
from skye.ui import server as ui
from tests.test_multirepo import two_repos
from tests.test_ui_server import ui_server, _req
from tests.test_workspaces import git


def test_create_mission_accepts_and_validates_repos(tmp_path):
    backend, frontend = two_repos(tmp_path)
    state = tmp_path / "st"
    mid = ui.create_mission(state, {
        "goal": "ship across both", "repos": {"backend": str(backend),
                                              "frontend": str(frontend)},
        "steps": [{"id": "api", "role": "developer", "adapter": "claude-code",
                   "goal": "endpoint", "repo": "backend", "gates": []}]})
    snapshot = json.loads((state / "missions" / mid / "mission.json").read_text())
    assert snapshot["repos"] == {"backend": str(backend), "frontend": str(frontend)}
    assert snapshot["repo"] == ""  # no guessed default when repos carry the scope


def test_create_mission_rejects_invalid_repos_same_as_files(tmp_path):
    backend, frontend = two_repos(tmp_path)
    with pytest.raises(MissionError, match="same path"):
        ui.create_mission(tmp_path / "st", {
            "goal": "x", "repos": {"a": str(backend), "b": str(backend) + "/."},
            "steps": [{"id": "s", "role": "developer", "adapter": "claude-code",
                       "goal": "g", "repo": "a", "gates": []}]})
    with pytest.raises(MissionError, match="unknown repo name"):
        ui.create_mission(tmp_path / "st", {
            "goal": "x", "repos": {"a": str(backend)},
            "steps": [{"id": "s", "role": "developer", "adapter": "claude-code",
                       "goal": "g", "repo": "nope", "gates": []}]})


def test_post_missions_with_repos_runs_per_repo_worktrees(ui_server, tmp_path, monkeypatch):
    url, state_dir = ui_server
    backend, frontend = two_repos(tmp_path)
    monkeypatch.setenv("SKYE_POLICY", str(tmp_path / "policy.json"))
    (tmp_path / "policy.json").write_text(json.dumps({
        "workspaces": {"enabled": True},
        "allow_unattended": [{"adapter": "claude-code", "role": "*"}],
        "test_command": "true"}))
    from skye.adapters.base import Result
    monkeypatch.setattr(ui, "RUNNER",
                        lambda spec: Result(ok=True, output="done", command=["fake"]))
    code, data = _req(url, "/api/missions", method="POST", body={
        "goal": "both repos", "run": True,
        "repos": {"backend": str(backend), "frontend": str(frontend)},
        "steps": [{"id": "a", "role": "developer", "adapter": "claude-code",
                   "goal": "api", "repo": "backend", "gates": []},
                  {"id": "b", "role": "developer", "adapter": "claude-code",
                   "goal": "ui", "repo": "frontend", "needs": ["a"], "gates": []}]})
    assert code == 200 and data["running"] is True
    import time
    for _ in range(50):
        _, detail = _req(url, f"/api/missions/{data['id']}")
        if detail["status"]["status"] in ("done", "failed", "needs-owner"):
            break
        time.sleep(0.2)
    assert detail["status"]["status"] == "done"
    ws = detail["workspace"]
    assert ws["mode"] == "multi-worktree"
    assert set(ws["repos"]) == {"backend", "frontend"}


def test_mission_setup_contract_served(ui_server):
    url, _ = ui_server
    code, data = _req(url, "/api/mission-setup")
    assert code == 200
    assert "repos" in data["fields"] and "repo" in data["fields"]
    assert "plain" in data["fields"]["repos"]["note"]
    assert data["fields"]["goal"]["required"] is True
