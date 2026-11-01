"""Missions: persisted DYNAMIC workflows - the first moment "team" is true.

A mission is a JSON file naming steps, each step a job contract with its own role and adapter.
The workflow is dynamic, not a fixed chain:

- `needs`: a step runs only after the steps it names, and their recorded outcomes are folded into
  its brief - the developer sees the PM's scoping, the tester sees the developer's result.
- `when`: a step can be conditional on a referenced step's settled status ({"when": {"step":
  "review", "status": "done"}}); an unmet condition SKIPS the step (journaled), and steps needing
  a skipped step skip in cascade.
- proposals: a step whose output ends with a JSON block {"propose_steps": [...]} EXTENDS the
  workflow at run time - this is how a PM's scoping becomes concrete dev/test steps without an
  LLM anywhere in the coordination path. Proposals are validated like any contract (known
  role/adapter, fresh ids, needs pointing at existing steps), capped (MAX_STEPS), journaled as
  accepted/rejected, and dispatched through the same policy gate as authored steps: an owner who
  did not pre-approve the adapter/role gets a needs-owner pause, never a surprise run.

Durability is the point: every transition is journaled to <state_dir>/missions/<id>/journal.jsonl
BEFORE it is relied on, so a crash mid-mission is recoverable:

- a step that settled (done or skipped) is never re-run - its recorded outcome feeds later briefs;
- dynamically accepted steps are rebuilt from the journal on resume, in acceptance order;
- a step dispatched but never settled was interrupted. For the Root adapter the journal holds the
  Root job id, and resume REATTACHES to that job (polls it) instead of starting a duplicate. For
  the CLI adapters (claude-code/codex) no reattach exists, and the interrupted attempt may have
  left partial external effects (half-edited files, side effects) that blindly repeating would
  compound - so resume STOPS there with status "interrupted" and waits for an explicit operator
  decision: `skye mission redispatch <file> --step <id>` (re-run that step, journaled as an
  operator-chosen redispatch) or `skye mission reset` (start clean);
- a mission stops at needs-owner exactly like a single dispatch, writing the pending step contract
  for `skye approve`; `skye mission run` again resumes after the approval.

A failed step fails the mission (no silent retries). `skye mission reset` archives the journal so
a fixed mission can start clean.
"""

from __future__ import annotations

import json
import os
import threading
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
MAX_STEPS = 32  # hard cap on authored + dynamically proposed steps: a runaway proposer stops here


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
        when = step.get("when")
        if when is not None:
            if (not isinstance(when, dict) or when.get("step") not in seen
                    or when.get("status") not in ("done", "failed", "gate-failed", "skipped")):
                raise MissionError(
                    f"step '{step_id}': when must name an earlier step and a settled status")
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
        self._lock = threading.Lock()  # parallel steps journal from worker threads

    def append(self, step: str, event: str, **fields: Any) -> None:
        entry = {"v": 1, "ts": time.time(), "step": step, "event": event, **fields}
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
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
            if kind in ("dispatched", "settled", "needs-owner", "redispatch", "reattached",
                        "skipped"):
                if kind == "dispatched" or states.get(step, {}).get("event") not in (
                        "settled", "skipped"):
                    states[step] = dict(entry)
                elif kind in ("settled", "skipped"):
                    states[step] = dict(entry)
            elif kind == "worker-job" and step in states and states[step].get("event") in (
                    "dispatched", "reattached", "redispatch"):
                states[step]["worker_job"] = entry.get("worker_job", "")
        return states

    def accepted_steps(self) -> list[dict[str, Any]]:
        """Dynamically proposed-and-accepted steps, in acceptance order (resume rebuilds the
        worklist from these; the journal, not memory, carries them across a crash)."""
        return [entry["spec"] for entry in self.entries()
                if entry.get("event") == "step-accepted" and isinstance(entry.get("spec"), dict)]


@dataclass
class MissionOutcome:
    status: str  # "done" | "failed" | "gate-failed" | "needs-owner"
    mission_id: str
    reason: str = ""
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending_contract: str = ""  # written for `skye approve` when status is needs-owner


