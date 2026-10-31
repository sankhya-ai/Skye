"""Delegated memory scope proven through REAL job execution and a FRESH RESUME - not direct view tests.

The policy reads ONLY the host-stamped SessionRecord.delegated. These tests run whole jobs through
the HTTP app with real Dhee: a delegated child whose role lists the memory tools (so a refusal is
the POLICY, not a missing tool), and a depth-2 chain whose delegated child parks and resumes as a
fresh worker (jobs.py re-stamps delegated on every fresh resume), retrying owner-scope memory calls
from the resumed session. Project memory stays shared by design; nothing here is per-job secrecy.

Scripting note: each session's runtime plays the installed script from position 0, and a parked
job's resume fires the moment its child finishes - so entries after an accept must be safe when a
DIFFERENT session replays them from the top (a top-level replay of an owner-scope call succeeding
is expected and asserted nowhere; the delegated session's own tool results are the evidence).
"""
from __future__ import annotations

import json
import time

from conftest import of_type, requires_runtime, run_turn, text, tool_call

OWNER_FACT = "The owner prefers purple dashboards and tabs for indentation."
PLANTED = "worker plants an owner fact: the owner loves Comic Sans"


def _wait_terminal(client, job_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        item = client.get(f"/api/jobs/{job_id}").json()
        if item["status"] not in ("running", "waiting"):
            return item
        time.sleep(0.2)
    raise AssertionError("job did not settle")


def _wait_parked(client, job_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        item = client.get(f"/api/jobs/{job_id}").json()
        if item["status"] == "waiting":
            return item
        time.sleep(0.2)
    raise AssertionError("job did not park")


def _session_turn(client, workspace, script, replies, prompt):
    script(replies)
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, prompt)
    results = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(events, "ToolResult")}
    return session_id, events, results


def _tool_events(client, session_id):
    page = client.get(f"/api/sessions/{session_id}/events").json()
    events = page.get("events", page) if isinstance(page, dict) else page
    results = {}
    prompts = []
    for e in events:
        if e.get("type") == "ToolResult":
            results[e["payload"]["tool_call_id"]] = e["payload"]["return_value"]
        elif e.get("type") == "UserPrompt":
            prompts.append(e["payload"])
    return results, prompts


def _roles(client, workspace, script, extra):
    client.put("/api/settings", json={"dynamic_tools": True})
    replies = [
        tool_call("r1", "create_role", {"name": "builder", "description": "Builds and may ask for help.", "tools": ["ReadFile", "Grep", "recommend_job"]}),
        tool_call("r2", "create_role", extra),
        text("roles done"),
    ]
    role_session, _, results = _session_turn(client, workspace, script, replies, "make two roles")
    assert results["r1"]["is_error"] is False, results["r1"]
    assert results["r2"]["is_error"] is False, results["r2"]
    # The owner promotes both roles to the durable catalogue: a job session cannot see another
    # session's private roles, so the parent's recommend_job and the accept path need them durable.
    for name in ("builder", extra["name"]):
        promoted = client.post(f"/api/registry/roles/{name}/promote")
        assert promoted.status_code == 200, promoted.text
    return role_session


@requires_runtime
def test_delegated_child_job_memory_is_project_only_in_execution(client, workspace, script):
    """A delegated child whose role LISTS the memory tools tries owner-scope read/write mid-job: the
    policy (not tool absence) refuses both; its automatic first-turn packet carries no owner fact."""
    role_session = _roles(client, workspace, script, {"name": "mem_worker", "description": "Worker with memory tools.", "tools": ["ReadFile", "memory_recall", "memory_remember"]})
    # Seed the owner's own memory through an unrestricted top-level session.
    _, _, seeded = _session_turn(client, workspace, script, [tool_call("s1", "memory_remember", {"content": OWNER_FACT, "kind": "preference", "scope": "owner"}), text("ok")], "remember my taste")
    assert seeded["s1"]["is_error"] is False, seeded["s1"]

    # Parent proposes a delegated child (goal chosen to be highly similar to the owner fact, so an
    # UNRESTRICTED packet would surface it and its absence proves the restriction).
    script([tool_call("j", "recommend_job", {"goal": "pick dashboard colors and indentation style", "role": "mem_worker", "reason": "taste lookup"}), text("proposed")])
    created = client.post("/api/jobs", json={"goal": "design the settings page", "workspace": str(workspace), "role": "builder", "source_session": role_session})
    assert created.status_code == 200, created.text
    parent = created.json()
    parked = _wait_parked(client, parent["id"])
    request = client.get("/api/job-requests").json()["requests"][0]

    script([
        tool_call("c1", "memory_recall", {"query": "purple dashboards indentation", "scope": "owner"}),
        tool_call("c2", "memory_remember", {"content": PLANTED, "scope": "owner"}),
        tool_call("c3", "memory_recall", {"query": "purple dashboards indentation"}),
        text("child done"),
    ])
    child = client.post(f"/api/job-requests/{request['id']}/accept").json()["job"]
    done_child = _wait_terminal(client, child["id"])
    assert done_child["status"] == "finished", done_child
    _wait_terminal(client, parent["id"])

    results, prompts = _tool_events(client, child["session"])
    # Explicit owner-scope read and write: refused by the delegated policy at one of two layers -
    # the restricted tool DEFINITIONS hide `owner` from the schema enum (validation error), and
    # anything reaching the toolset hits MemoryScopeError. Both name the project-only restriction.
    for call in ("c1", "c2"):
        refused = results[call]
        text_of = refused["output"] + refused["message"]
        assert refused["is_error"] is True and "project" in text_of, results[call]
    # A scope-less recall is coerced to project: works, but never returns the owner fact.
    assert results["c3"]["is_error"] is False and "purple" not in results["c3"]["output"], results["c3"]
    # The automatic first-turn packet of the delegated session carries no owner fact.
    assert prompts, "the job's brief should be a UserPrompt event"
    assert all("purple" not in json.dumps(p.get("memory") or []) for p in prompts), prompts

    # The owner's own view is untouched: a fresh top-level session still reads the owner fact.
    _, _, check = _session_turn(client, workspace, script, [tool_call("v1", "memory_recall", {"query": "purple dashboards", "scope": "owner"}), text("ok")], "what was my taste?")
    assert check["v1"]["is_error"] is False and "purple" in check["v1"]["output"], check["v1"]


