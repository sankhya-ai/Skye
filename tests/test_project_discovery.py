"""Project-scoped repo discovery: the declared project folder is the scope, so the owner
never hand-labels backend/frontend - and the multi-repo recovery pause names the affected
repos with their own branches/bases, never a top-level None."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from skye import repocontext, workspaces
from skye.mission import MissionError, run_mission
from skye.ui import server as ui
from tests.test_multirepo import two_repos, two_repo_mission, writer
from tests.test_ui_server import _req, ui_server  # noqa: F401 - imported fixture
from tests.test_workspaces import ON, git, make_repo


# --- repocontext.discover_repos: bounded semantics ---

def test_discover_root_that_is_a_repo_is_the_one_repo(tmp_path):
    repo = make_repo(tmp_path, "solo")
    found = repocontext.discover_repos(repo)
    assert found["root_is_repo"] is True
    assert found["repos"] == {"solo": str(repo)}


def test_discover_immediate_subfolder_repos_only(tmp_path):
    backend, frontend = two_repos(tmp_path)
    plain = tmp_path / "docs"
    plain.mkdir()                       # not a git repo: counted, not included
    nested = plain / "inner"
    nested.mkdir()
    git(nested, "init", "-q")           # a repo TWO levels down: outside the bounded scope
    hidden = tmp_path / ".secret"
    hidden.mkdir()
    git(hidden, "init", "-q")           # hidden folders are never scanned

    found = repocontext.discover_repos(tmp_path)
    assert found["root_is_repo"] is False
    assert found["repos"] == {"backend": str(backend), "frontend": str(frontend)}
    assert found["skipped"] == 1        # docs counted as passed over, inner/hidden invisible
    assert found["overflow"] == []


def test_discover_reports_missing_and_empty_roots_honestly(tmp_path):
    missing = repocontext.discover_repos(tmp_path / "nope")
    assert missing["error"] and missing["repos"] == {}
    empty = tmp_path / "empty"
    empty.mkdir()
    found = repocontext.discover_repos(empty)
    assert found["repos"] == {} and found["skipped"] == 0 and not found.get("error")


# --- create_mission: the project folder supplies the repos, no manual labeling ---

def _project(tmp_path, monkeypatch, name, repo):
    monkeypatch.setenv("SKYE_PROJECTS", str(tmp_path / "skye.projects.json"))
    from skye import projects
    return projects.add({"name": name, "repo": str(repo)})


def test_project_folder_of_repos_composes_multirepo_mission(tmp_path, monkeypatch):
    (tmp_path / "scope").mkdir()
    backend, frontend = two_repos(tmp_path / "scope")
    project = _project(tmp_path, monkeypatch, "Skye", tmp_path / "scope")
    state = tmp_path / "st"
    mid = ui.create_mission(state, {"goal": "ship across both", "plan": True,
                                    "project": project["id"]})
    snapshot = json.loads((state / "missions" / mid / "mission.json").read_text())
    assert snapshot["repos"] == {"backend": str(backend), "frontend": str(frontend)}
    assert snapshot["repo"] == ""          # no guessed default; every step must name a repo
    assert "discovered in project" in snapshot["repos_source"]
    # the planner's brief carries the routing addendum built from the DISCOVERED repos
    brief = snapshot["steps"][0]["brief"]
    assert "backend" in brief and "frontend" in brief and "repo_reason" in brief


def test_project_that_is_a_git_repo_keeps_single_repo_behavior(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, "solo")
    project = _project(tmp_path, monkeypatch, "Solo", repo)
    mid = ui.create_mission(tmp_path / "st", {"goal": "x", "project": project["id"],
                                              "steps": [{"id": "s", "role": "developer",
                                                         "adapter": "claude-code",
                                                         "goal": "g", "gates": []}]})
    snapshot = json.loads((tmp_path / "st" / "missions" / mid / "mission.json").read_text())
    assert snapshot["repo"] == str(repo) and "repos" not in snapshot
    assert "repos_source" not in snapshot


def test_project_without_discoverable_repos_keeps_existing_contract(tmp_path, monkeypatch):
    """No repos inside the project folder: discovery changes nothing - the project path
    stays the default repo, and the workspace layer owns the fail-closed non-git verdict."""
    empty = tmp_path / "empty"
    empty.mkdir()
    project = _project(tmp_path, monkeypatch, "Empty", empty)
    mid = ui.create_mission(tmp_path / "st", {"goal": "x", "project": project["id"],
                                              "steps": [{"id": "s", "role": "developer",
                                                         "adapter": "claude-code",
                                                         "goal": "g", "gates": []}]})
    snapshot = json.loads((tmp_path / "st" / "missions" / mid / "mission.json").read_text())
    assert snapshot["repo"] == str(empty) and "repos" not in snapshot
    assert "repos_source" not in snapshot


def test_explicit_repos_win_over_project_discovery(tmp_path, monkeypatch):
    (tmp_path / "scope").mkdir()
    two_repos(tmp_path / "scope")
    chosen = make_repo(tmp_path, "chosen")
    project = _project(tmp_path, monkeypatch, "Skye", tmp_path / "scope")
    mid = ui.create_mission(tmp_path / "st", {
        "goal": "x", "project": project["id"], "repos": {"only": str(chosen)},
        "steps": [{"id": "s", "role": "developer", "adapter": "claude-code",
                   "goal": "g", "repo": "only", "gates": []}]})
    snapshot = json.loads((tmp_path / "st" / "missions" / mid / "mission.json").read_text())
    assert snapshot["repos"] == {"only": str(chosen)}
    assert "repos_source" not in snapshot


# --- multi-repo lost-tree recovery: the pause names each affected repo ---

def test_multi_repo_recovery_reason_names_affected_repos(tmp_path):
    backend, frontend = two_repos(tmp_path)
    state = tmp_path / "state"
    m = two_repo_mission("m-multi", backend, frontend)
    rec = workspaces.prepare(m, state_dir=state, policy=ON)
    wt = Path(rec["repos"]["backend"]["worktree"])
    (wt / "committed.txt").write_text("kept")
    git(wt, "add", "committed.txt"); git(wt, "commit", "-qm", "worker")
    git(backend, "worktree", "remove", "--force", str(wt))

    ran = []
    def spy(spec):
        ran.append(spec["role"])
        return writer(spec)

    out = run_mission(m, ON, runner=spy, state_dir=state, catalogues_dir="catalogues")
    assert out.status == "needs-owner" and ran == []
    assert "repo 'backend'" in out.reason and "skye/m-multi" in out.reason
    assert "base_rev" in out.reason and "None" not in out.reason
    assert "frontend" not in out.reason  # unaffected repos are not noise
    journal = (state / "missions" / "m-multi" / "journal.jsonl").read_text()
    event = next(json.loads(line) for line in journal.splitlines()
                 if "workspace-recovery-pending" in line)
    assert set(event["repos"]) == {"backend"}
    assert event["repos"]["backend"]["branch"] == "skye/m-multi"
    assert event["repos"]["backend"]["base_rev"]

    acked = workspaces.acknowledge_recovery(state / "missions" / "m-multi", actor="ashish")
    ack = acked["recovery_acknowledged"]
    assert ack["repos"]["backend"]["branch"] == "skye/m-multi"
    assert ack["repos"]["backend"]["base_rev"] == acked["repos"]["backend"]["base_rev"]

    out2 = run_mission(m, ON, runner=spy, state_dir=state, catalogues_dir="catalogues")
    assert out2.status == "done" and ran == ["developer", "developer"]


# --- discovery exclusions are reported by name, never silently omitted ---

def test_symlink_escaping_the_declared_folder_is_excluded_and_named(tmp_path):
    scope = tmp_path / "scope"
    scope.mkdir()
    backend = make_repo(scope, "backend")
    outside = make_repo(tmp_path, "outside")
    (scope / "escape").symlink_to(outside)
    inside_link_target = make_repo(scope, "real-inner")
    (scope / "alias").symlink_to(inside_link_target)

    found = repocontext.discover_repos(scope)
    assert found["outside_scope"] == ["escape"]
    assert set(found["repos"]) == {"backend", "real-inner", "alias"}
    assert "escape" not in found["repos"]


def test_git_marker_that_is_not_a_repository_is_named_invalid(tmp_path):
    scope = tmp_path / "scope"
    scope.mkdir()
    make_repo(scope, "backend")
    fake = scope / "fake"
    (fake / ".git").mkdir(parents=True)      # an empty directory named .git proves nothing
    (scope / "fake-file").mkdir()
    (scope / "fake-file" / ".git").write_text("gitdir: /nowhere/real")

    found = repocontext.discover_repos(scope)
    assert set(found["repos"]) == {"backend"}
    assert sorted(found["invalid"]) == ["fake", "fake-file"]


def test_unreadable_subfolder_is_named_inaccessible(tmp_path):
    scope = tmp_path / "scope"
    scope.mkdir()
    make_repo(scope, "backend")
    locked = scope / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        found = repocontext.discover_repos(scope)
    finally:
        locked.chmod(0o755)
    assert set(found["repos"]) == {"backend"}
    assert found["inaccessible"] == ["locked"]


def test_repos_source_names_exclusions(tmp_path, monkeypatch):
    scope = tmp_path / "scope"
    scope.mkdir()
    two_repos(scope)
    outside = make_repo(tmp_path, "outside")
    (scope / "escape").symlink_to(outside)
    fake = scope / "fake"
    (fake / ".git").mkdir(parents=True)
    project = _project(tmp_path, monkeypatch, "Skye", scope)
    mid = ui.create_mission(tmp_path / "st", {"goal": "x", "project": project["id"],
                                              "steps": [{"id": "s", "role": "developer",
                                                         "adapter": "claude-code",
                                                         "goal": "g", "repo": "backend",
                                                         "gates": []}]})
    snapshot = json.loads((tmp_path / "st" / "missions" / mid / "mission.json").read_text())
    assert "escape" in snapshot["repos_source"] and "outside" in snapshot["repos_source"]
    assert "fake" in snapshot["repos_source"]


# --- the composed planner is a nonwriting context step, enforced at compose time ---

def test_writable_planner_role_is_rejected(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, "solo")
    with pytest.raises(MissionError, match="nonwriting"):
        ui.create_mission(tmp_path / "st", {"goal": "x", "plan": True,
                                            "planner_role": "developer",
                                            "repo": str(repo)})


def test_default_planner_role_is_read_only_by_catalogue(tmp_path, monkeypatch):
    """Mechanism evidence: the composed planner dispatches under pm's catalogue, which the
    gateway resolves to read-only tools with writable=False - the same resolution the
    dispatcher applies at dispatch time (CLI-level scoping, not an OS boundary)."""
    from skye import gateway
    catalogue = gateway.resolve("pm")
    assert catalogue["writable"] is False
    assert not ({"Write", "Edit", "Bash"} & set(catalogue["tools"]))
    repo = make_repo(tmp_path, "solo")
    mid = ui.create_mission(tmp_path / "st", {"goal": "x", "plan": True,
                                              "repo": str(repo)})
    snapshot = json.loads((tmp_path / "st" / "missions" / mid / "mission.json").read_text())
    assert snapshot["steps"][0]["role"] == "pm"


# --- preview: the UI can show the exact candidates before anything starts ---

def test_project_repo_preview_endpoint(ui_server, tmp_path, monkeypatch):
    url, state_dir = ui_server
    scope = tmp_path / "scope"
    scope.mkdir()
    two_repos(scope)
    monkeypatch.setenv("SKYE_PROJECTS", str(tmp_path / "skye.projects.json"))
    status, payload = _req(url, "/api/projects", method="POST",
                           body={"project": {"name": "Skye", "repo": str(scope)}})
    assert status == 200, payload
    pid = payload["project"]["id"]
    status, payload = _req(url, f"/api/projects/{pid}/repos")
    assert status == 200, payload
    assert set(payload["discovery"]["repos"]) == {"backend", "frontend"}
    assert payload["discovery"]["root_is_repo"] is False
    status, payload = _req(url, "/api/projects/nope/repos")
    assert status == 404
