"""The reviewer: one finished turn in, reusable lessons out.

Ported from Chotu's Hermes-style background reviewer
(``assistant_runtime/self_improvement.py``) and aimed at coding work. After a
turn that did real work — or went wrong — a model reads a compact trace of
it and proposes:

* **skills**: class-level ways of working (how to verify a change in this
  kind of project, how to recover from this kind of failure), written as
  ``SKILL.md`` files the agent loads in later sessions;
* **memories**: durable facts (a project convention, an owner preference).

The guardrails are Chotu's: only class-level lessons, never a rule keyed on
an exact prompt, task id or hard-coded answer (the anti-overfit filter drops
those); credentials are redacted before anything leaves the machine; the
reviewer sees the skills already learned so it improves one instead of
writing a near-duplicate. Unlike Chotu, a failed review yields nothing — root
never invents a generic lesson to fill the gap.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ..llm import loads_lenient

REVIEW_SCHEMA = "root.learning_review.v1"

_SECRET = re.compile(
    r"(?i)(sk-[A-Za-z0-9_\-]{12,}|rt_[A-Za-z0-9_\-]{20,}|nvapi-[A-Za-z0-9_\-]{12,}|gh[pousr]_[A-Za-z0-9_\-]{12,}|github_pat_[A-Za-z0-9_\-]{12,}|AIza[A-Za-z0-9_\-]{12,}|xox[abp]-[A-Za-z0-9\-]{10,}|AKIA[A-Z0-9]{12,})"
)
_OVERFIT = re.compile(
    r"(?i)\b(exact prompt|instruction substring|task[-_ ]?id|hard-?cod(e|ed|ing)|prompt-specific|if the (instruction|prompt|request) (contains|says)|when the (prompt|request) contains|always answer)\b"
)
_TASK_ID = re.compile(r"\b([a-z0-9]{6,}_[0-9]{1,3}|(task|ticket|issue|case)[-_ #]?[0-9]+)\b", re.IGNORECASE)
_PREFERENCE = re.compile(r"(?i)\b(always|never|from now on|don't|do not|prefer|remember|make sure to|stop doing)\b")
_MEMORY_KINDS = {"user_preference": ("preference", "owner"), "project_fact": ("fact", "project"), "constraint": ("convention", "project")}

SYSTEM_PROMPT = """You are root's background reviewer. root is a coding agent. You read one finished turn and extract what is worth keeping for future sessions.

Keep only class-level lessons: how to plan, inspect, edit, verify, recover, or what the owner prefers. Never a rule tied to this exact request, a task id, a file that only matters today, or a hard-coded answer: if the turn suggests one, put it in rejected_overfit and keep only the general lesson.

Skills are reusable procedures for a kind of task, loaded into later sessions. If an existing skill covers the lesson, reuse its exact name to improve it instead of creating a near-duplicate. A skill must be specific enough to act on: concrete steps and checks, not advice like "be careful".

Memories are single durable facts: an owner preference (user_preference), a fact about this project such as a build or test command (project_fact), or a rule of this codebase (constraint).

`verified` says whether every edit in the turn was followed by a command that exercised it (tests, a script, a build). When it is false, the turn's claims of success are unproven: never record them as facts or as a procedure that worked. The lesson, if any, is how this kind of change should have been checked.

