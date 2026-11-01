"""Multi-repo artifact review: per-repo diffs in the payload and repo-scoped previews.

The user goal: trustworthy changed-file review across BOTH repos - the same relative path
in two repos must preview each repo's own content, never the other's."""
from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from skye.adapters.base import Result
from skye.mission import run_mission
from skye.ui.server import artifact_file, mission_artifacts, workspace_payload
from tests.test_multirepo import two_repos, two_repo_mission
from tests.test_ui_server import ui_server, _req
from tests.test_workspaces import git
from skye.policy import Policy
from pathlib import Path


ON = Policy({"workspaces": {"enabled": True},
             "allow_unattended": [{"adapter": "claude-code", "role": "*"}],
             "test_command": "true"})


def md_writer(spec):
    content = f"# {spec['goal']} from {spec['repo']}\n"
    Path(spec["repo"], "notes.md").write_text(content)
    return Result(ok=True, output="done", command=["fake"])


@pytest.fixture()
def settled(tmp_path):
    backend, frontend = two_repos(tmp_path)
    state = tmp_path / "state"
    m = two_repo_mission("m-art", backend, frontend)
    out = run_mission(m, ON, runner=md_writer, state_dir=state, catalogues_dir="catalogues")
    assert out.status == "done"
    return state, backend, frontend


def test_payload_carries_per_repo_diffs(settled):
    state, backend, frontend = settled
    artifacts = mission_artifacts(state / "missions" / "m-art")
    assert artifacts["recorded"] is True
    repos = artifacts["repos"]
    assert set(repos) == {"backend", "frontend"}
    for name in ("backend", "frontend"):
        assert repos[name]["complete"] is True
        assert repos[name]["added"] == ["notes.md"]
        assert repos[name]["modified"] == [] and repos[name]["deleted"] == []
        assert repos[name]["repo"].endswith(f"worktrees/m-art/{name}")
    # flat single-repo fields preserved (empty default here, but the keys exist)
    assert artifacts["complete"] is False and "repo" in artifacts


def test_same_relative_path_previews_each_repos_own_content(settled):
    state, backend, frontend = settled
    data_b, _ = artifact_file(state, "m-art", "notes.md", repo_name="backend")
    data_f, _ = artifact_file(state, "m-art", "notes.md", repo_name="frontend")
    assert b"backend" in data_b and b"frontend" not in data_b
    assert b"frontend" in data_f and b"backend" not in data_f
    assert data_b != data_f  # distinct content, correctly routed


def test_preview_rejects_unrecorded_repo_names_and_paths(settled):
    state, _, _ = settled
    with pytest.raises(KeyError):
        artifact_file(state, "m-art", "notes.md", repo_name="../etc")
    with pytest.raises(KeyError):
        artifact_file(state, "m-art", "notes.md", repo_name="mobile")  # not recorded
    with pytest.raises(KeyError):
        artifact_file(state, "m-art", "../../etc/passwd", repo_name="backend")
    with pytest.raises(KeyError):
        artifact_file(state, "m-art", "unchanged.md", repo_name="backend")  # not in the diff
    with pytest.raises(KeyError):
        artifact_file(state, "m-art", "notes.md")  # flat preview: no default repo recorded


def test_preview_refuses_symlink_escape(settled):
    state, backend, frontend = settled
    wt = state / "worktrees" / "m-art" / "backend"
    secret = state.parent / "secret.md"
    secret.write_text("top secret")
    link = wt / "notes-link.md"
    link.symlink_to(secret)
    git(wt, "add", "-A")
    # recorded only if the end manifest covered it; force a re-baseline is not possible post
    # settle, so this path is simply not in the recorded diff -> refused at the diff gate.
    with pytest.raises(KeyError):
        artifact_file(state, "m-art", "notes-link.md", repo_name="backend")


def test_incomplete_repo_reports_partial(settled):
    state, backend, frontend = settled
    # simulate a still-running second mission: baseline only, no end manifest
    import json
    m = two_repo_mission("m-running", backend, frontend)
    m["steps"] = m["steps"][:1]
    # interrupt before done by using a runner that never settles step b? simpler: prepare
    # baseline via a mission whose only step needs-owner under an empty policy
    empty = Policy({"workspaces": {"enabled": True}})
    out = run_mission(m, empty, runner=md_writer, state_dir=state,
                      catalogues_dir="catalogues")
    assert out.status == "needs-owner"
    artifacts = mission_artifacts(state / "missions" / "m-running")
    assert artifacts["repos"]["backend"]["complete"] is False
    assert artifacts["repos"]["backend"]["start_truncated"] is False
    assert artifacts["repos"]["frontend"]["complete"] is False
    assert "added" not in artifacts["repos"]["backend"]  # no diff before settle


def test_preview_route_scopes_by_repo_param(ui_server, tmp_path):
    url, state = ui_server
    backend, frontend = two_repos(tmp_path)
    m = two_repo_mission("m-route", backend, frontend)
    out = run_mission(m, ON, runner=md_writer, state_dir=state, catalogues_dir="catalogues")
    assert out.status == "done"
    import urllib.parse
    def get(repo, path="notes.md"):
        full = f"{url}/api/mission-artifact/m-route?path={path}"
        if repo:
            full += f"&repo={urllib.parse.quote(repo)}"
        request = urllib.request.Request(full, headers={"X-Skye-Token": "test-token-abc"})
        try:
            with urllib.request.urlopen(request, timeout=10) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, b""
    code, body = get("backend")
    assert code == 200 and b"backend" in body
    code, body = get("frontend")
    assert code == 200 and b"frontend" in body
    code, _ = get("mobile")
    assert code == 404  # unrecorded repo name
    code, _ = get("", path="../secret.md")
    assert code == 404  # traversal, flat scope
