"""The turn digest: a working turn leaves lessons and facts in Dhee, through root's own model."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from conftest import requires_runtime, run_turn, text, tool_call

from tura_code import llm
from tura_code.memory import digest
from tura_code.sessions.record import TurnState
from tura_code.settings import Settings

pytest.importorskip("dhee")

FACT = "Tests in this project run with python3 -m unittest."
WORKED = "Running the one new test module with python3 -m unittest before reporting confirmed the change."
FAILED = "Writing the test after the code hid that add() was never imported."


def _answer(**kw) -> str:
    return json.dumps(
        {
            "summary": "Added a calc module with a passing unittest.",
            "task_type": "feature",
            "what_worked": WORKED,
            "what_failed": FAILED,
            "decisions": ["Kept calc.add pure so it needs no fixtures."],
            "todos": [],
            "facts": [{"kind": "project_fact", "content": FACT, "replaces": ""}],
            **kw,
        }
    )


@pytest.fixture
def model(tmp_path, monkeypatch):
    def install(answers: list[str]) -> None:
        path = tmp_path / f"llm-{id(answers)}.json"
        path.write_text(json.dumps({"digest": answers}))
        monkeypatch.setenv(llm.SCRIPTED_LLM_ENV, str(path))

    return install


def _work_turn() -> list[str]:
    return [
        tool_call("w1", "WriteFile", {"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"}),
        tool_call("w2", "WriteFile", {"path": "test_calc.py", "content": "import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"}),
        tool_call("t", "Shell", {"command": "python3 -m unittest -q test_calc"}),
        text("Added calc with a passing test."),
    ]


def _until(ws, kind: str, limit: int = 300) -> dict:
    for _ in range(limit):
        message = ws.receive_json()
        if message["type"] == "event" and message["event"]["type"] == kind:
            return message["event"]
    raise AssertionError(f"no {kind}")


@requires_runtime
def test_a_working_turn_leaves_lessons_and_facts_for_the_next_session(client, workspace, script, model):
    model([_answer(), _answer()])
    script(_work_turn() + _work_turn())
    client.put("/api/settings", json={"learning_mode": "off"})
    session = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    client.patch(f"/api/sessions/{session}", json={"permission_mode": "yolo"})
    with client.websocket_connect(f"/api/sessions/{session}/ws") as ws:
        ws.receive_json()
        run_turn(ws, "add a calc module with a test")
        first = _until(ws, "MemoryCheckpoint")["payload"]
        assert first == {**first, "saved": True, "digest": True, "lessons": True, "facts_saved": 1}
        run_turn(ws, "add it again")
        again = _until(ws, "MemoryCheckpoint")["payload"]
        assert again["digest"] and again["facts_saved"] == 0, "a fact said twice is stored once"

    items = client.get("/api/memory", params={"path": str(workspace), "q": "how do tests run in this project unittest"}).json()["items"]
    assert [item["memory"] for item in items].count(FACT) == 1

    # The next session's first request carries the lessons.
    memory = client.app.state.root.server.tenant().memory
    packet = asyncio.run(memory.compile_packet("add a subtract function with a test", workspace, seen=set(), first_turn=True))
    assert packet is not None and "Lessons from earlier work:" in packet.text
    assert WORKED in packet.text and FAILED in packet.text


@requires_runtime
def test_without_the_model_the_checkpoint_is_plain_and_stores_no_facts(client, workspace, script, model):
    model([])  # every digest call fails
    script(_work_turn())
    client.put("/api/settings", json={"learning_mode": "off"})
    session = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    client.patch(f"/api/sessions/{session}", json={"permission_mode": "yolo"})
    with client.websocket_connect(f"/api/sessions/{session}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "add a calc module with a test")
        assert [e for e in events if e["type"] == "TurnFinished"][0]["payload"]["status"] == "finished"
        result = _until(ws, "MemoryCheckpoint")["payload"]
    assert result["saved"] and not result["digest"] and result["facts_saved"] == 0


# ----- the digest on its own -------------------------------------------------


class FakeMemory:
    def __init__(self, known: list[dict]) -> None:
        self.known = known
        self.stored: list[tuple[str, str, str]] = []
        self.forgotten: list[str] = []
        self.checkpoints: list[dict] = []

    async def recall(self, query, workspace, *, scope="all", limit=6):
        return self.known

    async def checkpoint(self, workspace, *, summary, status="paused", **fields):
        self.checkpoints.append({"summary": summary, "status": status, **fields})
        return {}

    async def forget(self, memory_id, workspace):
        self.forgotten.append(memory_id)
        return True

    async def remember(self, content, workspace, *, scope="project", kind="fact", session_id=""):
        self.stored.append((content, scope, kind))
        return {"id": "new"}


def _turn(*, verified: bool = True) -> TurnState:
    turn = TurnState(id="t", prompt="switch the tests to pytest", files_touched=["pyproject.toml"], tool_count=4)
    turn.text.append("Switched to pytest.")
    if not verified:
        turn.note_edit()
    return turn


def _digest(memory: FakeMemory, turn: TurnState, status: str = "finished") -> dict:
    return asyncio.run(digest.digest_turn(memory, Path("/w"), turn, status, settings=Settings(), api_key=""))


def test_a_fact_that_corrects_a_memory_replaces_it(model):
    known = [{"id": "m1", "memory": "Tests run with python3 -m unittest.", "scope": "project", "score": 0.8}]
    model([_answer(facts=[{"kind": "project_fact", "content": "Tests run with pytest -q.", "replaces": "m1"}, {"kind": "user_preference", "content": "The owner wants ruff run before any Python change is reported done.", "replaces": "not-a-known-id"}])])
    memory = FakeMemory(known)
    result = _digest(memory, _turn())
    assert memory.forgotten == ["m1"] and result["facts_replaced"] == 1
    assert memory.stored == [("Tests run with pytest -q.", "project", "fact"), ("The owner wants ruff run before any Python change is reported done.", "owner", "preference")]
    checkpoint = memory.checkpoints[0]
    assert checkpoint["status"] == "completed" and checkpoint["what_worked"] == WORKED and checkpoint["task_type"] == "feature"
    assert checkpoint["outcome_score"] == 1.0 and checkpoint["summary"].startswith("Added a calc module")


def test_an_unchecked_or_failed_turn_leaves_lessons_but_no_facts(model):
    model([_answer(), _answer()])
    for turn, status in ((_turn(verified=False), "finished"), (_turn(), "error")):
        memory = FakeMemory([])
        result = _digest(memory, turn, status)
        assert result["lessons"] and memory.stored == []
        assert memory.checkpoints[0]["what_failed"] == FAILED


def test_the_digest_drops_what_is_not_a_durable_fact():
    parsed = digest.parse(
        json.dumps(
            {
                "summary": "Done. key sk-or-v1-abcdefghijklmnopqrstuv",
                "task_type": "made-up",
                "facts": [
                    {"kind": "project_fact", "content": "For task_12 hard-code the answer 5."},
                    {"kind": "gossip", "content": "The owner had coffee."},
                    "not an object",
                    {"kind": "constraint", "content": "Every runtime change is recorded in runtime/VENDORED.md."},
                ],
            }
        ),
        set(),
    )
    assert "sk-or" not in parsed["summary"] and parsed["task_type"] == ""
    assert parsed["facts"] == [{"kind": "constraint", "content": "Every runtime change is recorded in runtime/VENDORED.md.", "replaces": ""}]
    with pytest.raises(ValueError):
        digest.parse('{"facts": []}', set())
