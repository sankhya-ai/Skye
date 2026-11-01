"""The Skye UI server: token-guarded, localhost-only API over the .skye state dir, with operator
actions (approve / resume / redispatch) that call the same functions the CLI uses."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from skye.adapters.base import Result
from skye.mission import load_mission, run_mission
from skye.policy import Policy
from skye.ui import server as ui


@pytest.fixture()
def ui_server(tmp_path):
    """A live UI server on a random localhost port over a tmp state dir."""
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


def _req(url, path, token="test-token-abc", method="GET", body=None, headers=None, query_token=False):
    full = f"{url}{path}"
    if query_token:
        full += ("&" if "?" in full else "?") + "token=test-token-abc"
    request = urllib.request.Request(
        full, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Skye-Token": token, "content-type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def _mission_with_pending(state_dir, tmp_path):
    """Run a mission under a policy that allows nothing: it pauses needs-owner with a pending
    contract on disk, exactly the state the approval screen exists for."""
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    mission_file = tmp_path / "mission.json"
    mission_file.write_text(json.dumps({
        "id": "m1", "goal": "ship it", "repo": str(ws),
        "steps": [{"id": "dev", "role": "developer", "adapter": "claude-code",
                   "goal": "build", "gates": []}]}))
    mission = load_mission(mission_file)
    policy = Policy({"allow_unattended": [], "test_command": "true"})
    outcome = run_mission(mission, policy, runner=lambda spec: Result(ok=True, output="x", command=["f"]),
                          state_dir=state_dir, approvals_path=ui.approvals_path_for(state_dir))
    assert outcome.status == "needs-owner"
    return mission


# ----- auth guard -----

def test_api_rejects_missing_or_wrong_token(ui_server):
    url, _ = ui_server
    status, _ = _req(url, "/api/missions", token="")
    assert status == 403
    status, _ = _req(url, "/api/missions", token="nope")
    assert status == 403
    status, _ = _req(url, "/api/missions", token=None) if False else (None, None)
    # no header at all
    request = urllib.request.Request(f"{url}/api/missions")
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(request, timeout=10)
    assert err.value.code == 403


def test_api_rejects_query_token_but_page_load_allows_it(ui_server):
    url, _ = ui_server
    # The session token in the URL is a bootstrap for the SPA only; API calls must use the header,
    # so a cross-site image/link cannot read state.
    status, _ = _req(url, "/api/missions", token="", query_token=True)
    assert status == 403
    # static SPA assets are served without the token (they hold no state; every /api/ call
    # still requires it), because the browser fetches them with neither header nor query token
    for path in ("/?token=test-token-abc", "/", "/assets/anything.js"):
        request = urllib.request.Request(f"{url}{path}")
        with urllib.request.urlopen(request, timeout=10) as resp:
            assert resp.status == 200


def test_post_rejects_cross_origin(ui_server):
    url, _ = ui_server
    host = url.split("://", 1)[1]
    status, body = _req(url, "/api/approve", method="POST", body={"mission": "m1", "step": "dev"},
                        headers={"Origin": "http://evil.example"})
    assert status == 403 and "cross-origin" in body["error"]
    status, _ = _req(url, "/api/approve", method="POST", body={"mission": "m1", "step": "dev"},
                     headers={"Origin": f"http://{host}"})
    assert status != 403


# ----- reads -----

def test_missions_overview_and_detail(ui_server, tmp_path):
    url, state_dir = ui_server
    _mission_with_pending(state_dir, tmp_path)
    status, body = _req(url, "/api/missions")
    assert status == 200
    row = next(r for r in body["missions"] if r["id"] == "m1")
    assert row["needs_owner"] is True and row["overall"] == "needs-owner"
    status, detail = _req(url, "/api/missions/m1")
    assert status == 200
    assert detail["status"]["status"] == "needs-owner"
    assert detail["pending_contracts"][0]["step"] == "dev"
    assert any(e["event"] == "needs-owner" for e in detail["journal"])


def test_unknown_mission_404s(ui_server):
    url, _ = ui_server
    status, _ = _req(url, "/api/missions/nope")
    assert status == 404


# ----- operator actions -----

def test_approve_consumes_pending_contract_and_resume_finishes(ui_server, tmp_path, monkeypatch):
    url, state_dir = ui_server
    _mission_with_pending(state_dir, tmp_path)
    monkeypatch.setattr(ui, "RUNNER",
                        lambda spec: Result(ok=True, output="built", command=["fake"]))
    status, body = _req(url, "/api/approve", method="POST", body={"mission": "m1", "step": "dev"})
    assert status == 200 and body["approved"] is True
    assert not (state_dir / "missions" / "m1" / "pending-dev.json").exists()
    # the one-time approval landed in the UI-scoped store
    stored = json.loads((state_dir / "approvals.json").read_text())["approvals"]
    assert len(stored) == 1
    status, body = _req(url, "/api/missions/m1/resume", method="POST", body={})
    assert status == 200 and body["resumed"] is True
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        _, detail = _req(url, "/api/missions/m1")
        if not detail["live"].get("running") and detail["live"].get("finished"):
            break
        time.sleep(0.1)
    _, detail = _req(url, "/api/missions/m1")
    assert detail["live"].get("error") in (None, ""), detail["live"]
    assert detail["status"]["status"] == "done"


def test_double_resume_is_rejected(ui_server, tmp_path, monkeypatch):
    url, state_dir = ui_server
    _mission_with_pending(state_dir, tmp_path)
    started = threading.Event()

    def slow(spec):
        started.wait(10)
        return Result(ok=True, output="x", command=["f"])

    monkeypatch.setattr(ui, "RUNNER", slow)
    _req(url, "/api/approve", method="POST", body={"mission": "m1", "step": "dev"})
    status, _ = _req(url, "/api/missions/m1/resume", method="POST", body={})
    assert status == 200
    status, body = _req(url, "/api/missions/m1/resume", method="POST", body={})
    assert status == 400 and "already running" in body["error"]
    started.set()


def test_overview_counts_settled_steps_and_marks_approved(ui_server, tmp_path, monkeypatch):
    """The overview must reflect journal truth: a settled-done step counts as done, dynamic steps
    count toward the total, and an approved-but-not-resumed mission says approved, not still
    needs-owner."""
    url, state_dir = ui_server
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    mission_file = tmp_path / "mission.json"
    mission_file.write_text(json.dumps({
        "id": "m2", "goal": "two steps", "repo": str(ws),
        "steps": [
            {"id": "dev", "role": "developer", "adapter": "claude-code", "goal": "build",
             "gates": []},
            {"id": "tester", "role": "tester", "adapter": "claude-code", "goal": "verify",
             "needs": ["dev"], "gates": []}]}))
    mission = load_mission(mission_file)
    policy = Policy({"allow_unattended": [{"adapter": "claude-code", "role": "developer"}],
                     "test_command": "true"})
    outcome = run_mission(mission, policy,
                          runner=lambda spec: Result(ok=True, output="x", command=["f"]),
                          state_dir=state_dir, approvals_path=ui.approvals_path_for(state_dir))
    assert outcome.status == "needs-owner"  # dev done, tester paused
    _, body = _req(url, "/api/missions")
    row = next(r for r in body["missions"] if r["id"] == "m2")
    assert (row["done"], row["steps"]) == (1, 2), row
    assert row["needs_owner"] is True and row["approved"] is False
    _req(url, "/api/approve", method="POST", body={"mission": "m2", "step": "tester"})
    _, body = _req(url, "/api/missions")
    row = next(r for r in body["missions"] if r["id"] == "m2")
    assert row["needs_owner"] is False and row["approved"] is True
    _, detail = _req(url, "/api/missions/m2")
    assert detail["approved"] is True and detail["pending_contracts"] == []


def test_roles_api_lists_and_creates_dynamic_roles(ui_server, tmp_path, monkeypatch):
    """The Agents surface is backed by the real registry: GET shows builtins with their tool
    catalogues; POST creates an owner-defined specialist that then validates in contracts."""
    url, state_dir = ui_server
    monkeypatch.setenv("SKYE_ROLES", str(tmp_path / "skye.roles.json"))

    status, payload = _req(url, "/api/roles")
    assert status == 200
    by_name = {r["name"]: r for r in payload["roles"]}
    assert by_name["developer"]["catalogue"]["writable"] is True
    assert by_name["explorer"]["source"] == "builtin"

    status, payload = _req(url, "/api/roles", method="POST", body={
        "role": {"name": "security-auditor", "description": "audits for vulnerabilities",
                 "catalogue": {"tools": ["Read", "Grep"], "writable": False}}})
    assert status == 200, payload
    assert payload["saved"] is True

    status, payload = _req(url, "/api/roles")
    created = {r["name"]: r for r in payload["roles"]}["security-auditor"]
    assert created["source"] == "registry"
    assert created["catalogue"] == {"tools": ["Read", "Grep"], "writable": False}

    from skye.contract import validate
    assert validate({"goal": "audit", "repo": "/tmp/x", "role": "security-auditor",
                     "adapter": "root"})["role"] == "security-auditor"

    status, payload = _req(url, "/api/roles", method="POST",
                           body={"role": {"name": "NO SPACES"}})
    assert status == 400 and "role name" in payload["error"]


def test_create_mission_with_plan_builds_a_planner_step(ui_server, tmp_path):
    """The composer never hardcodes a pipeline: {"plan": true} yields ONE planner step whose
    worker proposes the real steps at runtime via propose_steps."""
    url, state_dir = ui_server
    status, payload = _req(url, "/api/missions", method="POST",
                           body={"goal": "ship a landing page", "plan": True})
    assert status == 200, payload
    snapshot = json.loads(
        (state_dir / "missions" / payload["id"] / "mission.json").read_text())
    [step] = snapshot["steps"]
    assert step["id"] == "plan" and step["role"] == "pm"
    assert "propose_steps" in step["brief"] and "ship a landing page" in step["brief"]
    assert "needs" not in step  # no fixed pipeline - the planner decides the shape


def test_projects_group_missions(ui_server, tmp_path, monkeypatch):
    """Berd's Projects architecture over the real engine: an owner-defined project (name +
    repo path) groups the missions created under it, and the project supplies the repo."""
    url, state_dir = ui_server
    monkeypatch.setenv("SKYE_PROJECTS", str(tmp_path / "skye.projects.json"))
    repo = tmp_path / "repo"
    repo.mkdir()

    status, payload = _req(url, "/api/projects")
    assert status == 200 and payload["projects"] == []

    status, payload = _req(url, "/api/projects", method="POST",
                           body={"project": {"name": "Skye", "repo": str(repo)}})
    assert status == 200, payload
    project_id = payload["project"]["id"]

    # a project pointing nowhere is rejected before any mission can be filed under it
    status, payload = _req(url, "/api/projects", method="POST",
                           body={"project": {"name": "ghost", "repo": str(tmp_path / "nope")}})
    assert status == 400

    # the composer path: a planner mission under the project, repo inherited from the project
    status, payload = _req(url, "/api/missions", method="POST",
                           body={"goal": "ship the thing", "plan": True,
                                 "project": project_id})
    assert status == 200, payload
    snapshot = json.loads(
        (state_dir / "missions" / payload["id"] / "mission.json").read_text())
    assert snapshot["project"] == project_id and snapshot["repo"] == str(repo)

    status, payload = _req(url, "/api/missions")
    row = next(r for r in payload["missions"] if r["id"] == snapshot["id"])
    assert row["project"] == project_id


def test_planner_mission_grows_the_workflow_at_runtime(tmp_path, monkeypatch):
    """The composer's plan:true mission, end to end through the engine: ONE planner step runs,
    its worker proposes the real steps, and the mission grows and finishes - nothing hardcoded."""
    from skye.adapters.base import Result
    from skye.events import FileBridge
    from skye.mission import load_mission, mission_status, run_mission
    from skye.policy import Policy
    from skye.ui.server import create_mission

    monkeypatch.setenv("SKYE_ROLES", str(tmp_path / "skye.roles.json"))
    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    state_dir = tmp_path / "st"
    mission_id = create_mission(state_dir,
                                {"goal": "build and check a hello page", "plan": True,
                                 "adapter": "claude-code", "repo": str(repo)})
    mission = load_mission(state_dir / "missions" / mission_id / "mission.json")
    assert [s["id"] for s in mission["steps"]] == ["plan"]

    proposal = {"propose_steps": [
        {"id": "build", "role": "developer", "adapter": "claude-code",
         "goal": "build the page", "needs": []},
        {"id": "check", "role": "tester", "adapter": "claude-code",
         "goal": "check the page", "needs": ["build"]}]}
    ran = []

    def runner(spec):
        ran.append(spec["role"])
        if spec["step_id"] == "plan" if "step_id" in spec else spec["role"] == "pm":
            return Result(ok=True, output="Plan: " + json.dumps(proposal))
        return Result(ok=True, output=f"{spec['role']} done")

    outcome = run_mission(mission,
                          Policy({"allow_unattended": [{"adapter": "claude-code", "role": "*"}],
                                  "test_command": "true"}),
                          runner=runner, bridge=FileBridge(tmp_path / "events.jsonl"),
                          state_dir=state_dir)
    assert outcome.status == "done", outcome.reason
    assert ran == ["pm", "developer", "tester"]
    status = mission_status(mission, state_dir)
    assert status["status"] == "done"
    assert [s["id"] for s in status["steps"]] == ["plan", "build", "check"]


def test_backlog_round_trip_through_the_api(ui_server, tmp_path, monkeypatch):
    """Proposed feature -> owner approves -> Start creates a planner mission under the project;
    the feature records its mission. Rejected and unapproved starts are refused."""
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "skye.backlog.json"))
    monkeypatch.setenv("SKYE_PROJECTS", str(tmp_path / "skye.projects.json"))
    url, _ = ui_server
    status, payload = _req(url, "/api/projects", method="POST",
                           body={"project": {"name": "webshop", "repo": str(tmp_path)}})
    project_id = payload["project"]["id"]
    from skye import backlog
    feature = backlog.propose([{"title": "Add cart", "brief": "shopping cart"}],
                              project=project_id, mission="mission-seed")[0]
    status, payload = _req(url, "/api/backlog")
    assert status == 200
    assert payload["features"][0]["id"] == feature["id"]
    status, payload = _req(url, "/api/backlog/start", method="POST", body={"feature": feature["id"]})
    assert status == 409  # pending features cannot start
    status, payload = _req(url, "/api/backlog/decide", method="POST",
                           body={"feature": feature["id"], "decision": "approved", "note": "go"})
    assert status == 200, payload
    assert payload["feature"]["status"] == "approved"
    status, payload = _req(url, "/api/backlog/start", method="POST", body={"feature": feature["id"]})
    assert status == 200, payload
    mission_id = payload["id"]
    stored = backlog.list_features()[0]
    assert stored["mission_id"] == mission_id
    status, payload = _req(url, f"/api/missions/{mission_id}")
    assert status == 200
    assert payload["mission"]["project"] == project_id
    assert "Add cart" in payload["mission"]["goal"]


def test_issue_intake_files_a_backlog_proposal(ui_server, tmp_path, monkeypatch):
    """An error signal POST becomes a recorded issue + a PENDING backlog proposal; the owner
    still decides. A duplicate signal bumps occurrences without a second proposal."""
    monkeypatch.setenv("SKYE_ISSUES", str(tmp_path / "skye.issues.json"))
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "skye.backlog.json"))
    url, _ = ui_server
    status, payload = _req(url, "/api/issues", method="POST",
                           body={"kind": "production-log", "summary": "502s on /checkout",
                                 "detail": "upstream timeout", "project": "shop",
                                 "source": "deploy-feed"})
    assert status == 200, payload
    assert payload["recorded"] is True
    status, payload = _req(url, "/api/issues")
    assert len(payload["issues"]) == 1
    status, payload = _req(url, "/api/backlog")
    assert len(payload["features"]) == 1
    assert payload["features"][0]["status"] == "pending"
    assert "502s on /checkout" in payload["features"][0]["title"]
    status, payload = _req(url, "/api/issues", method="POST",
                           body={"kind": "production-log", "summary": "502s on /checkout",
                                 "project": "shop", "source": "deploy-feed"})
    assert payload["recorded"] is False
    status, payload = _req(url, "/api/backlog")
    assert len(payload["features"]) == 1
    status, payload = _req(url, "/api/issues", method="POST",
                           body={"kind": "bogus", "summary": "x"})
    assert status == 400


def test_verify_endpoint_replays_the_journey_and_delivers(ui_server, tmp_path, monkeypatch):
    """Owner started work, mission done, no journey yet -> awaiting-verification. The verify
    endpoint replays the owner-configured journey in the project repo; only a pass delivers,
    and the evidence is recorded either way."""
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "skye.backlog.json"))
    monkeypatch.setenv("SKYE_PROJECTS", str(tmp_path / "skye.projects.json"))
    monkeypatch.setenv("SKYE_ISSUES", str(tmp_path / "skye.issues.json"))
    url, _ = ui_server
    from skye import backlog
    status, payload = _req(url, "/api/projects", method="POST",
                           body={"project": {"name": "shop", "repo": str(tmp_path)}})
    project_id = payload["project"]["id"]
    feature = backlog.propose([{"title": "Fix checkout", "brief": "repair the 500"}],
                              project=project_id, mission="seed")[0]
    backlog.decide(feature["id"], "approved")
    backlog.mark_started(feature["id"], "m1")
    backlog.mark_delivered("m1", journey_ok=False)
    # no journey command configured -> honest 400, nothing delivered
    status, payload = _req(url, "/api/backlog/verify", method="POST", body={"feature": feature["id"]})
    assert status == 400, payload
    assert backlog.list_features()[0]["status"] == "awaiting-verification"
    # owner configures the journey (policy file in the server's cwd state)
    import json as _json
    from skye.ui.server import policy_path_default
    policy_path_default().write_text(_json.dumps(
        {"journey_command": "echo 'checkout journey OK'"}))
    status, payload = _req(url, "/api/backlog/verify", method="POST", body={"feature": feature["id"]})
    assert status == 200, payload
    assert payload["verified"] is True
    assert payload["feature"]["status"] == "delivered"
    assert "checkout journey OK" in payload["feature"]["verification"]["evidence"]
    policy_path_default().write_text(_json.dumps(
        {"journey_command": "echo 'still 500' >&2; exit 1"}))
    feature2 = backlog.propose([{"title": "Fix search", "brief": "repair"}],
                               project=project_id, mission="seed")[0]
    backlog.decide(feature2["id"], "approved")
    backlog.mark_started(feature2["id"], "m2")
    backlog.mark_delivered("m2", journey_ok=False)
    status, payload = _req(url, "/api/backlog/verify", method="POST", body={"feature": feature2["id"]})
    assert payload["verified"] is False
    stored = [f for f in backlog.list_features() if f["id"] == feature2["id"]][0]
    assert stored["status"] == "awaiting-verification"
    assert "still 500" in stored["verification"]["evidence"]
    policy_path_default().unlink()


def _seed_awaiting_feature(tmp_path, monkeypatch, title="Fix checkout"):
    from skye import backlog
    feature = backlog.propose([{"title": title, "brief": "repair"}],
                              project="", mission="seed")[0]
    backlog.decide(feature["id"], "approved")
    backlog.mark_started(feature["id"], f"m-{title}")
    backlog.mark_delivered(f"m-{title}", journey_ok=False)
    return feature


def test_verify_rejects_invalid_transitions(ui_server, tmp_path, monkeypatch):
    """The replay gate cannot deliver pending, rejected, or approved-but-unstarted work."""
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "skye.backlog.json"))
    url, _ = ui_server
    from skye import backlog
    pending = backlog.propose([{"title": "Idea", "brief": "x"}], mission="seed")[0]
    rejected = backlog.propose([{"title": "No", "brief": "x"}], mission="seed")[0]
    backlog.decide(rejected["id"], "rejected")
    unstarted = backlog.propose([{"title": "Later", "brief": "x"}], mission="seed")[0]
    backlog.decide(unstarted["id"], "approved")
    expected = {pending["id"]: "pending", rejected["id"]: "rejected",
                unstarted["id"]: "approved"}
    for feature_id, want in expected.items():
        status, payload = _req(url, "/api/backlog/verify", method="POST",
                               body={"feature": feature_id})
        assert status == 409, (want, payload)
        assert backlog.get(feature_id)["status"] == want
    status, payload = _req(url, "/api/backlog/verify", method="POST", body={"feature": "nope"})
    assert status == 404


def test_verify_ignores_caller_supplied_command_and_redacts(ui_server, tmp_path, monkeypatch):
    """Caller-supplied command/evidence is untrusted: only the owner policy command runs,
    and secret-looking output is masked before it is recorded."""
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "skye.backlog.json"))
    monkeypatch.setenv("SKYE_PROJECTS", str(tmp_path / "skye.projects.json"))
    url, _ = ui_server
    from skye import backlog
    status, payload = _req(url, "/api/projects", method="POST",
                           body={"project": {"name": "shop", "repo": str(tmp_path)}})
    project_id = payload["project"]["id"]
    feature = backlog.propose([{"title": "Fix", "brief": "x"}], project=project_id,
                              mission="seed")[0]
    backlog.decide(feature["id"], "approved")
    backlog.mark_started(feature["id"], "m-redact")
    backlog.mark_delivered("m-redact", journey_ok=False)
    import json as _json
    from skye.ui.server import policy_path_default
    policy_path_default().write_text(_json.dumps(
        {"journey_command": "echo DEPLOY_TOKEN=supersecret99; exit 1"}))
    try:
        status, payload = _req(url, "/api/backlog/verify", method="POST",
                               body={"feature": feature["id"], "command": "echo pwned",
                                     "evidence": "fake pass", "verified": True})
        assert status == 200
        assert payload["verified"] is False
        stored = backlog.get(feature["id"])
        assert stored["status"] == "awaiting-verification"
        assert "supersecret99" not in stored["verification"]["evidence"]
        assert "[redacted]" in stored["verification"]["evidence"]
        assert "pwned" not in stored["verification"]["evidence"]
        assert payload["command"] == "echo DEPLOY_TOKEN=supersecret99; exit 1"
    finally:
        policy_path_default().unlink(missing_ok=True)


def test_verify_times_out_and_serializes_concurrent_replays(ui_server, tmp_path, monkeypatch):
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "skye.backlog.json"))
    monkeypatch.setenv("SKYE_PROJECTS", str(tmp_path / "skye.projects.json"))
    url, _ = ui_server
    from skye import backlog
    status, payload = _req(url, "/api/projects", method="POST",
                           body={"project": {"name": "shop", "repo": str(tmp_path)}})
    project_id = payload["project"]["id"]
    feature = backlog.propose([{"title": "Slow", "brief": "x"}], project=project_id,
                              mission="seed")[0]
    backlog.decide(feature["id"], "approved")
    backlog.mark_started(feature["id"], "m-slow")
    backlog.mark_delivered("m-slow", journey_ok=False)
    import json as _json
    import threading
    from skye.ui.server import policy_path_default
    policy_path_default().write_text(_json.dumps(
        {"journey_command": "sleep 5", "journey_timeout_seconds": 1}))
    try:
        results: list[tuple[int, dict]] = []

        def hit():
            results.append(_req(url, "/api/backlog/verify", method="POST",
                                body={"feature": feature["id"]}))

        first = threading.Thread(target=hit)
        first.start()
        import time as _time
        _time.sleep(0.5)  # let the first replay start
        second = threading.Thread(target=hit)
        second.start()
        first.join(); second.join()
        codes = sorted(code for code, _ in results)
        assert codes == [200, 409], results
        ok = [p for code, p in results if code == 200][0]
        assert ok["timed_out"] is True and ok["verified"] is False
        stored = backlog.get(feature["id"])
        assert stored["status"] == "awaiting-verification"
        assert "timed out" in stored["verification"]["evidence"]
    finally:
        policy_path_default().unlink(missing_ok=True)
