"""The self-extension arcs as coherent user scenarios through the real app (scripted echo model, no provider, no cost).

Each scenario walks one loop end to end instead of asserting pieces in isolation:
A. discovery miss -> compose a tool -> use it in the same turn
B. generated-code proposal -> owner review -> refusal on a host without proven cgroup limits (or activation on one with them)
C. changed code after review -> activation refused
D. role adoption -> same-turn narrowing -> revocation keeps the narrowed snapshot
E. new-session isolation -> explicit owner promotion -> then usable
F. a session PROPOSES a job -> nothing starts -> the owner's accept runs exactly the stored fields (or dismiss ends it)
"""

from __future__ import annotations

import asyncio
import json

import time
from types import SimpleNamespace

import pytest

from conftest import of_type, requires_runtime, run_turn, text, tool_call

from tura_code import genplugins, jobs, launcher

MATH_SOURCE = (
    "import statistics\n\n\n"
    "def stats_summary(numbers):\n"
    "    values = [float(n) for n in numbers]\n"
    "    if not values:\n"
    "        raise ValueError('no numbers')\n"
    "    return {'mean': statistics.fmean(values)}\n"
)
MANIFEST = [{
    "name": "stats_summary",
    "description": "Mean of a list of numbers.",
    "parameters": {"type": "object", "properties": {"numbers": {"type": "array", "items": {"type": "number"}}},
                   "required": ["numbers"], "additionalProperties": False},
}]
MATH_TESTS = "from plugin import stats_summary\n\n\ndef test_mean():\n    assert stats_summary([2.0, 4.0])['mean'] == 3.0\n"


def _turn_results(client, workspace, script, session_id, prompt):
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, prompt)
    return events, {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(events, "ToolResult")}


@requires_runtime
def test_scenario_a_miss_compose_use_same_turn(client, workspace, script):
    """The loop Ashish asked for, composed-tools branch: the search misses (signal recorded, hint given), the agent
    composes a tool from existing ones, and uses it in the SAME turn through the gateway."""
    client.put("/api/settings", json={"dynamic_tools": True})
    script([
        tool_call("m", "find_tools", {"query": "zzzqqq xylophone tuning"}),
        tool_call("c", "create_tool", {
            "name": "process_digest",
            "description": "List background processes.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            "steps": [{"tool": "process", "arguments": {"action": "list"}}],
        }),
        tool_call("u", "use_tool", {"name": "process_digest", "arguments": {}}),
        text("done"),
    ])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    events, results = _turn_results(client, workspace, script, session_id, "find or make a process digest")
    assert "create_tool" in results["m"]["output"] and "propose_tool" in results["m"]["output"]  # the miss teaches the extension paths
    assert results["c"]["is_error"] is False
    assert results["u"]["is_error"] is False and "step 0" in results["u"]["output"]
    record = client.get(f"/api/sessions/{session_id}").json()
    assert record["capability_misses"] and record["capability_misses"][0]["query"] == "zzzqqq xylophone tuning"


@requires_runtime
def test_scenario_b_generated_code_respects_the_host_gate(client, workspace, script):
    """Generated branch, honest per host: where aggregate cgroup limits cannot be PROVEN (this environment),
    propose refuses before anything is stored and there is nothing to review; where they can, the candidate is
    verified-inert and the owner activates the reviewed hash. Both branches assert real behavior; the skip-labeled
    bounded-execution suite covers the supported-host path when such a host runs it."""
    prefix, reason = genplugins.execution_policy()
    script([tool_call("p", "propose_tool", {"name": "numstats", "tools": MANIFEST, "source": MATH_SOURCE, "tests": MATH_TESTS}), text("done")])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    _, results = _turn_results(client, workspace, script, session_id, "build the stats tool")
    if reason:
        assert results["p"]["is_error"] is True and "proven aggregate resource limits" in results["p"]["message"] + results["p"]["output"]
        assert client.get("/api/registry/plugins").json() == {}
        assert client.get("/api/registry/plugins/numstats/review").status_code == 404
    else:  # a host with proven limits: the full owner-gated arc
        assert results["p"]["is_error"] is False
        review = client.get("/api/registry/plugins/numstats/review").json()
        assert review["status"] == "verified" and review["hash_matches_record"] is True
        ok = client.post("/api/registry/plugins/numstats/activate", json={"version": 1, "sha256": review["sha256"]})
        assert ok.status_code == 200


@requires_runtime
def test_scenario_c_changed_code_cannot_be_activated(client, workspace, script, home, monkeypatch):
    """The owner reviews v1, the bytes change afterwards, and the reviewed hash no longer activates - at the
    review surface (mismatch reported) and at the activation gate (409). Verification stubbed; hash gates real."""
    monkeypatch.setattr(genplugins, "execution_policy", lambda: (["true"], ""))

    async def fake_jailed(store, name, version, payload, *, timeout, expected_sha256=None):
        return {"ok": True, "results": [{"name": "test_mean", "ok": True}], "isolation": "verification stubbed in this test"}

    monkeypatch.setattr(genplugins, "run_jailed", fake_jailed)
    script([tool_call("p", "propose_tool", {"name": "numstats", "tools": MANIFEST, "source": MATH_SOURCE, "tests": MATH_TESTS}), text("done")])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    _, results = _turn_results(client, workspace, script, session_id, "build the stats tool")
    assert json.loads(results["p"]["output"])["status"] == "verified"
    reviewed = client.get("/api/registry/plugins/numstats/review").json()
    (home / "registry" / "plugins" / "numstats" / "1" / "plugin.py").write_text(MATH_SOURCE + "\n# changed after review\n", "utf-8")
    stale = client.get("/api/registry/plugins/numstats/review").json()
    assert stale["hash_matches_record"] is False and stale["on_disk_sha256"] != reviewed["sha256"]
    refused = client.post("/api/registry/plugins/numstats/activate", json={"version": 1, "sha256": reviewed["sha256"]})
    assert refused.status_code == 409 and "no longer match" in refused.text


@requires_runtime
def test_scenario_d_adopt_narrow_revoke(client, workspace, script):
    """Role branch: adopt a durable role (narrowed, pinned, owner-notified), a native command is rejected in the
    SAME turn, and revoking the role afterwards leaves the narrowed snapshot in place."""
    client.put("/api/settings", json={"dynamic_tools": True})
    first = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    script([tool_call("r", "create_role", {"name": "reader", "description": "Read-only helper.", "tools": ["find_tools", "use_tool", "ReadFile"]}), text("done")])
    _turn_results(client, workspace, script, first, "make a role")
    assert client.post("/api/registry/roles/reader/promote").status_code == 200

    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    script([
        tool_call("a", "adopt_role", {"name": "reader"}),
        tool_call("s", "Shell", {"command": "echo should-not-run"}),
        text("done"),
    ])
    events, results = _turn_results(client, workspace, script, session_id, "adopt the reader role")
    assert results["a"]["is_error"] is False
    rejections = [e["payload"] for e in of_type(events, "ApprovalResolved") if e["payload"].get("response") == "reject" and e["payload"].get("sender") == "Shell"]
    assert rejections and "outside this session's tool scope" in rejections[0]["reason"]
    assert client.post("/api/registry/roles/reader/revoke").status_code == 200
    record = client.get(f"/api/sessions/{session_id}").json()
    assert record["role"] == "reader" and record["role_version"] == 1  # still pinned
    assert sorted(record["tool_scope"]) == ["ReadFile", "find_tools", "use_tool"]  # the narrowed snapshot stays

    script([tool_call("g", "use_tool", {"name": "process", "arguments": {"action": "list"}}), text("done")])
    _, results = _turn_results(client, workspace, script, session_id, "list processes")
    assert results["g"]["is_error"] is True  # revocation did not widen the session back


