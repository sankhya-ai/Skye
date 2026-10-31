"""Session search: finished turns are indexed and found again, by the owner and by the agent."""

from __future__ import annotations

from conftest import of_type, requires_runtime, run_turn, text, tool_call

from tura_code.history.index import HistoryIndex, match_expression, turns_of


def test_match_expression_quotes_words_and_prefixes_the_last():
    assert match_expression('fix the "login" bu') == '"fix" "the" "login" "bu"*'
    assert match_expression("  ** ") == ""


def test_turns_of_reads_a_page_of_events():
    events = [
        {"turn": "t1", "type": "UserPrompt", "payload": {"text": "why is CI red?"}},
        {"turn": "t1", "type": "ContentPart", "payload": {"type": "text", "text": "draft"}},
        {"turn": "t1", "type": "TurnBegin", "payload": {}},
        {"turn": "t1", "type": "ContentPart", "payload": {"type": "text", "text": "The cache key changed."}},
        {"turn": "t1", "type": "TurnFinished", "ts": 5.0, "payload": {"files_touched": ["ci.yml"]}},
        {"turn": "t2", "type": "UserPrompt", "payload": {"text": "unfinished"}},
    ]
    assert turns_of(events) == [{"turn_id": "t1", "finished_at": 5.0, "prompt": "why is CI red?", "reply": "The cache key changed.", "files": ["ci.yml"]}]


@requires_runtime
def test_turns_are_searchable_and_the_agent_finds_an_earlier_session(client, workspace, script):
    def session() -> str:
        return client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]

    script([text("Use `poetry run pytest -k smoke`: the smoke tests need the fixtures plugin.")])
    first = session()
    with client.websocket_connect(f"/api/sessions/{first}/ws") as ws:
        ws.receive_json()
        run_turn(ws, "how do I run the smoke tests?")

    hits = client.get("/api/search", params={"q": "smoke fixt"}).json()["results"]
    assert [h["session_id"] for h in hits] == [first]
    assert "[smoke]" in hits[0]["prompt"] and hits[0]["title"] == "how do I run the smoke tests?"

    script([tool_call("c1", "session_search", {"query": "smoke tests", "this_workspace": True}), text("Found it.")])
    second = session()
    with client.websocket_connect(f"/api/sessions/{second}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "remind me how we ran smoke tests")
    result = of_type(events, "ToolResult")[0]["payload"]["return_value"]
    assert "poetry run pytest -k smoke" in result["output"], result
    assert "remind me" not in result["output"], "the asking session is not its own answer"

    client.delete(f"/api/sessions/{first}")
    assert [h["session_id"] for h in client.get("/api/search", params={"q": "smoke fixtures"}).json()["results"]] == []


def test_older_sessions_are_backfilled_from_their_last_page(tmp_path):
    from tura_code.events import EventLog
    from tura_code.sessions.record import SessionRecord

    sessions = tmp_path / "sessions"
    record = SessionRecord(id="abc123", workspace="/w", title="Old work")
    record.save(sessions / "abc123")
    log = EventLog(sessions / "abc123" / "events.jsonl")
    for seq, (kind, payload) in enumerate(
        [("UserPrompt", {"text": "migrate the database"}), ("ContentPart", {"type": "text", "text": "Ran alembic upgrade head."}), ("TurnFinished", {"files_touched": []})],
        start=1,
    ):
        log.record({"seq": seq, "ts": 1.0, "turn": "t1", "type": kind, "payload": payload})
    log.flush()
    index = HistoryIndex(tmp_path / "history.db", sessions)
    hits = index.search("alembic")
    assert [(h["session_id"], h["title"]) for h in hits] == [("abc123", "Old work")]
    index.close()
