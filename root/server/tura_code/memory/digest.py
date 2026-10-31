"""The turn digest: what a working turn leaves in memory.

Dhee's checkpoint turns ``what_worked`` and ``what_failed`` into insights and
warnings, which come back as "Lessons from earlier work" in the next session's
first packet. Local memory has no LLM of its own to write them, and the
agent rarely calls ``memory_remember`` itself, so a plain checkpoint left
memory empty. After a turn that did real work, one call to root's own model
(the tenant's endpoint and key, ``learning_model`` or the session model)
reads a compact trace of it and returns the checkpoint's fields plus the few
durable facts the turn established.

Facts are kept only from a turn that finished with every edit checked: an
unverified "fixed it" is a claim, not a fact. The model sees the memories
already stored about the request, so it restates none of them, and a fact
that corrects one names it in ``replaces``: the old one is forgotten. Without
a key, or when the call fails, the checkpoint is the plain request and
outcome, as before. Nothing here ever fails a turn.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import launcher, llm
from ..learning.review import clip, overfit, redact

if TYPE_CHECKING:
    from ..sessions import TurnState
    from ..settings import Settings
    from .gateway import MemoryView

logger = logging.getLogger(__name__)

DIGEST_TIMEOUT_SECONDS = 90.0
TASK_TYPES = ("bug_fix", "feature", "refactor", "testing", "docs", "investigation", "config", "other")
#: Reviewer kinds to (Dhee kind, scope), as the learning reviewer names them.
FACT_KINDS = {"project_fact": ("fact", "project"), "constraint": ("convention", "project"), "user_preference": ("preference", "owner")}

SYSTEM_PROMPT = """You write the memory a coding agent keeps after one turn of work. You read the request, the tool trace and the final reply, and return what the next session should know.

- summary: one or two sentences: what was asked and where it stands now.
- what_worked: the approach that moved the work forward, stated so it applies to the next task of this kind ("ran the single failing test with -k before the whole suite"). Empty if nothing stood out.
- what_failed: an approach that cost time or broke something, and why. Empty if nothing did.
- decisions: choices made and their reason, one short sentence each.
- todos: work the turn left unfinished.
- facts: durable facts this turn established that a later session could not cheaply rediscover: how to build, test or run the project, a convention of this codebase, a constraint, or a preference the owner stated. Never what the task itself was, a file's contents, a one-off value, or anything already in known_memories. A fact that corrects a known memory gives that memory's id in "replaces". Usually there are none.

When `verified` is false, the edits were never exercised by a check: do not report them as working, and keep facts empty. Each string is one standalone sentence. Return only JSON:
{"summary": "...", "task_type": "bug_fix|feature|refactor|testing|docs|investigation|config|other", "what_worked": "", "what_failed": "", "decisions": [], "todos": [], "facts": [{"kind": "project_fact|constraint|user_preference", "content": "...", "replaces": ""}]}"""


def build_messages(turn: "TurnState", status: str, known: list[dict[str, Any]]) -> list[dict[str, str]]:
    payload = {
        "request": clip(redact(turn.prompt), 1_500),
        "final_reply": clip(redact(turn.reply()), 1_500),
        "status": status,
        "verified": turn.verified,
        "checks_run": turn.checks,
        "tool_errors": turn.errors,
        "owner_rejections": turn.rejections,
        "files_touched": turn.files_touched[:30],
        "tool_trace": [{**step, "arguments": clip(redact(step.get("arguments", "")), 300), "result": clip(redact(step.get("result", "")), 200)} for step in turn.trace(40)],
        "known_memories": [{"id": item["id"], "memory": clip(item["memory"], 300)} for item in known],
    }
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def _text(value: Any, limit: int = 300) -> str:
    return clip(redact(value), limit) if isinstance(value, str) else ""


def _list(value: Any, limit: int) -> list[str]:
    values = value if isinstance(value, list) else []
    return [clip(redact(v), 260) for v in values[:limit] if isinstance(v, str) and v.strip()]


def parse(text: str, known_ids: set[str]) -> dict[str, Any]:
    """The model's digest, normalised and filtered. Raises ValueError on garbage."""
    raw = llm.loads_lenient(text)
    if not isinstance(raw, dict) or not _text(raw.get("summary")):
        raise ValueError("the digest must be a JSON object with a summary")
    facts = []
    for item in raw.get("facts") or []:
        if not isinstance(item, dict):
            continue
        content = _text(item.get("content"), 600)
        kind = str(item.get("kind") or "project_fact")
        if not content or kind not in FACT_KINDS or overfit(content):
            continue
        replaces = str(item.get("replaces") or "")
        facts.append({"kind": kind, "content": content, "replaces": replaces if replaces in known_ids else ""})
    task_type = str(raw.get("task_type") or "")
    return {
        "summary": _text(raw.get("summary"), 600),
        "task_type": task_type if task_type in TASK_TYPES else "",
        "what_worked": _text(raw.get("what_worked")),
        "what_failed": _text(raw.get("what_failed")),
        "decisions": _list(raw.get("decisions"), 5),
        "todos": _list(raw.get("todos"), 5),
        "facts": facts[:3],
    }


