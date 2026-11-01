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
