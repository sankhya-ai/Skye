"""Recovery handoff: the owner hands a FAILED step to the mission's lead.

The failed step stays on the record but stops failing the mission and never re-runs on
its own. The lead's planning session re-opens (its recorded session resumes) with the
failure as context, and its recovery proposals go through the ordinary validated
propose-steps path - no blind retries, no widened grants.
"""

import json

import pytest

from skye import teams
from skye.mission import (Journal, MissionError, handoff_to_lead, load_mission,
                          mission_status, run_mission)
from skye.policy import Policy


def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYE_TEAMS", str(tmp_path / "teams.json"))
    monkeypatch.setenv("SKYE_ROLES", str(tmp_path / "roles.json"))
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "backlog.json"))
    monkeypatch.setenv("SKYE_ISSUES", str(tmp_path / "issues.json"))
    monkeypatch.setenv("SKYE_PROJECTS", str(tmp_path / "projects.json"))


def _mission(tmp_path, monkeypatch, steps, mid="m-rec", **extra):
    _env(tmp_path, monkeypatch)
    path = tmp_path / "mission.json"
    path.write_text(json.dumps({"id": mid, "goal": "g", "repo": str(tmp_path),
                                "steps": steps, **extra}))
    return load_mission(path)


def _team_mission(tmp_path, monkeypatch, steps, **extra):
    _env(tmp_path, monkeypatch)
    teams.add({"name": "website", "lead": "pm", "members": ["designer", "frontend-dev"]})
    team_id = teams.list_teams()[0]["id"]
    return _mission(tmp_path, monkeypatch, steps, team=team_id, **extra)


def _ok(output="done"):
    class R:
        ok = True
        returncode = 0
        job_id = ""
    R.output = output
    return R


def _fail(output="boom: the thing broke"):
    class R:
        ok = False
        returncode = 1
        job_id = ""
    R.output = output
    return R


POLICY = Policy({"allow_unattended": [{"adapter": "root", "role": "*"}]})


def _failed_member_mission(tmp_path, monkeypatch):
    """Run a team mission where the lead proposes a member step that then fails."""
    calls = []

    def runner(spec):
        calls.append(spec["role"])
        if spec["role"] == "pm":
            return _ok(json.dumps({"propose_steps": [
                {"id": "ui", "role": "frontend-dev", "adapter": "root",
                 "goal": "build the page"}]}))
        return _fail()

    mission = _team_mission(tmp_path, monkeypatch, [
        {"id": "plan", "role": "pm", "adapter": "root", "goal": "g", "gates": []}])
    outcome = run_mission(mission, POLICY, runner=runner, state_dir=tmp_path / ".skye")
    assert outcome.status == "failed"
    assert calls == ["pm", "frontend-dev"]
    return mission


def test_handoff_recovers_through_the_lead(tmp_path, monkeypatch):
    mission = _failed_member_mission(tmp_path, monkeypatch)
    state_dir = tmp_path / ".skye"

    handed = handoff_to_lead(state_dir, mission["id"], "ui")
    assert handed == {"step": "ui", "to": "plan"}

    # Between the decision and the recovery run the task is paused, never "done".
    status = mission_status(mission, state_dir)
    assert status["status"] == "needs-owner"
    ui_step = next(s for s in status["steps"] if s["id"] == "ui")
    assert ui_step["handed_to"] == "plan"

    briefs = []

    def runner(spec):
        briefs.append((spec["role"], spec.get("brief", "")))
        if spec["role"] == "pm":
            return _ok(json.dumps({"propose_steps": [
                {"id": "ui-retry", "role": "frontend-dev", "adapter": "root",
                 "goal": "build the page, fixed", "recovers": "ui"}]}))
        return _ok("built")

    outcome = run_mission(mission, POLICY, runner=runner, state_dir=state_dir)
    assert outcome.status == "done"

    # The lead re-ran with the failure in its brief; the replacement ran; the failed
    # step never re-ran itself.
    lead_briefs = [b for role, b in briefs if role == "pm"]
    assert len(lead_briefs) == 1
    assert "ui" in lead_briefs[0] and "boom" in lead_briefs[0]
    assert "handed back" in lead_briefs[0]
    assert [role for role, _ in briefs] == ["pm", "frontend-dev"]

    status = mission_status(mission, state_dir)
    assert status["status"] == "done"
    ui_step = next(s for s in status["steps"] if s["id"] == "ui")
    assert ui_step["status"] == "failed" and ui_step["handed_to"] == "plan"

    journal = Journal(state_dir / "missions" / mission["id"] / "journal.jsonl")
    entries = [e for e in journal.entries() if e.get("event") == "recovery-handoff"]
    assert len(entries) == 1
    assert entries[0]["step"] == "ui" and entries[0]["to"] == "plan"
    resolved = [e for e in journal.entries() if e.get("event") == "recovery-resolved"]
    assert len(resolved) == 1
    assert resolved[0]["step"] == "ui" and resolved[0]["resolution"] == "replaced"
    assert resolved[0]["replaced_by"] == ["ui-retry"]


