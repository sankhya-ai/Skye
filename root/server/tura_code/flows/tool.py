"""`coding_flow`: the agent hands a substantial change to isolated candidates.

Reads (list, status, review) and starting work run directly; `apply` merges
into the owner's checkout and `rollback` reverts it, so both always ask the
owner, bound to the exact diff hash or commit, and never earn trust.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..sessions.host_tools import tool_error
from .store import FlowError

if TYPE_CHECKING:
    from ..sessions import SessionRuntime

TOOL_NAME = "coding_flow"
OPERATIONS = ("create", "start", "status", "list", "review", "stop", "cancel", "apply", "rollback")


def definition() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "Only when the owner asks for a flow: root implements the task in an isolated Git worktree with its own "
            "agent, runs your verification commands, repairs failures from the exact test output, and keeps the verified "
            "patch. Nothing touches the checkout until the owner applies the reviewed patch, and an apply can be rolled "
            "back exactly. `candidate_count` above 1 repeats the whole job per candidate (and its cost): only when the "
            "owner asked for alternatives, and never above their Settings limit. `create` then `start`; progress arrives "
            "in this session; `review` shows the diff; `apply`/`rollback` ask the owner. Otherwise edit files directly."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": list(OPERATIONS)},
                "flow_id": {"type": "string", "description": "The id `create` returned, e.g. `flow1`."},
                "task": {"type": "string", "description": "For create: the change, fully specified."},
                "acceptance": {"type": "array", "items": {"type": "string"}, "description": "For create: conditions the change must meet."},
                "verification_commands": {"type": "array", "items": {"type": "string"}, "maxItems": 8, "description": "For create: read-only or test commands the host runs on each candidate, e.g. `python3 -m pytest -q`."},
                "candidate_count": {"type": "integer", "minimum": 1, "maximum": 3},
                "review_sha256": {"type": "string", "description": "For apply: the diff hash `review` returned."},
                "applied_commit": {"type": "string", "description": "For rollback: the commit `apply` created."},
            },
            "required": ["operation"],
            "additionalProperties": False,
        },
    }


def _ok(data: Any, brief: str) -> dict[str, Any]:
    return {"is_error": False, "output": json.dumps(data, indent=1)[:40_000], "message": "", "display": [{"type": "brief", "text": brief}]}


class FlowToolset:
    plugin = "flows"

    def definitions(self, runtime: "SessionRuntime") -> list[dict[str, Any]]:
        # A candidate works inside a flow; it never starts or drives one.
        return [definition()] if runtime.record.kind == "session" else []

    def handles(self, name: str) -> bool:
        return name == TOOL_NAME

    async def call(self, runtime: "SessionRuntime", call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        service = runtime.tenant.flows
        operation = str(arguments.get("operation") or "")
        flow_id = str(arguments.get("flow_id") or "")
        try:
            if operation == "create":
                flow = await service.create(
                    Path(runtime.record.workspace),
                    str(arguments.get("task") or ""),
                    owner_session=runtime.record.id,
                    acceptance=arguments.get("acceptance"),
                    verification_commands=arguments.get("verification_commands"),
                    # One attempt unless asked; never more than the owner's limit (each candidate repeats the job).
                    candidate_count=max(1, min(int(arguments.get("candidate_count") or 1), runtime.tenant.settings.get().flow_max_candidates)),
                )
                return _ok({"flow_id": flow["id"], "status": flow["status"], "candidates": len(flow["candidates"]), "next": "start"}, f"Prepared {flow['id']}")
            if operation == "list":
                return _ok([{k: f[k] for k in ("id", "status", "task")} for f in service.list()], "Listed flows")
            if operation == "start":
                return _ok(service.start(flow_id), f"Started {flow_id}")
            if operation == "status":
                return _ok(service.get(flow_id), service.get(flow_id)["status"])
            if operation == "review":
                return _ok(await service.review(flow_id), "Patch ready for review")
            if operation == "stop":
                return _ok(await service.stop(flow_id), "Stopped")
            if operation == "cancel":
                return _ok(await service.cancel(flow_id), "Cancelled")
            if operation == "apply":
                sha = str(arguments.get("review_sha256") or "")
                review = await service.review(flow_id)
                paths = ", ".join(review["diff"]["changed_paths"][:6])
                allowed = await runtime.approve_host_action(call_id, "apply coding flow", f"Merge {flow_id} (diff {sha[:12]}: {paths}) into the checkout")
                if not allowed:
                    return tool_error("The owner did not approve applying the patch.", "Rejected")
                return _ok(await service.apply(flow_id, sha), "Applied")
            if operation == "rollback":
                commit = str(arguments.get("applied_commit") or "")
                if not await runtime.approve_host_action(call_id, "roll back coding flow", f"Revert {flow_id}'s merge {commit[:12]}"):
                    return tool_error("The owner did not approve the rollback.", "Rejected")
                return _ok(await service.rollback(flow_id, commit), "Rolled back")
            return tool_error(f"Unknown operation `{operation}`; use one of {', '.join(OPERATIONS)}.", "Unknown operation")
        except FlowError as error:
            return tool_error(str(error), "Flow error")
