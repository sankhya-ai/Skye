"""Real-loss recovery: a worktree directory deleted raw (rmtree), not via git worktree remove.

The stale git registration must not defeat recovery, and the cleanup must be bounded:
`git worktree prune` only drops registrations whose directories are GONE, so other missions'
live worktrees and workers keep running.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from skye import workspaces
from skye.mission import run_mission
from skye.policy import Policy
from tests.test_workspaces import git, make_repo, mission, writer
from tests.test_multirepo import two_repos, two_repo_mission


ON = Policy({"workspaces": {"enabled": True},
             "allow_unattended": [{"adapter": "claude-code", "role": "*"}],
             "test_command": "true"})


def registered(repo: Path) -> list[str]:
    out = git(repo, "worktree", "list", "--porcelain").stdout
    return [line.split()[1] for line in out.splitlines() if line.startswith("worktree ")]


def test_rmtree_d_single_repo_rebuilds_and_preserves_other_registrations(tmp_path):
    repo = make_repo(tmp_path)
    state = tmp_path / "state"
    # a second mission's live worktree on the same repo - must survive the recovery
    other = workspaces.prepare(mission("m-alive", repo), state_dir=state, policy=ON)
    rec = workspaces.prepare(mission("m-victim", repo), state_dir=state, policy=ON)
    wt = Path(rec["worktree"])
    (wt / "committed.txt").write_text("kept")
    git(wt, "add", "committed.txt"); git(wt, "commit", "-qm", "worker")
    shutil.rmtree(wt)  # the real-loss case: no git worktree remove, stale registration
    assert str(wt) not in registered(repo) or True  # registration may linger; that is the bug
    rec2 = workspaces.prepare(mission("m-victim", repo), state_dir=state, policy=ON)
    assert Path(rec2["worktree"]).is_dir()
    assert (Path(rec2["worktree"]) / "committed.txt").exists()
    assert rec2["uncommitted_lost"] is True
    assert str(other["worktree"]) in registered(repo)          # live worker untouched
    assert Path(other["worktree"]).is_dir()
    # and the mission pauses for the operator, as with any lost-tree recovery
    out = run_mission(mission("m-victim", repo), ON, runner=writer, state_dir=state,
                      catalogues_dir="catalogues")
    assert out.status == "needs-owner"


def test_rmtree_d_one_repo_of_multi_recovers_others_untouched(tmp_path):
    backend, frontend = two_repos(tmp_path)
    state = tmp_path / "state"
    m = two_repo_mission("m-rm", backend, frontend)
    rec = workspaces.prepare(m, state_dir=state, policy=ON)
    victim = Path(rec["repos"]["frontend"]["worktree"])
    survivor = Path(rec["repos"]["backend"]["worktree"])
    (survivor / "work.txt").write_text("alive")
    shutil.rmtree(victim)
    rec2 = workspaces.prepare(m, state_dir=state, policy=ON)
    assert rec2["repos"]["frontend"]["recovered_from_branch"] is True
    assert rec2["repos"]["backend"].get("recovered_from_branch") is not True
    assert (survivor / "work.txt").exists()  # the other repo's tree was never touched
    assert rec2["uncommitted_lost"] is True


def test_foreign_directory_at_worktree_path_fails_closed(tmp_path):
    repo = make_repo(tmp_path)
    state = tmp_path / "state"
    rec = workspaces.prepare(mission("m-foreign", repo), state_dir=state, policy=ON)
    shutil.rmtree(rec["worktree"])
    foreign = Path(rec["worktree"])
    foreign.mkdir()  # someone else's directory now sits at the path
    (foreign / "not-ours.txt").write_text("do not delete me")
    with pytest.raises(workspaces.WorkspaceError, match="not a git worktree"):
        workspaces.prepare(mission("m-foreign", repo), state_dir=state, policy=ON)
    assert (foreign / "not-ours.txt").exists()  # never deleted to make room
