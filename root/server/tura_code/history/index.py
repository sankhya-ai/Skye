"""The search index over a person's past turns: SQLite FTS5 in ``<tenant>/history.db``.

One row per finished turn — the session's title and workspace, the question,
the final answer, the files it touched. Turns are added as they finish; a
session that predates the index is read once from its newest event-log page
(``EventLog.page``: never a whole log), so search covers recent history from
the first day.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

from ..events import EventLog
from ..sessions.record import SessionRecord

SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS turns USING fts5(
    session_id UNINDEXED, turn_id UNINDEXED, finished_at UNINDEXED, workspace UNINDEXED,
    title, prompt, reply, files, tokenize = 'porter unicode61'
);
CREATE TABLE IF NOT EXISTS indexed_sessions (session_id TEXT PRIMARY KEY);
"""


def match_expression(query: str) -> str:
    """Words to an FTS5 query: every word must appear, the last one as a prefix."""
    words = re.findall(r"\w+", query.lower())
    if not words:
        return ""
    quoted = [f'"{w}"' for w in words[:-1]] + [f'"{words[-1]}"*']
    return " ".join(quoted)


class HistoryIndex:
    def __init__(self, path: Path, sessions_dir: Path) -> None:
        self.path = path
        self.sessions_dir = sessions_dir
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        self._backfilled = False

    def _conn(self) -> sqlite3.Connection:
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(self.path), check_same_thread=False)
            self._db.executescript(SCHEMA)
        return self._db

    def add(self, *, session_id: str, turn_id: str, finished_at: float, workspace: str, title: str, prompt: str, reply: str, files: list[str]) -> None:
        with self._lock:
            db = self._conn()
            db.execute("DELETE FROM turns WHERE session_id = ? AND turn_id = ?", (session_id, turn_id))
            db.execute(
                "INSERT INTO turns VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, turn_id, finished_at, workspace, title, prompt, reply[:20_000], " ".join(files)),
            )
            db.execute("INSERT OR IGNORE INTO indexed_sessions VALUES (?)", (session_id,))
            db.commit()

    def forget(self, session_id: str) -> None:
        with self._lock:
            db = self._conn()
            db.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
            db.execute("DELETE FROM indexed_sessions WHERE session_id = ?", (session_id,))
            db.commit()

    def backfill(self) -> int:
        """Index sessions that predate the index, from each one's newest page of events."""
        with self._lock:
            done = {row[0] for row in self._conn().execute("SELECT session_id FROM indexed_sessions")}
        added = 0
        for path in sorted(self.sessions_dir.glob("*/session.json")) if self.sessions_dir.is_dir() else []:
            session_id = path.parent.name
            if session_id in done:
                continue
            try:
                record = SessionRecord.load(path)
            except (OSError, ValueError):
                continue
            if record.kind == "session":
                for turn in turns_of(EventLog(path.parent / "events.jsonl").page()["events"]):
                    self.add(session_id=record.id, workspace=record.workspace, title=record.title, **turn)
                    added += 1
            with self._lock:
                self._conn().execute("INSERT OR IGNORE INTO indexed_sessions VALUES (?)", (session_id,))
                self._conn().commit()
        return added

    def search(self, query: str, *, limit: int = 10, workspace: str = "", plain: bool = False) -> list[dict[str, Any]]:
        """Best matches first. ``plain``: the stored question and answer (trimmed) instead of
        snippets with ``[match]`` marks — for the agent, whose commands must stay intact."""
        expression = match_expression(query)
        if not expression:
            return []
        if not self._backfilled:
            self.backfill()
            self._backfilled = True
        columns = "substr(prompt, 1, 600), substr(reply, 1, 1500)" if plain else "snippet(turns, 5, '[', ']', ' … ', 12), snippet(turns, 6, '[', ']', ' … ', 24)"
        sql = f"SELECT session_id, turn_id, finished_at, workspace, title, {columns} FROM turns WHERE turns MATCH ?"
        params: list[Any] = [expression]
        if workspace:
            sql += " AND workspace = ?"
            params.append(workspace)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        with self._lock:
            try:
                rows = self._conn().execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                return []
        return [
            {"session_id": r[0], "turn_id": r[1], "finished_at": r[2], "workspace": r[3], "title": r[4], "prompt": r[5], "reply": r[6]}
            for r in rows
            if (self.sessions_dir / r[0]).is_dir()  # a deleted session is gone from search too
        ]

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None


def turns_of(events: list[dict]) -> list[dict]:
    """Finished turns in a page of events: ``{turn_id, finished_at, prompt, reply, files}``."""
    turns: dict[str, dict] = {}
    order: list[str] = []
    for event in events:
        turn_id = event.get("turn")
        if not turn_id:
            continue
        entry = turns.get(turn_id)
        if entry is None:
            entry = turns[turn_id] = {"turn_id": turn_id, "finished_at": 0.0, "prompt": "", "reply": [], "files": [], "done": False}
            order.append(turn_id)
        payload = event.get("payload") or {}
        kind = event.get("type")
        if kind == "UserPrompt":
            entry["prompt"] = str(payload.get("text") or "")
        elif kind == "ContentPart" and payload.get("type") == "text":
            entry["reply"].append(str(payload.get("text") or ""))
        elif kind == "TurnBegin":
            entry["reply"].clear()
        elif kind == "TurnFinished":
            entry.update(done=True, finished_at=float(event.get("ts") or 0), files=list(payload.get("files_touched") or []))
    return [
        {"turn_id": t["turn_id"], "finished_at": t["finished_at"], "prompt": t["prompt"], "reply": " ".join("".join(t["reply"]).split()), "files": t["files"]}
        for t in (turns[i] for i in order)
        if t["done"] and t["prompt"]
    ]
