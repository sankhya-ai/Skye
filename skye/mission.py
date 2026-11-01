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
from concurrent.futures import ThreadPoolExecutor, as_completed
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
MAX_PARALLEL_STEPS = 4  # wave width for independent steps; the cross-process concurrency
# registry in the dispatcher remains the real cap - this only bounds how wide one mission fans out


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
    max_rounds = raw.get("max_review_rounds")
    if max_rounds is not None and (not isinstance(max_rounds, int)
                                   or not 0 <= max_rounds <= 8):
        raise MissionError("max_review_rounds must be an integer 0-8")
    return {"id": mission_id, "goal": str(raw.get("goal") or "").strip(),
            "repo": str(raw.get("repo") or "").strip(), "steps": steps,
            "project": str(raw.get("project") or "").strip(),
            "max_review_rounds": max_rounds}


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
                        "skipped", "review-round"):
                if kind == "dispatched" or states.get(step, {}).get("event") not in (
                        "settled", "skipped", "review-round"):
                    states[step] = dict(entry)
                elif kind in ("settled", "skipped", "review-round"):
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


def extract_feature_proposals(output: str) -> list[dict[str, Any]]:
    """A worker's proposed FEATURES for the team backlog: the last JSON object carrying a
    "propose_features" list. Same envelope handling as step proposals; entries are data for the
    owner's queue - proposing never starts work."""
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
            if isinstance(parsed, dict) and isinstance(parsed.get("propose_features"), list):
                found = parsed
        if found is not None:
            return [item for item in found["propose_features"] if isinstance(item, dict)]
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


def _step_claims(step: dict[str, Any]) -> list[str]:
    """The step's declared write-paths, lightly normalized (load_mission already validated them
    through the contract validator)."""
    return [c.strip().replace("\\", "/").lstrip("./").rstrip("/")
            for c in (step.get("claims") or []) if str(c).strip()]


def _handoff_brief(step: dict[str, Any], outcomes: dict[str, dict[str, Any]], *,
                   mission_id: str = "", discussion_tail: str = "",
                   unacked: list[dict[str, Any]] | None = None) -> str:
    base = str(step.get("brief") or step["goal"]).strip()
    parts = [base]
    if step.get("review_feedback"):
        parts += ["", "## Review feedback - the reviewer sent this back; address every point",
                  str(step["review_feedback"])[:OUTPUT_TAIL]]
    if unacked:
        parts += ["", "## Unacked contract changes for your role",
                  "Read each one and ack with `skye ack` before building on it - or object on "
                  "the team line instead of silently adapting:"]
        for change in unacked[:4]:
            parts.append(f"- {change.get('id')} by {change.get('author')}: "
                         f"{str(change.get('body') or '')[:300]}")
    needs = step.get("needs") or []
    if needs:
        parts += ["", "## Handoff from previous steps"]
        for need in needs:
            outcome = outcomes.get(need) or {}
            parts.append(f"### {need} ({outcome.get('role', '?')}) - {outcome.get('status', '?')}")
            tail = (outcome.get("output") or "").strip()
            if tail:
                parts.append(tail[-OUTPUT_TAIL:])
    if discussion_tail:
        parts += ["", "## Team line (latest)", discussion_tail]
    if mission_id:
        say_cmd = f"skye say {mission_id} --as {step['role']}"
        parts += ["", "## Talking to the team",
                  f"This mission's team line: post with `{say_cmd} 'message'` "
                  "(kind contract-change with --ack-needed ROLE when you change an interface "
                  f"others depend on); read new posts with `skye hear {mission_id} --since <cursor>`. "
                  "Running teammates do not see your post until they read it - post early, "
                  "not after finishing."]
    return "\n".join(parts)