@requires_runtime
def test_scenario_e_session_isolation_then_explicit_promotion(client, workspace, script):
    """A tool one session creates is invisible to a fresh session until the owner promotes it - isolation by
    default, durability only by the owner's separate act."""
    client.put("/api/settings", json={"dynamic_tools": True})
    first = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    script([
        tool_call("c", "create_tool", {
            "name": "process_digest",
            "description": "List background processes.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            "steps": [{"tool": "process", "arguments": {"action": "list"}}],
        }),
        text("done"),
    ])
    _, results = _turn_results(client, workspace, script, first, "make a digest tool")
    assert results["c"]["is_error"] is False

    second = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    script([tool_call("u", "use_tool", {"name": "process_digest", "arguments": {}}), text("done")])
    _, results = _turn_results(client, workspace, script, second, "run the digest")
    assert results["u"]["is_error"] is True and "not an available tool" in results["u"]["message"]

    assert client.post("/api/registry/tools/process_digest/promote").status_code == 200
    script([tool_call("u2", "use_tool", {"name": "process_digest", "arguments": {}}), text("done")])
    _, results = _turn_results(client, workspace, script, second, "run the digest again")
    assert results["u2"]["is_error"] is False and "step 0" in results["u2"]["output"]


# ----- Scenario F: a session proposes a job; the owner's decision is the only start path ---------------------------


