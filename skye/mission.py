"""Missions: the smallest multi-contract workflow - the first moment "team" is true.

A mission is a JSON file naming an ordered chain of steps, each step a job contract with its own
role and adapter. Steps after the first declare `needs`: the outcomes of those earlier steps are
folded into the next step's brief, so the developer sees the PM's scoping and the tester sees the
developer's result. Execution is sequential (one step at a time); there is deliberately no
scheduler or parallel fan-out yet.

Durability is the point: every transition is journaled to <state_dir>/missions/<id>/journal.jsonl
BEFORE it is relied on, so a crash mid-mission is recoverable:

- a step that settled is never re-run - its recorded outcome feeds later briefs;
- a step dispatched but never settled was interrupted. For the Root adapter the journal holds the
  Root job id, and resume REATTACHES to that job (polls it) instead of starting a duplicate. For
  the CLI adapters (claude-code/codex) no reattach exists - resume re-dispatches the step and the
  journal says so, honestly;
- a mission stops at needs-owner exactly like a single dispatch, writing the pending step contract
  for `skye approve`; `skye mission run` again resumes after the approval.

A failed step fails the mission (no silent retries). `skye mission reset` archives the journal so
a fixed mission can start clean.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import events
from .approvals import fingerprint
from .contract import ContractError, validate
from .dispatcher import Outcome, dispatch
from .policy import Policy

OUTPUT_TAIL = 1500


class MissionError(ValueError):
    pass


def default_state_dir() -> str:
    return os.environ.get("SKYE_STATE_DIR", "").strip() or ".skye"


def load_mission(path: str | Path) -> dict[str, Any]:
    """Read and validate a mission file. Steps are contracts minus repo (the mission's top-level
    repo applies unless a step overrides); needs must point at EARLIER steps, keeping execution a
    plain sequential chain with data dependencies, not a graph runner."""
    try:
        raw = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError) as error:
        raise MissionError(f"cannot read the mission file: {error}") from error
    if not isinstance(raw, dict):
        raise MissionError("a mission is a JSON object")
    steps = raw.get("steps")
    if not isinstance(steps, list) or not steps:
        raise MissionError("a mission needs a non-empty steps list")
    seen: set[str] = set()
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise MissionError(f"step {index} is not an object")
        step_id = str(step.get("id") or "").strip()
        if not step_id:
            raise MissionError(f"step {index} needs an id")
        if step_id in seen:
            raise MissionError(f"duplicate step id '{step_id}'")
        seen.add(step_id)
        needs = step.get("needs") or []
        if not isinstance(needs, list) or any(not isinstance(n, str) for n in needs):
            raise MissionError(f"step '{step_id}': needs must be a list of step ids")
        unknown = [n for n in needs if n not in seen]
        if unknown:
            raise MissionError(f"step '{step_id}' needs unknown or later step(s): {', '.join(unknown)}")
        candidate = {**step, "repo": step.get("repo") or raw.get("repo", "")}
        try:
            validate(candidate)
        except ContractError as error:
            raise MissionError(f"step '{step_id}': {error}") from error
    mission_id = str(raw.get("id") or "").strip() or fingerprint(
        {"goal": raw.get("goal", ""), "steps": steps})[:16]
    return {"id": mission_id, "goal": str(raw.get("goal") or "").strip(),
            "repo": str(raw.get("repo") or "").strip(), "steps": steps}


class Journal:
    """Append-only mission journal. Replaying it reconstructs where the mission actually got to;
    it, not memory, is the truth resume acts on."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, step: str, event: str, **fields: Any) -> None:
        entry = {"v": 1, "ts": time.time(), "step": step, "event": event, **fields}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def entries(self) -> list[dict[str, Any]]:
        try:
            lines = self.path.read_text("utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                out.append({"step": "", "event": "corrupt", "raw": line[:200]})
        return out

    def step_states(self) -> dict[str, dict[str, Any]]:
        """The latest settled/dispatched state per step, with the worker job id folded in."""
        states: dict[str, dict[str, Any]] = {}
        for entry in self.entries():
            step, kind = entry.get("step"), entry.get("event")
            if not step:
                continue
            if kind in ("dispatched", "settled", "needs-owner", "redispatch", "reattached"):
                if kind == "dispatched" or states.get(step, {}).get("event") != "settled":
                    states[step] = dict(entry)
                elif kind == "settled":
                    states[step] = dict(entry)
            elif kind == "worker-job" and step in states and states[step].get("event") in (
                    "dispatched", "reattached", "redispatch"):
                states[step]["worker_job"] = entry.get("worker_job", "")
        return states


@dataclass
class MissionOutcome:
    status: str  # "done" | "failed" | "gate-failed" | "needs-owner"
    mission_id: str
    reason: str = ""
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending_contract: str = ""  # written for `skye approve` when status is needs-owner


def _handoff_brief(step: dict[str, Any], outcomes: dict[str, dict[str, Any]]) -> str:
    base = str(step.get("brief") or step["goal"]).strip()
    needs = step.get("needs") or []
    if not needs:
        return base
    parts = [base, "", "## Handoff from previous steps"]
    for need in needs:
        outcome = outcomes.get(need) or {}
        parts.append(f"### {need} ({outcome.get('role', '?')}) - {outcome.get('status', '?')}")
        tail = (outcome.get("output") or "").strip()
        if tail:
            parts.append(tail[-OUTPUT_TAIL:])
    return "\n".join(parts)


def _step_contract(mission: dict[str, Any], step: dict[str, Any],
                   outcomes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    contract = {k: step[k] for k in ("goal", "role", "adapter") }
    contract["repo"] = step.get("repo") or mission["repo"]
    for optional in ("gates", "budget", "branch", "contract_keys"):
        if optional in step:
            contract[optional] = step[optional]
    contract["brief"] = _handoff_brief(step, outcomes)
    return validate(contract)


def run_mission(mission: dict[str, Any], policy: Policy | None = None, *, runner=None,
                bridge=None, state_dir: str | Path | None = None,
                approvals_path: str | Path | None = None,
                catalogues_dir: str = "catalogues") -> MissionOutcome:
    """Run or resume a mission. The journal decides what already happened; settled-done steps are
    skipped with their recorded outcomes carried into later briefs."""
    policy = policy or Policy.load()
    state_dir = Path(state_dir or default_state_dir())
    mission_dir = state_dir / "missions" / mission["id"]
    journal = Journal(mission_dir / "journal.jsonl")
    bridge = bridge if bridge is not None else events.from_env(log_path=mission_dir / "events.jsonl")
    emit = bridge.publish
    context = {"mission": mission["id"]}
    states = journal.step_states()
    outcomes: dict[str, dict[str, Any]] = {}
    for step in mission["steps"]:
        step_id = step["id"]
        context["step"] = step_id
        state = states.get(step_id, {})
        kind = state.get("event", "")
        if kind == "settled":
            if state.get("status") == "done":
                outcomes[step_id] = state  # never re-run: the recorded outcome feeds later briefs
                continue
            reason = (f"step '{step_id}' previously settled as {state.get('status')}: "
                      f"{state.get('reason', '')}. Fix the cause, then `skye mission reset` and run again.")
            emit(events.make_event("mission", {}, status="failed", reason=reason, **context))
            return MissionOutcome(status=state.get("status", "failed"), mission_id=mission["id"],
                                  reason=reason, steps=outcomes)
        contract = _step_contract(mission, step, outcomes)
        resume_job_id = ""
        if kind == "dispatched":
            prior_job = state.get("worker_job", "")
            if step["adapter"] == "root" and prior_job:
                resume_job_id = prior_job  # reattach: poll the existing Root job, never duplicate it
                journal.append(step_id, "reattached", worker_job=prior_job)
            else:
                journal.append(step_id, "redispatch",
                               reason="previous attempt interrupted before settling; no reattach for this adapter")
        journal.append(step_id, "dispatched", fingerprint=fingerprint(contract))
        emit(events.make_event("mission-step", contract, status="dispatched", **context))
        job_observer = lambda job_id, _s=step_id: journal.append(_s, "worker-job", worker_job=job_id)
        outcome: Outcome = dispatch(
            contract, policy, runner=runner, bridge=bridge, state_dir=state_dir,
            approvals_path=approvals_path, catalogues_dir=catalogues_dir,
            resume_job_id=resume_job_id or None, job_observer=job_observer,
            event_context=dict(context))
        if outcome.status == "needs-owner":
            journal.append(step_id, "needs-owner", reason=outcome.reason,
                           fingerprint=outcome.fingerprint)
            pending_path = mission_dir / f"pending-{step_id}.json"
            pending_path.write_text(json.dumps(outcome.contract, indent=2) + "\n", "utf-8")
            emit(events.make_event("mission", {}, status="needs-owner", reason=outcome.reason, **context))
            return MissionOutcome(status="needs-owner", mission_id=mission["id"],
                                  reason=(f"step '{step_id}': {outcome.reason}. Approve exactly it with "
                                          f"`skye approve {pending_path}` then `skye mission run` again."),
                                  steps=outcomes, pending_contract=str(pending_path))
        journal.append(step_id, "settled", status=outcome.status, reason=outcome.reason,
                       output=outcome.worker_output[-4000:], gates=outcome.gates,
                       worker_job=outcome.worker_job, role=step["role"])
        record = {"status": outcome.status, "reason": outcome.reason, "role": step["role"],
                  "output": outcome.worker_output[-4000:]}
        outcomes[step_id] = record
        if outcome.status != "done":
            emit(events.make_event("mission", {}, status=outcome.status,
                                   reason=f"step '{step_id}': {outcome.reason}", **context))
            return MissionOutcome(status=outcome.status, mission_id=mission["id"],
                                  reason=f"step '{step_id}': {outcome.reason}", steps=outcomes)
    emit(events.make_event("mission", {}, status="done", **context))
    return MissionOutcome(status="done", mission_id=mission["id"], steps=outcomes)


def mission_status(mission: dict[str, Any], state_dir: str | Path | None = None) -> dict[str, Any]:
    """Read-only: per-step last state from the journal plus the overall mission state."""
    state_dir = Path(state_dir or default_state_dir())
    states = Journal(state_dir / "missions" / mission["id"] / "journal.jsonl").step_states()
    steps = [{"id": step["id"], "role": step["role"], "adapter": step["adapter"],
              "state": states.get(step["id"], {}).get("event", "pending"),
              "status": states.get(step["id"], {}).get("status", ""),
              "reason": states.get(step["id"], {}).get("reason", "")}
             for step in mission["steps"]]
    if not states:
        overall = "not-started"
    elif all(s["state"] == "settled" and s["status"] == "done" for s in steps):
        overall = "done"
    else:
        last = next((s for s in reversed(steps) if s["state"] != "pending"), steps[0])
        overall = {"needs-owner": "needs-owner", "dispatched": "interrupted"}.get(
            last["state"], last["status"] or last["state"])
    return {"mission": mission["id"], "status": overall, "steps": steps}


def reset_mission(mission: dict[str, Any], state_dir: str | Path | None = None) -> str:
    """Archive the journal so the mission starts clean. The old journal is kept (renamed), never
    deleted: what happened stays on the record."""
    state_dir = Path(state_dir or default_state_dir())
    journal = state_dir / "missions" / mission["id"] / "journal.jsonl"
    if not journal.exists():
        return ""
    archived = journal.with_name(f"journal.{int(time.time())}.jsonl")
    journal.rename(archived)
    return str(archived)
