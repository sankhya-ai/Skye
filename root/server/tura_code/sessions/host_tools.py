"""Tools the host provides to the runtime (``initialize``'s ``external_tools``).

Each toolset contributes definitions for a given session and answers the calls
for its names. The tenant decides which toolsets exist (memory, background
processes, coding flows); a session only dispatches.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .. import processes
from ..memory import TOOL_NAMES as MEMORY_TOOL_NAMES
from ..memory import CHECKPOINT_TOOL
from ..memory import tool_definitions as memory_tool_definitions

if TYPE_CHECKING:
    from .runtime import SessionRuntime


class Toolset(Protocol):
    def definitions(self, runtime: "SessionRuntime") -> list[dict[str, Any]]: ...

    def handles(self, name: str) -> bool: ...

    async def call(self, runtime: "SessionRuntime", call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


def tool_error(message: str, brief: str = "Failed") -> dict[str, Any]:
    return {"is_error": True, "output": "", "message": message, "display": [{"type": "brief", "text": brief}]}


class MemoryToolset:
    def definitions(self, runtime: "SessionRuntime") -> list[dict[str, Any]]:
        return memory_tool_definitions(project_only=runtime.tenant.memory_for(runtime.record).project_only) if runtime.tenant.memory.enabled else []

    def handles(self, name: str) -> bool:
        return name in MEMORY_TOOL_NAMES

    async def call(self, runtime: "SessionRuntime", call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        unchecked = name == CHECKPOINT_TOOL and runtime.turn is not None and not runtime.turn.verified
        if unchecked:
            # A checkpoint outlives the session; an unexercised "fixed it" must not come back as a fact.
            arguments = {**arguments, "summary": f"UNVERIFIED (no check ran after the last edit): {arguments.get('summary') or ''}".strip()}
        result = await runtime.tenant.memory_for(runtime.record).handle_tool(name, arguments, workspace=Path(runtime.record.workspace), session_id=runtime.record.id)
        if unchecked and not result.get("is_error"):
            result["output"] = (
                "Saved as UNVERIFIED: this turn edited files and has run nothing that exercises them. "
                "Run the tests or a script that reproduces the problem before calling it done.\n\n" + str(result.get("output") or "")
            )
        return result


class ProcessToolset:
    def definitions(self, runtime: "SessionRuntime") -> list[dict[str, Any]]:
        return [processes.tool_definition()]

    def handles(self, name: str) -> bool:
        return name == processes.TOOL_NAME

    async def call(self, runtime: "SessionRuntime", call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await processes.handle(
            runtime.processes,
            arguments,
            approve=lambda command: runtime.approve_host_command(call_id, command, "Start in the background"),
        )