If nothing is worth keeping, return should_save false with empty lists. Most turns teach nothing new. Keep it compact: at most two skills and three memories, each list item one short sentence. Return only JSON:
{"should_save": true, "summary": "one sentence", "skill_candidates": [{"name": "kebab-case-name", "trigger": "when to use it", "lesson": "what to do and why", "steps": ["..."], "verification": ["..."], "pitfalls": ["..."]}], "memory_candidates": [{"kind": "user_preference|project_fact|constraint", "content": "one standalone sentence"}], "rejected_overfit": ["..."]}"""


def redact(text: str) -> str:
    return _SECRET.sub("[redacted]", text)


def clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")[:64] or "learned-skill"


def overfit(text: str) -> bool:
    return bool(_OVERFIT.search(text) or _TASK_ID.search(text))


def should_review(*, prompt: str, status: str, files_touched: int, tool_count: int, errors: int, rejections: int) -> bool:
    """Whether a turn is worth a review call: real work, trouble, or a stated preference."""
    if prompt.startswith("/"):
        return False
    if status not in ("finished", "cancelled") or errors >= 2 or rejections >= 1:
        return True
    if files_touched and tool_count >= 4:
        return True
    return bool(_PREFERENCE.search(prompt))


def build_messages(turn: dict[str, Any], skills: list[dict[str, str]]) -> list[dict[str, str]]:
    payload = {
        "request": clip(redact(turn.get("prompt", "")), 1_500),
        "final_reply": clip(redact(turn.get("reply", "")), 1_500),
        "status": turn.get("status"),
        "files_touched": turn.get("files_touched", [])[:30],
        "tool_trace": [{**step, "arguments": clip(redact(step.get("arguments", "")), 300)} for step in turn.get("trace", [])[:40]],
        "verified": turn.get("verified", True),
        "checks_run": turn.get("checks", 0),
        "tool_errors": turn.get("errors", 0),
        "owner_rejections": turn.get("rejections", 0),
        "existing_skills": [{"name": s["name"], "description": clip(s.get("description", ""), 200)} for s in skills[:40]],
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _strings(value: Any, limit: int) -> list[str]:
    values = value if isinstance(value, list) else ([value] if isinstance(value, str) else [])
    return [clip(redact(v), 260) for v in values[:limit] if str(v or "").strip()]


def parse(text: str) -> dict[str, Any]:
    """The reviewer's answer, normalised and filtered. Raises ValueError on garbage."""
    raw = loads_lenient(text)
    if not isinstance(raw, dict):
        raise ValueError("the review must be a JSON object")
    rejected = _strings(raw.get("rejected_overfit"), 10)
    skills = []
    for item in raw.get("skill_candidates") or []:
        if not isinstance(item, dict):
            continue
        skill = {
            "name": slug(item.get("name")),
            "trigger": clip(redact(item.get("trigger", "")), 260),
            "lesson": clip(redact(item.get("lesson", "")), 1_200),
            "steps": _strings(item.get("steps"), 10),
            "verification": _strings(item.get("verification"), 8),
            "pitfalls": _strings(item.get("pitfalls"), 8),
        }
        combined = " ".join([skill["name"], skill["trigger"], skill["lesson"], *skill["steps"]])
        if not skill["lesson"] or not skill["trigger"]:
            continue
        if overfit(combined):
            rejected.append(clip(combined, 260))
            continue
        skills.append(skill)
    memories = []
    for item in raw.get("memory_candidates") or []:
        if isinstance(item, str):
            item = {"kind": "project_fact", "content": item}
        if not isinstance(item, dict):
            continue
        content = clip(redact(item.get("content", "")), 600)
        kind = str(item.get("kind") or "project_fact")
        if not content or kind not in _MEMORY_KINDS:
            continue
        if overfit(content):
            rejected.append(content)
            continue
        dhee_kind, scope = _MEMORY_KINDS[kind]
        memories.append({"kind": dhee_kind, "scope": scope, "content": content})
    save = bool(raw.get("should_save")) and bool(skills or memories)
    return {
        "schema_version": REVIEW_SCHEMA,
        "should_save": save,
        "summary": clip(redact(raw.get("summary", "")), 300),
        "skills": skills[:4] if save else [],
        "memories": memories[:4] if save else [],
        "rejected_overfit": rejected[:10],
    }


def fingerprint(kind: str, body: dict[str, Any]) -> str:
    key = body.get("name") if kind == "skill" else " ".join(str(body.get("content", "")).lower().split())
    return hashlib.sha256(f"{kind}:{key}:{body.get('lesson', '')[:200] if kind == 'skill' else ''}".encode()).hexdigest()[:24]