def _wait_job(client, job_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        item = client.get(f"/api/jobs/{job_id}").json()
        if item["status"] != "running":
            return item
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def _wait_terminal(client, job_id, timeout=30):
    # Wait until a job is neither running nor parked.
    end = time.time() + timeout
    while time.time() < end:
        item = client.get(f"/api/jobs/{job_id}").json()
        if item["status"] not in ("running", "waiting"):
            return item
        time.sleep(0.2)
    raise AssertionError("job did not settle")


def _wait_parked(client, job_id, resumes=0, timeout=30):
    # Wait until a job is parked, optionally after a given number of resumes.
    end = time.time() + timeout
    while time.time() < end:
        item = client.get(f"/api/jobs/{job_id}").json()
        if item["status"] == "waiting" and int(item.get("resumes") or 0) == resumes:
            return item
        time.sleep(0.2)
    raise AssertionError(f"job did not park with resumes={resumes}")


def _proposer_role(client, workspace, script, name="builder"):
    # A session-lifetime role that may propose jobs (the one management tool a role can name).
    client.put("/api/settings", json={"dynamic_tools": True})
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    script([tool_call("r", "create_role", {"name": name, "description": "Builds and may ask for help.", "tools": ["ReadFile", "Grep", "recommend_job"]}), text("done")])
    _, results = _turn_results(client, workspace, script, session_id, "make a role")
    assert results["r"]["is_error"] is False
    return session_id


def _prompt_events(client, session_id):
    page = client.get(f"/api/sessions/{session_id}/events").json()
    events = page.get("events", page) if isinstance(page, dict) else page
    return [e for e in events if e.get("type") == "UserPrompt"]


@requires_runtime
def test_scenario_f_session_proposes_owner_decides(client, workspace, script):
    """The loop Ashish asked for, delegation branch: the session recommends a job, NOTHING starts, the owner sees the
    pending request in the transcript and on the API, and their accept runs exactly the stored fields."""
    script([
        tool_call("j", "recommend_job", {"goal": "summarise the repo", "role": "explorer", "reason": "read-only sweep while you sleep"}),
        text("proposed"),
    ])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    events, results = _turn_results(client, workspace, script, session_id, "get the repo summarised")
    assert results["j"]["is_error"] is False, results["j"]
    payload = json.loads(results["j"]["output"])
    assert payload["status"] == "pending" and payload["requested_by"] == session_id
    assert "Nothing started" in payload["what_happens_now"]
    # The owner SEES the recommendation in the session transcript, with its goal and role.
    notice = [e for e in events if e["type"] == "JobRecommended"]
    assert notice and notice[0]["payload"]["goal"] == "summarise the repo" and notice[0]["payload"]["role"] == "explorer"
    # Pending for the owner; no job exists yet.
    requests = client.get("/api/job-requests").json()["requests"]
    assert [r["id"] for r in requests] == [payload["id"]] and requests[0]["status"] == "pending"
    # The accept runs EXACTLY the stored fields (the endpoint takes no body that could change them).
    script([text("Looked. All read-only.")])
    accepted = client.post(f"/api/job-requests/{payload['id']}/accept")
    assert accepted.status_code == 200, accepted.text
    job = accepted.json()["job"]
    assert job["goal"] == "summarise the repo" and job["role"] == "explorer"
    item = _wait_job(client, job["id"])
    assert item["status"] != "running"
    assert accepted.json()["request"]["status"] == "accepted" and accepted.json()["request"]["job_id"] == job["id"]
    # A resolved request never resolves again.
    assert client.post(f"/api/job-requests/{payload['id']}/accept").status_code == 409
    assert client.post(f"/api/job-requests/{payload['id']}/dismiss").status_code == 409


@requires_runtime
def test_recommend_job_with_unknown_role_is_refused_and_stores_nothing(client, workspace, script):
    script([tool_call("j", "recommend_job", {"goal": "x", "role": "wizard"}), text("ok")])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    _, results = _turn_results(client, workspace, script, session_id, "recommend")
    assert results["j"]["is_error"] is True and "Unknown role" in results["j"]["message"]
    assert client.get("/api/job-requests").json()["requests"] == []


@requires_runtime
def test_recommend_job_cannot_spend_another_sessions_private_role(client, workspace, script):
    """A session-lifetime role is private: only its creator may name it, and the accept carries source_session so the
    owner's start of THAT role stays explicit."""
    script([tool_call("r", "create_role", {"name": "reader", "description": "Read-only helper.", "tools": ["ReadFile"]}), text("done")])
    session_a = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    _, results_a = _turn_results(client, workspace, script, session_a, "make a role")
    assert results_a["r"]["is_error"] is False
    script([tool_call("j", "recommend_job", {"goal": "snoop", "role": "reader"}), text("ok")])
    session_b = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    _, results_b = _turn_results(client, workspace, script, session_b, "recommend")
    assert results_b["j"]["is_error"] is True and "private" in results_b["j"]["message"]
    assert client.get("/api/job-requests").json()["requests"] == []


@requires_runtime
def test_recommend_job_with_own_private_role_pins_version_and_accepts(client, workspace, script):
    script([
        tool_call("r", "create_role", {"name": "reader", "description": "Read-only helper.", "tools": ["ReadFile"]}),
        tool_call("j", "recommend_job", {"goal": "sweep", "role": "reader", "reason": "my own role"}),
        text("done"),
    ])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    _, results = _turn_results(client, workspace, script, session_id, "make a role and recommend a job")
    assert results["j"]["is_error"] is False, results["j"]
    payload = json.loads(results["j"]["output"])
    assert payload["role_version"] == 1 and payload["source_session"] == session_id  # pinned at proposal time
    script([text("Swept.")])
    accepted = client.post(f"/api/job-requests/{payload['id']}/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["job"]["role"] == "reader"
    _wait_job(client, accepted.json()["job"]["id"])


@requires_runtime
def test_dismiss_closes_the_request_and_nothing_runs(client, workspace, script):
    script([tool_call("j", "recommend_job", {"goal": "sweep", "role": "explorer"}), text("ok")])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    _, results = _turn_results(client, workspace, script, session_id, "recommend")
    payload = json.loads(results["j"]["output"])
    dismissed = client.post(f"/api/job-requests/{payload['id']}/dismiss")
    assert dismissed.status_code == 200 and dismissed.json()["request"]["status"] == "dismissed"
    assert client.post(f"/api/job-requests/{payload['id']}/accept").status_code == 409
    assert client.get(f"/api/job-requests/{payload['id']}/accept".replace(f"/job-requests/{payload['id']}/accept", "/jobs")).status_code != 200  # no jobs endpoint listing; nothing ran


# ----- JobRequestStore unit tests (no runtime) --------------------------------------------------------------------


def _store(tmp_path):
    return jobs.JobRequestStore(SimpleNamespace(root=tmp_path))


def _record(rid, created=1.0):
    return {"id": rid, "goal": "g", "role": "explorer", "role_version": 0, "workspace": "/w", "reason": "",
            "requested_by": "s1", "source_session": "s1", "status": "pending", "created_at": created}


def test_job_request_store_round_trip_and_single_resolve(tmp_path):
    store = _store(tmp_path)
    store.create(_record("r1"))
    assert [r["id"] for r in store.list()] == ["r1"] and store.list(status="pending")[0]["goal"] == "g"
    accepted = store.mark("r1", "accepted", job_id="job-1")
    assert accepted["status"] == "accepted" and accepted["job_id"] == "job-1"
    with pytest.raises(jobs.JobError):
        store.mark("r1", "dismissed")
    with pytest.raises(jobs.JobError):
        store.mark("missing", "accepted")
    fresh = _store(tmp_path)  # survives a restart (same tenant root)
    assert fresh.get("r1")["status"] == "accepted"


def test_job_request_store_caps_and_drops_resolved_first(tmp_path):
    store = _store(tmp_path)
    for i in range(jobs.MAX_REQUESTS_KEPT + 5):
        store.create(_record(f"r{i}", created=float(i)))
    assert len(store.list()) == jobs.MAX_REQUESTS_KEPT
    for i in range(3):
        store.mark(f"r{i + 5}", "dismissed")
    store.create(_record("new", created=999.0))
    ids = [r["id"] for r in store.list()]
    assert len(ids) == jobs.MAX_REQUESTS_KEPT and "new" in ids and "r5" not in ids  # the oldest RESOLVED dropped first


def test_request_job_validates_goal_and_pins(tmp_path):
    tenant = SimpleNamespace(root=tmp_path, registry=None, workspace_root=tmp_path, job_requests=_store(tmp_path))
    runtime = SimpleNamespace(record=SimpleNamespace(id="s1", workspace="/w"))
    payload, problem = jobs.request_job(tenant, runtime, {"goal": "  "})
    assert payload is None and "goal" in problem
    payload, problem = jobs.request_job(tenant, runtime, {"goal": "sweep", "role": "explorer", "reason": "r"})
    assert problem is None
    assert payload["status"] == "pending" and payload["role_version"] == 0 and payload["source_session"] == "s1"
    assert tenant.job_requests.get(payload["id"])["goal"] == "sweep"


# ----- Owner-start edges: one winner, no false start, owner sees the real scope ----------------------------------


@requires_runtime
def test_accept_after_role_revocation_fails_pending_and_dismissable(client, workspace, script):
    """Pinned-version discipline: the role is revoked between proposal and accept, so the accept REFUSES (the
    re-resolution fails), nothing starts, the request stays pending and the owner can still dismiss it."""
    script([
        tool_call("r", "create_role", {"name": "reader", "description": "Read-only helper.", "tools": ["ReadFile"]}),
        tool_call("j", "recommend_job", {"goal": "sweep", "role": "reader"}),
        text("done"),
    ])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    _, results = _turn_results(client, workspace, script, session_id, "make a role and recommend a job")
    payload = json.loads(results["j"]["output"])
    assert client.post("/api/registry/roles/reader/revoke").status_code == 200
    refused = client.post(f"/api/job-requests/{payload['id']}/accept")
    assert refused.status_code == 400 and "Unknown role" in refused.text
    still = client.get("/api/job-requests").json()["requests"][0]
    assert still["status"] == "pending" and "job_id" not in still  # no false start, request not lost
    assert client.post(f"/api/job-requests/{payload['id']}/dismiss").status_code == 200


@requires_runtime
def test_a_narrowed_session_cannot_even_propose(client, workspace, script):
    """Adopting a role narrows the MANAGEMENT tools away with everything else: a session narrowed to explorer
    cannot call recommend_job at all. Proposing new work is a management act, not something a focused role does."""
    client.put("/api/settings", json={"dynamic_tools": True})
    script([
        tool_call("a", "adopt_role", {"name": "explorer"}),
        tool_call("j", "recommend_job", {"goal": "build the feature", "role": "developer"}),
        text("done"),
    ])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    _, results = _turn_results(client, workspace, script, session_id, "adopt explorer and recommend a developer job")
    assert results["a"]["is_error"] is False  # narrowed
    assert results["j"]["is_error"] is True  # recommend_job is outside the narrowed scope
    assert client.get("/api/job-requests").json()["requests"] == []  # nothing was stored


@requires_runtime
def test_the_owner_sees_the_effective_scope_before_starting(client, workspace, script):
    """The stored request carries the role's resolved tool scope, so the owner reviews the actual permissions
    being authorized - the role label never hides them."""
    script([tool_call("j", "recommend_job", {"goal": "build the feature", "role": "developer", "reason": "needs writes and shell"}), text("done")])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    _, results = _turn_results(client, workspace, script, session_id, "recommend a developer job")
    assert results["j"]["is_error"] is False
    payload = json.loads(results["j"]["output"])
    assert "Shell" in payload["scope"]
    stored = client.get("/api/job-requests").json()["requests"][0]
    assert set(stored["scope"]) == set(jobs.ROLES["developer"])


def test_concurrent_accepts_have_exactly_one_winner(tmp_path):
    """Two accepts racing one pending request: the store lock admits one; the other sees the resolved state.
    Exactly one start runs, so exactly one job would exist."""
    import threading

    store = _store(tmp_path)
    store.create(_record("r1"))
    started = []
    outcomes = []

    def slow_start(record):
        time.sleep(0.05)  # widen the race window: the loser must still not enter start()
        started.append(record["id"])
        return {"id": "job-1"}

    def try_accept():
        try:
            store.accept("r1", slow_start)
            outcomes.append("accepted")
        except jobs.JobError as error:
            outcomes.append(str(error))

    threads = [threading.Thread(target=try_accept) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert started == ["r1"]  # start ran ONCE
    assert outcomes.count("accepted") == 1 and sum("already accepted" in o for o in outcomes) == 1
    assert store.get("r1")["status"] == "accepted" and store.get("r1")["job_id"] == "job-1"


def test_failed_start_leaves_the_request_pending_and_dismissable(tmp_path):
    store = _store(tmp_path)
    store.create(_record("r1"))

    def broken_start(record):
        raise jobs.JobError("isolation `bwrap` is not available here, so the job was not started")

    with pytest.raises(jobs.JobError, match="not available"):
        store.accept("r1", broken_start)
    assert store.get("r1")["status"] == "pending" and "job_id" not in store.get("r1")
    dismissed = store.mark("r1", "dismissed")  # the owner can still close it out
    assert dismissed["status"] == "dismissed"
    with pytest.raises(jobs.JobError):
        store.accept("r1", lambda record: {"id": "job-late"})  # and no late accept revives it


def test_accept_racing_dismiss_has_one_winner(tmp_path):
    """Dismiss holds the same lock: an accept that arrives after a dismiss sees the resolved state and starts nothing."""
    store = _store(tmp_path)
    store.create(_record("r1"))
    store.mark("r1", "dismissed")
    with pytest.raises(jobs.JobError, match="already dismissed"):
        store.accept("r1", lambda record: {"id": "job-should-not-exist"})
    assert store.get("r1")["status"] == "dismissed" and "job_id" not in store.get("r1")

def _requests_file(request_id):
    """The tenant's job-request store file that holds this request (several tenants can share /tmp in one pytest run)."""
    import json as _json
    from pathlib import Path
    for candidate in Path("/tmp").glob("**/jobs/requests.json"):
        try:
            data = _json.loads(candidate.read_text())
        except (OSError, ValueError):
            continue
        if request_id in data:
            return candidate, data
    raise AssertionError("requests store file for the tenant not found")


# ----- Crash-window idempotency and pinned limits --------------------------------------------------------------------


@requires_runtime
def test_accept_after_crash_window_adopts_the_existing_job(client, workspace, script):
    """Failure injection: the accept created and persisted the job, then the host 'died' before the accepted state
    was saved - on disk the request reads pending while its job exists. The retried accept ADOPTS that job: same
    job id, no duplicate start, and the job records which request it fulfills."""
    script([tool_call("j", "recommend_job", {"goal": "sweep", "role": "explorer"}), text("ok")])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    _, results = _turn_results(client, workspace, script, session_id, "recommend")
    payload = json.loads(results["j"]["output"])
    script([text("Swept.")])
    first = client.post(f"/api/job-requests/{payload['id']}/accept")
    assert first.status_code == 200, first.text
    job_id = first.json()["job"]["id"]
    item = client.get(f"/api/jobs/{job_id}").json()
    assert item.get("request_id") == payload["id"]  # lineage: the job names the request it fulfills
    # Simulate the crash window: the request's accepted state never landed on disk.
    import json as _json
    store_file, data = _requests_file(payload["id"])
    data[payload["id"]]["status"] = "pending"
    del data[payload["id"]]["job_id"]
    del data[payload["id"]]["resolved_at"]
    store_file.write_text(_json.dumps(data))
    # The retried accept must not start a second job.
    second = client.post(f"/api/job-requests/{payload['id']}/accept")
    assert second.status_code == 200, second.text
    assert second.json()["job"]["id"] == job_id  # SAME job adopted
    assert second.json()["request"]["status"] == "accepted"


@requires_runtime
def test_accept_applies_the_stored_budget_values(client, workspace, script):
    """The accept binds the budget from the STORED RECORD, never a fresh Budget() read. The file rewrite below
    stands in for 'this request was pinned when the defaults differed' - through normal operations the record is
    immutable (no API or session path edits it; see test_pending_requests_are_immutable_through_the_api), so the
    values the owner reviewed are the values that run."""
    script([tool_call("j", "recommend_job", {"goal": "sweep", "role": "explorer"}), text("ok")])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    _, results = _turn_results(client, workspace, script, session_id, "recommend")
    payload = json.loads(results["j"]["output"])
    assert payload["budget"]["timeout_seconds"] == 900 and payload["budget"]["max_steps"] == 0  # defaults pinned at proposal
    # Stand-in for a proposal pinned under different defaults: the record now holds a tighter value than current defaults.
    import json as _json
    store_file, data = _requests_file(payload["id"])
    data[payload["id"]]["budget"]["max_steps"] = 3
    store_file.write_text(_json.dumps(data))
    script([text("Swept.")])
    accepted = client.post(f"/api/job-requests/{payload['id']}/accept")
    assert accepted.status_code == 200, accepted.text
    item = client.get(f"/api/jobs/{accepted.json()['job']['id']}").json()
    assert item["budget"]["max_steps"] == 3  # the REVIEWED value ran, not a fresh default


def test_store_accept_adopts_existing_job_without_starting(tmp_path):
    """Store-level injection of the same window: a job already exists for the request; accept must not call start."""
    store = _store(tmp_path)
    store.create(_record("r1"))
    calls = []
    record, item = store.accept("r1", lambda rec: calls.append(rec) or {"id": "job-new"},
                                find_existing=lambda rid: {"id": "job-existing"})
    assert calls == [] and item["id"] == "job-existing"
    assert record["job_id"] == "job-existing" and record["status"] == "accepted"


@requires_runtime
def test_pending_requests_are_immutable_through_the_api(client, workspace, script):
    """The review boundary: no normal operation can change what a pending request asks. The store has no update
    method (only create/mark/accept), no session tool names one, and the HTTP surface has no mutation verb."""
    script([tool_call("j", "recommend_job", {"goal": "sweep", "role": "explorer"}), text("ok")])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    _, results = _turn_results(client, workspace, script, session_id, "recommend")
    payload = json.loads(results["j"]["output"])
    for method in (client.put, client.patch, client.delete):
        assert method(f"/api/job-requests/{payload['id']}").status_code == 405
    assert client.post(f"/api/job-requests/{payload['id']}", json={"goal": "changed", "role": "developer"}).status_code in (404, 405)
    stored = client.get("/api/job-requests").json()["requests"][0]
    assert stored["goal"] == "sweep" and stored["role"] == "explorer"  # untouched
    # NOTE: the file on disk is plain JSON like the registry and trust ledger; host filesystem access can rewrite
    # it. The boundary is 'no normal API or session operation', not OS-level tamper-proofing.


@requires_runtime
def test_pinned_role_version_content_is_what_runs(client, workspace, script):
    """Binding through NORMAL operations: v1 is proposed and reviewed; a newer v2 of the role is created before the
    click (role versions are append-only - content never edits in place). The accept runs the PINNED v1."""
    script([tool_call("r", "create_role", {"name": "reader", "description": "Read-only helper.", "tools": ["ReadFile"]}), text("done")])
    first = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    _, results = _turn_results(client, workspace, script, first, "make a role")
    assert results["r"]["is_error"] is False
    script([tool_call("j", "recommend_job", {"goal": "sweep", "role": "reader"}), text("ok")])
    _, results = _turn_results(client, workspace, script, first, "recommend")
    payload = json.loads(results["j"]["output"])
    assert payload["role_version"] == 1 and payload["scope"] == ["ReadFile"]
    # A new version exists by the time the owner clicks.
    script([tool_call("r2", "create_role", {"name": "reader", "description": "Read-only helper, wider.", "tools": ["ReadFile", "Grep"]}), text("done")])
    _, results2 = _turn_results(client, workspace, script, first, "revise the role")
    assert results2["r2"]["is_error"] is False
    script([text("Swept.")])
    accepted = client.post(f"/api/job-requests/{payload['id']}/accept")
    assert accepted.status_code == 200, accepted.text
    item = client.get(f"/api/jobs/{accepted.json()['job']['id']}").json()
    assert item["role_version"] == 1  # the REVIEWED version ran, not v2


# ----- Layer 1: governed recursive proposals (jobs asking for help) ----------------------------------------------


@requires_runtime
def test_scenario_g_job_proposes_sub_job_with_lineage(client, workspace, script):
    """Recursive help, governed: a job whose role names recommend_job proposes a sub-job; the request carries parent
    lineage, depth and an escalation flag; the owner's accept stamps the child item with the same lineage."""
    client.put("/api/settings", json={"dynamic_tools": True})
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    script([tool_call("r", "create_role", {"name": "builder", "description": "Builds and may ask for help.", "tools": ["ReadFile", "Grep", "recommend_job"]}), text("done")])
    _, results = _turn_results(client, workspace, script, session_id, "make a role")
    assert results["r"]["is_error"] is False
    script([tool_call("j", "recommend_job", {"goal": "check the utils", "role": "explorer", "reason": "parallel look"}),
            text("proposed"), text("parent final: utils checked")])
    parent = client.post("/api/jobs", json={"goal": "build the thing", "workspace": str(workspace), "role": "builder", "source_session": session_id}).json()
    parked = _wait_parked(client, parent["id"])  # the turn ended with the proposal open: the worker parked, no model calls
    requests = client.get("/api/job-requests").json()["requests"]
    assert len(requests) == 1
    req = requests[0]
    assert req["parent_job_id"] == parent["id"] and req["depth"] == 1
    assert req["escalation"] is True  # explorer's Glob is outside the builder role's tools: the owner must see that
    assert parked["waiting_on"]["requests"] == [req["id"]]
    script([text("Checked. Done: utils are fine.")])
    accepted = client.post(f"/api/job-requests/{req['id']}/accept")
    assert accepted.status_code == 200, accepted.text
    child = accepted.json()["job"]
    assert child.get("parent_job_id") == parent["id"] and child.get("depth") == 1
    _wait_terminal(client, child["id"])
    # The child's completion resumed the parked parent ONCE, delivered the outcome, and the parent finished.
    done = _wait_terminal(client, parent["id"])
    assert done["status"] == "finished", done
    assert done["resumes"] == 1 and done["delivered"] == [child["id"]]
    # Layer 3: the resume ran as a FRESH worker - a new session (new identity), the parked one kept as history.
    assert done["session"] != parked["session"] and done["session_history"] == [parked["session"]]
    prompts = _prompt_events(client, done["session"])
    resume = [e for e in prompts if "Delegated work you asked for" in (e.get("payload", {}).get("text") or "")]
    assert len(resume) == 1
    brief = resume[0]["payload"]["text"]
    assert "check the utils" in brief and "build the thing" in brief  # small brief: mission goal + child outcome
    old_prompts = _prompt_events(client, parked["session"])
    assert not any("Delegated work" in (e.get("payload", {}).get("text") or "") for e in old_prompts)  # disposed, not continued


@requires_runtime
def test_role_may_name_recommend_job_but_not_other_management_tools(client, workspace, script):
    """The widening is deliberate and narrow: recommend_job is nameable (proposing executes nothing); create_tool
    and the other management tools stay unnameable in roles."""
    script([
        tool_call("r1", "create_role", {"name": "helper", "description": "May ask for help.", "tools": ["ReadFile", "recommend_job"]}),
        tool_call("r2", "create_role", {"name": "sneaky", "description": "x", "tools": ["ReadFile", "create_tool"]}),
        text("done"),
    ])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    _, results = _turn_results(client, workspace, script, session_id, "make roles")
    assert results["r1"]["is_error"] is False
    assert results["r2"]["is_error"] is True and "create_tool" in results["r2"]["message"]


def _fake_tenant_with_parent(root, parent_scope, parent_depth=0, pending_children=0):
    store = _store(root)
    parent = {"id": "job-p", "session": "sess-p", "depth": parent_depth, "scope": parent_scope}
    for i in range(pending_children):
        store.create({**_record(f"c{i}", created=float(i)), "parent_job_id": "job-p"})
    return SimpleNamespace(root=root, registry=None, workspace_root=root, job_requests=store,
                           jobs=SimpleNamespace(items={"job-p": parent}))


def test_job_proposal_depth_bound_refuses(tmp_path):
    tenant = _fake_tenant_with_parent(tmp_path, ["ReadFile", "recommend_job"], parent_depth=jobs.MAX_PROPOSAL_DEPTH)
    runtime = SimpleNamespace(record=SimpleNamespace(id="sess-p", workspace="/w"))
    payload, problem = jobs.request_job(tenant, runtime, {"goal": "deeper", "role": "explorer"})
    assert payload is None and "depth" in problem
    assert tenant.job_requests.list() == []  # nothing stored


def test_job_proposal_fanout_bound_refuses(tmp_path):
    tenant = _fake_tenant_with_parent(tmp_path, ["ReadFile", "recommend_job"], pending_children=jobs.MAX_PENDING_PER_PARENT)
    runtime = SimpleNamespace(record=SimpleNamespace(id="sess-p", workspace="/w"))
    payload, problem = jobs.request_job(tenant, runtime, {"goal": "more", "role": "explorer"})
    assert payload is None and "waiting for the owner" in problem
    assert len(tenant.job_requests.list()) == jobs.MAX_PENDING_PER_PARENT  # unchanged


def test_job_proposal_escalation_flag(tmp_path):
    runtime = SimpleNamespace(record=SimpleNamespace(id="sess-p", workspace="/w"))
    wide = _fake_tenant_with_parent(tmp_path / "a", ["ReadFile", "Glob", "Grep", "recommend_job"])
    payload, problem = jobs.request_job(wide, runtime, {"goal": "g", "role": "explorer"})
    assert problem is None and payload["escalation"] is False and payload["depth"] == 1 and payload["parent_job_id"] == "job-p"
    narrow = _fake_tenant_with_parent(tmp_path / "b", ["ReadFile", "recommend_job"])
    payload, problem = jobs.request_job(narrow, runtime, {"goal": "g", "role": "explorer"})
    assert problem is None and payload["escalation"] is True  # Glob/Grep are beyond the parent


def test_chat_session_proposal_carries_no_lineage(tmp_path):
    tenant = SimpleNamespace(root=tmp_path, registry=None, workspace_root=tmp_path, job_requests=_store(tmp_path),
                             jobs=SimpleNamespace(items={}))
    runtime = SimpleNamespace(record=SimpleNamespace(id="s1", workspace="/w"))
    payload, problem = jobs.request_job(tenant, runtime, {"goal": "g", "role": "explorer"})
    assert problem is None
    assert payload["parent_job_id"] == "" and payload["depth"] == 0 and payload["escalation"] is False


# ----- Layer 2: parking and event-driven resume (durable missions, disposable workers) -----------------------------


@requires_runtime
def test_scenario_h_cancel_while_parked_never_resumes(client, workspace, script):
    """The owner cancels a parked parent: no worker is running, the parked state ends, and a later child
    completion does NOT resurrect it."""
    session_id = _proposer_role(client, workspace, script)
    script([tool_call("j", "recommend_job", {"goal": "side quest", "role": "explorer", "reason": "r"}),
            text("proposed"), text("must never be played")])
    parent = client.post("/api/jobs", json={"goal": "main work", "workspace": str(workspace), "role": "builder", "source_session": session_id}).json()
    _wait_parked(client, parent["id"])
    req = client.get("/api/job-requests").json()["requests"][0]
    cancelled = client.post(f"/api/jobs/{parent['id']}/cancel").json()
    assert cancelled["status"] == "stopped" and cancelled["stopped_by"] == "cancelled"
    script([text("child done")])
    child = client.post(f"/api/job-requests/{req['id']}/accept").json()["job"]
    _wait_terminal(client, child["id"])
    time.sleep(1.5)  # long enough for any (wrong) resume to fire
    item = client.get(f"/api/jobs/{parent['id']}").json()
    assert item["status"] == "stopped" and item["resumes"] == 0 and item["delivered"] == []


@requires_runtime
def test_scenario_i_dismissed_proposals_resume_parent(client, workspace, script):
    """A dismissal is an outcome: each dismiss resumes the parked parent once; with nothing left outstanding the
    parent finishes instead of parking forever."""
    session_id = _proposer_role(client, workspace, script)
    # Every resume spawns a FRESH worker (layer 3): its process resolves the current script at spawn, so each
    # resume reply is installed right before the event that triggers that resume.
    script([
        tool_call("j1", "recommend_job", {"goal": "part one", "role": "explorer", "reason": "r"}),
        tool_call("j2", "recommend_job", {"goal": "part two", "role": "explorer", "reason": "r"}),
        text("proposed both"),
    ])
    parent = client.post("/api/jobs", json={"goal": "two-part work", "workspace": str(workspace), "role": "builder", "source_session": session_id}).json()
    _wait_parked(client, parent["id"])
    reqs = client.get("/api/job-requests").json()["requests"]
    assert len(reqs) == 2
    script([text("first dismissal seen")])
    client.post(f"/api/job-requests/{reqs[0]['id']}/dismiss")
    parked = _wait_parked(client, parent["id"], resumes=1)  # resumed once, parked again on the second proposal
    assert parked["delivered_requests"] == [reqs[0]["id"]]
    script([text("second dismissal seen")])
    client.post(f"/api/job-requests/{reqs[1]['id']}/dismiss")
    done = _wait_terminal(client, parent["id"])
    assert done["status"] == "finished" and done["resumes"] == 2
    assert done["delivered_requests"] == [reqs[0]["id"], reqs[1]["id"]]


@requires_runtime
def test_scenario_j_cancelled_child_outcome_delivered(client, workspace, script, monkeypatch):
    """A child the owner CANCELS is still an outcome: the parked parent is resumed with the cancelled status.
    (The provider turns slow for the child via a local mock, so the child is genuinely mid-turn at cancel time.)"""
    from mock_provider import MockProvider

    session_id = _proposer_role(client, workspace, script)
    script([tool_call("j", "recommend_job", {"goal": "long check", "role": "explorer", "reason": "r"}),
            text("proposed")])
    parent = client.post("/api/jobs", json={"goal": "main work", "workspace": str(workspace), "role": "builder", "source_session": session_id}).json()
    _wait_parked(client, parent["id"])
    req = client.get("/api/job-requests").json()["requests"][0]
    monkeypatch.delenv(launcher.SCRIPTED_ECHO_ENV, raising=False)  # the child and the resume turn use the mock provider
    with MockProvider([{"delay": 10, "text": "late child reply"}, {"text": "parent final: noted the cancel"}]) as mock:
        client.put("/api/settings", json={"base_url": mock.base_url, "openrouter_api_key": "sk-test-mock", "model": "mock/model"})
        child = client.post(f"/api/job-requests/{req['id']}/accept").json()["job"]
        end = time.time() + 10
        while time.time() < end:  # the child's turn has genuinely started (and its process resolved its provider)
            if _prompt_events(client, child["session"]):
                break
            time.sleep(0.1)
        else:
            raise AssertionError("child turn never started")
        client.post(f"/api/jobs/{child['id']}/cancel")
        cancelled = _wait_terminal(client, child["id"])
        assert cancelled["status"] == "stopped" and cancelled["stopped_by"] == "cancelled", cancelled
        done = _wait_terminal(client, parent["id"])
    assert done["status"] == "finished", done
    assert done["resumes"] == 1 and done["delivered"] == [child["id"]]
    prompts = _prompt_events(client, done["session"])
    resume = [e for e in prompts if "Delegated work you asked for" in (e.get("payload", {}).get("text") or "")]
    assert len(resume) == 1
    body = resume[0]["payload"]["text"]
    assert child["id"] in body and '"status": "stopped"' in body and '"stopped_by": "cancelled"' in body


@requires_runtime
def test_scenario_k_parked_mission_wall_deadline_fires(client, workspace, script):
    """The approved wall clock includes parked time: with no events at all, a still-parked job stops at the
    deadline instead of lingering."""
    session_id = _proposer_role(client, workspace, script)
    script([tool_call("j", "recommend_job", {"goal": "never accepted", "role": "explorer", "reason": "r"}), text("proposed")])
    parent = client.post("/api/jobs", json={"goal": "main work", "workspace": str(workspace), "role": "builder",
                                            "source_session": session_id, "budget": {"timeout_seconds": 3}}).json()
    _wait_parked(client, parent["id"])
    done = _wait_terminal(client, parent["id"], timeout=20)
    assert done["status"] == "stopped" and done["stopped_by"] == "mission_timeout", done


# ----- Layer 2 units: gate, clamp, delivery dedup, recovery sweep, compact+redacted payload -------------------------


def _jobs_with(tmp_path, items=(), requests=()):
    store = SimpleNamespace(list=lambda status="": [r for r in requests if not status or r.get("status") == status])
    j = jobs.Jobs(SimpleNamespace(root=tmp_path, job_requests=store))
    for it in items:
        j.items[it["id"]] = it
    return j


def test_mission_budget_gate(tmp_path):
    j = _jobs_with(tmp_path)
    base = {"id": "p", "budget": {"max_tokens": 100, "max_cost_usd": 1.0, "timeout_seconds": 60},
            "started": time.time(), "resumes": 0, "used": {}}
    assert j._gate({**base, "used": {"tokens": 50, "cost_usd": 0.5}}) is None
    assert j._gate({**base, "used": {"tokens": 100}}) == "mission_token_budget"   # accumulated over ALL turns
    assert j._gate({**base, "used": {"cost_usd": 1.5}}) == "mission_cost_budget"
    assert j._gate({**base, "started": time.time() - 120}) == "mission_timeout"   # parked time counts
    assert j._gate({**base, "resumes": jobs.MAX_RESUMES}) == "resume_cap"


def test_clamp_record_tightens_to_remaining():
    rec = SimpleNamespace(max_tokens=0, max_cost_usd=0.0)
    item = {"budget": {"max_tokens": 100, "max_cost_usd": 2.0}, "used": {"tokens": 30, "cost_usd": 0.5}}
    jobs.Jobs._clamp_record(item, rec)
    assert rec.max_tokens == 70 and rec.max_cost_usd == 1.5  # the resume turn's ENFORCED caps are the remainder


def test_deliverables_dedup_and_settled_semantics(tmp_path):
    reqs = [{"id": "q1", "parent_job_id": "p", "status": "dismissed"}, {"id": "q2", "parent_job_id": "p", "status": "pending"}]
    kids = [{"id": "c2", "parent_job_id": "p", "status": "finished"},
            {"id": "c1", "parent_job_id": "p", "status": "waiting"},
            {"id": "c3", "parent_job_id": "p", "status": "error: boom"}]
    parent = {"id": "p", "delivered": ["c2"], "delivered_requests": []}
    j = _jobs_with(tmp_path, items=[parent, *kids], requests=reqs)
    children, requests = j._deliverables(j.items["p"])
    assert [c["id"] for c in children] == ["c3"]   # c2 already delivered; c1 still parked: no outcome yet
    assert [r["id"] for r in requests] == ["q1"]   # a pending proposal is not an outcome


async def test_maybe_resume_gate_stop_records_undelivered(tmp_path):
    parent = {"id": "p", "session": "s", "status": "waiting", "resumes": jobs.MAX_RESUMES, "budget": {}, "used": {},
              "delivered": [], "delivered_requests": [], "started": time.time()}
    child = {"id": "c", "parent_job_id": "p", "status": "finished", "result": None, "role": "explorer", "goal": "g"}
    j = _jobs_with(tmp_path, items=[parent, child])
    await j._maybe_resume("p")
    p = j.items["p"]
    assert p["status"] == "stopped" and p["stopped_by"] == "resume_cap"
    assert p["undelivered"] == ["c"] and j.tasks == {}  # named, never silently dropped; no turn launched


async def test_maybe_resume_never_resurrects_a_decided_job(tmp_path):
    parent = {"id": "p", "status": "stopped", "delivered": [], "delivered_requests": []}
    child = {"id": "c", "parent_job_id": "p", "status": "finished"}
    j = _jobs_with(tmp_path, items=[parent, child])
    await j._maybe_resume("p")
    assert j.items["p"]["status"] == "stopped" and j.tasks == {}


async def test_recovery_sweep_after_restart(monkeypatch, tmp_path):
    """Host restart: running jobs are marked interrupted (existing rule), PARKED jobs survive, and outcomes that
    settled while the host was down are delivered by the sweep."""
    d = tmp_path / "jobs"
    d.mkdir()
    (d / "p.json").write_text(json.dumps({"id": "p", "session": "sess-p", "status": "waiting", "budget": {}, "used": {},
                                          "resumes": 0, "delivered": [], "delivered_requests": [], "started": time.time()}))
    (d / "c.json").write_text(json.dumps({"id": "c", "parent_job_id": "p", "status": "running", "started": time.time()}))
    (d / "r.json").write_text(json.dumps({"id": "r", "session": "s", "status": "running", "started": time.time()}))
    calls = []

    async def fake_resume(self, pid):
        calls.append(pid)

    monkeypatch.setattr(jobs.Jobs, "_maybe_resume", fake_resume)
    j = jobs.Jobs(SimpleNamespace(root=tmp_path, job_requests=SimpleNamespace(list=lambda status="": [])))
    assert j.items["r"]["status"] == "interrupted" and j.items["c"]["status"] == "interrupted"  # the existing crash rule
    assert j.items["p"]["status"] == "waiting"  # parked is the durable state: nothing was mid-turn
    await asyncio.sleep(0.2)  # the sweep scheduled at construction runs
    assert calls == ["p"]  # the interrupted child's outcome is deliverable to the parked parent


async def test_resume_drive_delivers_compact_redacted_outcome(tmp_path):
    """The resume turn gets child OUTCOMES only - redacted contract data, no transcripts, no session internals -
    on the parent's own parked session."""
    class FakeRuntime:
        def __init__(self):
            self._turn_done = asyncio.Event()
            self.record = SimpleNamespace(id="sess-p", max_tokens=0, max_cost_usd=0.0, last_status="finished")
            self.last_budget, self.last_reply, self.last_error, self.last_files = {}, "done", "", []
            self.prompts = []

        async def prompt(self, text, attachments=None):
            self.prompts.append(text)
            self._turn_done.set()

        async def wait_turn(self, timeout=None):
            return None

        def save_record(self):
            return None

    fake = FakeRuntime()
    broker = SimpleNamespace(redact_json=lambda v: json.loads(json.dumps(v).replace("s3cr3t", "[redacted]")))
    tenant = SimpleNamespace(root=tmp_path, job_requests=SimpleNamespace(list=lambda status="": []),
                             sessions=SimpleNamespace(create=lambda *a, **kw: fake), broker=broker,
                             proxy=SimpleNamespace(enabled=False))
    j = _jobs_with(tmp_path)
    j.tenant = tenant
    parent = {"id": "p", "session": "sess-p", "status": "running", "budget": {}, "used": {}, "resumes": 1,
              "delivered": [], "delivered_requests": [], "started": time.time(), "contract": ["summary"],
              "credentials": ["stripe-key"], "workspace": "/w", "role": "builder", "goal": "the mission"}
    child = {"id": "c", "session": "sess-child", "parent_job_id": "p", "status": "finished", "role": "explorer",
             "goal": "g", "result": {"contract": {"data": {"note": "s3cr3t found"}}}}
    j.items.update({"p": parent, "c": child})
    await j._resume_drive(parent, [child], [])
    assert parent["status"] == "finished" and parent["delivered"] == ["c"]
    assert parent["session_history"] == ["sess-p"]  # the parked session is history, not the worker
    prompt = fake.prompts[0]
    assert "[redacted] found" in prompt and "s3cr3t" not in prompt
    assert "sess-child" not in prompt  # outcomes only, never the child's session internals
    assert "summary" in prompt  # the mission's contract reminder survives the resume
    assert "the mission" in prompt  # the goal anchors the small brief
    assert fake.record.credentials == []  # grant closed at terminal (layer 4); during the turn the record carried
    # the name, broker-resolved at use...
    assert "stripe-key" not in prompt  # ...never inherited through narrative


async def test_resume_turn_error_is_not_a_delivery(tmp_path):
    # A resume turn that ERRORS (provider/launch failure) presented nothing: the outcomes stay undelivered and
    # named, the error is visible, and nothing retries on its own.
    class FakeRuntime:
        def __init__(self):
            self._turn_done = asyncio.Event()
            self.record = SimpleNamespace(id="sess-p", max_tokens=0, max_cost_usd=0.0, last_status="error")
            self.last_budget, self.last_reply, self.last_error, self.last_files = {}, "", "provider exploded", []

        async def prompt(self, text, attachments=None):
            self._turn_done.set()

        async def wait_turn(self, timeout=None):
            return None

        def save_record(self):
            return None

    fake = FakeRuntime()
    tenant = SimpleNamespace(root=tmp_path, job_requests=SimpleNamespace(list=lambda status="": []),
                             sessions=SimpleNamespace(create=lambda *a, **kw: fake),
                             broker=SimpleNamespace(redact_json=lambda v: v), proxy=SimpleNamespace(enabled=False))
    j = _jobs_with(tmp_path)
    j.tenant = tenant
    parent = {"id": "p", "session": "sess-p", "status": "running", "budget": {}, "used": {}, "resumes": 1,
              "delivered": [], "delivered_requests": [], "started": time.time(), "contract": [], "credentials": []}
    child = {"id": "c", "parent_job_id": "p", "status": "finished", "role": "explorer", "goal": "g", "result": None}
    j.items.update({"p": parent, "c": child})
    await j._resume_drive(parent, [child], [])
    assert parent["status"].startswith("error") and parent["delivered"] == [] and parent["undelivered"] == ["c"]


# ----- Layer 4: bounded delegation + credential/scope properties across the mission ---------------------------------


def test_job_proposal_mission_delegation_cap(tmp_path):
    # The pending-fanout cap alone would allow an unbounded SEQUENCE of accepted children, and a per-parent cap
    # alone would multiply down the tree; the mission cap counts the WHOLE TREE from the root job.
    store = _store(tmp_path)
    parent = {"id": "job-p", "session": "sess-p", "depth": 0, "scope": ["ReadFile", "recommend_job"], "mission_root_id": "job-p"}
    children = {f"c{i}": {"id": f"c{i}", "parent_job_id": "job-p", "status": "finished", "mission_root_id": "job-p"}
                for i in range(jobs.MAX_DELEGATIONS_PER_MISSION - 1)}
    children["grandchild"] = {"id": "grandchild", "parent_job_id": "c0", "status": "finished", "mission_root_id": "job-p"}  # tree-wide, not per-parent
    other = {"other": {"id": "other", "parent_job_id": "job-x", "status": "finished", "mission_root_id": "job-x"}}  # another mission's tree does not count
    tenant = SimpleNamespace(root=tmp_path, registry=None, workspace_root=tmp_path, job_requests=store,
                             jobs=SimpleNamespace(items={**children, **other, "job-p": parent}))
    runtime = SimpleNamespace(record=SimpleNamespace(id="sess-p", workspace="/w"))
    payload, problem = jobs.request_job(tenant, runtime, {"goal": "one more", "role": "explorer"})
    assert payload is None and "delegation cap" in problem
    assert tenant.job_requests.list() == []


def test_terminal_job_credential_grant_closes(tmp_path):
    # Job-terminal closes the GRANT, not only the driver: the clearing covers the current session record and
    # every superseded (parked) session the job ever drove; the item keeps the names as the audit record.
    from tura_code.sessions.record import SessionRecord

    sessions_dir = tmp_path / "sessions"
    old_dir, new_dir = sessions_dir / "sess-old", sessions_dir / "sess-new"
    for session_id, directory in (("sess-old", old_dir), ("sess-new", new_dir)):
        record = SessionRecord(id=session_id, workspace="/w", title="t")
        record.credentials = ["api"]
        record.save(directory)
    tenant = SimpleNamespace(sessions=SimpleNamespace(dir=sessions_dir))
    j = _jobs_with(tmp_path)
    j.tenant = tenant
    item = {"id": "p", "session": "sess-new", "session_history": ["sess-old"], "credentials": ["api"]}
    j._close_credentials(item)
    for session_id in ("sess-old", "sess-new"):
        assert SessionRecord.load(sessions_dir / session_id / "session.json").credentials == []
    assert item["credentials"] == ["api"]  # the audit record of what the job WAS granted stays


def test_proposal_carries_no_credentials(tmp_path):
    # A proposal cannot ask for credentials: the stored record has no field for them, so an accepted child never
    # inherits the parent's credentials - the owner grants credentials only by a direct, owner-authenticated start.
    parent = {"id": "job-p", "session": "sess-p", "depth": 0, "scope": ["ReadFile", "recommend_job"],
              "credentials": ["stripe-key"]}
    tenant = SimpleNamespace(root=tmp_path, registry=None, workspace_root=tmp_path, job_requests=_store(tmp_path),
                             jobs=SimpleNamespace(items={"job-p": parent}))
    runtime = SimpleNamespace(record=SimpleNamespace(id="sess-p", workspace="/w"))
    payload, problem = jobs.request_job(tenant, runtime, {"goal": "g", "role": "explorer"})
    assert problem is None
    record = tenant.job_requests.list()[0]
    assert "credentials" not in record and "credentials" not in str(sorted(record.keys()))


async def test_resume_worker_gets_pinned_scope_not_current_role(tmp_path):
    # Scope pinning across resumes: the fresh worker is created with the mission's pinned scope; nothing in the
    # resume consults the registry, so a wider role version created while parked cannot widen the worker.
    created = {}

    class FakeRuntime:
        def __init__(self):
            self._turn_done = asyncio.Event()
            self.record = SimpleNamespace(id="sess-new", max_tokens=0, max_cost_usd=0.0, last_status="finished")
            self.last_budget, self.last_reply, self.last_error, self.last_files = {}, "done", "", []
            self.prompts = []

        async def prompt(self, text, attachments=None):
            self.prompts.append(text)
            self._turn_done.set()

        async def wait_turn(self, timeout=None):
            return None

        def save_record(self):
            return None

    fake = FakeRuntime()

    def fake_create(workspace, title, mode="", tool_scope=()):
        created["tool_scope"] = list(tool_scope)
        return fake

    # No registry on this tenant AT ALL: any lookup would fail loudly.
    tenant = SimpleNamespace(root=tmp_path, job_requests=SimpleNamespace(list=lambda status="": []),
                             sessions=SimpleNamespace(create=fake_create),
                             broker=SimpleNamespace(redact_json=lambda v: v), proxy=SimpleNamespace(enabled=False))
    j = _jobs_with(tmp_path)
    j.tenant = tenant
    parent = {"id": "p", "session": "sess-p", "status": "running", "budget": {}, "used": {}, "resumes": 1,
              "delivered": [], "delivered_requests": [], "started": time.time(), "contract": [], "credentials": [],
              "workspace": "/w", "role": "builder", "goal": "g", "scope": ["ReadFile", "Grep"],
              "isolation": "bwrap-model-only", "parent_job_id": "root-job"}
    j.items["p"] = parent
    await j._resume_drive(parent, [], [])
    assert created["tool_scope"] == ["ReadFile", "Grep"]  # pinned at start, not re-resolved
    assert parent["session"] == "sess-new" and parent["session_history"] == ["sess-p"]
    # a fresh resume worker keeps the mission's jail and is stamped as the same delegated job (host-set metadata)
    assert fake.record.isolation == "bwrap-model-only"
    assert fake.record.job == "p" and fake.record.delegated is True


@requires_runtime
def test_scenario_l_sequential_delegation_is_bounded(client, workspace, monkeypatch):
    """Adversarial: a mission that proposes again after each accepted child completes is still bounded - the
    mission-wide delegation cap refuses the next proposal, the refusal reaches the worker, nothing is stored."""
    from mock_provider import MockProvider

    monkeypatch.setattr(jobs, "MAX_DELEGATIONS_PER_MISSION", 1)
    replies = [
        {"tool_calls": [{"name": "create_role", "arguments": {"name": "builder", "description": "d", "tools": ["ReadFile", "Grep", "recommend_job"]}}]},
        {"text": "done"},
        {"tool_calls": [{"name": "recommend_job", "arguments": {"goal": "child one", "role": "explorer", "reason": "r"}}]},
        {"text": "proposed"},
        {"text": "child one done"},
        {"tool_calls": [{"name": "recommend_job", "arguments": {"goal": "child two", "role": "explorer", "reason": "r"}}]},
        {"text": "final: cap reached"},
    ]
    with MockProvider(replies) as mock:
        client.put("/api/settings", json={"base_url": mock.base_url, "openrouter_api_key": "sk-test-mock", "model": "mock/model", "dynamic_tools": True})
        session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
        _, results = _turn_results(client, workspace, None, session_id, "make a role")
        assert list(results.values())[0]["is_error"] is False
        parent = client.post("/api/jobs", json={"goal": "keep delegating", "workspace": str(workspace), "role": "builder", "source_session": session_id}).json()
        _wait_parked(client, parent["id"])
        req = client.get("/api/job-requests").json()["requests"][0]
        child = client.post(f"/api/job-requests/{req['id']}/accept").json()["job"]
        _wait_terminal(client, child["id"])
        done = _wait_terminal(client, parent["id"], timeout=40)
    assert done["status"] == "finished", done
    assert done["resumes"] == 1
    assert len(client.get("/api/job-requests").json()["requests"]) == 1  # the second proposal was refused, nothing stored
    page = client.get(f"/api/sessions/{done['session']}/events").json()
    events = page.get("events", page) if isinstance(page, dict) else page
    tool_results = [e for e in events if e.get("type") == "ToolResult"]
    assert any("delegation cap" in str(e.get("payload", {}).get("return_value", "")) for e in tool_results)


@requires_runtime
def test_scenario_m_delegated_code_running_child_needs_the_jail_or_the_ack(client, workspace, script):
    """A delegated child whose role runs code: a plain accept is REFUSED (its scope ceiling means nothing against
    its own code - unjailed it reads the store as the owner and fetches the owner token). The failed start leaves
    the request pending; the owner's explicit accept-time choices (jail, or the named ack) are the only way through."""
    session_id = _proposer_role(client, workspace, script)
    script([tool_call("j", "recommend_job", {"goal": "build the thing", "role": "developer", "reason": "code needed"}), text("proposed")])
    parent = client.post("/api/jobs", json={"goal": "delegate code work", "workspace": str(workspace), "role": "builder", "source_session": session_id}).json()
    _wait_parked(client, parent["id"])
    req = client.get("/api/job-requests").json()["requests"][0]
    refused = client.post(f"/api/job-requests/{req['id']}/accept")
    assert refused.status_code == 400, refused.text
    assert "accept_unprotected_credentials" in refused.text and "scope ceiling" in refused.text
    assert client.get("/api/job-requests").json()["requests"][0]["status"] == "pending"  # failed start: still the owner's to decide
    script([text("done")])
    accepted = client.post(f"/api/job-requests/{req['id']}/accept", json={"isolation": "bwrap-model-only"})
    assert accepted.status_code == 200, accepted.text
    child = accepted.json()["job"]
    done = _wait_terminal(client, child["id"])
    assert done["status"] == "finished", done
    assert done["isolation"] == "bwrap-model-only"
    assert done["accepted_with"] == {"isolation": "bwrap-model-only", "accept_unprotected_credentials": False}


@requires_runtime
def test_delegated_code_guard_at_the_start_boundary(client, workspace, script):
    """The same guard at start(): parent_job_id + code-running role refuses without the jail or the ack; an
    owner-started job (no parent_job_id) with the same role is untouched - the owner's own delegate by design."""
    script([text("done")])
    refused = client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), "role": "developer", "parent_job_id": "job-x"})
    assert refused.status_code == 400 and "scope ceiling" in refused.text, refused.text
    ack = client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), "role": "developer", "parent_job_id": "job-x", "accept_unprotected_credentials": True})
    assert ack.status_code == 200, ack.text
    assert _wait_terminal(client, ack.json()["id"])["status"] == "finished"
    own = client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), "role": "developer"})
    assert own.status_code == 200, own.text
    assert _wait_terminal(client, own.json()["id"])["status"] == "finished"


