"""History: searching everything a person has asked root, and what it answered.

``index`` (SQLite FTS5 over finished turns) is filled after every turn and
backfilled once from older sessions. The agent searches it with
``session_search`` (what did we decide last week, how did we fix this before);
the owner searches it from the sidebar.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from ..sessions.host_tools import tool_error
from .index import HistoryIndex

if TYPE_CHECKING:
    from ..sessions import SessionRuntime

TOOL = "session_search"

DEFINITION = {
    "name": TOOL,
    "description": (
        "Search the owner's past root sessions — their questions and root's answers, across every workspace — for "
        "earlier decisions, fixes, commands and explanations. Words must all match; the last may be a prefix. "
        "Set `this_workspace` to search only sessions in the current workspace."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "this_workspace": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}


class HistoryToolset:
    plugin = "history"

    def __init__(self, index: HistoryIndex) -> None:
        self.index = index

    def definitions(self, runtime: "SessionRuntime") -> list[dict[str, Any]]:
        return [] if runtime.record.kind != "session" else [DEFINITION]

    def handles(self, name: str) -> bool:
        return name == TOOL

    async def call(self, runtime: "SessionRuntime", call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return tool_error("`query` is required.", "Invalid arguments")
        workspace = runtime.record.workspace if arguments.get("this_workspace") else ""
        found = await asyncio.to_thread(self.index.search, query, limit=int(arguments.get("limit") or 8), workspace=workspace, plain=True)
        found = [hit for hit in found if hit["session_id"] != runtime.record.id]
        if not found:
            return {"is_error": False, "output": "No earlier session matches.", "message": "", "display": [{"type": "brief", "text": "No matches"}]}
        blocks = []
        for hit in found:
            when = time.strftime("%Y-%m-%d", time.localtime(hit["finished_at"])) if hit["finished_at"] else "?"
            blocks.append(f"[{when}] “{hit['title']}” in {hit['workspace']}\nasked: {hit['prompt']}\nanswered: {hit['reply']}")
        return {"is_error": False, "output": "\n\n".join(blocks), "message": "", "display": [{"type": "brief", "text": f"{len(found)} earlier turns"}]}


def install(server) -> None:
    def extend(tenant) -> None:
        tenant.history = HistoryIndex(tenant.root / "history.db", tenant.sessions_dir)
        tenant.toolsets.append(HistoryToolset(tenant.history))

        async def close() -> None:
            tenant.history.close()

        tenant.closers.append(close)

    async def after_turn(tenant, runtime, turn, status) -> None:
        if runtime.record.kind != "session" or not turn.prompt or turn.prompt.startswith("/"):
            return
        await asyncio.to_thread(
            tenant.history.add,
            session_id=runtime.record.id,
            turn_id=turn.id,
            finished_at=time.time(),
            workspace=runtime.record.workspace,
            title=runtime.record.title,
            prompt=turn.prompt,
            reply=turn.reply(),
            files=turn.files_touched,
        )

    server.extensions.append(extend)
    server.after_turn_hooks.append(after_turn)


def router(app_state):
    from fastapi import APIRouter, Depends

    from ..tenancy import Tenant

    api = APIRouter(prefix="/api")

    @api.get("/search")
    async def search(q: str = "", workspace: str = "", limit: int = 20, tenant: Tenant = Depends(app_state.auth.tenant)) -> dict:
        hits = await asyncio.to_thread(tenant.history.search, q, limit=max(1, min(limit, 50)), workspace=workspace)
        return {"results": hits}

    return api
