"""Workspace slice: per-mission git worktrees, honestly framed (file separation, not a sandbox)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from skye import workspaces
from skye.mission import run_mission, reset_mission
from skye.policy import Policy


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True)


def make_repo(tmp: Path, name: str = "repo") -> Path:
    repo = tmp / name
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t.t")
    git(repo, "config", "user.name", "t")
    (repo / "base.txt").write_text("base\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


def mission(mid: str, repo: Path) -> dict:
    return {"id": mid, "title": mid, "repo": str(repo),
            "steps": [{"id": "dev", "role": "developer", "adapter": "claude-code",
                       "goal": "write worker.txt", "gates": []}]}


ON = Policy({"workspaces": {"enabled": True},
             "allow_unattended": [{"adapter": "claude-code", "role": "*"}],
             "test_command": "true"})
OFF = Policy({"allow_unattended": [{"adapter": "claude-code", "role": "*"}],
              "test_command": "true"})


def writer(spec):
    Path(spec["repo"], "worker.txt").write_text("w\n")
    from skye.adapters.base import Result
    return Result(ok=True, output="done", command=["fake"])


def run(m, state: Path, policy):
    return run_mission(m, policy, runner=writer, state_dir=state,
                       catalogues_dir="catalogues")


def test_disabled_runs_on_shared_tree(tmp_path):
    repo = make_repo(tmp_path)
    out = run(mission("m-off", repo), tmp_path / "state", OFF)
    assert out.status == "done"
    assert (repo / "worker.txt").exists()  # shared tree, exactly as before


def test_enabled_runs_in_worktree_owner_tree_untouched(tmp_path):
    repo = make_repo(tmp_path)
    state = tmp_path / "state"
    out = run(mission("m-wt", repo), state, ON)
    assert out.status == "done"
    wt = state / "worktrees" / "m-wt"
    assert (wt / "worker.txt").exists()
    assert not (repo / "worker.txt").exists()  # owner's checkout untouched
    assert git(repo, "rev-parse", "--verify", "skye/m-wt").returncode == 0
    rec = json.loads((state / "missions" / "m-wt" / "workspace.json").read_text())
    assert rec["worktree"] == str(wt) and rec["branch"] == "skye/m-wt"
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == rec["base_rev"]


def test_prepare_idempotent_resume_reuses(tmp_path):
    repo = make_repo(tmp_path)
    state = tmp_path / "state"
    m = mission("m-res", repo)
    r1 = workspaces.prepare(m, state_dir=state, policy=ON)
    (Path(r1["worktree"]) / "progress.txt").write_text("x")
    r2 = workspaces.prepare(m, state_dir=state, policy=ON)
    assert r2["worktree"] == r1["worktree"]
    assert (Path(r2["worktree"]) / "progress.txt").exists()


def test_dirty_base_recorded_and_not_carried(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "wip.txt").write_text("owner work in progress")
    state = tmp_path / "state"
    rec = workspaces.prepare(mission("m-dirty", repo), state_dir=state, policy=ON)
    assert "wip.txt" in rec["dirty_at_start"]
    assert not (Path(rec["worktree"]) / "wip.txt").exists()  # owner WIP stays out


def test_worktree_lost_branch_survives_recreates_from_branch(tmp_path):
    repo = make_repo(tmp_path)
    state = tmp_path / "state"
    rec = workspaces.prepare(mission("m-lost", repo), state_dir=state, policy=ON)
    wt = Path(rec["worktree"])
    (wt / "work.txt").write_text("committed worker output")
    git(wt, "add", "-A"); git(wt, "commit", "-qm", "worker")
    git(repo, "worktree", "remove", str(wt))  # tree lost, branch kept
    rec2 = workspaces.prepare(mission("m-lost", repo), state_dir=state, policy=ON)
    assert (Path(rec2["worktree"]) / "work.txt").exists()  # recovered from the branch


def test_record_but_tree_and_branch_gone_fails_closed(tmp_path):
    repo = make_repo(tmp_path)
    state = tmp_path / "state"
    rec = workspaces.prepare(mission("m-gone", repo), state_dir=state, policy=ON)
    git(repo, "worktree", "remove", str(rec["worktree"]))
    git(repo, "branch", "-D", "skye/m-gone")
    with pytest.raises(workspaces.WorkspaceError, match="not silently restarting"):
        workspaces.prepare(mission("m-gone", repo), state_dir=state, policy=ON)


def test_parallel_missions_separate_trees_and_branches(tmp_path):
    repo = make_repo(tmp_path)
    state = tmp_path / "state"
    a = run(mission("m-a", repo), state, ON)
    b = run(mission("m-b", repo), state, ON)
    assert a.status == b.status == "done"
    assert (state / "worktrees" / "m-a" / "worker.txt").exists()
    assert (state / "worktrees" / "m-b" / "worker.txt").exists()
    branches = git(repo, "branch", "--list", "skye/*").stdout
    assert "skye/m-a" in branches and "skye/m-b" in branches
    assert not (repo / "worker.txt").exists()


def test_nongit_repo_falls_back_with_reason(tmp_path):
    plain = tmp_path / "plain"; plain.mkdir()
    rec = workspaces.prepare(mission("m-plain", plain), state_dir=tmp_path / "state", policy=ON)
    assert rec["worktree"] == "" and "not a git repository" in rec["reason"]


def test_reset_removes_clean_worktree_keeps_dirty_and_branch(tmp_path):
    repo = make_repo(tmp_path)
    state = tmp_path / "state"
    run(mission("m-clean", repo), state, ON)
    # the worker write is uncommitted in the worktree -> dirty -> kept
    rec = workspaces.read_record(state / "missions" / "m-clean")
    result = workspaces.release(rec)
    assert result["removed"] is False and "uncommitted" in result["reason"]
    wt = Path(rec["worktree"])
    git(wt, "add", "-A"); git(wt, "commit", "-qm", "done")
    reset_mission(mission("m-clean", repo), state_dir=state)
    assert not wt.exists()  # clean now: removed
    assert git(repo, "rev-parse", "--verify", "skye/m-clean").returncode == 0  # branch kept
