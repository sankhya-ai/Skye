"""The Hermes-style learning loop, from a finished turn to a skill the agent loads."""

from __future__ import annotations

import json

import pytest
from conftest import of_type, requires_runtime, run_turn, text, tool_call

from tura_code import llm
from tura_code.learning import outcomes, review
from tura_code.learning.skills import SkillLibrary

SKILL = {
    "name": "Verify Python changes",
    "trigger": "After editing Python code in a project that uses unittest",
    "lesson": "Run the touched test modules with python3 -m unittest before reporting success, and read failures before editing again.",
    "steps": ["Find the test module next to the changed code", "Run python3 -m unittest -q <module>"],
    "verification": ["The unittest run reports OK"],
    "pitfalls": ["Do not claim success from a syntax check alone"],
}
OVERFIT = {"name": "calc task_12", "trigger": "when the prompt contains calc", "lesson": "hard-code the answer 5"}
MEMORY = {"kind": "project_fact", "content": "Tests in this project run with python3 -m unittest."}


def _answer(**kw) -> str:
    return json.dumps({"should_save": True, "summary": "Verified a Python change with unittest.", "skill_candidates": [SKILL, OVERFIT], "memory_candidates": [MEMORY], "rejected_overfit": [], **kw})


@pytest.fixture
def reviewer(tmp_path, monkeypatch):
    def install(answers: list[str]) -> None:
        path = tmp_path / f"llm-{len(answers)}-{id(answers)}.json"
        path.write_text(json.dumps({"review": answers}))
        monkeypatch.setenv(llm.SCRIPTED_LLM_ENV, str(path))

    return install


def _work_turn() -> list[str]:
    return [
        tool_call("r", "ReadFile", {"path": "README.md"}),
        tool_call("w1", "WriteFile", {"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"}),
        tool_call("w2", "WriteFile", {"path": "test_calc.py", "content": "import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"}),
        tool_call("t", "Shell", {"command": "python3 -m unittest -q test_calc"}),
        text("Added calc with a passing test."),
    ]


def _until(ws, kind: str, limit: int = 200) -> dict:
    for _ in range(limit):
        message = ws.receive_json()
        if message["type"] == "event" and message["event"]["type"] == kind:
            return message["event"]
    raise AssertionError(f"no {kind}")


