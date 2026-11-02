"""Saved teams: durable lead + member rosters that CONSTRAIN what the lead may delegate.

A saved team grants nothing by itself: the lead's planning session proposes steps, the
engine rejects proposals outside the roster (journaled), and every accepted member session
still passes the ordinary dispatcher policy gate before it runs.
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from skye import teams
from skye.mission import load_mission, run_mission
from skye.policy import Policy
from skye.ui import server as ui


def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYE_TEAMS", str(tmp_path / "teams.json"))
    monkeypatch.setenv("SKYE_ROLES", str(tmp_path / "roles.json"))
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "backlog.json"))
    monkeypatch.setenv("SKYE_ISSUES", str(tmp_path / "issues.json"))
    monkeypatch.setenv("SKYE_PROJECTS", str(tmp_path / "projects.json"))


def _save(tmp_path, monkeypatch, **overrides):
    _env(tmp_path, monkeypatch)
    record = {"name": "website", "lead": "pm",
              "members": ["designer", "frontend-dev"], **overrides}
    return teams.add(record)


# --- registry validation -------------------------------------------------

def test_team_roundtrip_and_upsert(tmp_path, monkeypatch):
    saved = _save(tmp_path, monkeypatch, description="site crew")
    assert saved["id"].startswith("team-")
    assert teams.get(saved["id"])["name"] == "website"
    assert teams.get("website")["id"] == saved["id"]  # lookup by name too
    assert [t["id"] for t in teams.list_teams()] == [saved["id"]]
    again = teams.add({**saved, "members": ["designer", "artist"]})
    assert again["id"] == saved["id"]  # upsert by id, not a duplicate
    assert len(teams.list_teams()) == 1
    removed = teams.remove(saved["id"])
    assert removed["name"] == "website"
    assert teams.list_teams() == []
    with pytest.raises(teams.TeamError):
        teams.remove(saved["id"])


def test_team_validation_rejects_unknown_roles_and_lead_overlap(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    with pytest.raises(teams.TeamError, match="lead"):
        teams.add({"name": "x", "lead": "nobody", "members": ["developer"]})
    with pytest.raises(teams.TeamError, match="member"):
        teams.add({"name": "x", "lead": "pm", "members": ["nobody"]})
    with pytest.raises(teams.TeamError, match="own member"):
        teams.add({"name": "x", "lead": "pm", "members": ["pm", "developer"]})
    with pytest.raises(teams.TeamError, match="at least one member"):
        teams.add({"name": "x", "lead": "pm", "members": []})
    with pytest.raises(teams.TeamError, match="name"):
        teams.add({"name": "", "lead": "pm", "members": ["developer"]})


def test_team_lead_must_be_nonwriting(tmp_path, monkeypatch):
    """The lead runs the planning session, a nonwriting context step: a writable lead would
    fail at compose time anyway, so the registry rejects it at save with the reason named."""
    _env(tmp_path, monkeypatch)
    with pytest.raises(teams.TeamError, match="writable"):
        teams.add({"name": "x", "lead": "developer", "members": ["designer"]})


def test_team_project_assignment_must_exist(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    with pytest.raises(teams.TeamError, match="unknown project"):
        teams.add({"name": "x", "lead": "pm", "members": ["developer"],
                   "projects": ["proj-nope"]})


def test_proposed_agent_cannot_be_saved_into_a_roster(tmp_path, monkeypatch):
    """A worker-proposed agent is not a capability until the owner promotes it - a roster
    cannot smuggle it in either."""
    from skye import agents
    _env(tmp_path, monkeypatch)
    agents.propose([{"name": "seo-scout", "instructions": "read only scouting"}],
                   mission="m1")
    with pytest.raises(teams.TeamError, match="active agent"):
        teams.add({"name": "x", "lead": "pm", "members": ["seo-scout"]})
    agents.promote("seo-scout")
    saved = teams.add({"name": "x", "lead": "pm", "members": ["seo-scout"]})
    assert saved["members"] == ["seo-scout"]


# --- mission engine constraint --------------------------------------------

def _team_mission(tmp_path, monkeypatch, steps, **extra):
    _save(tmp_path, monkeypatch)
    team_id = teams.list_teams()[0]["id"]
    path = tmp_path / "mission.json"
    path.write_text(json.dumps({"id": "m-team", "goal": "g", "repo": str(tmp_path),
                                "team": team_id, "steps": steps, **extra}))
    return load_mission(path)


def test_authored_steps_must_stay_inside_the_roster(tmp_path, monkeypatch):
    mission = _team_mission(tmp_path, monkeypatch, [
        {"id": "paint", "role": "designer", "adapter": "root", "goal": "hero art"}])
    assert mission["team"]["name"] == "website"
    assert mission["team"]["members"] == ["designer", "frontend-dev"]
    with pytest.raises(Exception, match="not part of team"):
        _team_mission(tmp_path, monkeypatch, [
            {"id": "ops", "role": "devops", "adapter": "root", "goal": "deploy"}])


def test_unknown_team_fails_closed(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    path = tmp_path / "mission.json"
    path.write_text(json.dumps({"id": "m-x", "goal": "g", "repo": str(tmp_path),
                                "team": "team-ghost",
                                "steps": [{"id": "a", "role": "designer",
                                           "adapter": "root", "goal": "x"}]}))
    with pytest.raises(Exception, match="unknown team"):
        load_mission(path)


def test_lead_proposals_are_constrained_to_members(tmp_path, monkeypatch):
    """The heart of the feature: the lead proposes one in-roster step and one off-roster
    step. The member step is accepted and dispatched through the normal policy path; the
    off-roster one is rejected and journaled with the roster named."""
    dispatched = []

    def runner(spec):
        class R:
            ok = True
            returncode = 0
            job_id = ""
            output = "done"
        dispatched.append(spec["role"])
        if spec["role"] == "pm":
            R.output = json.dumps({"propose_steps": [
                {"id": "ui", "role": "frontend-dev", "adapter": "root",
                 "goal": "build the page"},
                {"id": "ship", "role": "devops", "adapter": "root",
                 "goal": "deploy it"}]})
        return R

    mission = _team_mission(tmp_path, monkeypatch, [
        {"id": "plan", "role": "pm", "adapter": "root", "goal": "g", "gates": []}])
    policy = Policy({"allow_unattended": [{"adapter": "root", "role": "*"}],
                     "max_concurrent": 2})
    outcome = run_mission(mission, policy, runner=runner, state_dir=tmp_path / ".skye")
    assert outcome.status == "done"
    assert dispatched == ["pm", "frontend-dev"]
    journal = (tmp_path / ".skye/missions/m-team/journal.jsonl").read_text()
    rejected = [json.loads(line) for line in journal.splitlines()
                if '"proposal-rejected"' in line]
    assert len(rejected) == 1
    assert "devops" in rejected[0]["reason"] and "website" in rejected[0]["reason"]
    assert "not a member of team" in rejected[0]["reason"]


def test_member_sessions_still_pass_the_policy_gate(tmp_path, monkeypatch):
    """Team selection never widens grants: a member whose adapter/role is not on the
    unattended list pauses needs-owner exactly like any other contract."""
    def runner(spec):
        class R:
            ok = True
            returncode = 0
            job_id = ""
            output = json.dumps({"propose_steps": [
                {"id": "ui", "role": "frontend-dev", "adapter": "root",
                 "goal": "build the page"}]})
        return R

    mission = _team_mission(tmp_path, monkeypatch, [
        {"id": "plan", "role": "pm", "adapter": "root", "goal": "g", "gates": []}])
    policy = Policy({"allow_unattended": [{"adapter": "root", "role": "pm"}]})
    outcome = run_mission(mission, policy, runner=runner, state_dir=tmp_path / ".skye")
    assert outcome.status == "needs-owner"
    assert "frontend-dev" in outcome.reason
    assert (tmp_path / ".skye/missions/m-team/pending-ui.json").exists()


def test_roster_snapshot_survives_team_edit(tmp_path, monkeypatch):
    """A mission defined under a team keeps the roster it was created with: editing the
    saved team afterwards cannot retarget the mission's constraint."""
    mission = _team_mission(tmp_path, monkeypatch, [
        {"id": "plan", "role": "pm", "adapter": "root", "goal": "g", "gates": []}])
    teams.add({**teams.list_teams()[0], "members": ["devops"]})  # owner edits the roster
    assert mission["team"]["members"] == ["designer", "frontend-dev"]  # snapshot stands