@requires_runtime
def test_fresh_resume_of_a_delegated_job_keeps_the_restriction(client, workspace, script):
    """Depth 2: the delegated child proposes its own sub-job, parks, and RESUMES AS A FRESH WORKER
    (jobs.py re-stamps delegated from parent_job_id on every fresh resume). The resumed session's
    owner-scope calls must still be refused, and its packet must still carry no owner fact."""
    role_session = _roles(client, workspace, script, {"name": "mem_delegator", "description": "Worker with memory tools that may ask for help.", "tools": ["ReadFile", "recommend_job", "memory_recall", "memory_remember"]})
    _, _, seeded = _session_turn(client, workspace, script, [tool_call("s1", "memory_remember", {"content": OWNER_FACT, "kind": "preference", "scope": "owner"}), text("ok")], "remember my taste")
    assert seeded["s1"]["is_error"] is False, seeded["s1"]

    script([tool_call("j", "recommend_job", {"goal": "pick dashboard colors and indentation style", "role": "mem_delegator", "reason": "taste lookup"}), text("proposed")])
    created = client.post("/api/jobs", json={"goal": "design the settings page", "workspace": str(workspace), "role": "builder", "source_session": role_session})
    assert created.status_code == 200, created.text
    parent = created.json()
    _wait_parked(client, parent["id"])
    child_req = client.get("/api/job-requests").json()["requests"][0]

    # The child's first turn: proposes the grandchild and parks.
    script([tool_call("g", "recommend_job", {"goal": "look at the readme", "role": "explorer", "reason": "parallel look"}), text("proposing")])
    child = client.post(f"/api/job-requests/{child_req['id']}/accept").json()["job"]
    parked_child = _wait_parked(client, child["id"])
    grand_req = [r for r in client.get("/api/job-requests").json()["requests"]][0]

    # Grandchild turn and the child's FRESH-resume turn share this file (each session replays from
    # position 0). Both are delegated, so the owner-scope entries are policy-refused in BOTH.
    script([
        tool_call("m1", "memory_remember", {"content": PLANTED, "scope": "owner"}),
        tool_call("m2", "memory_recall", {"query": "purple dashboards", "scope": "owner"}),
        text("grandchild looked; child final"),
    ])
    grandchild = client.post(f"/api/job-requests/{grand_req['id']}/accept").json()["job"]
    assert _wait_terminal(client, grandchild["id"])["status"] == "finished"
    done_child = _wait_terminal(client, child["id"])
    assert done_child["status"] == "finished", done_child
    assert done_child["resumes"] == 1 and done_child["delivered"] == [grandchild["id"]], done_child
    _wait_terminal(client, parent["id"])

    # The resume ran as a FRESH worker session...
    assert done_child["session"] != parked_child["session"]
    results, prompts = _tool_events(client, done_child["session"])
    # ...stamped delegated again, so its owner-scope calls are refused by the policy (schema enum
    # or MemoryScopeError - both name the project-only restriction)...
    for call in ("m1", "m2"):
        refused = results[call]
        assert refused["is_error"] is True and "project" in (refused["output"] + refused["message"]), results[call]
    # ...and its resumed-turn memory packet still carries no owner fact.
    assert all("purple" not in json.dumps(p.get("memory") or []) for p in prompts), prompts
