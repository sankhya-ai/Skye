"""Coding flows end to end: worktrees, candidate agents, host checks, repair, judge, apply, rollback."""

from __future__ import annotations

import json
import subprocess
import time

import pytest
from conftest import of_type, requires_runtime, run_turn, text, tool_call

from tura_code import llm
from tura_code.flows import judge

pytestmark = requires_runtime

BUGGY = "def add(a, b):\n    return a - b\n"
TEST = "import unittest\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
CHECK = "python3 -m unittest -q test_calc"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(workspace):
    (workspace / "calc.py").write_text(BUGGY)
    (workspace / "test_calc.py").write_text(TEST)
    _git(workspace, "init", "-q")
    _git(workspace, "add", ".")
    _git(workspace, "-c", "user.email=o@o", "-c", "user.name=o", "commit", "-qm", "init")
    return workspace


def _scripts(tmp_path, monkeypatch, scripts: dict) -> None:
    path = tmp_path / f"flow-script-{time.monotonic_ns()}.json"
    path.write_text(json.dumps(scripts))
    monkeypatch.setenv("TURA_CODE_SCRIPTED_ECHO", str(path))


def _judge_picks(monkeypatch, winner: str | None) -> list:
    """A judge that reads the real evidence and ranks by it (or answers garbage)."""
    calls = []

    async def fake(messages, **kw):
        calls.append(kw["purpose"])
        if winner is None:
            return "I like candidate 2 best!"
        evidence = json.loads(messages[1]["content"].split("Candidate evidence:\n", 1)[1])["candidates"]
        ranking = sorted(evidence, key=lambda c: c["candidate_id"] != winner)
        return json.dumps(
            {
                "schema_version": judge.JUDGMENT_SCHEMA,
                "winner": winner,
                "summary": "Both pass; the winner is the smaller, clearer change.",
                "ranking": [
                    {"candidate_id": c["candidate_id"], "diff_sha256": c["diff_sha256"], "rank": i + 1, "verdict": "pass", "score": 0.9 - i * 0.1, "acceptance_coverage": ["add returns the sum"], "strengths": ["tests pass"], "risks": ["none found"]}
                    for i, c in enumerate(ranking)
                ],
            }
        )

    monkeypatch.setattr(llm, "complete", fake)
    return calls