def test_handoff_failure_is_recorded_and_keeps_failing(tmp_path, monkeypatch):
    """A recovery that itself fails does not sweep the original failure away: the
    mission reports the lead's failure, and the member stays handed off on the record."""
    mission = _failed_member_mission(tmp_path, monkeypatch)
    state_dir = tmp_path / ".skye"
    handoff_to_lead(state_dir, mission["id"], "ui")

    def runner(spec):
        return _fail("the lead could not recover either")

    outcome = run_mission(mission, POLICY, runner=runner, state_dir=state_dir)
    assert outcome.status == "failed"
    assert "plan" in outcome.reason
    status = mission_status(mission, state_dir)
    assert status["status"] == "failed"
    ui_step = next(s for s in status["steps"] if s["id"] == "ui")
    assert ui_step["handed_to"] == "plan"


def test_handoff_rejects_steps_without_a_failure(tmp_path, monkeypatch):
    def runner(spec):
        return _ok(json.dumps({"propose_steps": [
            {"id": "ui", "role": "frontend-dev", "adapter": "root", "goal": "x"}]}))
    mission = _team_mission(tmp_path, monkeypatch, [
        {"id": "plan", "role": "pm", "adapter": "root", "goal": "g", "gates": []}])
    state_dir = tmp_path / ".skye"
    outcome = run_mission(mission, POLICY, runner=runner, state_dir=state_dir)
    assert outcome.status == "done"
    with pytest.raises(MissionError, match="no recorded failure"):
        handoff_to_lead(state_dir, mission["id"], "ui")
    with pytest.raises(MissionError, match="unknown step"):
        handoff_to_lead(state_dir, mission["id"], "ghost")


def test_handoff_requires_a_lead(tmp_path, monkeypatch):
    """An authored workflow with no planner has no one to hand to: the error names the
    real recovery paths instead of guessing."""
    def runner(spec):
        return _fail()

    mission = _mission(tmp_path, monkeypatch, [
        {"id": "paint", "role": "designer", "adapter": "root", "goal": "art",
         "gates": []}])
    state_dir = tmp_path / ".skye"
    outcome = run_mission(mission, POLICY, runner=runner, state_dir=state_dir)
    assert outcome.status == "failed"
    with pytest.raises(MissionError, match="no lead"):
        handoff_to_lead(state_dir, mission["id"], "paint")


def test_adhoc_mission_hands_to_the_proposing_planner(tmp_path, monkeypatch):
    """No saved team: the lead is the planner step that proposed the failed step."""
    def runner(spec):
        if spec["role"] == "pm":
            return _ok(json.dumps({"propose_steps": [
                {"id": "build", "role": "developer", "adapter": "root", "goal": "x"}]}))
        return _fail("kaboom")

    mission = _mission(tmp_path, monkeypatch, [
        {"id": "plan", "role": "pm", "adapter": "root", "goal": "g", "gates": []}])
    state_dir = tmp_path / ".skye"
    outcome = run_mission(mission, POLICY, runner=runner, state_dir=state_dir)
    assert outcome.status == "failed"
    handed = handoff_to_lead(state_dir, mission["id"], "build")
    assert handed == {"step": "build", "to": "plan"}

    def runner2(spec):
        if spec["role"] == "pm":
            return _ok(json.dumps({"recovery": [
                {"step": "build", "disposition": "waive",
                 "reason": "the goal stands without the extra build step"}]}))
        return _ok()
    outcome = run_mission(mission, POLICY, runner=runner2, state_dir=state_dir)
    assert outcome.status == "done"
    assert mission_status(mission, state_dir)["status"] == "done"


