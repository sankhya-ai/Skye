"""Multi-repository missions: named repos, per-repo worktrees, cross-repo step chains.

The user's product shape: a project may be a monorepo (the one-repo case, unchanged) OR
separate backend/frontend/mobile repos with agents working across them. Separate repos are
never reinterpreted as paths inside one repo: each named repo is its own git repository with
its own worktree, branch, baseline and recovery semantics.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from skye import workspaces
from skye.adapters.base import Result
from skye.mission import MissionError, load_mission, run_mission
from skye.policy import Policy
from tests.test_workspaces import git, make_repo


ON = Policy({"workspaces": {"enabled": True},
             "allow_unattended": [{"adapter": "claude-code", "role": "*"}],
             "test_command": "true"})


def two_repos(tmp: Path):
    return make_repo(tmp, "backend"), make_repo(tmp, "frontend")


def two_repo_mission(mid: str, backend: Path, frontend: Path) -> dict:
    return {"id": mid, "title": mid, "goal": "ship the feature across both repos",
            "repos": {"backend": str(backend), "frontend": str(frontend)},
            "steps": [
                {"id": "api", "role": "developer", "adapter": "claude-code",
                 "goal": "add the endpoint", "repo": "backend", "gates": []},
                {"id": "ui", "role": "developer", "adapter": "claude-code",
                 "goal": "call the endpoint", "repo": "frontend", "needs": ["api"],
                 "gates": []}]}


def writer(spec):
    Path(spec["repo"], f"{spec['role']}-was-here.txt").write_text(spec["repo"] + "\n")
    return Result(ok=True, output="done", command=["fake"])


def test_steps_run_in_separate_worktrees_per_repo(tmp_path):
    backend, frontend = two_repos(tmp_path)
    state = tmp_path / "state"
    seen = []
    def spy(spec):
        seen.append((spec["goal"][:3], spec["repo"]))
        return writer(spec)
    out = run_mission(two_repo_mission("m-multi", backend, frontend), ON, runner=spy,
                      state_dir=state, catalogues_dir="catalogues")
    assert out.status == "done"
    wt_backend = state / "worktrees" / "m-multi" / "backend"
    wt_frontend = state / "worktrees" / "m-multi" / "frontend"
    assert seen[0][1] == str(wt_backend) and seen[1][1] == str(wt_frontend)
    assert (wt_backend / "developer-was-here.txt").exists()
    assert (wt_frontend / "developer-was-here.txt").exists()
    # each repo carries its own branch; neither owner checkout was touched
    assert git(backend, "rev-parse", "--verify", "skye/m-multi").returncode == 0
    assert git(frontend, "rev-parse", "--verify", "skye/m-multi").returncode == 0
    assert not (backend / "developer-was-here.txt").exists()
    assert not (frontend / "developer-was-here.txt").exists()
    # both repos baselined for the delivery diff
    artifacts = json.loads((state / "missions" / "m-multi" / "artifacts.json").read_text())
    assert set(artifacts["repos"]) == {"backend", "frontend"}
    assert artifacts["repos"]["backend"]["start"] and artifacts["repos"]["frontend"]["start"]


def test_cross_repo_needs_chain_orders_work(tmp_path):
    backend, frontend = two_repos(tmp_path)
    order = []
    def spy(spec):
        order.append(spec["goal"])
        return Result(ok=True, output="ok", command=["fake"])
    out = run_mission(two_repo_mission("m-chain", backend, frontend), ON, runner=spy,
                      state_dir=tmp_path / "state", catalogues_dir="catalogues")
    assert out.status == "done"
    assert order == ["add the endpoint", "call the endpoint"]


def test_unknown_bare_repo_name_rejected(tmp_path):
    backend, frontend = two_repos(tmp_path)
    mission = two_repo_mission("m-bad", backend, frontend)
    mission["steps"][0]["repo"] = "mobil"  # typo: bare name, not declared
    path = tmp_path / "m.json"
    path.write_text(json.dumps(mission))
    with pytest.raises(MissionError, match="unknown repo name 'mobil'.*backend, frontend"):
        load_mission(path)


def test_literal_path_override_still_works_beside_names(tmp_path):
    backend, frontend = two_repos(tmp_path)
    other = make_repo(tmp_path, "other")
    mission = two_repo_mission("m-lit", backend, frontend)
    mission["steps"][1]["repo"] = str(other)  # explicit path: author's own opt-out
    path = tmp_path / "m.json"
    path.write_text(json.dumps(mission))
    loaded = load_mission(path)  # validates: literal paths are still legal
    assert loaded["repos"]["backend"] == str(backend)
    seen = []
    def spy(spec):
        seen.append(spec["repo"])
        return Result(ok=True, output="ok", command=["fake"])
    out = run_mission(loaded, ON, runner=spy, state_dir=tmp_path / "state",
                      catalogues_dir="catalogues")
    assert out.status == "done"
    assert seen[1] == str(other)  # literal override honored verbatim


def test_nongit_named_repo_fails_closed(tmp_path):
    backend, _ = two_repos(tmp_path)
    plain = tmp_path / "mobile"
    plain.mkdir()
    mission = two_repo_mission("m-ng", backend, plain)
    mission["repos"]["frontend"] = str(plain)
    del mission["repos"]["backend"]
    mission["repo"] = str(backend)
    with pytest.raises(workspaces.WorkspaceError, match="'frontend'.*not a git repository"):
        workspaces.prepare(mission, state_dir=tmp_path / "state", policy=ON)


def test_lost_tree_in_one_repo_pauses_whole_mission(tmp_path):
    backend, frontend = two_repos(tmp_path)
    state = tmp_path / "state"
    m = two_repo_mission("m-loss", backend, frontend)
    rec = workspaces.prepare(m, state_dir=state, policy=ON)
    git(frontend, "worktree", "remove", "--force",
        rec["repos"]["frontend"]["worktree"])
    ran = []
    out = run_mission(m, ON, runner=lambda s: ran.append(1) or writer(s),
                      state_dir=state, catalogues_dir="catalogues")
    assert out.status == "needs-owner" and ran == []  # paused, nothing dispatched
    record = workspaces.read_record(state / "missions" / "m-loss")
    assert record["uncommitted_lost"] is True
    assert record["repos"]["frontend"]["recovered_from_branch"] is True
    assert record["repos"]["backend"].get("recovered_from_branch") is not True
    workspaces.acknowledge_recovery(state / "missions" / "m-loss", actor="ashish")
    out2 = run_mission(m, ON, runner=lambda s: ran.append(1) or writer(s),
                       state_dir=state, catalogues_dir="catalogues")
    assert out2.status == "done" and len(ran) == 2


def test_payload_reports_per_repo_modes(tmp_path):
    from skye.ui.server import workspace_payload
    backend, frontend = two_repos(tmp_path)
    state = tmp_path / "state"
    run_mission(two_repo_mission("m-pay", backend, frontend), ON, runner=writer,
                state_dir=state, catalogues_dir="catalogues")
    payload = workspace_payload(state / "missions" / "m-pay")
    assert payload["mode"] == "multi-worktree"
    assert set(payload["repos"]) == {"backend", "frontend"}
    assert all(r["mode"] == "worktree" for r in payload["repos"].values())
    assert all(r["branch"] == "skye/m-pay" for r in payload["repos"].values())
    assert payload["recovery_pending"] is False


def test_reset_releases_each_repo_worktree_when_clean(tmp_path):
    backend, frontend = two_repos(tmp_path)
    state = tmp_path / "state"
    run_mission(two_repo_mission("m-rel", backend, frontend), ON, runner=writer,
                state_dir=state, catalogues_dir="catalogues")
    record = workspaces.read_record(state / "missions" / "m-rel")
    # worker writes are uncommitted in both worktrees -> both kept
    result = workspaces.release(record)
    assert result["removed"] is False and set(result["per_repo"]) == {"backend", "frontend"}
    for rec in record["repos"].values():
        wt = Path(rec["worktree"])
        git(wt, "add", "-A"); git(wt, "commit", "-qm", "done")
    from skye.mission import reset_mission
    reset_mission(two_repo_mission("m-rel", backend, frontend), state_dir=state)
    for rec in record["repos"].values():
        assert not Path(rec["worktree"]).exists()  # clean now: removed
    assert git(backend, "rev-parse", "--verify", "skye/m-rel").returncode == 0  # branch kept
    assert git(frontend, "rev-parse", "--verify", "skye/m-rel").returncode == 0


def test_interrupted_prepare_retry_recovers_honestly(tmp_path):
    """A prepare that died after creating one repo's branch must not fail the retry with
    'branch exists' nor pretend a fresh start: the retry rebuilds from the branch, flags the
    uncertainty, and the aggregate pause applies."""
    backend, frontend = two_repos(tmp_path)
    state = tmp_path / "state"
    m = two_repo_mission("m-retry", backend, frontend)
    # simulate the interrupted first prepare: a stray branch in frontend, no record
    git(frontend, "branch", "skye/m-retry")
    rec = workspaces.prepare(m, state_dir=state, policy=ON)
    assert rec["repos"]["frontend"]["recovered_from_branch"] is True
    assert rec["uncommitted_lost"] is True  # the pause will fire until the operator acks
