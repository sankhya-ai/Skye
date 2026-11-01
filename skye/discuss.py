"""The team line: the mission-scoped discussion channel the workers actually talk on.

Settled-step handoffs move FINISHED outcomes; the team line is for everything in between - the
backend developer posting an API contract change the moment it is decided, the frontend developer
acking and adapting, a tester asking the room a question. It is an append-only journal
(missions/<id>/discussion.jsonl, fsync'd before reliance, flock-serialized across the separate
worker PROCESSES that post to it), with a monotonic seq cursor so readers poll only what is new.

Delivery honesty: there is no push into an already-running CLI worker - Claude Code and Codex
cannot receive a message mid-run. What exists instead, in order of strength:
  1. every entry is folded into the NEXT steps' briefs (guaranteed, at dispatch);
  2. a running worker polls with `skye hear <mission> --since <cursor>` when it chooses
     (the brief tells it how; Root workers can wire the same reads as dynamic tools);
  3. when a Chamber line is configured (SKYE_LINE_INVITE/SKYE_LINE_TO), posts also ride the
     existing event bridge as sealed line messages, so other PROCESSES watching the line see
     them in real time - the Chamber-native path, reused rather than rebuilt.

Interference control is NOT this module's log - it is the claims gate in the mission scheduler
(a step whose declared write-paths intersect a running step's claims does not dispatch) plus the
contract-change ack vocabulary here: a change entry names the roles that must ack, and unacked()
tells the scheduler and the briefs exactly who has not caught up.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

KINDS = ("note", "broadcast", "question", "answer", "contract-change", "ack")
MAX_BODY = 4000
MAX_REFS = 16
AUTHOR = re.compile(r"^[a-z][a-z0-9-]{0,39}$|^owner$")


class DiscussionError(ValueError):
    pass


def _paths(state_dir: str | Path, mission: str) -> tuple[Path, Path]:
    base = Path(state_dir) / "missions" / mission
    base.mkdir(parents=True, exist_ok=True)
    return base / "discussion.jsonl", base / "discussion.lock"


def _entries(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text("utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def post(state_dir: str | Path, mission: str, *, author: str, kind: str, body: str,
         refs: list[str] | None = None, requires_ack: list[str] | None = None,
         ack_of: str = "", bridge: Any = None,
         event_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Append one entry, fsync'd, under the inter-process lock. ``author`` is a role name or
    "owner". contract-change entries name the roles that must ack in ``requires_ack``."""
    author = str(author or "").strip()
    if not AUTHOR.match(author):
        raise DiscussionError(f"author must be a role name or 'owner', got {author!r}")
    if kind not in KINDS:
        raise DiscussionError(f"kind must be one of {KINDS}, got {kind!r}")
    body = str(body or "").strip()
    if not body:
        raise DiscussionError("an empty message says nothing")
    refs = [str(r)[:300] for r in (refs or [])][:MAX_REFS]
    requires_ack = [str(r) for r in (requires_ack or []) if AUTHOR.match(str(r))]
    if kind == "contract-change" and not requires_ack:
        raise DiscussionError("a contract-change must name the role(s) that must ack it")
    path, lock_path = _paths(state_dir, mission)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            seq = len(_entries(path)) + 1
            entry = {"v": 1, "seq": seq, "ts": time.time(),
                     "id": f"msg-{secrets.token_hex(4)}", "author": author, "kind": kind,
                     "body": body[:MAX_BODY], "refs": refs, "requires_ack": requires_ack,
                     "ack_of": str(ack_of or "")}
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    if bridge is not None:
        from . import events
        bridge.publish(events.make_event(
            "mission-discussion", {"role": author, "goal": ""},
            status=kind, reason=body[:160], **(event_context or {})))
    return entry


def ack(state_dir: str | Path, mission: str, change_id: str, *, author: str,
        note: str = "", bridge: Any = None,
        event_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Record that a role caught up with a contract-change: seen and adapting (or objecting -
    say so in the note; the record proves the ack happened, the note carries the stance)."""
    path, _ = _paths(state_dir, mission)
    known = {e.get("id") for e in _entries(path) if e.get("kind") == "contract-change"}
    if change_id not in known:
        raise DiscussionError(f"unknown contract-change {change_id!r}")
    return post(state_dir, mission, author=author, kind="ack",
                body=(note or f"ack {change_id}"), ack_of=change_id,
                bridge=bridge, event_context=event_context)


def read(state_dir: str | Path, mission: str, *, since: int = 0,
         limit: int = 100) -> dict[str, Any]:
    """Entries after seq ``since`` plus the new cursor. Polling is the honest delivery for a
    running worker: it reads when it chooses, and misses nothing in between."""
    path, _ = _paths(state_dir, mission)
    entries = [e for e in _entries(path) if int(e.get("seq") or 0) > since][: max(1, limit)]
    cursor = entries[-1]["seq"] if entries else since
    return {"entries": entries, "cursor": cursor}


def unacked(state_dir: str | Path, mission: str, roles: list[str]) -> list[dict[str, Any]]:
    """Contract-changes still waiting on an ack from at least one of ``roles``."""
    path, _ = _paths(state_dir, mission)
    entries = _entries(path)
    acked = {(e.get("ack_of"), e.get("author")) for e in entries if e.get("kind") == "ack"}
    out = []
    for entry in entries:
        if entry.get("kind") != "contract-change":
            continue
        waiting = [r for r in entry.get("requires_ack") or []
                   if r in roles and (entry.get("id"), r) not in acked]
        if waiting:
            out.append({**entry, "waiting_on": waiting})
    return out


def tail_text(state_dir: str | Path, mission: str, *, limit: int = 8,
              chars: int = 2000) -> str:
    """The attributed tail folded into the next steps' briefs, bounded for token discipline."""
    path, _ = _paths(state_dir, mission)
    entries = _entries(path)[-limit:]
    lines = []
    for e in entries:
        prefix = f"#{e.get('seq')} {e.get('author', '?')} ({e.get('kind', 'note')})"
        if e.get("ack_of"):
            prefix += f" re {e['ack_of']}"
        lines.append(f"{prefix}: {str(e.get('body') or '')[:240]}")
    return "\n".join(lines)[-chars:]