def _wait(client, flow_id, statuses, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        flow = client.get(f"/api/flows/{flow_id}").json()
        if flow["status"] in statuses and not flow.get("running"):
            return flow
        time.sleep(0.2)
    raise AssertionError(f"flow stuck at {flow['status']}: {json.dumps(flow)[:2000]}")


def _start_flow(client, repo, candidates=2):
    # More than one candidate is the owner's choice (Settings), never the agent's.
    client.put("/api/settings", json={"flow_max_candidates": candidates})
    session = client.post("/api/sessions", json={"workspace": str(repo)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{session}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "fix add() with a coding flow")
    results = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(events, "ToolResult")}
    assert not results["create"]["is_error"], results["create"]
    assert json.loads(results["create"]["output"])["flow_id"] == "flow1"
    assert json.loads(results["start"]["output"])["status"] == "started"
    return session


def _main(candidates=2):
    return [
        tool_call("create", "coding_flow", {"operation": "create", "task": "Make add() return the sum of its arguments.", "acceptance": ["add(2, 3) == 5"], "verification_commands": [CHECK], "candidate_count": candidates}),
        tool_call("start", "coding_flow", {"operation": "start", "flow_id": "flow1"}),
        text("Started flow1; I will report when a verified patch is ready."),
    ]


def test_candidates_are_repaired_judged_applied_and_rolled_back(client, repo, tmp_path, monkeypatch):
    _scripts(
        tmp_path,
        monkeypatch,
        {
            "main": _main(),
            # Wrong first, then repaired from the host's exact test output.
            "candidate_1": [
                tool_call("c1a", "StrReplaceFile", {"path": "calc.py", "edit": {"old": "a - b", "new": "a * b"}}),
                text("Changed the operator."),
                tool_call("c1b", "StrReplaceFile", {"path": "calc.py", "edit": {"old": "a * b", "new": "a + b"}}),
                text("Fixed after the failing test."),
            ],
            "candidate_2": [tool_call("c2", "WriteFile", {"path": "calc.py", "content": "def add(a, b):\n    return b + a\n"}), text("Rewrote add.")],
        },
    )
    judged = _judge_picks(monkeypatch, "candidate_2")
    session = _start_flow(client, repo)
    flow = _wait(client, "flow1", {"ready_to_apply", "failed", "needs_review"})
    assert flow["status"] == "ready_to_apply", flow
    by_id = {c["id"]: c for c in flow["candidates"]}
    assert by_id["candidate_1"]["attempts"] == 2 and by_id["candidate_1"]["status"] == "verified"
    assert by_id["candidate_2"]["attempts"] == 1 and by_id["candidate_2"]["status"] == "verified"
    assert judged == ["judge"] and flow["selected"] == "candidate_2" and flow["judgment"]["winner"] == "candidate_2"
    # Nothing touched the owner's checkout yet.
    assert (repo / "calc.py").read_text() == BUGGY and _git(repo, "status", "--porcelain") == ""

    review = client.get("/api/flows/flow1/review").json()
    assert review["diff"]["changed_paths"] == ["calc.py"] and "+    return b + a" in review["diff"]["patch"]
    assert client.post("/api/flows/flow1/apply", json={"review_sha256": "0" * 64}).status_code == 409
    applied = client.post("/api/flows/flow1/apply", json={"review_sha256": review["diff"]["sha256"]}).json()
    assert applied["status"] == "applied"
    assert (repo / "calc.py").read_text() == "def add(a, b):\n    return b + a\n"
    assert subprocess.run(["python3", "-m", "unittest", "-q", "test_calc"], cwd=repo, capture_output=True).returncode == 0
    assert _git(repo, "log", "--merges", "--format=%s") == "Apply root coding flow flow1"
    assert _git(repo, "worktree", "list").count("\n") == 0  # candidates cleaned up
    assert [s for s in client.get("/api/sessions").json()["sessions"]] and all(s["kind"] == "session" for s in client.get("/api/sessions").json()["sessions"])

    rolled = client.post("/api/flows/flow1/rollback", json={"applied_commit": applied["apply"]["applied_commit"]}).json()
    assert rolled["status"] == "rolled_back" and (repo / "calc.py").read_text() == BUGGY

    # The owner's session heard about every step.
    with client.websocket_connect(f"/api/sessions/{session}/ws") as ws:
        history = ws.receive_json()["history"]
    statuses = [e["payload"]["status"] for e in history if e["type"] == "FlowUpdated"]
    assert statuses[0] == "preparing" and "ready_to_apply" in statuses and statuses[-1] == "rolled_back"


def test_no_verified_candidate_fails_and_cancel_cleans_up(client, repo, tmp_path, monkeypatch):
    wrong = [tool_call(f"w{i}", "WriteFile", {"path": "calc.py", "content": f"def add(a, b):\n    return a * b + {i}\n"}) for i in range(3)]
    _scripts(tmp_path, monkeypatch, {"main": _main(candidates=1), "candidate_1": [wrong[0], text("try"), wrong[1], text("try"), wrong[2], text("try")]})
    _start_flow(client, repo, candidates=1)
    flow = _wait(client, "flow1", {"failed", "ready_to_apply"})
    assert flow["status"] == "failed" and flow["candidates"][0]["attempts"] == 3
    assert "AssertionError" in flow["candidates"][0]["verification"][0]["output_tail"]
    assert client.post("/api/flows/flow1/apply", json={"review_sha256": "x"}).status_code == 409
    worktree_count = _git(repo, "worktree", "list").count("\n")
    assert worktree_count == 1
    client.post("/api/flows/flow1/cancel")
    assert _git(repo, "worktree", "list").count("\n") == 0
    assert "root/flow1" not in _git(repo, "branch", "--list")


def test_an_undecided_judge_leaves_the_choice_to_the_owner(client, repo, tmp_path, monkeypatch):
    fix = lambda i: [tool_call(f"f{i}", "WriteFile", {"path": "calc.py", "content": f"def add(a, b):\n    total = a + b  # {i}\n    return total\n"}), text("done")]  # noqa: E731
    _scripts(tmp_path, monkeypatch, {"main": _main(), "candidate_1": fix(1), "candidate_2": fix(2)})
    _judge_picks(monkeypatch, None)
    _start_flow(client, repo)
    flow = _wait(client, "flow1", {"needs_review", "ready_to_apply", "failed"})
    assert flow["status"] == "needs_review" and flow["controls"]["can_select"] and "contract" in flow["judge_error"] or "JSON" in flow["judge_error"]
    selected = client.post("/api/flows/flow1/select", json={"candidate_id": "candidate_1"}).json()
    assert selected["status"] == "ready_to_apply" and selected["selected"] == "candidate_1"
    # Uncommitted edits in the checkout pause the apply instead of mixing changes.
    (repo / "test_calc.py").write_text(TEST + "# owner's own work in progress\n")
    sha = client.get("/api/flows/flow1/review").json()["diff"]["sha256"]
    refused = client.post("/api/flows/flow1/apply", json={"review_sha256": sha})
    assert refused.status_code == 409 and "uncommitted" in refused.json()["detail"]


def test_flows_refuse_unsafe_checks_and_plain_folders(client, repo, workspace, tmp_path, monkeypatch):
    _scripts(
        tmp_path,
        monkeypatch,
        {"main": [tool_call("bad", "coding_flow", {"operation": "create", "task": "x", "verification_commands": ["rm -rf build && pytest"]}), text("ok")]},
    )
    session = client.post("/api/sessions", json={"workspace": str(repo)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{session}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "try a flow")
    result = of_type(events, "ToolResult")[0]["payload"]["return_value"]
    assert result["is_error"] and "verification commands must only read" in result["message"]
    plain = tmp_path / "plain"
    plain.mkdir()
    service = client.app.state.root.server.tenant().flows
    import asyncio

    with pytest.raises(Exception, match="Git repository"):
        asyncio.run(service.create(plain, "x", owner_session=session))


def test_the_agent_gets_one_candidate_unless_the_owner_raised_the_limit(client, repo, tmp_path, monkeypatch):
    _scripts(tmp_path, monkeypatch, {"main": [tool_call("create", "coding_flow", {"operation": "create", "task": "Make add() add.", "verification_commands": [CHECK], "candidate_count": 3}), text("ok")]})
    session = client.post("/api/sessions", json={"workspace": str(repo)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{session}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "fix add()")
    created = json.loads(next(e for e in of_type(events, "ToolResult") if e["payload"]["tool_call_id"] == "create")["payload"]["return_value"]["output"])
    assert created["candidates"] == 1