def _step_contract(mission: dict[str, Any], step: dict[str, Any],
                   outcomes: dict[str, dict[str, Any]], *,
                   discussion_tail: str = "",
                   unacked: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    contract = {k: step[k] for k in ("goal", "role", "adapter") }
    contract["repo"] = step.get("repo") or mission["repo"]
    for optional in ("gates", "budget", "branch", "contract_keys", "claims"):
        if optional in step:
            contract[optional] = step[optional]
    contract["brief"] = _handoff_brief(step, outcomes, mission_id=mission["id"],
                                       discussion_tail=discussion_tail, unacked=unacked)
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
    # Artifact baseline: snapshot the mission repo's file manifest once, at the FIRST run of
    # this mission (a resume keeps the original baseline - re-baselining would quietly excuse
    # changes from earlier attempts). The end snapshot lands when the mission settles done, so
    # the UI can show what the team actually changed, derived from the tree itself.
    artifacts_path = mission_dir / "artifacts.json"
    if mission.get("repo") and not artifacts_path.exists():
        from .journey import repo_manifest
        start_manifest, start_truncated = repo_manifest(mission["repo"])
        artifacts_path.write_text(json.dumps(
            {"repo": mission["repo"], "start": start_manifest,
             "start_truncated": start_truncated}) + "\n", "utf-8")
    bridge = bridge if bridge is not None else events.from_env(log_path=mission_dir / "events.jsonl")
    emit = bridge.publish
    context = {"mission": mission["id"]}
    states = journal.step_states()
    outcomes: dict[str, dict[str, Any]] = {}
    worklist = list(mission["steps"]) + journal.accepted_steps()  # authored, then dynamic (journaled)

    # Seed from the journal: states a prior run already settled are carried, never re-run.
    pending: dict[str, dict[str, Any]] = {}
    for step in worklist:
        step_id = step["id"]
        state = states.get(step_id, {})
        kind = state.get("event", "")
        if kind == "settled":
            if state.get("status") == "done":
                outcomes[step_id] = state  # the recorded outcome feeds later briefs
                continue
            reason = (f"step '{step_id}' previously settled as {state.get('status')}: "
                      f"{state.get('reason', '')}. Fix the cause, then `skye mission reset` and run again.")
            emit(events.make_event("mission", {}, status="failed", reason=reason, **context))
            return MissionOutcome(status=state.get("status", "failed"), mission_id=mission["id"],
                                  reason=reason, steps=outcomes)
        if kind == "skipped":
            outcomes[step_id] = {"status": "skipped", "role": step["role"],
                                 "reason": state.get("reason", ""), "output": ""}
            continue
        if kind == "review-round":
            # Sent back by the reviewer before the crash: pending again, with the feedback.
            pending[step_id] = {**step, "review_feedback": state.get("feedback", "")}
            continue
        pending[step_id] = step

    # Wave scheduler: independent steps (needs all terminal) dispatch CONCURRENTLY, capped at
    # MAX_PARALLEL_STEPS; dependency order, skip cascades, the policy gate and the journal keep
    # their exact sequential semantics. Settlement is processed in worklist order on the main
    # thread, so the journal's settled/skip/proposal sequence is deterministic.
    while pending:
        ready: list[dict[str, Any]] = []
        resume_jobs: dict[str, str] = {}
        interrupted_reason = ""
        skip_progress = False
        for step_id, step in pending.items():
            needs = step.get("needs") or []
            when = step.get("when")
            refs = list(needs) + ([when["step"]] if when else [])
            if not all(ref in outcomes for ref in refs):
                continue  # dependencies not terminal yet
            skipped_needs = [n for n in needs if outcomes.get(n, {}).get("status") == "skipped"]
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
                skip_progress = True
                continue
            kind = states.get(step_id, {}).get("event", "")
            if kind in ("dispatched", "reattached", "redispatch"):
                prior_job = states.get(step_id, {}).get("worker_job", "")
                if redispatch == step_id:
                    # The operator's explicit choice beats reattach, even with a prior job id
                    # on record: they inspected and chose a FRESH run. Journaled as their
                    # decision (with the superseded job id), never as a reattach, and no
                    # resume_jobs entry - the stale job must not be polled as if it were
                    # this attempt.
                    journal.append(step_id, "redispatch",
                                   reason="operator explicitly re-runs the interrupted step",
                                   superseded_job=prior_job or "")
                    ready.append(step)
                elif step["adapter"] == "root" and prior_job:
                    # Reattach only to a job the Root server STILL holds. If the journaled job
                    # is gone (wiped/replaced Root home) or the server cannot confirm it, the
                    # attempt's side effects are unknown: pause for the operator. Never settle
                    # this as a plain failure (which terminates the resume choices) and never
                    # fall through to a fresh launch under partial-effects uncertainty.
                    from . import adapters as _adapters
                    try:
                        prior_state = _adapters.get("root").job_state(prior_job)
                    except Exception as error:
                        if not interrupted_reason:
                            interrupted_reason = (
                                f"step '{step_id}' cannot reattach: the Root server could not "
                                f"confirm journaled job {prior_job} ({error}). Its side effects are "
                                "unknown and no duplicate was launched. Restore the server and "
                                "resume, or choose: "
                                f"`skye mission redispatch <file> --step {step_id}` to re-run the "
                                "step, or `skye mission reset <file>` to start clean.")
                    else:
                        if prior_state is None:
                            if not interrupted_reason:
                                interrupted_reason = (
                                    f"step '{step_id}' was attached to Root job {prior_job}, but "
                                    "that job no longer exists on the Root server (the Root home "
                                    "was likely wiped or replaced). Its side effects are unknown "
                                    "and no duplicate was launched. Inspect for partial effects, "
                                    "then choose: "
                                    f"`skye mission redispatch <file> --step {step_id}` to re-run "
                                    "the step, or `skye mission reset <file>` to start clean.")
                        else:
                            resume_jobs[step_id] = prior_job  # reattach: poll the existing Root job, never duplicate it
                            journal.append(step_id, "reattached", worker_job=prior_job)
                            ready.append(step)
                elif not interrupted_reason:
                    # No reattach possible and no operator decision: STOP. The interrupted attempt
                    # may have left partial external effects; silently repeating the step could
                    # compound them. The operator inspects and chooses: redispatch or reset.
                    orphan = ""
                    if step["adapter"] == "root":
                        # Crash window: POST accepted before the job id reached the journal leaves
                        # an orphan Root job server-side. Reattach needs the id, so point the
                        # operator at Root's own job records instead of guessing a match ourselves.
                        orphan = (f" A Root job may have been submitted just before the crash without "
                                  f"its id reaching the journal (dispatched at "
                                  f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(states.get(step_id, {}).get('ts') or 0))}); "
                                  f"check Root's job records under the Root home's jobs/ directory "
                                  f"for that orphan before re-running.")
                    interrupted_reason = (f"step '{step_id}' was interrupted before settling and cannot be "
                                          f"reattached ({step['adapter']} jobs are not resumable without a "
                                          f"journaled job id).{orphan} Inspect for partial effects, then choose: "
                                          f"`skye mission redispatch <file> --step {step_id}` to re-run exactly "
                                          f"this step, or `skye mission reset <file>` to start clean.")
            else:
                ready.append(step)
        # Claims gate: a step whose declared write-paths intersect another selected step's
        # claims - or a RUNNING dispatch's claims anywhere on this state dir - waits for a
        # later wave. Worklist order wins; held steps stay pending and are retried after the
        # wave settles. This is the interference guard for parallel work: colliding writes are
        # prevented by serialization, not merely logged after the fact.
        held_reasons: list[str] = []
        if ready:
            from .contract import claims_conflict
            from .registry import Registry
            live_claims: list[tuple[list[str], str]] = []
            try:
                for record in Registry(state_dir).others():
                    if record.get("claims"):
                        live_claims.append((list(record["claims"]),
                                            f"{record.get('role', '?')} "
                                            f"({record.get('goal', '')[:60]})"))
            except OSError:
                pass  # no registry dir yet: no live claims exist
            selected: list[dict[str, Any]] = []
            selected_claims: list[tuple[list[str], str]] = []
            for step in ready:
                claims = _step_claims(step)
                conflict = None
                if claims:
                    for other_claims, who in selected_claims + live_claims:
                        hit = claims_conflict(claims, other_claims)
                        if hit:
                            conflict = f"claims '{hit[0]}' intersects '{hit[1]}' held by {who}"
                            break
                if conflict:
                    journal.append(step["id"], "claim-held", reason=conflict, role=step["role"])
                    emit(events.make_event("mission-step", {"goal": step["goal"],
                                                            "role": step["role"],
                                                            "adapter": step["adapter"]},
                                           status="claim-held", reason=conflict, **context))
                    held_reasons.append(f"{step['id']}: {conflict}")
                else:
                    selected.append(step)
                    if claims:
                        selected_claims.append((claims, f"step {step['id']}"))
            ready = selected
        # Skips settled above remove steps from consideration; drop them from pending now.
        for step_id in list(pending):
            if step_id in outcomes:
                del pending[step_id]
        for step in ready:
            if step["id"] in pending:
                del pending[step["id"]]
        if not ready:
            if skip_progress:
                continue  # a skip-only wave: dependencies settled, next wave re-evaluates
            if interrupted_reason:
                emit(events.make_event("mission", {}, status="interrupted",
                                       reason=interrupted_reason, **context))
                return MissionOutcome(status="interrupted", mission_id=mission["id"],
                                      reason=interrupted_reason, steps=outcomes)
            # Nothing runnable because every ready step is claim-held against dispatches
            # still running elsewhere: transient, not a failure - re-run when they settle.
            if held_reasons:
                reason = ("every runnable step is claim-held against work still running: "
                          + "; ".join(held_reasons)
                          + ". Re-run `skye mission run` once the holder settles.")
                emit(events.make_event("mission", {}, status="blocked-claims", reason=reason, **context))
                return MissionOutcome(status="blocked-claims", mission_id=mission["id"],
                                      reason=reason, steps=outcomes)
            # Nothing ready and nothing running: every remaining step waits on a need that can no
            # longer settle (its dependency failed or was skipped in a way validation missed).
            leftover = ", ".join(sorted(pending))
            reason = f"no runnable steps remain; blocked: {leftover}"
            emit(events.make_event("mission", {}, status="failed", reason=reason, **context))
            return MissionOutcome(status="failed", mission_id=mission["id"], reason=reason,
                                  steps=outcomes)

        # Dispatch this wave concurrently. Contracts, briefs and the journal's dispatched entries
        # are prepared in worklist order on the main thread; only the blocking dispatch() calls
        # (worker run + gates) run on worker threads.
        from . import discuss
        discussion_tail = discuss.tail_text(state_dir, mission["id"])
        wave_roles = [s["role"] for s in ready]
        unacked = discuss.unacked(state_dir, mission["id"], wave_roles)
        contracts: dict[str, dict[str, Any]] = {}
        for step in ready:
            step_id = step["id"]
            context["step"] = step_id
            contracts[step_id] = _step_contract(
                mission, step, outcomes, discussion_tail=discussion_tail,
                unacked=[c for c in unacked if step["role"] in (c.get("waiting_on") or [])])
            journal.append(step_id, "dispatched", fingerprint=fingerprint(contracts[step_id]))
            emit(events.make_event("mission-step", contracts[step_id], status="dispatched", **context))

        def _run_one(step: dict[str, Any]) -> Outcome:
            step_id = step["id"]
            job_observer = lambda job_id, _s=step_id: journal.append(_s, "worker-job", worker_job=job_id)
            return dispatch(
                contracts[step_id], policy, runner=runner, bridge=bridge, state_dir=state_dir,
                approvals_path=approvals_path, catalogues_dir=catalogues_dir,
                resume_job_id=resume_jobs.get(step_id) or None, job_observer=job_observer,
                event_context=dict(context, step=step_id))

        # Wave width is bounded by BOTH the conservative module cap and the owner's policy:
        # policy.max_concurrent (default 1) is the team-wide concurrency the owner actually
        # allowed, so a default policy keeps missions strictly sequential. The cross-process
        # registry in the dispatcher remains the enforcement of last resort.
        width = max(1, min(MAX_PARALLEL_STEPS, policy.max_concurrent))
        results: dict[str, Outcome] = {}
        with ThreadPoolExecutor(max_workers=width, thread_name_prefix="skye-wave") as pool:
            futures = {pool.submit(_run_one, step): step["id"] for step in ready}
            for future in as_completed(futures):
                results[futures[future]] = future.result()

        # Settle in worklist order: deterministic journal sequence, and the first
        # needs-owner/failure in dependency order is the one the mission reports.
        wave_needs_owner: tuple[str, Outcome] | None = None
        wave_failed: tuple[str, Outcome] | None = None
        for step in ready:
            step_id = step["id"]
            context["step"] = step_id
            outcome = results[step_id]
            if outcome.status == "claim-held":
                # Another process claimed overlapping write-paths first (atomic registry check
                # at dispatch). Hold the step: no settled entry, retried on the next wave or
                # run. If every runnable step ends up held, the wave-loop exits blocked-claims.
                journal.append(step_id, "claim-held", reason=outcome.reason, role=step["role"])
                pending[step_id] = step
                continue
            if outcome.status == "needs-owner":
                journal.append(step_id, "needs-owner", reason=outcome.reason,
                               fingerprint=outcome.fingerprint)
                pending_path = mission_dir / f"pending-{step_id}.json"
                pending_path.write_text(json.dumps(outcome.contract, indent=2) + "\n", "utf-8")
                if wave_needs_owner is None:
                    wave_needs_owner = (step_id, outcome)
                continue
            # Review send-back: a reviewer asking for changes re-queues the step with the
            # feedback, bounded per step by max_review_rounds and journaled every round - a
            # redo edge, never a silent retry. No "settled" lands, so a crash mid-loop resumes
            # into the redo (the journal's review-round entry re-seeds the feedback).
            if outcome.status == "gate-failed":
                review_gate = next((g for g in outcome.gates
                                    if g.get("gate") == "review" and g.get("changes_requested")),
                                   None)
                if review_gate is not None:
                    max_rounds = int(mission.get("max_review_rounds")
                                     if mission.get("max_review_rounds") is not None
                                     else policy.max_review_rounds)
                    rounds = sum(1 for e in journal.entries()
                                 if e.get("step") == step_id and e.get("event") == "review-round")
                    if rounds < max_rounds:
                        feedback = str(review_gate.get("feedback") or review_gate.get("detail") or "")
                        journal.append(step_id, "review-round", round=rounds + 1,
                                       feedback=feedback[:2000], role=step["role"])
                        emit(events.make_event("mission-step", contracts[step_id],
                                               status="review-round", reason=feedback[:160],
                                               **context))
                        pending[step_id] = {**step, "review_feedback": feedback[:2000]}
                        continue
            journal.append(step_id, "settled", status=outcome.status, reason=outcome.reason,
                           output=outcome.worker_output[-4000:], gates=outcome.gates,
                           worker_job=outcome.worker_job, role=step["role"])
            outcomes[step_id] = {"status": outcome.status, "reason": outcome.reason,
                                 "role": step["role"], "output": outcome.worker_output[-4000:],
                                 "gates": outcome.gates}
            if outcome.status != "done":
                if wave_failed is None:
                    wave_failed = (step_id, outcome)
                continue
            # Feature proposals for the owner queue: journaled and persisted, never started.
            from . import backlog
            raw_features = extract_feature_proposals(outcome.worker_output)
            # Filter like contracts before they reach the owner queue: a feature needs a
            # title and a brief. One malformed entry must not sink the valid ones.
            valid_features = [f for f in raw_features
                              if str(f.get("title") or "").strip()
                              and str(f.get("brief") or "").strip()]
            dropped = len(raw_features) - len(valid_features)
            if dropped:
                journal.append(step_id, "features-rejected",
                               reason=f"{dropped} proposal(s) missing a title or brief")
            try:
                proposed_features = backlog.propose(
                    valid_features,
                    project=mission.get("project", ""), mission=mission["id"])
            except backlog.BacklogError as error:
                proposed_features = []
                journal.append(step_id, "features-rejected", reason=str(error))
            for feature in proposed_features:
                journal.append(step_id, "feature-proposed", feature_id=feature["id"],
                               title=feature["title"])
                emit(events.make_event("mission-step", {}, status="feature-proposed",
                                       feature=feature["title"], **context))
            # Agent proposals: a step may propose new specialist DEFINITIONS ({"propose_agents":
            # [...]}). They land in the roles registry as PROPOSED - visible to the owner,
            # unusable by contracts until promoted. A proposed step naming an unpromoted agent
            # is rejected by ordinary contract validation; the owner promotes first.
            from . import agents
            accepted_agents, rejected_agents = agents.propose(
                agents.extract_proposals(outcome.worker_output), mission=mission["id"])
            for record in accepted_agents:
                journal.append(step_id, "agent-accepted", agent=record["name"])
                emit(events.make_event("mission-step", {}, status="agent-accepted",
                                       reason=f"proposed agent '{record['name']}' awaits owner "
                                              "promotion", **context))
            for row in rejected_agents:
                journal.append(step_id, "agent-rejected", agent=row["name"], reason=row["reason"])
            # Dynamic growth: a settled step may propose follow-up steps. Validated, capped,
            # journaled BEFORE they join the workflow - and dispatched later through the same
            # policy gate.
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
                for optional in ("brief", "needs", "gates", "budget", "when", "repo",
                                 "contract_keys", "claims"):
                    if optional in proposal:
                        spec[optional] = proposal[optional]
                journal.append(step_id, "step-accepted", spec=spec)
                emit(events.make_event("mission-step", spec, status="step-accepted", **context))
                worklist.append(spec)
                pending[spec["id"]] = spec
        if wave_needs_owner is not None:
            step_id, outcome = wave_needs_owner
            pending_path = mission_dir / f"pending-{step_id}.json"
            emit(events.make_event("mission", {}, status="needs-owner", reason=outcome.reason, **context))
            return MissionOutcome(status="needs-owner", mission_id=mission["id"],
                                  reason=(f"step '{step_id}': {outcome.reason}. Approve exactly it with "
                                          f"`skye approve {pending_path}` then `skye mission run` again."),
                                  steps=outcomes, pending_contract=str(pending_path))
        if wave_failed is not None:
            step_id, outcome = wave_failed
            emit(events.make_event("mission", {}, status=outcome.status,
                                   reason=f"step '{step_id}': {outcome.reason}", **context))
            return MissionOutcome(status=outcome.status, mission_id=mission["id"],
                                  reason=f"step '{step_id}': {outcome.reason}", steps=outcomes)
    emit(events.make_event("mission", {}, status="done", **context))
    from . import backlog
    # Delivery needs journey evidence: scan the journaled gates for a passed user-journey
    # replay. Without one the feature waits as awaiting-verification - 'done' is not
    # 'delivered'.
    journey_ok, journey_evidence = False, ""
    for step_id, record in outcomes.items():
        for gate in record.get("gates") or []:
            if gate.get("gate") == "journey" and gate.get("ok"):
                journey_ok = True
                journey_evidence = str(gate.get("evidence") or gate.get("detail") or "")
    if mission.get("repo") and artifacts_path.exists():
        from .journey import repo_manifest
        end_manifest, end_truncated = repo_manifest(mission["repo"])
        record = json.loads(artifacts_path.read_text("utf-8"))
        record["end"] = end_manifest
        record["end_truncated"] = end_truncated
        artifacts_path.write_text(json.dumps(record) + "\n", "utf-8")
    backlog.mark_delivered(mission["id"], journey_ok=journey_ok, evidence=journey_evidence)
    # The ticket is the permanent home: the mission's team-line discussion folds into its
    # thread so a later repair reads the working conversation, not just the outcomes.
    from . import discuss
    folded = backlog.fold_discussion(
        mission["id"], discuss.read(state_dir, mission["id"], since=0, limit=500)["entries"])
    if folded:
        emit(events.make_event("mission", {}, status="discussion-folded",
                               reason=f"{folded} team-line entr(ies) folded into the ticket",
                               **context))
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
    else:
        # Aggregate, not "last step wins": with parallel waves an early step can pause
        # needs-owner while a later independent step settles done - the mission is still paused.
        kinds = [s["state"] for s in steps]
        settled_not_done = [s for s in steps if s["state"] == "settled" and s["status"] != "done"]
        if "needs-owner" in kinds:
            overall = "needs-owner"
        elif any(k in ("dispatched", "reattached", "redispatch") for k in kinds):
            overall = "interrupted"  # in-flight at crash; the UI overlays "running" when live
        elif settled_not_done:
            overall = settled_not_done[0]["status"] or "failed"
        elif all(s["state"] == "settled" and s["status"] == "done" for s in steps):
            overall = "done"
        elif any(s["state"] == "skipped" for s in steps) and all(
                s["state"] in ("settled", "skipped") for s in steps):
            overall = "done"  # everything terminal, nothing failed
        else:
            overall = "running"
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