def test_handoff_constrains_recovery_proposals_to_the_roster(tmp_path, monkeypatch):
    """Recovery never widens the roster: an off-roster proposal in the lead's recovery
    answer is rejected exactly like a planning-time proposal."""
    mission = _failed_member_mission(tmp_path, monkeypatch)
    state_dir = tmp_path / ".skye"
    handoff_to_lead(state_dir, mission["id"], "ui")

    def runner(spec):
        if spec["role"] == "pm":
            return _ok(json.dumps({"propose_steps": [
                {"id": "ship", "role": "devops", "adapter": "root", "goal": "deploy"},
                {"id": "ui-retry", "role": "frontend-dev", "adapter": "root",
                 "goal": "build fixed", "recovers": "ui"}]}))
        return _ok("built")

    outcome = run_mission(mission, POLICY, runner=runner, state_dir=state_dir)
    assert outcome.status == "done"
    journal = (state_dir / "missions" / mission["id"] / "journal.jsonl").read_text()
    rejected = [json.loads(line) for line in journal.splitlines()
                if '"proposal-rejected"' in line]
    assert any("devops" in r["reason"] and "not a member of team" in r["reason"]
               for r in rejected)


# --- UI endpoint -----------------------------------------------------------

def test_handoff_endpoint_journals_and_resumes(tmp_path, monkeypatch):
    """The server's handoff action validates, journals the decision and resumes the
    mission; it refuses while the mission is live and on steps with no failure."""
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer
    from skye.ui import server as ui

    _env(tmp_path, monkeypatch)
    monkeypatch.setenv("SKYE_POLICY", str(tmp_path / "policy.json"))
    teams.add({"name": "website", "lead": "pm", "members": ["designer", "frontend-dev"]})
    team_id = teams.list_teams()[0]["id"]
    state_dir = tmp_path / "st"
    mission = _team_mission(tmp_path, monkeypatch, [
        {"id": "plan", "role": "pm", "adapter": "root", "goal": "g", "gates": []}])

    def runner(spec):
        if spec["role"] == "pm":
            return _ok(json.dumps({"propose_steps": [
                {"id": "ui", "role": "frontend-dev", "adapter": "root", "goal": "x"}]}))
        return _fail()
    outcome = run_mission(mission, POLICY, runner=runner, state_dir=state_dir)
    assert outcome.status == "failed"

    # The mission file must live where the server resolves it.
    import shutil
    mission_dir = state_dir / "missions" / mission["id"]
    shutil.copy(tmp_path / "mission.json", mission_dir / "mission.json")

    resumed = []
    monkeypatch.setattr(ui, "resume_in_thread",
                        lambda sd, mid, **kw: resumed.append(mid))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), ui.UiHandler)
    httpd.state_dir = state_dir
    httpd.token = "test-token-abc"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]

        def post(step):
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/missions/{mission['id']}/handoff",
                method="POST", data=json.dumps({"step": step}).encode(),
                headers={"X-Skye-Token": "test-token-abc",
                         "content-type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=10) as resp:
                    return resp.status, json.loads(resp.read() or b"{}")
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")

        status, body = post("ui")
        assert status == 200, body
        assert body == {"handed": "ui", "to": "plan", "resumed": True}
        assert resumed == [mission["id"]]
        journal = (mission_dir / "journal.jsonl").read_text()
        assert '"recovery-handoff"' in journal

        status, body = post("plan")  # settled done - nothing to hand back
        assert status == 400
        assert "no recorded failure" in body["error"]
    finally:
        httpd.shutdown()
        thread.join(5)

def test_handoff_is_safe_when_repeated(tmp_path, monkeypatch):
    """Handing the same failure twice changes nothing: the latest journal entry wins,
    the lead still has exactly one open recovery, and the task stays paused."""
    mission = _failed_member_mission(tmp_path, monkeypatch)
    state_dir = tmp_path / ".skye"

    handoff_to_lead(state_dir, mission["id"], "ui")
    handed = handoff_to_lead(state_dir, mission["id"], "ui")
    assert handed == {"step": "ui", "to": "plan"}

    from skye.mission import _open_recovery_handoffs
    journal = Journal(state_dir / "missions" / mission["id"] / "journal.jsonl")
    open_recoveries = _open_recovery_handoffs(journal.entries(), {"plan", "ui"})
    assert [item["id"] for item in open_recoveries["plan"]] == ["ui"]

    status = mission_status(mission, state_dir)
    assert status["status"] == "needs-owner"
    ui_step = next(s for s in status["steps"] if s["id"] == "ui")
    assert ui_step["status"] == "failed" and ui_step["handed_to"] == "plan"


def test_handoff_survives_a_restart(tmp_path, monkeypatch):
    """A crash between the handoff and the recovery run loses nothing: re-reading the
    journal from disk still shows the open recovery and the paused task, and the next
    run re-opens the lead with the failure in its brief."""
    mission = _failed_member_mission(tmp_path, monkeypatch)
    state_dir = tmp_path / ".skye"
    handoff_to_lead(state_dir, mission["id"], "ui")

    # Fresh reads from disk, as a restarted process would see them.
    reloaded = load_mission(tmp_path / "mission.json")
    status = mission_status(reloaded, state_dir)
    assert status["status"] == "needs-owner"
    ui_step = next(s for s in status["steps"] if s["id"] == "ui")
    assert ui_step["status"] == "failed" and ui_step["handed_to"] == "plan"

    def runner(spec):
        if spec["role"] == "pm":
            assert "handed back" in spec.get("brief", "")
            return _ok(json.dumps({"propose_steps": []}))
        return _ok("built")

    outcome = run_mission(reloaded, POLICY, runner=runner, state_dir=state_dir)
    # The lead settled with no recovery steps and no waiver: NOT an answer.
    assert outcome.status == "needs-owner"
    status = mission_status(reloaded, state_dir)
    assert status["status"] == "needs-owner"
    ui_step = next(s for s in status["steps"] if s["id"] == "ui")
    assert ui_step["status"] == "failed" and ui_step["handed_to"] == "plan"


def test_explicit_waiver_closes_without_erasing_the_failure(tmp_path, monkeypatch):
    """The lead may answer 'the goal stands without it' - but only as an explicit,
    justified waiver. Then the task can finish, while the failed step keeps its failed
    record: handed to the lead, never marked done, never implied recovered."""
    mission = _failed_member_mission(tmp_path, monkeypatch)
    state_dir = tmp_path / ".skye"
    handoff_to_lead(state_dir, mission["id"], "ui")

    calls = []

    def runner(spec):
        calls.append(spec["role"])
        if spec["role"] == "pm":
            return _ok(json.dumps({"recovery": [
                {"step": "ui", "disposition": "waive",
                 "reason": "the page moved to the existing marketing site"}]}))
        return _ok("built")

    outcome = run_mission(mission, POLICY, runner=runner, state_dir=state_dir)
    assert outcome.status == "done"
    assert calls == ["pm"]  # nothing re-ran for the failed step

    status = mission_status(mission, state_dir)
    assert status["status"] == "done"
    ui_step = next(s for s in status["steps"] if s["id"] == "ui")
    assert ui_step["state"] == "settled"
    assert ui_step["status"] == "failed"
    assert ui_step["handed_to"] == "plan"
    assert ui_step["reason"]

    journal = Journal(state_dir / "missions" / mission["id"] / "journal.jsonl")
    resolved = [e for e in journal.entries() if e.get("event") == "recovery-resolved"]
    assert len(resolved) == 1
    assert resolved[0]["resolution"] == "waived"
    assert "marketing site" in resolved[0]["reason"]


def test_unaddressed_handoff_stays_open(tmp_path, monkeypatch):
    """A lead that settles done with no replacement steps and no explicit waiver has
    NOT answered: the handoff stays open, the task pauses needs-owner, and the record
    says why. A later run that waives explicitly closes it."""
    mission = _failed_member_mission(tmp_path, monkeypatch)
    state_dir = tmp_path / ".skye"
    handoff_to_lead(state_dir, mission["id"], "ui")

    def empty_runner(spec):
        if spec["role"] == "pm":
            return _ok(json.dumps({"propose_steps": []}))
        return _ok("built")

    outcome = run_mission(mission, POLICY, runner=empty_runner, state_dir=state_dir)
    assert outcome.status == "needs-owner"

    journal = Journal(state_dir / "missions" / mission["id"] / "journal.jsonl")
    unaddressed = [e for e in journal.entries()
                   if e.get("event") == "recovery-unaddressed"]
    assert len(unaddressed) == 1 and unaddressed[0]["step"] == "ui"
    assert not [e for e in journal.entries() if e.get("event") == "recovery-resolved"]

    status = mission_status(mission, state_dir)
    assert status["status"] == "needs-owner"

    # A bare waive with no justification is not an answer either: the next run must
    # re-open the lead (recovery brief again) rather than closing on the malformed
    # disposition.
    def bare_waive_runner(spec):
        if spec["role"] == "pm":
            assert "handed back" in spec.get("brief", "")
            return _ok(json.dumps({"recovery": [
                {"step": "ui", "disposition": "waive", "reason": "  "}]}))
        return _ok("built")

    outcome = run_mission(mission, POLICY, runner=bare_waive_runner, state_dir=state_dir)
    assert outcome.status == "needs-owner"

    def waive_runner(spec):
        if spec["role"] == "pm":
            return _ok(json.dumps({"recovery": [
                {"step": "ui", "disposition": "waive",
                 "reason": "superseded by the existing endpoint"}]}))
        return _ok("built")

    outcome = run_mission(mission, POLICY, runner=waive_runner, state_dir=state_dir)
    assert outcome.status == "done"
    status = mission_status(mission, state_dir)
    assert status["status"] == "done"
    ui_step = next(s for s in status["steps"] if s["id"] == "ui")
    assert ui_step["status"] == "failed" and ui_step["handed_to"] == "plan"



def test_unnamed_proposal_cannot_pick_among_several_handoffs(tmp_path, monkeypatch):
    """Two failures handed to the same lead: an unnamed replacement is ambiguous and
    resolves NEITHER handoff (the task pauses). Only proposals naming their failure
    ("recovers") close the right one - unrelated proposals never close a handoff."""
    calls = []

    def runner(spec):
        calls.append(spec["role"])
        if spec["role"] == "pm":
            return _ok(json.dumps({"propose_steps": [
                {"id": "ui", "role": "frontend-dev", "adapter": "root", "goal": "page"},
                {"id": "art", "role": "designer", "adapter": "root", "goal": "art"}]}))
        return _fail()

    mission = _team_mission(tmp_path, monkeypatch, [
        {"id": "plan", "role": "pm", "adapter": "root", "goal": "g", "gates": []}])
    outcome = run_mission(mission, POLICY, runner=runner, state_dir=tmp_path / ".skye")
    assert outcome.status == "failed"
    state_dir = tmp_path / ".skye"
    handoff_to_lead(state_dir, mission["id"], "ui")
    handoff_to_lead(state_dir, mission["id"], "art")

    def vague_runner(spec):
        if spec["role"] == "pm":
            return _ok(json.dumps({"propose_steps": [
                {"id": "ui-2", "role": "frontend-dev", "adapter": "root",
                 "goal": "page, fixed"}]}))
        return _ok("built")

    outcome = run_mission(mission, POLICY, runner=vague_runner, state_dir=state_dir)
    assert outcome.status == "needs-owner"  # ambiguous: nothing resolved
    journal = Journal(state_dir / "missions" / mission["id"] / "journal.jsonl")
    assert not [e for e in journal.entries() if e.get("event") == "recovery-resolved"]

    def named_runner(spec):
        if spec["role"] == "pm":
            return _ok(json.dumps({"propose_steps": [
                {"id": "ui-3", "role": "frontend-dev", "adapter": "root",
                 "goal": "page, fixed", "recovers": "ui"}],
                "recovery": [{"step": "art", "disposition": "waive",
                              "reason": "the launch ships without new artwork"}]}))
        return _ok("built")

    outcome = run_mission(mission, POLICY, runner=named_runner, state_dir=state_dir)
    assert outcome.status == "done"
    journal = Journal(state_dir / "missions" / mission["id"] / "journal.jsonl")
    resolved = {e["step"]: e for e in journal.entries()
                if e.get("event") == "recovery-resolved"}
    assert resolved["ui"]["resolution"] == "replaced"
    assert resolved["ui"]["replaced_by"] == ["ui-3"]
    assert resolved["art"]["resolution"] == "waived"
    status = mission_status(mission, state_dir)
    assert status["status"] == "done"


def test_unnamed_proposal_never_resolves_even_a_single_handoff(tmp_path, monkeypatch):
    """One open handoff, one unnamed replacement: still NOT an answer. Relevance is
    never inferred from counts - only a named "recovers" or an explicit waiver closes
    a handoff; the unnamed proposal stays on the record as ordinary new work."""
    mission = _failed_member_mission(tmp_path, monkeypatch)
    state_dir = tmp_path / ".skye"
    handoff_to_lead(state_dir, mission["id"], "ui")

    def runner(spec):
        if spec["role"] == "pm":
            return _ok(json.dumps({"propose_steps": [
                {"id": "ui-2", "role": "frontend-dev", "adapter": "root",
                 "goal": "build the page, fixed"}]}))
        return _ok("built")

    outcome = run_mission(mission, POLICY, runner=runner, state_dir=state_dir)
    assert outcome.status == "needs-owner"  # handoff still open
    journal = Journal(state_dir / "missions" / mission["id"] / "journal.jsonl")
    assert not [e for e in journal.entries() if e.get("event") == "recovery-resolved"]
    # The unnamed proposal itself was still accepted and ran - it just isn't a recovery.
    status = mission_status(mission, state_dir)
    assert any(s["id"] == "ui-2" and s["status"] == "done" for s in status["steps"])
    ui_step = next(s for s in status["steps"] if s["id"] == "ui")
    assert ui_step["status"] == "failed" and ui_step["handed_to"] == "plan"