@requires_runtime
def test_a_reviewed_skill_reaches_the_agent_and_a_memory_reaches_dhee(client, workspace, script, reviewer, home):
    reviewer([_answer()])
    script(_work_turn() + [text("Using what I learned.")])
    session = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    client.patch(f"/api/sessions/{session}", json={"permission_mode": "accept_edits"})
    with client.websocket_connect(f"/api/sessions/{session}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "add a calc module with a test")
        assert of_type(events, "TurnFinished")[0]["payload"]["status"] == "finished"
        reviewed = _until(ws, "LearningReviewed")["payload"]
    assert reviewed["ok"] and reviewed["mode"] == "review" and reviewed["promoted"] == []
    assert sorted(c["kind"] for c in reviewed["candidates"]) == ["memory", "skill"]
    assert reviewed["rejected_overfit"] == 1  # the hard-coded answer never became a candidate

    overview = client.get("/api/learning").json()
    skill = next(c for c in overview["candidates"] if c["kind"] == "skill")
    memory = next(c for c in overview["candidates"] if c["kind"] == "memory")
    assert skill["title"] == "verify-python-changes" and skill["status"] == "candidate"
    assert overview["skills"] == []

    promoted = client.post(f"/api/learning/{skill['id']}/promote").json()
    assert promoted["status"] == "promoted"
    skill_file = home / "skills" / "verify-python-changes" / "SKILL.md"
    written = skill_file.read_text()
    assert written.startswith("---\nname: verify-python-changes\n") and "python3 -m unittest -q <module>" in written
    assert client.post(f"/api/learning/{skill['id']}/promote").status_code == 409

    # The next prompt restarts the runtime with the skill loaded.
    with client.websocket_connect(f"/api/sessions/{session}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "carry on")
    ready = [e["payload"] for e in events if e["type"] == "RuntimeStatus" and e["payload"].get("state") == "ready"]
    assert ready, "the runtime should restart after a skill is promoted"
    assert "skill:verify-python-changes" in [c["name"] for c in ready[-1]["slash_commands"]]

    client.post(f"/api/learning/{memory['id']}/promote")
    recalled = client.get("/api/memory", params={"path": str(workspace), "q": "how do tests run in this project unittest"}).json()
    assert any("unittest" in item["memory"] for item in recalled["items"])


@requires_runtime
def test_rejected_lessons_stay_rejected_and_auto_mode_promotes(client, workspace, script, reviewer, home):
    preference = json.dumps({"should_save": True, "summary": "Owner preference.", "memory_candidates": [{"kind": "user_preference", "content": "The owner wants ruff run before finishing any Python change."}]})
    reviewer([_answer(), _answer(), preference])
    script(_work_turn() + _work_turn() + [text("Noted.")])
    session = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    client.patch(f"/api/sessions/{session}", json={"permission_mode": "yolo"})
    with client.websocket_connect(f"/api/sessions/{session}/ws") as ws:
        ws.receive_json()
        run_turn(ws, "add a calc module with a test")
        first = _until(ws, "LearningReviewed")["payload"]
        for candidate in first["candidates"]:
            assert client.post(f"/api/learning/{candidate['id']}/reject").json()["status"] == "rejected"
        run_turn(ws, "do it again")
        second = _until(ws, "LearningReviewed")["payload"]
        assert second["candidates"] == []  # the same lessons are not proposed twice

        client.put("/api/settings", json={"learning_mode": "auto"})
        run_turn(ws, "From now on, always run ruff before you finish.")
        third = _until(ws, "LearningReviewed")["payload"]
    assert len(third["promoted"]) == 1 and third["mode"] == "auto"
    remembered = client.get("/api/memory", params={"path": str(workspace), "q": "ruff before finishing python change"}).json()
    assert any("ruff" in item["memory"] and item["scope"] == "owner" for item in remembered["items"])
    assert not (home / "skills").exists() or not list((home / "skills").glob("*/SKILL.md"))


@requires_runtime
def test_a_quick_answer_is_not_reviewed_and_a_bad_review_is_harmless(client, workspace, script, reviewer):
    reviewer(["not json at all"])
    script([text("Paris."), *_work_turn()])
    session = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    client.patch(f"/api/sessions/{session}", json={"permission_mode": "yolo"})
    with client.websocket_connect(f"/api/sessions/{session}/ws") as ws:
        ws.receive_json()
        quick = run_turn(ws, "what is the capital of France?")
        assert of_type(quick, "LearningReviewed") == []
        run_turn(ws, "add a calc module with a test")
        failed = _until(ws, "LearningReviewed")["payload"]
    assert failed["ok"] is False and failed["candidates"] == []
    assert client.get("/api/learning").json()["candidates"] == []


def test_review_parsing_filters_and_redacts():
    messages = review.build_messages(
        {"prompt": "use key sk-or-v1-abcdefghijklmnop to deploy", "reply": "", "status": "finished", "trace": [{"tool": "Shell", "arguments": '{"command": "export OPENROUTER_API_KEY=sk-or-v1-zzzzzzzzzzzzzzzz"}', "status": "completed", "result": ""}]},
        [{"name": "old-skill", "description": "does a thing"}],
    )
    payload = messages[1]["content"]
    assert "sk-or-v1" not in payload and "[redacted]" in payload and "old-skill" in payload
    parsed = review.parse("```json\n" + _answer(memory_candidates=[MEMORY, {"kind": "gossip", "content": "x"}, {"kind": "constraint", "content": "Never edit task_42 files"}]) + "\n```")
    assert [s["name"] for s in parsed["skills"]] == ["verify-python-changes"]
    assert [m["content"] for m in parsed["memories"]] == [MEMORY["content"]]
    assert len(parsed["rejected_overfit"]) == 2
    empty = review.parse(json.dumps({"should_save": True, "skill_candidates": [], "memory_candidates": []}))
    assert empty["should_save"] is False
    with pytest.raises(ValueError):
        review.parse("no json here")


def test_skill_updates_keep_history_and_roll_back(tmp_path):
    library = SkillLibrary(tmp_path)
    skill = {**SKILL, "name": "verify-python-changes"}
    assert library.write(skill, source="s1")["updated"] is False
    assert library.write({**skill, "lesson": "Run pytest instead."}, source="s2")["updated"] is True
    assert "pytest instead" in library.read("verify-python-changes")
    assert library.list()[0]["versions"] == 1
    assert library.rollback("verify-python-changes")
    assert "unittest before reporting" in library.read("verify-python-changes")
    with pytest.raises(ValueError):
        library.path("../escape")
    assert library.remove("verify-python-changes") and library.list() == []


def test_should_review_picks_real_work_trouble_and_preferences():
    base = dict(status="finished", files_touched=0, tool_count=0, errors=0, rejections=0)
    assert not review.should_review(prompt="what is 2+2", **base)
    assert review.should_review(prompt="refactor", **{**base, "files_touched": 2, "tool_count": 5})
    assert review.should_review(prompt="fix", **{**base, "errors": 2})
    assert review.should_review(prompt="fix", **{**base, "status": "max_steps_reached"})
    assert review.should_review(prompt="From now on use tabs", **base)
    assert not review.should_review(prompt="/compact", **{**base, "errors": 5})


def test_lenient_json_keeps_what_a_cut_off_review_managed_to_say():
    whole = _answer()
    # Cut off mid-way through the second skill: the first skill and the flag survive.
    cut = whole[: whole.index('"calc task_12"') + 8]
    kept = llm.loads_lenient(cut)
    assert kept["should_save"] is True and kept["skill_candidates"][0]["name"] == "Verify Python changes"
    assert review.parse(cut)["skills"][0]["name"] == "verify-python-changes"
    # Lossless repairs: a fence, a trailing comma, a raw newline inside a string.
    assert llm.loads_lenient('```json\n{"a": [1, 2,], "b": "x\ny"}\n```') == {"a": [1, 2], "b": "x\ny"}
    # An unescaped quote breaks the tail; everything before it is kept.
    broken = '{"should_save": true, "summary": "ok", "memory_candidates": [{"kind": "project_fact", "content": "uses make"}, {"content": "he said "hi" twice"}]}'
    assert llm.loads_lenient(broken)["memory_candidates"] == [{"kind": "project_fact", "content": "uses make"}]
    with pytest.raises(ValueError):
        llm.loads_lenient("no json here")


@requires_runtime
def test_auto_mode_promotes_nothing_from_edits_that_were_never_run(client, workspace, script, reviewer, home):
    reviewer([_answer()])
    script([
        tool_call("w", "WriteFile", {"path": "calc.py", "content": "def add(a, b):\n    return a - b\n"}),
        tool_call("d", "Shell", {"command": "git diff"}),
        tool_call("r", "ReadFile", {"path": "calc.py"}),
        tool_call("g", "Grep", {"pattern": "def add"}),  # four calls: enough work to be reviewed
        text("Fixed add."),
    ])
    client.put("/api/settings", json={"learning_mode": "auto"})
    session = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    client.patch(f"/api/sessions/{session}", json={"permission_mode": "yolo"})
    with client.websocket_connect(f"/api/sessions/{session}/ws") as ws:
        ws.receive_json()
        run_turn(ws, "fix add in calc.py")
        reviewed = _until(ws, "LearningReviewed")["payload"]
    assert reviewed["ok"] and reviewed["verified"] is False
    assert reviewed["candidates"] and reviewed["promoted"] == []  # waiting for the owner, not kept
    assert all(c["status"] == "candidate" for c in client.get("/api/learning").json()["candidates"])


@requires_runtime
def test_auto_mode_keeps_a_skill_only_when_a_second_session_proposes_it(client, workspace, script, reviewer, home):
    reviewer([_answer(), _answer()])
    script(_work_turn() + _work_turn())
    client.put("/api/settings", json={"learning_mode": "auto"})
    promoted_kinds = []
    for prompt in ("add a calc module with a test", "add it again in a fresh session"):
        session = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
        client.patch(f"/api/sessions/{session}", json={"permission_mode": "yolo"})
        with client.websocket_connect(f"/api/sessions/{session}/ws") as ws:
            ws.receive_json()
            run_turn(ws, prompt)
            reviewed = _until(ws, "LearningReviewed")["payload"]
        overview = {c["id"]: c for c in client.get("/api/learning").json()["candidates"]}
        promoted_kinds.append(sorted(overview[i]["kind"] for i in reviewed["promoted"]))
        if prompt.startswith("add a calc"):
            assert not (home / "skills" / "verify-python-changes").exists()  # one sighting: an anecdote
    assert promoted_kinds == [["memory"], ["skill"]]
    assert (home / "skills" / "verify-python-changes" / "SKILL.md").exists()


def test_skill_outcomes_compare_a_version_with_what_it_replaced(tmp_path):
    ledger = outcomes.OutcomeLedger(tmp_path / "outcomes.json")
    for _ in range(outcomes.MIN_BASELINE):
        ledger.record({}, True)
    for _ in range(outcomes.MIN_USES - 1):
        ledger.record({"s": "v1"}, False)
    assert not ledger.assess("s", "v1")["underperforming"]  # too few uses to judge
    ledger.record({"s": "v1"}, False)
    verdict = ledger.assess("s", "v1")
    assert verdict["underperforming"] and verdict["against"] == "baseline" and verdict["rate"] == 0

    # A new version is held to the one it replaced once that has evidence, not the baseline.
    for _ in range(outcomes.MIN_USES):
        ledger.record({"s": "v2"}, True)
    assert ledger.assess("s", "v2")["against"] == "version v1" and not ledger.assess("s", "v2")["underperforming"]
    # One unlucky turn in five is not "confidently worse" than a perfect baseline.
    ledger.record({}, True)
    for clean in (True, True, True, True, False):
        ledger.record({"t": "w1"}, clean)
    assert not ledger.assess("t", "w1")["underperforming"]
    assert outcomes.wilson(0, 5)[1] < outcomes.wilson(10, 10)[0] < outcomes.wilson(2, 5)[1]


def test_retired_skills_are_not_proposed_again(tmp_path):
    from tura_code.learning.store import CandidateStore

    store = CandidateStore(tmp_path / "learning.json")
    store.retire("verify-python-changes")
    item = {"kind": "skill", "title": "verify-python-changes", "body": {}, "fingerprint": "f", "session_id": "s"}
    assert store.add([item]) == ([], [])
    assert store.add([{**item, "title": "other", "fingerprint": "g"}])[0]


def _skill_turn(skill_md) -> list[str]:
    return [
        tool_call("r", "ReadFile", {"path": str(skill_md)}),
        tool_call("w", "WriteFile", {"path": "calc.py", "content": "def add(a, b):\n    return a - b\n"}),
        text("Done."),  # the edit is never checked: not a clean turn
    ]


@requires_runtime
def test_a_skill_version_that_makes_turns_worse_is_rolled_back(client, workspace, script, home):
    library = SkillLibrary(home / "skills")
    good = {**SKILL, "name": "verify-python-changes"}
    library.write(good, source="s1")
    ledger = outcomes.OutcomeLedger(home / "skill_outcomes.json")
    v1 = outcomes.version_of(library.read("verify-python-changes"))
    for _ in range(outcomes.MIN_USES):
        ledger.record({"verify-python-changes": v1}, True)
    library.write({**good, "lesson": "Skip the tests; the change is obviously right."}, source="s2")
    skill_md = library.path("verify-python-changes")

    script(sum((_skill_turn(skill_md) for _ in range(outcomes.MIN_USES)), []))
    client.put("/api/settings", json={"learning_mode": "auto"})
    session = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    client.patch(f"/api/sessions/{session}", json={"permission_mode": "yolo"})
    with client.websocket_connect(f"/api/sessions/{session}/ws") as ws:
        ws.receive_json()
        for i in range(outcomes.MIN_USES):
            run_turn(ws, f"change add, take {i}")
        outcome = _until(ws, "SkillOutcome")["payload"]
    assert outcome["action"] == "rolled_back" and outcome["against"] == f"version {v1}" and outcome["uses"] == outcomes.MIN_USES
    assert "unittest before reporting" in library.read("verify-python-changes")  # the version it replaced is kept
    skills = client.get("/api/learning").json()["skills"]
    assert skills[0]["outcome"]["version"] == v1 and not skills[0]["outcome"]["underperforming"]