def extract_proposals(output: str) -> list[dict[str, Any]]:
    """A worker's proposed follow-up steps: the last JSON object in its output carrying a
    "propose_steps" list. Handles the Root adapter's JSON envelope (the model's reply is a string
    field inside the result JSON) as well as raw stdout. Proposals are DATA - each is validated
    like an authored contract step before it may join the workflow; nothing in them executes."""
    texts = [output or ""]
    try:
        data = json.loads(output or "")
    except ValueError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("reply"), str):
        texts.insert(0, data["reply"])
    decoder = json.JSONDecoder()
    for text in texts:
        found = None
        for index, char in enumerate(text):
            if char != "{":
                continue
            try:
                parsed, _end = decoder.raw_decode(text, index)
            except ValueError:
                continue
            if isinstance(parsed, dict) and isinstance(parsed.get("propose_steps"), list):
                found = parsed  # the last such block wins
        if found is not None:
            return [item for item in found["propose_steps"] if isinstance(item, dict)]
    return []


def _validate_proposal(step: dict[str, Any], known_ids: set[str], mission: dict[str, Any]) -> str:
    """Why a proposed step cannot join the workflow, or '' when it can."""
    step_id = str(step.get("id") or "").strip()
    if not step_id:
        return "a proposed step needs an id"
    if step_id in known_ids:
        return f"proposed step id '{step_id}' already exists"
    needs = step.get("needs") or []
    if not isinstance(needs, list) or any(n not in known_ids for n in needs):
        return f"proposed step '{step_id}' needs unknown step(s): {needs}"
    try:
        validate({**step, "repo": step.get("repo") or mission.get("repo", "")})
    except ContractError as error:
        return f"proposed step '{step_id}': {error}"
    return ""


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
                catalogues_dir: str = "catalogues",
                redispatch: str | None = None) -> MissionOutcome:
    """``redispatch`` names an interrupted step the OPERATOR explicitly chose to re-run
    (skye mission redispatch <file> --step <id>). Without it, an interrupted step that cannot
    reattach pauses the mission instead of blindly repeating work with unknown partial effects."""
    """Run or resume a mission. The journal decides what already happened; settled-done steps are
    skipped with their recorded outcomes carried into later briefs."""
    policy = policy or Policy.load()
    state_dir = Path(state_dir or default_state_dir())
    mission_dir = state_dir / "missions" / mission["id"]
    mission_dir.mkdir(parents=True, exist_ok=True)
    # Snapshot the mission source next to the journal: the UI and later resumes read the exact
    # definition this run used, not whatever the file says after later edits.
    snapshot = mission_dir / "mission.json"
    rendered = json.dumps(mission, indent=2, sort_keys=True) + "\n"
    if not snapshot.exists() or snapshot.read_text("utf-8") != rendered:
        snapshot.write_text(rendered, "utf-8")
    journal = Journal(mission_dir / "journal.jsonl")
    bridge = bridge if bridge is not None else events.from_env(log_path=mission_dir / "events.jsonl")
    emit = bridge.publish
    context = {"mission": mission["id"]}
    states = journal.step_states()
    outcomes: dict[str, dict[str, Any]] = {}
    worklist = list(mission["steps"]) + journal.accepted_steps()  # authored, then dynamic (journaled)
    index = 0
    while index < len(worklist):
        step = worklist[index]
        index += 1
        step_id = step["id"]
        context["step"] = step_id
        state = states.get(step_id, {})
        kind = state.get("event", "")
        if kind in ("settled", "skipped"):
            if state.get("status") == "done":
                outcomes[step_id] = state  # never re-run: the recorded outcome feeds later briefs
                continue
            if kind == "skipped":
                outcomes[step_id] = {"status": "skipped", "role": step["role"],
                                     "reason": state.get("reason", ""), "output": ""}
                continue
            reason = (f"step '{step_id}' previously settled as {state.get('status')}: "
                      f"{state.get('reason', '')}. Fix the cause, then `skye mission reset` and run again.")
            emit(events.make_event("mission", {}, status="failed", reason=reason, **context))
            return MissionOutcome(status=state.get("status", "failed"), mission_id=mission["id"],
                                  reason=reason, steps=outcomes)
        # Conditional step: an unmet when skips it; needing a skipped step skips in cascade.
        when = step.get("when")
        skipped_needs = [n for n in (step.get("needs") or [])
                         if outcomes.get(n, {}).get("status") == "skipped"]
        if skipped_needs or (when and outcomes.get(when["step"], {}).get("status") != when["status"]):
            reason = (f"needs skipped step(s): {', '.join(skipped_needs)}" if skipped_needs else
                      f"when: step '{when['step']}' settled as "
                      f"'{outcomes.get(when['step'], {}).get('status', 'pending')}', "
                      f"not '{when['status']}'")
            journal.append(step_id, "skipped", status="skipped", reason=reason, role=step["role"])
            emit(events.make_event("mission-step", {"goal": step["goal"], "role": step["role"],
                                                    "adapter": step["adapter"]},
                                   status="skipped", reason=reason, **context))
            outcomes[step_id] = {"status": "skipped", "role": step["role"], "reason": reason,
                                 "output": ""}
            continue
        contract = _step_contract(mission, step, outcomes)
        resume_job_id = ""
        if kind == "dispatched":
            prior_job = state.get("worker_job", "")
            if step["adapter"] == "root" and prior_job:
                resume_job_id = prior_job  # reattach: poll the existing Root job, never duplicate it
                journal.append(step_id, "reattached", worker_job=prior_job)
            elif redispatch == step_id:
                journal.append(step_id, "redispatch",
                               reason="operator explicitly re-runs the interrupted step")
            else:
                # No reattach possible and no operator decision: STOP. The interrupted attempt
                # may have left partial external effects; silently repeating the step could
                # compound them. The operator inspects and chooses: redispatch or reset.
                orphan = ""
                if step["adapter"] == "root":
                    # Crash window: POST accepted before the job id reached the journal leaves an
                    # orphan Root job server-side. Reattach needs the id, so point the operator at
                    # Root's own job records instead of guessing a match ourselves.
                    orphan = (f" A Root job may have been submitted just before the crash without "
                              f"its id reaching the journal (dispatched at "
                              f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(state.get('ts') or 0))}); "
                              f"check Root's job records under the Root home's jobs/ directory "
                              f"for that orphan before re-running.")
                reason = (f"step '{step_id}' was interrupted before settling and cannot be "
                          f"reattached ({step['adapter']} jobs are not resumable without a "
                          f"journaled job id).{orphan} Inspect for partial effects, then choose: "
                          f"`skye mission redispatch <file> --step {step_id}` to re-run exactly "
                          f"this step, or `skye mission reset <file>` to start clean.")
                emit(events.make_event("mission", {}, status="interrupted", reason=reason, **context))
                return MissionOutcome(status="interrupted", mission_id=mission["id"],
                                      reason=reason, steps=outcomes)
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
        # Dynamic growth: a settled step may propose follow-up steps. Validated, capped, journaled
        # BEFORE they join the worklist - and dispatched later through the same policy gate.
        for proposal in extract_proposals(outcome.worker_output):
            known = {s["id"] for s in worklist} | {
                str(p.get("id")) for p in journal.accepted_steps()}
            problem = _validate_proposal(proposal, known, mission)
            proposal_id = str(proposal.get("id") or "?")
            if problem:
                journal.append(step_id, "proposal-rejected", reason=problem,
                               proposal_id=proposal_id)
                emit(events.make_event("mission-step", {}, status="proposal-rejected",
                                       reason=problem, **context))
                continue
            if len(worklist) >= MAX_STEPS:
                journal.append(step_id, "proposal-rejected", proposal_id=proposal_id,
                               reason=f"the mission already has {MAX_STEPS} steps (the cap)")
                continue
            spec = {k: proposal[k] for k in ("id", "role", "adapter", "goal")}
            for optional in ("brief", "needs", "gates", "budget", "when", "repo", "contract_keys"):
                if optional in proposal:
                    spec[optional] = proposal[optional]
            journal.append(step_id, "step-accepted", spec=spec)
            emit(events.make_event("mission-step", spec, status="step-accepted", **context))
            worklist.append(spec)
    emit(events.make_event("mission", {}, status="done", **context))
    return MissionOutcome(status="done", mission_id=mission["id"], steps=outcomes)


def mission_status(mission: dict[str, Any], state_dir: str | Path | None = None) -> dict[str, Any]:
    """Read-only: per-step last state from the journal plus the overall mission state."""
    state_dir = Path(state_dir or default_state_dir())
    journal = Journal(state_dir / "missions" / mission["id"] / "journal.jsonl")
    states = journal.step_states()
    # Dynamic steps count toward the mission's state exactly like authored ones: "done" while a
    # worker-proposed step is still running would be a lie.
    all_steps = list(mission["steps"]) + journal.accepted_steps()
    steps = [{"id": step["id"], "role": step["role"], "adapter": step["adapter"],
              "state": states.get(step["id"], {}).get("event", "pending"),
              "status": states.get(step["id"], {}).get("status", ""),
              "reason": states.get(step["id"], {}).get("reason", "")}
             for step in all_steps]
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
