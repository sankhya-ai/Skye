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
    # comfortable budget: full detail, every repo named
    brief = repocontext.format_for_brief(digests, max_chars=600)
    assert "big" in brief and "gone" in brief and "top level" in brief
    # tight budget: per-line share trims detail, every repo still named
    tight = repocontext.format_for_brief(digests, max_chars=350)
    assert "big" in tight and "gone" in tight and "chars trimmed" in tight
    # impossible budget: degrades to named lines, then NAMES what it must drop
    tiny = repocontext.format_for_brief(digests, max_chars=60)
    assert "repos left out of this briefing" in tiny and "gone" in tiny


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
    assert "not guess" in brief.replace("\n", " ")        # ambiguous work stays with the owner
    assert '"deferred"' in brief                            # ...via the structured blocker
    assert "not instructions" in brief                      # digest content is data, not policy
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
         "goal": "work nowhere", "repo": "mobile",
         "repo_reason": "says the work is mobile"},           # undeclared name
        {"id": "lost", "role": "developer", "adapter": "claude-code",
         "goal": "work anywhere",
         "repo_reason": "claims it fits somewhere"},          # no repo, no default
        {"id": "mute", "role": "developer", "adapter": "claude-code",
         "goal": "no rationale", "repo": "backend"},          # routing without a reason
        {"id": "echo", "role": "developer", "adapter": "claude-code",
         "goal": "empty rationale", "repo": "backend",
         "repo_reason": "backend"},                           # restating the name is not a why
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
    assert "needs a repo_reason" in rejections["mute"]
    assert "not restate the repo name" in rejections["echo"]


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


# ---------- deferred work: a visible owner blocker, never a false done ----------

def test_deferred_work_pauses_the_mission_for_the_owner(tmp_path, monkeypatch):
    backend, frontend = two_repos(tmp_path)
    state = tmp_path / "state"
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "backlog.json"))
    output = json.dumps({
        "propose_steps": [
            {"id": "api", "role": "developer", "adapter": "claude-code",
             "goal": "add the totals endpoint", "repo": "backend",
             "repo_reason": "the API lives in backend"}],
        "deferred": [
            {"goal": "mobile app totals", "reason": "no mobile repo is declared"},
        ]})
    def run(spec):
        if spec["goal"] == "plan it":
            return Result(ok=True, output=output, command=["fake"])
        return Result(ok=True, output="done", command=["fake"])
    mission = {"id": "m-defer", "goal": "totals everywhere",
               "repos": {"backend": str(backend), "frontend": str(frontend)},
               "steps": [{"id": "plan", "role": "developer", "adapter": "claude-code",
                          "goal": "plan it", "repo": "backend", "gates": []}]}
    out = run_mission(mission, ON, runner=run, state_dir=state,
                      catalogues_dir="catalogues")
    # the routed step still ran and settled - the pause is about the UNROUTED work
    assert out.status == "needs-owner"
    assert "mobile app totals" in out.reason and "backlog" in out.reason
    journal = [json.loads(ln) for ln in
               (state / "missions" / "m-defer" / "journal.jsonl").read_text().splitlines()]
    deferred = [e for e in journal if e.get("event") == "work-deferred"]
    assert len(deferred) == 1 and deferred[0]["feature_id"]
    # the owner's queue holds it, waiting for their decision
    from skye import backlog
    feature = backlog.list_features()[0]
    assert feature["status"] == "pending"
    assert "mobile app totals" in feature["title"]
    # status reads agree: not a false done
    from skye.mission import mission_status
    blocked = mission_status(mission, state_dir=state)
    assert blocked["status"] == "needs-owner"
    assert blocked["deferred"][0]["feature_id"] == feature["id"]
    # the owner's decision - either way - resolves the blocker
    backlog.decide(feature["id"], "rejected", note="no mobile app planned",
                   path=tmp_path / "backlog.json")
    resolved = mission_status(mission, state_dir=state)
    assert resolved["status"] == "done" and "deferred" not in resolved


def test_deferred_graph_meta_and_redacted_timeline(tmp_path, monkeypatch):
    backend, frontend = two_repos(tmp_path)
    state = tmp_path / "state"
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "backlog.json"))
    secret = "AKIA-TEST-SECRET-7f3b9d"
    output = json.dumps({
        "propose_steps": [
            {"id": "api", "role": "developer", "adapter": "claude-code",
             "goal": "add the endpoint", "repo": "backend",
             "repo_reason": "the API lives in backend"}],
        "deferred": [{"goal": "mobile app", "reason": "no mobile repo declared"}]})
    def run(spec):
        if spec["goal"] == "plan it":
            return Result(ok=True, output=output, command=["fake"])
        # a worker's free text can carry anything - including leaked-looking material
        return Result(ok=True, output=f"done; env dump: {secret}", command=["fake"])
    mission = {"id": "m-redact", "goal": "totals",
               "repos": {"backend": str(backend), "frontend": str(frontend)},
               "steps": [{"id": "plan", "role": "developer", "adapter": "claude-code",
                          "goal": "plan it", "repo": "backend", "gates": []}]}
    out = run_mission(mission, ON, runner=run, state_dir=state,
                      catalogues_dir="catalogues")
    assert out.status == "needs-owner"
    # the raw journal DOES hold the worker's output (it is the engine's record)
    raw = (state / "missions" / "m-redact" / "journal.jsonl").read_text()
    assert secret in raw
    graph = mission_graph(state, "m-redact")
    assert graph["meta"]["status"] == "needs-owner"
    assert graph["meta"]["deferred"][0]["goal"] == "mobile app"
    timeline_text = json.dumps(graph["timeline"])
    assert secret not in timeline_text            # the progress view never carries it
    for entry in graph["timeline"]:
        assert "output" not in entry and "spec" not in entry and "feedback" not in entry
    # events, steps, statuses and gate summaries still show what happened and how
    kinds = {e.get("event") for e in graph["timeline"]}
    assert {"dispatched", "settled", "step-accepted", "work-deferred"} <= kinds


def test_malformed_deferred_blocks_instead_of_faking_done(tmp_path, monkeypatch):
    """A planner that says 'work is missing' in an unusable shape must not get a clean done."""
    backend, frontend = two_repos(tmp_path)
    state = tmp_path / "state"
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "backlog.json"))
    output = json.dumps({
        "propose_steps": [
            {"id": "api", "role": "developer", "adapter": "claude-code",
             "goal": "add the endpoint", "repo": "backend",
             "repo_reason": "the API lives in backend"}],
        "deferred": [{"goal": "", "reason": "no goal given"}]})  # unusable record
    def run(spec):
        if spec["goal"] == "plan it":
            return Result(ok=True, output=output, command=["fake"])
        return Result(ok=True, output="done", command=["fake"])
    mission = {"id": "m-maldefer", "goal": "totals",
               "repos": {"backend": str(backend), "frontend": str(frontend)},
               "steps": [{"id": "plan", "role": "developer", "adapter": "claude-code",
                          "goal": "plan it", "repo": "backend", "gates": []}]}
    out = run_mission(mission, ON, runner=run, state_dir=state,
                      catalogues_dir="catalogues")
    assert out.status == "needs-owner"           # fail-closed, not done
    assert "unrouted" in out.reason
    from skye.mission import mission_status
    status = mission_status(mission, state_dir=state)
    assert status["status"] == "needs-owner"
    assert "unreadable deferred work" in status["deferred"][0]["goal"]
    assert "Re-run planning or reset" in status["deferred"][0]["reason"]