# --- UI composer + endpoints ----------------------------------------------

@pytest.fixture()
def ui_server(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    monkeypatch.setenv("SKYE_POLICY", str(tmp_path / "policy.json"))
    state_dir = tmp_path / "st"
    state_dir.mkdir()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), ui.UiHandler)
    httpd.state_dir = state_dir
    httpd.token = "test-token-abc"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", state_dir
    httpd.shutdown()
    thread.join(5)


def _req(url, path, method="GET", body=None):
    request = urllib.request.Request(
        f"{url}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Skye-Token": "test-token-abc", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def test_teams_endpoints_create_list_delete(ui_server, tmp_path, monkeypatch):
    url, _ = ui_server
    project_dir = tmp_path / "site"
    project_dir.mkdir()
    status, created_project = _req(url, "/api/projects", "POST",
                                   {"project": {"name": "site", "repo": str(project_dir)}})
    assert status == 200
    project_id = created_project["project"]["id"]
    status, created = _req(url, "/api/teams", "POST",
                           {"team": {"name": "website", "lead": "pm",
                                     "members": ["designer", "frontend-dev"],
                                     "handoff": "designer posts assets before frontend builds",
                                     "projects": [project_id]}})
    assert status == 200, created
    team_id = created["team"]["id"]
    status, listed = _req(url, "/api/teams")
    assert status == 200
    (row,) = listed["teams"]
    assert row["name"] == "website"
    assert row["lead_description"]
    assert [m["name"] for m in row["member_details"]] == ["designer", "frontend-dev"]
    assert row["project_names"] == ["site"]
    status, deleted = _req(url, "/api/teams/delete", "POST", {"id": team_id})
    assert status == 200 and deleted["deleted"]
    status, _ = _req(url, "/api/teams/delete", "POST", {"id": team_id})
    assert status == 404


def test_team_validation_error_surfaces_over_http(ui_server):
    url, _ = ui_server
    status, body = _req(url, "/api/teams", "POST",
                        {"team": {"name": "x", "lead": "pm", "members": ["nobody"]}})
    assert status == 400
    assert "member" in body["error"]


def test_composer_team_mission_uses_lead_and_constrains(ui_server, tmp_path, monkeypatch):
    """POST /api/missions with a team: the planner step IS the lead, its brief names the
    roster and the enforcement, and the mission snapshot carries the team for the engine."""
    url, state_dir = ui_server
    _save(tmp_path, monkeypatch, handoff="design first, then build")
    team_id = teams.list_teams()[0]["id"]
    project_dir = tmp_path / "site"
    project_dir.mkdir()
    status, body = _req(url, "/api/missions", "POST",
                        {"goal": "new landing page", "team": team_id,
                         "repo": str(project_dir)})
    assert status == 200, body
    snapshot = json.loads(
        (state_dir / "missions" / body["id"] / "mission.json").read_text())
    assert snapshot["team"]["name"] == "website"
    assert snapshot["team"]["lead"] == "pm"
    (step,) = snapshot["steps"]
    assert step["role"] == "pm"  # the saved lead plans, never a generic role
    assert "frontend-dev" in step["brief"] and "designer" in step["brief"]
    assert "rejects steps for any" in step["brief"]  # enforcement stated, not implied
    assert "design first, then build" in step["brief"]  # the owner's handoff note rides along


def test_composer_rejects_unknown_team_and_solo_team_mix(ui_server, tmp_path, monkeypatch):
    url, _ = ui_server
    _save(tmp_path, monkeypatch)
    team_id = teams.list_teams()[0]["id"]
    project_dir = tmp_path / "site"
    project_dir.mkdir()
    status, body = _req(url, "/api/missions", "POST",
                        {"goal": "g", "team": "team-ghost", "repo": str(project_dir)})
    assert status == 400 and "unknown team" in body["error"]
    status, body = _req(url, "/api/missions", "POST",
                        {"goal": "g", "team": team_id, "solo": True,
                         "repo": str(project_dir)})
    assert status == 400 and "not both" in body["error"]


def test_composer_enforces_project_assignment(ui_server, tmp_path, monkeypatch):
    """A team assigned to projects composes only for those projects."""
    url, _ = ui_server
    bound = tmp_path / "bound"
    bound.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    from skye import projects
    bound_project = projects.add({"name": "bound", "repo": str(bound)})
    _save(tmp_path, monkeypatch, projects=[bound_project["id"]])
    team_id = teams.list_teams()[0]["id"]
    status, body = _req(url, "/api/missions", "POST",
                        {"goal": "g", "team": team_id, "repo": str(other)})
    assert status == 200  # no project chosen: explicit repo scope stays allowed
    status, body = _req(url, "/api/projects", "POST",
                        {"project": {"name": "other", "repo": str(other)}})
    other_id = body["project"]["id"]
    status, body = _req(url, "/api/missions", "POST",
                        {"goal": "g", "team": team_id, "project": other_id})
    assert status == 400 and "assigned" in body["error"]
    status, body = _req(url, "/api/missions", "POST",
                        {"goal": "g", "team": team_id, "project": bound_project["id"]})
    assert status == 200


def test_delete_guard_blocks_teams_with_work_in_flight(ui_server, tmp_path, monkeypatch):
    url, state_dir = ui_server
    _save(tmp_path, monkeypatch)
    team_id = teams.list_teams()[0]["id"]
    project_dir = tmp_path / "site"
    project_dir.mkdir()
    # A mission with no unattended rules pauses needs-owner: work in flight.
    (tmp_path / "policy.json").write_text(json.dumps({"allow_unattended": []}))
    status, body = _req(url, "/api/missions", "POST",
                        {"goal": "g", "team": team_id, "repo": str(project_dir),
                         "run": True})
    assert status == 200
    import time
    for _ in range(50):  # the mission thread pauses needs-owner shortly after dispatch
        journal = state_dir / "missions" / body["id"] / "journal.jsonl"
        if journal.exists() and "needs-owner" in journal.read_text():
            break
        time.sleep(0.1)
    status, body = _req(url, "/api/teams/delete", "POST", {"id": team_id})
    assert status == 409
    assert "in flight" in body["error"]


def test_snapshot_persists_across_reload_and_team_edit(tmp_path, monkeypatch):
    """The roster recorded in mission.json is the run's definition: reloading (resume after
    a restart) uses it verbatim - an edited or deleted saved team never retargets the run."""
    mission = _team_mission(tmp_path, monkeypatch, [
        {"id": "plan", "role": "pm", "adapter": "root", "goal": "g", "gates": []}])
    # Persist exactly what the server persists, then edit AND delete the saved team.
    snapshot_path = tmp_path / "persisted.json"
    snapshot_path.write_text(json.dumps(mission))
    teams.add({**teams.list_teams()[0], "members": ["devops"], "name": "renamed"})
    reloaded = load_mission(snapshot_path)
    assert reloaded["team"]["members"] == ["designer", "frontend-dev"]  # snapshot stands
    assert reloaded["team"]["name"] == "website"
    teams.remove(teams.list_teams()[0]["id"])
    reloaded = load_mission(snapshot_path)  # deleted team: the snapshot still runs
    assert reloaded["team"]["lead"] == "pm"
    with pytest.raises(Exception, match="not part of team"):
        bad = dict(reloaded)
        bad["steps"] = [{"id": "x", "role": "devops", "adapter": "root", "goal": "g"}]
        snapshot_path.write_text(json.dumps(bad))
        load_mission(snapshot_path)


def test_string_team_reference_fails_closed_after_delete(tmp_path, monkeypatch):
    """An older mission FILE that references the team by id (pre-snapshot) cannot load once
    the team is gone: fail closed with the registry named, never a guessed roster."""
    _save(tmp_path, monkeypatch)
    team_id = teams.list_teams()[0]["id"]
    teams.remove(team_id)
    path = tmp_path / "mission.json"
    path.write_text(json.dumps({"id": "m-old", "goal": "g", "repo": str(tmp_path),
                                "team": team_id,
                                "steps": [{"id": "a", "role": "designer",
                                           "adapter": "root", "goal": "x"}]}))
    with pytest.raises(Exception, match="unknown team"):
        load_mission(path)


def test_all_off_roster_proposals_pause_instead_of_quiet_success(tmp_path, monkeypatch):
    """The lead staffs the goal entirely outside its roster: every proposal is rejected, no
    member session runs, and the mission must NOT report done - it pauses needs-owner with
    the resolution named (widen the team = an explicit owner grant)."""
    def runner(spec):
        class R:
            ok = True
            returncode = 0
            job_id = ""
            output = json.dumps({"propose_steps": [
                {"id": "ship", "role": "devops", "adapter": "root", "goal": "deploy"},
                {"id": "qa", "role": "tester", "adapter": "root", "goal": "break it"}]})
        return R

    mission = _team_mission(tmp_path, monkeypatch, [
        {"id": "plan", "role": "pm", "adapter": "root", "goal": "g", "gates": []}])
    policy = Policy({"allow_unattended": [{"adapter": "root", "role": "*"}]})
    outcome = run_mission(mission, policy, runner=runner, state_dir=tmp_path / ".skye")
    assert outcome.status == "needs-owner"
    assert "outside its saved roster" in outcome.reason
    assert "NOT done" in outcome.reason
    journal = (tmp_path / ".skye/missions/m-team/journal.jsonl").read_text()
    assert journal.count('"proposal-rejected"') == 2


def test_mixed_proposals_still_complete_with_rejections_visible(tmp_path, monkeypatch):
    """One member step accepted + one off-roster rejection: the mission completes (the owner
    sees the rejection in the trace), and the rejection stays journaled."""
    def runner(spec):
        class R:
            ok = True
            returncode = 0
            job_id = ""
            output = "done"
        if spec["role"] == "pm":
            R.output = json.dumps({"propose_steps": [
                {"id": "ui", "role": "frontend-dev", "adapter": "root", "goal": "page"},
                {"id": "ship", "role": "devops", "adapter": "root", "goal": "deploy"}]})
        return R

    mission = _team_mission(tmp_path, monkeypatch, [
        {"id": "plan", "role": "pm", "adapter": "root", "goal": "g", "gates": []}])
    policy = Policy({"allow_unattended": [{"adapter": "root", "role": "*"}]})
    outcome = run_mission(mission, policy, runner=runner, state_dir=tmp_path / ".skye")
    assert outcome.status == "done"
    journal = (tmp_path / ".skye/missions/m-team/journal.jsonl").read_text()
    assert '"proposal-rejected"' in journal  # visible in the trace, not silent


# --- review gate vs roster -------------------------------------------------

def test_review_gate_reviewer_is_a_policy_role_outside_the_roster(tmp_path, monkeypatch):
    """The review gate's reviewer is a dispatcher-spawned REVIEW-POLICY role, the same
    class as the lead: it judges diffs and never authors work, so it is not delegation and
    is NOT required to be a roster member. The roster constrains who may be delegated
    WORK; Skye's mandatory quality gates stand outside it by design."""
    calls = []

    def runner(spec):
        class R:
            ok = True
            returncode = 0
            job_id = ""
        calls.append(spec["role"])
        R.output = '{"verdict": "pass"}' if spec["role"] == "reviewer" else "implemented"
        return R

    mission = _team_mission(tmp_path, monkeypatch, [
        {"id": "ui", "role": "frontend-dev", "adapter": "root", "goal": "build the page",
         "gates": ["review"]}])
    assert "reviewer" not in mission["team"]["members"]
    policy = Policy({"allow_unattended": [{"adapter": "root", "role": "*"}]})
    outcome = run_mission(mission, policy, runner=runner, state_dir=tmp_path / ".skye")
    assert outcome.status == "done"
    assert calls == ["frontend-dev", "reviewer"]  # the gate reviewer ran though off-roster


def test_reviewer_cannot_be_delegated_work_outside_the_roster(tmp_path, monkeypatch):
    """Same reviewer name, different class: as delegated WORK it is off-roster like any
    other role. An authored reviewer step fails load; a lead proposal for reviewer work is
    rejected and journaled, and with no member session the mission pauses needs-owner."""
    with pytest.raises(Exception, match="not part of team"):
        _team_mission(tmp_path, monkeypatch, [
            {"id": "rev", "role": "reviewer", "adapter": "root", "goal": "grade"}])

    def runner(spec):
        class R:
            ok = True
            returncode = 0
            job_id = ""
            output = json.dumps({"propose_steps": [
                {"id": "rev", "role": "reviewer", "adapter": "root", "goal": "grade it"}]})
        return R

    mission = _team_mission(tmp_path, monkeypatch, [
        {"id": "plan", "role": "pm", "adapter": "root", "goal": "g", "gates": []}])
    policy = Policy({"allow_unattended": [{"adapter": "root", "role": "*"}]})
    outcome = run_mission(mission, policy, runner=runner, state_dir=tmp_path / ".skye")
    assert outcome.status == "needs-owner"
    journal = (tmp_path / ".skye/missions/m-team/journal.jsonl").read_text()
    assert '"proposal-rejected"' in journal and "reviewer" in journal
