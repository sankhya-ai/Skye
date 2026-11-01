"""Automatic repo routing (planner routes steps across owner-declared repos) and the workflow
graph evidence feed (real nodes/edges/timeline for the UI's progress view).

User goals behind this milestone: Skye-started sessions figure out which declared repo the
work belongs to (no per-task hand-flagging), and the owner can see what happened and how -
structure and journal records, never a decorative diagram."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from skye import repocontext
from skye.adapters.base import Result
from skye.mission import run_mission
from skye.ui.server import create_mission, mission_graph
from tests.test_multirepo import two_repos
from tests.test_ui_server import ui_server, _req
from tests.test_workspaces import git, ON


# ---------- repo digests: small, truthful, owner-scoped ----------

def test_digests_read_only_declared_repos(tmp_path):
    declared = tmp_path / "declared"
    declared.mkdir()
    (declared / "package.json").write_text('{"name": "@acme/web"}')
    (declared / "README.md").write_text("# Web\nThe storefront app.\n")
    (declared / "src").mkdir()
    neighbor = tmp_path / "neighbor"  # present on disk, NOT declared
    neighbor.mkdir()
    (neighbor / "secret-marker.txt").write_text("never read me")

    digests = repocontext.repo_digests({"web": str(declared)})
    digest = digests["web"]
    assert digest["exists"] is True
    assert digest["package_name"] == "@acme/web"
    assert "package.json" in digest["manifests"]
    assert "src/" in digest["top"]
    assert "storefront" in digest["readme"]
    # nothing from the undeclared neighbor appears anywhere
    assert "secret-marker" not in json.dumps(digests)
    assert "neighbor" not in json.dumps(digests)


def test_digests_caps_and_missing_paths_are_visible(tmp_path):
    repo = tmp_path / "big"
    repo.mkdir()
    for i in range(10):
        (repo / f"file-{i:02d}.txt").write_text("x")
    (repo / "README.md").write_text("r" * 5000)
    digests = repocontext.repo_digests({"big": str(repo), "gone": str(tmp_path / "gone")},
                                       max_entries=4, max_readme=100)
    big = digests["big"]
    assert len(big["top"]) == 4 and big["overflow"] == 7  # 11 entries - 4 shown
    assert big["readme_truncated"] is True and len(big["readme"]) == 100
    gone = digests["gone"]
    assert gone["exists"] is False and "does not exist" in gone["error"]
    brief = repocontext.format_for_brief(digests, max_chars=120)
    assert brief.endswith("- digests, not full trees)")  # the cap announces itself


# ---------- planner routing ----------

def test_planner_brief_carries_routing_instructions_and_digests(tmp_path):
    backend, frontend = two_repos(tmp_path)
    (backend / "pyproject.toml").write_text('[project]\nname = "api"\n')
    state = tmp_path / "state"
    # the planner step itself runs somewhere: the owner picks the default repo at compose
    # time (same contract the UI follows), routing then assigns each PROPOSED step its repo
    mission_id = create_mission(state, {
        "goal": "add cart totals end to end", "plan": True, "repo": str(backend),
        "repos": {"backend": str(backend), "frontend": str(frontend)}})
    mission = json.loads((state / "missions" / mission_id / "mission.json").read_text())
    brief = mission["steps"][0]["brief"]
    assert '"repo"' in brief and '"repo_reason"' in brief  # routing is required, not optional
    assert "backend" in brief and "frontend" in brief      # both declared names offered
    assert "pyproject.toml" in brief                       # digest signal, not a tree dump
    assert "do not guess" in brief                         # ambiguous work stays with the owner
    # single-repo / no-repo missions keep the plain brief
    plain_id = create_mission(state, {"goal": "x", "plan": True,
                                      "repo": str(backend), "id": "m-plain"})
    plain = json.loads((state / "missions" / plain_id / "mission.json").read_text())
    assert "Declared repositories" not in plain["steps"][0]["brief"]


def planner_runner(proposals, log):
    """The plan step (goal marker "plan it") plays the planner: emits propose_steps. Later
    steps record where they ran. Contracts carry no step id, so the goal is the marker."""
    def run(spec):
        if spec["goal"] == "plan it":
            return Result(ok=True, output=json.dumps({"propose_steps": proposals}),
                          command=["fake"])
        log.append({"goal": spec["goal"], "repo": spec["repo"]})
        return Result(ok=True, output="done", command=["fake"])
    return run


def test_planner_routes_steps_into_the_right_worktrees(tmp_path):
    backend, frontend = two_repos(tmp_path)
    state = tmp_path / "state"
    log = []
    proposals = [
        {"id": "api", "role": "developer", "adapter": "claude-code",
         "goal": "add the totals endpoint", "repo": "backend",
         "repo_reason": "the API lives in backend"},
        {"id": "ui", "role": "developer", "adapter": "claude-code",
         "goal": "render totals", "repo": "frontend", "needs": ["api"],
         "repo_reason": "the cart UI is a frontend screen"},
    ]
    mission = {"id": "m-routed", "goal": "cart totals", "repos": {"backend": str(backend),
               "frontend": str(frontend)},
               "steps": [{"id": "plan", "role": "developer", "adapter": "claude-code",
                          "goal": "plan it", "repo": "backend", "gates": []}]}
    out = run_mission(mission, ON, runner=planner_runner(proposals, log),
                      state_dir=state, catalogues_dir="catalogues")
    assert out.status == "done"
    # routed steps ran in THEIR repos' worktrees - the routing actually steered execution
    by_goal = {row["goal"]: row["repo"] for row in log}
    assert by_goal["add the totals endpoint"].endswith("worktrees/m-routed/backend")
    assert by_goal["render totals"].endswith("worktrees/m-routed/frontend")
    # and the routing evidence is journaled: name + the planner's one-line reason
    journal = (state / "missions" / "m-routed" / "journal.jsonl").read_text()
    accepted = [json.loads(ln) for ln in journal.splitlines()
                if json.loads(ln).get("event") == "step-accepted"]
    assert {e["spec"]["repo"] for e in accepted} == {"backend", "frontend"}
    assert any(e["spec"].get("repo_reason") == "the cart UI is a frontend screen"
               for e in accepted)


def test_bad_routing_is_rejected_not_guessed(tmp_path):
    backend, frontend = two_repos(tmp_path)
    state = tmp_path / "state"
    log = []
    proposals = [
        {"id": "ghost", "role": "developer", "adapter": "claude-code",
         "goal": "work nowhere", "repo": "mobile"},          # undeclared name
        {"id": "lost", "role": "developer", "adapter": "claude-code",
         "goal": "work anywhere"},                            # no repo, no default
    ]
    mission = {"id": "m-badroute", "goal": "x",
               "repos": {"backend": str(backend), "frontend": str(frontend)},
               "steps": [{"id": "plan", "role": "developer", "adapter": "claude-code",
                          "goal": "plan it", "repo": "backend", "gates": []}]}
    out = run_mission(mission, ON, runner=planner_runner(proposals, log),
                      state_dir=state, catalogues_dir="catalogues")
    assert out.status == "done"
    assert log == []  # neither misrouted step ever ran
    journal = [json.loads(ln) for ln in
               (state / "missions" / "m-badroute" / "journal.jsonl").read_text().splitlines()]
    rejections = {e.get("proposal_id"): e.get("reason", "")
                  for e in journal if e.get("event") == "proposal-rejected"}
    assert "unknown repo name 'mobile'" in rejections["ghost"]
    assert "repo is required" in rejections["lost"]


# ---------- workflow graph evidence ----------

def test_graph_is_the_real_structure_and_timeline(tmp_path):
    backend, frontend = two_repos(tmp_path)
    state = tmp_path / "state"
    log = []
    proposals = [
        {"id": "api", "role": "developer", "adapter": "claude-code",
         "goal": "add the totals endpoint", "repo": "backend",
         "repo_reason": "API lives here"},
        {"id": "ui", "role": "developer", "adapter": "claude-code",
         "goal": "render totals", "repo": "frontend", "needs": ["api"],
         "repo_reason": "UI lives here"},
    ]
    mission = {"id": "m-graph", "goal": "cart totals",
               "repos": {"backend": str(backend), "frontend": str(frontend)},
               "steps": [{"id": "plan", "role": "developer", "adapter": "claude-code",
                          "goal": "plan it", "repo": "backend", "gates": []}]}
    out = run_mission(mission, ON, runner=planner_runner(proposals, log),
                      state_dir=state, catalogues_dir="catalogues")
    assert out.status == "done"

    graph = mission_graph(state, "m-graph")
    assert graph["meta"]["status"] == "done"
    assert graph["meta"]["repos"] == ["backend", "frontend"]
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert nodes["plan"]["origin"] == "authored" and nodes["plan"]["repo"] == "backend"
    assert nodes["api"]["origin"] == "proposed" and nodes["api"]["proposed_by"] == "plan"
    assert nodes["ui"]["repo_reason"] == "UI lives here"
    assert nodes["api"]["state"] == "done" and nodes["ui"]["state"] == "done"
    # repo labels are the DECLARED names, not worktree paths
    assert nodes["ui"]["repo"] == "frontend"
    assert graph["edges"] == [{"from": "api", "to": "ui", "kind": "needs"}]
    # the timeline is the verbatim journal - the same records resume acts on
    events = [(e.get("step"), e.get("event")) for e in graph["timeline"]]
    assert ("plan", "settled") in events and ("ui", "dispatched") in events
    assert all("ts" in e for e in graph["timeline"])


def test_graph_pending_nodes_and_unknown_mission(tmp_path):
    backend, _ = two_repos(tmp_path)
    state = tmp_path / "state"
    mission_id = create_mission(state, {"goal": "later", "repo": str(backend),
                                        "id": "m-idle",
                                        "steps": [{"id": "one", "role": "developer",
                                                   "adapter": "claude-code", "goal": "g",
                                                   "gates": []}]})
    graph = mission_graph(state, mission_id)
    assert graph["nodes"][0]["state"] == "pending"  # no journal record, said plainly
    with pytest.raises(KeyError):
        mission_graph(state, "m-nope")


def test_graph_route_live(ui_server, tmp_path):
    url, state = ui_server
    backend, _ = two_repos(tmp_path)
    mission_id = create_mission(state, {"goal": "later", "repo": str(backend), "id": "m-live",
                                        "steps": [{"id": "one", "role": "developer",
                                                   "adapter": "claude-code", "goal": "g",
                                                   "gates": []}]})
    code, payload = _req(url, f"/api/missions/{mission_id}/graph")
    assert code == 200
    assert payload["nodes"][0]["id"] == "one"
    assert payload["meta"]["workspace"] in ("shared", "worktree")
    code, _ = _req(url, "/api/missions/m-nope/graph")
    assert code == 404
