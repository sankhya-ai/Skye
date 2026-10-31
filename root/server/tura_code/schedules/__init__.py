"""Schedules: prompts root runs by itself, on a timetable — Hermes' cron, for a coding agent.

``when`` (cron and plain forms), ``service`` (each person's schedules, the
server's clock, unattended runs). The agent sets one up with the ``schedule``
tool when asked ("check CI every morning and tell me what broke"); the owner
approves it, and manages them on the Schedules page.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ..sessions.host_tools import tool_error
from .service import MODES, Scheduler, ScheduleError, Schedules, describe

if TYPE_CHECKING:
    from ..sessions import SessionRuntime

TOOL = "schedule"

DEFINITION = {
    "name": TOOL,
    "description": (
        "Schedule a prompt to run by itself later and on repeat, in this workspace — e.g. a nightly test run or a "
        "morning summary of open PRs. create: `name`, `prompt` (written for a future you with no other context), `when` "
        "(cron like `0 9 * * 1-5`, or `every 2h`, `daily 09:00`, `weekdays 18:30`, `@daily`), `mode` (`plan` read-only — "
        "the default —, `default`, or `accept_edits` to let it change files). Runs are unattended: anything that would "
        "need the owner's approval is declined. list: this person's schedules. delete: by `id`. The owner approves a new schedule."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "list", "delete"]},
            "name": {"type": "string"},
            "prompt": {"type": "string"},
            "when": {"type": "string"},
            "mode": {"type": "string", "enum": list(MODES)},
            "id": {"type": "string"},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


def _ok(output: str, brief: str) -> dict[str, Any]:
    return {"is_error": False, "output": output, "message": "", "display": [{"type": "brief", "text": brief}]}


class ScheduleToolset:
    plugin = "schedules"

    def definitions(self, runtime: "SessionRuntime") -> list[dict[str, Any]]:
        # A scheduled run never schedules more: nothing replicates on its own.
        if runtime.record.kind != "session" or runtime.record.unattended:
            return []
        return [DEFINITION]

    def handles(self, name: str) -> bool:
        return name == TOOL

    async def call(self, runtime: "SessionRuntime", call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        schedules: Schedules = runtime.tenant.schedules
        action = str(arguments.get("action") or "")
        if action == "list":
            items = schedules.list()
            return _ok("\n".join(describe(i) for i in items) or "No schedules.", f"{len(items)} schedules")
        if runtime.record.permission_mode == "plan":
            return tool_error("Plan mode does not change schedules.", "Read-only")
        try:
            if action == "delete":
                item = schedules.get(str(arguments.get("id") or ""))
                if not await runtime.approve_host_action(call_id, "schedule delete", f"Delete the schedule “{item['name']}” ({item['when']})"):
                    return tool_error("The owner kept the schedule.", "Rejected")
                schedules.delete(item["id"])
                return _ok(f"Deleted “{item['name']}”.", "Deleted schedule")
            if action == "create":
                when, prompt, mode = str(arguments.get("when") or ""), str(arguments.get("prompt") or ""), str(arguments.get("mode") or "plan")
                from .when import to_cron

                to_cron(when)  # a bad `when` is the agent's to fix before the owner is asked
                description = f"Run “{arguments.get('name') or prompt[:60]}” {when} in {mode} mode, unattended"
                if not await runtime.approve_host_action(call_id, "schedule create", description, [{"type": "brief", "text": prompt[:600]}]):
                    return tool_error("The owner did not approve the schedule.", "Rejected")
                item = schedules.create(name=str(arguments.get("name") or ""), workspace=runtime.record.workspace, prompt=prompt, when=when, mode=mode, created_by="agent")
                return _ok(f"Scheduled: {describe(item)}", "Scheduled")
        except (ScheduleError, ValueError) as error:
            return tool_error(str(error), "Invalid schedule")
        return tool_error(f"Unknown action {action!r}.", "Invalid arguments")


def install(server) -> None:
    def extend(tenant) -> None:
        tenant.schedules = Schedules(tenant)
        tenant.toolsets.append(ScheduleToolset())
        tenant.closers.append(tenant.schedules.close)

    server.extensions.append(extend)
    server.scheduler = Scheduler(server)
    server.background.append(server.scheduler)


class NewSchedule(BaseModel):
    name: str = ""
    workspace: str
    prompt: str
    when: str
    mode: str = "plan"


def router(app_state):
    from fastapi import APIRouter, Depends, HTTPException

    from ..tenancy import Tenant

    api = APIRouter(prefix="/api/schedules")
    tenant_of = app_state.auth.tenant

    @api.get("")
    async def list_schedules(tenant: Tenant = Depends(tenant_of)) -> dict:
        return {"schedules": tenant.schedules.list(), "modes": list(MODES)}

    @api.post("")
    async def create(body: NewSchedule, tenant: Tenant = Depends(tenant_of)) -> dict:
        try:
            return tenant.schedules.create(**body.model_dump())
        except ScheduleError as error:
            raise HTTPException(400, str(error)) from error

    @api.patch("/{schedule_id}")
    async def update(schedule_id: str, body: dict[str, Any], tenant: Tenant = Depends(tenant_of)) -> dict:
        try:
            return tenant.schedules.update(schedule_id, body)
        except ScheduleError as error:
            raise HTTPException(400, str(error)) from error

    @api.delete("/{schedule_id}")
    async def delete(schedule_id: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        return {"removed": tenant.schedules.delete(schedule_id)}

    @api.post("/{schedule_id}/run")
    async def run(schedule_id: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        try:
            return {"session": await tenant.schedules.run_now(schedule_id)}
        except ScheduleError as error:
            raise HTTPException(400, str(error)) from error

    return api
