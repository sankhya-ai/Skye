"""The typed memory tools the runtime calls, and the role text that explains them."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import MemoryScopeError, MemoryUnavailableError

if TYPE_CHECKING:
    from .gateway import MemoryView

logger = logging.getLogger(__name__)

RECALL_TOOL = "memory_recall"
REMEMBER_TOOL = "memory_remember"
CHECKPOINT_TOOL = "memory_checkpoint"
TOOL_NAMES = frozenset({RECALL_TOOL, REMEMBER_TOOL, CHECKPOINT_TOOL})
_KINDS = ("preference", "convention", "command", "decision", "fact", "gotcha")

MEMORY_ROLE = """\
You have long-term memory (Dhee) that persists across sessions and projects:
- memory_recall: search it when earlier decisions, project conventions, or the owner's preferences could matter.
- memory_remember: save a durable fact worth knowing next time — an owner preference, a project convention, a build/test command, an architectural decision, a gotcha you hit. One fact per call. Never store secrets, transient task state, or code.
- memory_checkpoint: at a natural stopping point in long work, record what was done, what worked, what failed, and what remains.
Relevant memories are attached automatically at the start of a request in the `## System` section. Treat them as advisory: the owner's current request wins."""


def tool_definitions(project_only: bool = False) -> list[dict[str, Any]]:
    scope_prop = {
        "type": "string",
        "enum": ["project"] if project_only else ["project", "owner"],
        "description": "`project` for facts about this codebase." if project_only else "`project` for facts about this codebase; `owner` for the person's own preferences that apply everywhere.",
    }
    return [
        {
            "name": RECALL_TOOL,
            "description": (
                "Search long-term memory for facts from earlier sessions: project conventions, build and "
                "test commands, architectural decisions, and known gotchas" + ("" if project_only else ", and the owner's preferences") + ". "
                "Returns the most relevant memories with ids."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What you want to know, in plain words."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Maximum results (default 6)."},
                    "scope": {
                        "type": "string",
                        "enum": ["project"] if project_only else ["all", "project", "owner"],
                        "description": "Which memory to search (default all).",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": REMEMBER_TOOL,
            "description": (
                "Store ONE durable fact in long-term memory so it is known in future sessions. Use for "
                + ("" if project_only else "owner preferences, ")
                + "project conventions, build/test commands, architectural decisions, and gotchas. "
                "Never store secrets, credentials, transient task state, or code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The fact, written as a standalone sentence."},
                    "kind": {"type": "string", "enum": list(_KINDS), "description": "What sort of fact this is."},
                    "scope": scope_prop,
                },
                "required": ["content"],
                "additionalProperties": False,
            },
        },
        {
            "name": CHECKPOINT_TOOL,
            "description": (
                "Record progress on the current work in long-term memory: a summary, what worked, what failed, "
                "decisions made, remaining todos, and files touched. Returns the durable context Dhee holds for "
                "this project. Use at a natural stopping point in long, multi-step work."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "What was being worked on and where it stands."},
                    "what_worked": {"type": "string"},
                    "what_failed": {"type": "string"},
                    "decisions": {"type": "array", "items": {"type": "string"}},
                    "todos": {"type": "array", "items": {"type": "string"}},
                    "files_touched": {"type": "array", "items": {"type": "string"}},
                    "session_id": {"type": "string"},
                    "workspace_root": {"type": "string"},
                },
                "required": ["summary"],
                "additionalProperties": True,
            },
        },
    ]


def _return(output: str, message: str = "", *, error: bool = False, brief: str = "") -> dict[str, Any]:
    display = [{"type": "brief", "text": brief}] if brief else []
    return {"is_error": error, "output": output, "message": message, "display": display}


async def handle_tool(memory: "MemoryView", name: str, arguments: dict[str, Any], *, workspace: Path, session_id: str) -> dict[str, Any]:
    try:
        if name == RECALL_TOOL:
            query = str(arguments.get("query") or "").strip()
            if not query:
                return _return("", "`query` is required.", error=True, brief="Missing query")
            limit = max(1, min(20, int(arguments.get("limit") or 6)))
            hits = await memory.recall(query, workspace, scope=str(arguments.get("scope") or "all"), limit=limit)
            if not hits:
                return _return("No relevant memories found.", "", brief="No memories")
            lines = [f"- [{h['scope']}] {h['memory']} (id: {h['id']})" for h in hits]
            return _return("\n".join(lines), f"{len(hits)} memories", brief=f"Recalled {len(hits)}")
        if name == REMEMBER_TOOL:
            content = str(arguments.get("content") or "").strip()
            if not content:
                return _return("", "`content` is required.", error=True, brief="Nothing to remember")
            kind = str(arguments.get("kind") or "fact")
            scope = "owner" if arguments.get("scope") == "owner" else "project"  # a delegated view refuses "owner"
            result = await memory.remember(content, workspace, scope=scope, kind=kind, session_id=session_id)
            if isinstance(result, dict) and result.get("stored") is False:
                reason = result.get("reason") or result.get("event") or "rejected by Dhee's admission policy"
                return _return(f"Not stored: {reason}", "", brief="Not stored")
            memory_id = result.get("id", "") if isinstance(result, dict) else ""
            if isinstance(result, dict) and result.get("duplicate"):
                return _return(f"Already in {scope} memory (id: {memory_id}).", "", brief="Already remembered")
            return _return(f"Stored in {scope} memory (id: {memory_id}).", "", brief=f"Remembered ({scope})")
        if name == CHECKPOINT_TOOL:
            summary = str(arguments.get("summary") or "").strip() or "Work in progress."
            fields = {k: arguments.get(k) for k in ("what_worked", "what_failed", "decisions", "todos", "files_touched")}
            await memory.checkpoint(workspace, summary=summary, **fields)
            restored = await memory.compile_packet(summary, workspace, seen=set(), first_turn=True)
            body = restored.text if restored else "No additional durable memory for this project yet."
            return _return(f"Checkpoint saved.\n\n{body}", "", brief="Checkpoint saved")
        return _return("", f"Unknown memory tool `{name}`.", error=True, brief="Unknown tool")
    except MemoryScopeError as error:
        return _return("", str(error), error=True, brief="Memory scope")
    except MemoryUnavailableError as error:
        return _return("", f"Memory is unavailable: {error}", error=True, brief="Memory unavailable")
    except Exception as error:  # noqa: BLE001 - a memory fault is a tool error, not a failed turn
        logger.exception("memory tool %s failed", name)
        return _return("", f"Memory error: {error}", error=True, brief="Memory error")
