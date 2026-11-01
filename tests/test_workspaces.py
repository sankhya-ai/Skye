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
    """Recovery is honest: committed work comes back via the branch; the lost tree's
    UNCOMMITTED changes are gone and the record says so - never a silent full recovery."""
    repo = make_repo(tmp_path)
    state = tmp_path / "state"
    rec = workspaces.prepare(mission("m-lost", repo), state_dir=state, policy=ON)
    wt = Path(rec["worktree"])
    (wt / "work.txt").write_text("committed worker output")
    (wt / "scratch.txt").write_text("uncommitted scratch")
    git(wt, "add", "work.txt"); git(wt, "commit", "-qm", "worker")
    git(repo, "worktree", "remove", "--force", str(wt))  # tree lost, branch kept
    rec2 = workspaces.prepare(mission("m-lost", repo), state_dir=state, policy=ON)
    assert (Path(rec2["worktree"]) / "work.txt").exists()  # committed work recovered
    assert not (Path(rec2["worktree"]) / "scratch.txt").exists()  # uncommitted lost
    assert rec2["recovered_from_branch"] is True
    assert rec2["uncommitted_lost"] is True
    assert "UNCOMMITTED" in rec2["recovery_note"]


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


def test_nongit_repo_fails_closed_when_workspaces_enabled(tmp_path):
    """Enabled workspaces + no isolatable repo: refuse rather than quietly run 'isolated'
    work on the shared tree."""
    plain = tmp_path / "plain"; plain.mkdir()
    with pytest.raises(workspaces.WorkspaceError, match="allow_shared_fallback"):
        workspaces.prepare(mission("m-plain", plain), state_dir=tmp_path / "state", policy=ON)
    rec = json.loads(
        (tmp_path / "state" / "missions" / "m-plain" / "workspace.json").read_text())
    assert "mission refused" in rec["reason"]  # the refusal is on record, not silent


def test_nongit_fallback_only_with_explicit_permission(tmp_path):
    plain = tmp_path / "plain"; plain.mkdir()
    permitted = Policy({"workspaces": {"enabled": True, "allow_shared_fallback": True},
                        "allow_unattended": [{"adapter": "claude-code", "role": "*"}],
                        "test_command": "true"})
    out = run(mission("m-shared", plain), tmp_path / "state", permitted)
    assert out.status == "done"
    assert (plain / "worker.txt").exists()
    rec = workspaces.read_record(tmp_path / "state" / "missions" / "m-shared")
    assert "shared fallback explicitly permitted" in rec["reason"]


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


def test_lost_tree_recovery_pauses_until_operator_ack(tmp_path):
    """The load-bearing case: after a lost-tree rebuild, the mission must NOT keep executing
    on assumption. It pauses needs-owner with base context bound; the operator reviews and
    acknowledges; only then does dispatch run - in the rebuilt tree."""
    repo = make_repo(tmp_path)
    state = tmp_path / "state"
    m = mission("m-amb", repo)
    rec = workspaces.prepare(m, state_dir=state, policy=ON)
    wt = Path(rec["worktree"])
    (wt / "committed.txt").write_text("kept")
    (wt / "scratch.txt").write_text("gone")
    git(wt, "add", "committed.txt"); git(wt, "commit", "-qm", "worker")
    git(repo, "worktree", "remove", "--force", str(wt))

    ran = []
    def spy(spec):
        ran.append(spec["role"])
        return writer(spec)

    out = run_mission(m, ON, runner=spy, state_dir=state, catalogues_dir="catalogues")
    assert out.status == "needs-owner"
    assert ran == []  # nothing dispatched on assumption
    assert "uncommitted" in out.reason and "skye/m-amb" in out.reason
    journal = (state / "missions" / "m-amb" / "journal.jsonl").read_text()
    assert "workspace-recovery-pending" in journal
    # the pause is sticky: a blind resume hits it again
    out2 = run_mission(m, ON, runner=spy, state_dir=state, catalogues_dir="catalogues")
    assert out2.status == "needs-owner" and ran == []

    acked = workspaces.acknowledge_recovery(state / "missions" / "m-amb", actor="ashish")
    assert acked["recovery_acknowledged"]["by"] == "ashish"
    assert acked["recovery_acknowledged"]["base_rev"] == acked["base_rev"]

    out3 = run_mission(m, ON, runner=spy, state_dir=state, catalogues_dir="catalogues")
    assert out3.status == "done" and ran == ["developer"]
    rebuilt = state / "worktrees" / "m-amb"
    assert (rebuilt / "committed.txt").exists()       # committed work came back
    assert not (rebuilt / "scratch.txt").exists()      # uncommitted honestly gone


def test_acknowledge_without_pending_recovery_refused(tmp_path):
    repo = make_repo(tmp_path)
    state = tmp_path / "state"
    workspaces.prepare(mission("m-calm", repo), state_dir=state, policy=ON)
    import pytest as _pt
    with _pt.raises(workspaces.WorkspaceError, match="no lost-tree recovery"):
        workspaces.acknowledge_recovery(state / "missions" / "m-calm")


def test_server_ack_recovery_endpoint(tmp_path):
    """The operator decision is reachable through the UI server, same as approve/resume."""
    import threading
    from http.server import ThreadingHTTPServer
    from tests.test_ui_server import _req
    from skye.ui import server as ui
    state = tmp_path / "st"; state.mkdir()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), ui.UiHandler)
    httpd.state_dir = state
    httpd.token = "test-token-abc"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    repo = tmp_path / "repo"; repo.mkdir()
    git(repo, "init", "-q"); git(repo, "config", "user.email", "t@t.t")
    git(repo, "config", "user.name", "t")
    (repo / "b.txt").write_text("b"); git(repo, "add", "-A"); git(repo, "commit", "-qm", "i")
    rec = workspaces.prepare(mission("m-ui", repo), state_dir=state, policy=ON)
    git(repo, "worktree", "remove", "--force", rec["worktree"])
    code, data = _req(url, "/api/missions/m-ui/ack-recovery", method="POST", body={})
    assert code == 404  # no mission.json snapshot yet -> unknown mission
    (state / "missions" / "m-ui" / "mission.json").write_text("{}")
    code, data = _req(url, "/api/missions/m-ui/ack-recovery", method="POST", body={})
    assert code == 400  # recovery not pending until a rebuild actually happens
    workspaces.prepare(mission("m-ui", repo), state_dir=state, policy=ON)  # rebuilds, flags loss
    code, data = _req(url, "/api/missions/m-ui/ack-recovery", method="POST", body={})
    assert code == 200 and data["acknowledged"] is True and data["branch"] == "skye/m-ui"
    httpd.shutdown(); thread.join(5)