def outcome_score(turn: "TurnState", status: str) -> float:
    """A coarse score for Dhee's per-task-type performance tracking."""
    if status != "finished":
        return 0.2
    return 1.0 if turn.verified and not turn.rejections else 0.6


async def digest_turn(memory: "MemoryView", workspace: Path, turn: "TurnState", status: str, *, settings: "Settings", api_key: str) -> dict[str, Any]:
    """Checkpoint one working turn, with the model's digest when it can be had."""
    known: list[dict[str, Any]] = []
    try:
        known = await memory.recall(turn.prompt, workspace, limit=8)
    except Exception:  # noqa: BLE001 - the digest only loses its dedupe context
        logger.debug("digest recall failed", exc_info=True)
    digest: dict[str, Any] | None = None
    # Scripted turns ask the model only when its answers are scripted too.
    if os.environ.get(llm.SCRIPTED_LLM_ENV) or (api_key and not launcher.scripted()):
        try:
            answer = await llm.complete(
                build_messages(turn, status, known),
                base_url=settings.base_url,
                api_key=api_key,
                model=settings.learning_model or settings.model,
                purpose="digest",
                max_tokens=900,
                timeout=DIGEST_TIMEOUT_SECONDS,
                json_mode=True,
            )
            digest = parse(answer, {item["id"] for item in known})
        except (llm.LLMError, ValueError) as error:
            logger.info("turn digest skipped: %s", error)
    summary = f"Request: {turn.prompt[:400]}\nOutcome ({status}): {turn.reply()[:600] or 'no final reply'}"
    fields: dict[str, Any] = {"files_touched": turn.files_touched}
    if digest:
        summary = f"{digest['summary']}\nRequest: {turn.prompt[:400]}"
        fields.update({k: digest[k] for k in ("task_type", "what_worked", "what_failed", "decisions", "todos")})
        if digest["task_type"]:
            fields["outcome_score"] = outcome_score(turn, status)
    await memory.checkpoint(workspace, summary=summary, status="completed" if status == "finished" else "paused", **fields)

    saved = replaced = 0
    if digest and status == "finished" and turn.verified:
        for fact in digest["facts"]:
            kind, scope = FACT_KINDS[fact["kind"]]
            if scope == "owner" and getattr(memory, "project_only", False):
                continue  # a delegated worker never writes the owner's preferences
            try:
                if fact["replaces"] and await memory.forget(fact["replaces"], workspace):
                    replaced += 1
                stored = await memory.remember(fact["content"], workspace, scope=scope, kind=kind)
                saved += 0 if isinstance(stored, dict) and stored.get("duplicate") else 1
            except Exception:  # noqa: BLE001
                logger.info("digest fact not stored", exc_info=True)
    return {
        "digest": digest is not None,
        "lessons": bool(digest and (digest["what_worked"] or digest["what_failed"])),
        "facts_saved": saved,
        "facts_replaced": replaced,
    }