@requires_runtime
def test_standalone_child_stays_delegated_for_the_guard(client, workspace, script):
    """Lineage cannot be erased to bypass the jail guard: the parent is CANCELLED before accept, so the child is
    standalone - but the stored record's parent_job_id still drives start(), so a code-running standalone child
    still needs the jail or the explicit ack. The delegation marker is on the session record either way."""
    session_id = _proposer_role(client, workspace, script)
    script([tool_call("j", "recommend_job", {"goal": "build the thing", "role": "developer", "reason": "code needed"}), text("proposed")])
    parent = client.post("/api/jobs", json={"goal": "delegate code work", "workspace": str(workspace), "role": "builder", "source_session": session_id}).json()
    _wait_parked(client, parent["id"])
    client.post(f"/api/jobs/{parent['id']}/cancel")
    assert _wait_terminal(client, parent["id"])["status"] == "stopped"
    req = client.get("/api/job-requests").json()["requests"][0]
    refused = client.post(f"/api/job-requests/{req['id']}/accept")
    assert refused.status_code == 400 and "scope ceiling" in refused.text  # still delegated: no bypass by outliving the parent
    script([text("done")])
    accepted = client.post(f"/api/job-requests/{req['id']}/accept", json={"accept_unprotected_credentials": True})
    assert accepted.status_code == 200, accepted.text
    child = accepted.json()["job"]
    assert child["standalone"] and child["parent_job_id"] == parent["id"]  # marked, lineage kept
    done = _wait_terminal(client, child["id"])
    assert done["status"] == "finished", done
    session = client.get(f"/api/sessions/{done['session']}").json()
    assert session.get("job") == child["id"] and session.get("delegated") is True
