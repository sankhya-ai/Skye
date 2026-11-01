"""The dispatcher: plain code, no LLM anywhere in the coordination path.

Flow per contract: validate -> register in the running registry (when a state dir is configured)
-> policy check (standing rule OR a consumed one-time owner approval; the concurrency cap is
never approval-overridable) -> resolve the role's tool catalogue -> run the worker adapter (with
the contract's timeout budget wired through, and unenforceable budget fields reported as notes,
never silently dropped) -> run the quality gates in order. The "tests" gate runs the POLICY's test
command (never a contract-supplied one). The "review" gate dispatches a reviewer contract through
the same dispatch path and requires an explicit approve verdict - a second worker looks at the
diff; Skye itself reads and judges nothing beyond that verdict marker.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import adapters, approvals, events, gateway, review
from .contract import validate
from .policy import Policy
from .registry import Registry
from skye import journey


@dataclass
class Outcome:
    status: str  # "done" | "needs-owner" | "failed" | "gate-failed"
    contract: dict[str, Any]
    reason: str = ""
    worker_output: str = ""
    gates: list[dict[str, Any]] = field(default_factory=list)
    fingerprint: str = ""
    worker_job: str = ""  # the worker's own job id (Root), for audit and crash recovery
    budget_notes: list[str] = field(default_factory=list)


def dispatch(contract: dict[str, Any], policy: Policy | None = None, running: int | None = None,
             runner=None, catalogues_dir: str = "catalogues", bridge=None,
             state_dir: str | Path | None = None, approvals_path: str | Path | None = None,
             resume_job_id: str | None = None, job_observer=None,
             event_context: dict[str, Any] | None = None) -> Outcome:
    """Run one contract through policy, worker and gates. ``runner`` replaces the adapter call in
    tests; production code never passes it. ``bridge`` (default: events.from_env()) receives the
    lifecycle events this dispatch emits - the Chamber line when configured, else the local log.
    ``state_dir`` enables the running registry (the real max_concurrent enforcement); without it
    the ``running`` count is taken as given. ``resume_job_id`` reattaches to an existing Root job
    instead of starting a new one (crash recovery; Root adapter only)."""
    policy = policy or Policy.load()
    bridge = bridge if bridge is not None else events.from_env()
    context = dict(event_context or {})

    def emit(kind: str, spec: dict[str, Any], **fields: Any) -> None:
        bridge.publish(events.make_event(kind, spec, **context, **fields))

    try:
        spec = validate(contract)
    except Exception as error:
        emit("failed", contract, status="failed", reason=str(error))
        return Outcome(status="failed", contract=contract, reason=str(error))
    fp = approvals.fingerprint(spec)

    registry = None
    live = int(running or 0)
    if state_dir is not None:
        try:
            registry = Registry(state_dir)
            live = registry.acquire_and_count(spec, fp)  # atomic: the cap check below holds
        except OSError as error:
            # The cap cannot be counted without the registry: say so, and fall back to the count
            # we were given rather than pretending the cap was checked.
            emit("registry-error", spec, reason=f"running registry unavailable ({error}); the "
                                                "concurrency cap falls back to the caller's count")
            registry = None
    try:
        blocked_kind, blocked = policy.check(spec, running=live)
        approved = None
        if blocked_kind == "rule":
            approved = approvals.peek(spec, approvals_path)
            if approved:
                blocked_kind, blocked = "ok", ""
        if blocked_kind != "ok":
            hint = (f" Contract fingerprint {fp[:12]}: to run exactly this contract once, the owner "
                    f"runs `skye approve <job.json>`.") if blocked_kind == "rule" else ""
            emit("needs-owner", spec, status="needs-owner", reason=blocked, fingerprint=fp)
            return Outcome(status="needs-owner", contract=spec, reason=blocked + hint, fingerprint=fp)
        catalogue = gateway.resolve(spec["role"], catalogues_dir)
        spec["tools"] = catalogue["tools"]
        spec["tools_writable"] = catalogue["writable"]
        adapter = adapters.get(spec["adapter"])
        if job_observer is not None:
            adapter.job_observer = job_observer
        if resume_job_id and spec["adapter"] != "root" and runner is None:
            reason = (f"crash recovery cannot reattach on the {spec['adapter']} adapter: only Root "
                      "jobs are resumable; re-dispatch this contract instead")
            emit("failed", spec, status="failed", reason=reason, fingerprint=fp)
            return Outcome(status="failed", contract=spec, reason=reason, fingerprint=fp)
        if approved is not None:
            consumed = approvals.consume(spec, approvals_path)  # single-use, consumed on start
            emit("approved", spec, status="running", fingerprint=fp,
                 reason=f"one-time owner approval consumed (approved at {consumed.get('approved_at', '?')})"
                 if consumed else "one-time owner approval")
        enforced = set(getattr(adapter, "ENFORCED_BUDGET", ("timeout_seconds",)))
        forwarded = set(getattr(adapter, "FORWARDED_BUDGET", ()))
        notes = []
        for key in spec["budget"]:
            if key in enforced:
                continue
            if key in forwarded:
                notes.append(f"budget field '{key}' is forwarded to the {spec['adapter']} worker's "
                             "own budget machinery; its enforcement depends on that worker's "
                             "configuration (e.g. provider-reported pricing), so Skye does not "
                             "guarantee it as a hard bound")
            else:
                notes.append(f"budget field '{key}' is not enforceable on the {spec['adapter']} "
                             "adapter; it is recorded but does not limit the worker")
        timeout = int(spec["budget"].get("timeout_seconds") or 1800)
        emit("accepted", spec, status="running", authorized_via="approval" if approved else "rule",
             budget_notes=notes, fingerprint=fp)
        if runner is not None:
            run = runner
        elif resume_job_id:
            run = lambda s: adapter.run(s, timeout=timeout, resume_job_id=resume_job_id)
        else:
            run = lambda s: adapter.run(s, timeout=timeout)
        try:
            result = run(spec)
        except Exception as error:
            emit("failed", spec, status="failed", reason=str(error), fingerprint=fp)
            return Outcome(status="failed", contract=spec, reason=str(error), fingerprint=fp,
                           budget_notes=notes)
        worker_job = getattr(result, "job_id", "") or ""
        outcome = Outcome(status="done", contract=spec, worker_output=result.output,
                          fingerprint=fp, worker_job=worker_job, budget_notes=notes)
        if not result.ok:
            outcome.status = "failed"
            outcome.reason = f"worker exited {result.returncode}"
            emit("failed", spec, status="failed", reason=outcome.reason, worker_job=worker_job,
                 fingerprint=fp)
            return outcome
        for gate in spec["gates"]:
            outcome.gates.append(_run_gate(gate, spec, policy, runner, bridge=bridge,
                                           approvals_path=approvals_path, event_context=context))
            emit("gate", spec, status="ok" if outcome.gates[-1]["ok"] else "failed",
                 gate=gate, reason=outcome.gates[-1]["detail"], worker_job=worker_job,
                 fingerprint=fp)
            if not outcome.gates[-1]["ok"]:
                outcome.status = "gate-failed"
                outcome.reason = outcome.gates[-1]["detail"]
                return outcome
        emit("done", spec, status="done", worker_job=worker_job, fingerprint=fp)
        return outcome
    finally:
        if registry is not None:
            registry.release()


def _run_gate(gate: str, spec: dict[str, Any], policy: Policy, runner=None, bridge=None,
              approvals_path: str | Path | None = None,
              event_context: dict[str, Any] | None = None) -> dict[str, Any]:
    if gate == "journey":
        # The delivery bar the owner set: replay the affected USER JOURNEY, not just tests.
        # The command is owner-configured (policy.journey_command); its output tail is the
        # recorded evidence. No command configured means the journey CANNOT be verified -
        # fail honestly, never wave it through.
        if not policy.journey_command:
            return {"gate": "journey", "ok": False,
                    "detail": "the team policy names no journey command; the journey cannot be replayed"}
        result = journey.run_journey(policy.journey_command, cwd=spec["repo"],
                                     timeout=policy.journey_timeout_seconds)
        return {"gate": "journey", "ok": result["passed"],
                "detail": ("journey replay passed"
                           if result["passed"]
                           else ("journey replay timed out" if result["timed_out"]
                                 else f"journey replay failed ({result['returncode']})")),
                "command": policy.journey_command, "evidence": result["evidence"]}
    if gate == "tests":
        if not policy.test_command:
            return {"gate": "tests", "ok": False,
                    "detail": "the team policy names no test command; the tests gate cannot run"}
        proc = subprocess.run(policy.test_command, cwd=spec["repo"], shell=True,
                              capture_output=True, text=True, timeout=1800)
        return {"gate": "tests", "ok": proc.returncode == 0,
                "detail": "tests passed" if proc.returncode == 0 else f"tests failed ({proc.returncode})",
                "command": policy.test_command}
    if gate == "review":
        # A reviewer CONTRACT through the same dispatch path: a second worker judges the diff, so
        # no worker grades its own work and Skye judges nothing itself. The reviewer runs inside
        # this dispatch's concurrency slot (sequentially, after the worker finished - real
        # concurrency stays one), and its verdict decides the gate: only an explicit "approve"
        # passes; "reject", a missing verdict, or a reviewer that never finished all fail.
        verdict = dispatch(review.build_review_contract(spec, policy.review_role or "reviewer"),
                           policy, runner=runner, bridge=bridge, approvals_path=approvals_path,
                           event_context=event_context)
        tail = verdict.worker_output[-500:]
        if verdict.status != "done":
            return {"gate": "review", "ok": False,
                    "detail": f"the reviewer did not finish: {verdict.reason}", "reviewer": tail}
        decision, reasons = review.parse_verdict(verdict.worker_output)
        if decision == "pass":
            return {"gate": "review", "ok": True, "detail": "reviewer passed the changes",
                    "reviewer": tail}
        if decision == "block":
            return {"gate": "review", "ok": False,
                    "detail": f"reviewer blocked the changes: {reasons or 'no reasons given'}",
                    "reviewer": tail}
        return {"gate": "review", "ok": False,
                "detail": "the reviewer finished without a parseable verdict; the review gate "
                          "requires an explicit approve", "reviewer": tail}
    return {"gate": gate, "ok": False, "detail": f"unknown gate '{gate}'"}
