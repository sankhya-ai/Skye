"""The dispatcher: plain code, no LLM anywhere in the coordination path.

Flow per contract: validate -> policy check -> resolve the role's tool catalogue -> run the worker
adapter -> run the quality gates in order. The "tests" gate runs the POLICY's test command (never a
contract-supplied one). The "review" gate dispatches a reviewer contract through the same policy -
a second worker looks at the diff; Skye itself reads and judges nothing.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Any

from . import adapters, gateway
from .contract import validate
from .policy import Policy


@dataclass
class Outcome:
    status: str  # "done" | "needs-owner" | "failed" | "gate-failed"
    contract: dict[str, Any]
    reason: str = ""
    worker_output: str = ""
    gates: list[dict[str, Any]] = field(default_factory=list)


def dispatch(contract: dict[str, Any], policy: Policy | None = None, running: int = 0,
             runner=None, catalogues_dir: str = "catalogues") -> Outcome:
    """Run one contract through policy, worker and gates. ``runner`` replaces the adapter call in
    tests; production code never passes it."""
    policy = policy or Policy.load()
    try:
        spec = validate(contract)
    except Exception as error:
        return Outcome(status="failed", contract=contract, reason=str(error))
    blocked = policy.unattended_ok(spec, running=running)
    if blocked:
        return Outcome(status="needs-owner", contract=spec, reason=blocked)
    catalogue = gateway.resolve(spec["role"], catalogues_dir)
    spec["tools"] = catalogue["tools"]
    spec["tools_writable"] = catalogue["writable"]
    adapter = adapters.get(spec["adapter"])
    run = runner or adapter.run
    try:
        result = run(spec)
    except Exception as error:
        return Outcome(status="failed", contract=spec, reason=str(error))
    outcome = Outcome(status="done", contract=spec, worker_output=result.output)
    if not result.ok:
        outcome.status = "failed"
        outcome.reason = f"worker exited {result.returncode}"
        return outcome
    for gate in spec["gates"]:
        outcome.gates.append(_run_gate(gate, spec, policy, runner))
        if not outcome.gates[-1]["ok"]:
            outcome.status = "gate-failed"
            outcome.reason = outcome.gates[-1]["detail"]
            return outcome
    return outcome


def _run_gate(gate: str, spec: dict[str, Any], policy: Policy, runner=None) -> dict[str, Any]:
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
        # no worker grades its own work and Skye judges nothing itself.
        review = {"goal": f"Review the changes in {spec['repo']} for the job: {spec['goal']}",
                  "repo": spec["repo"], "role": "reviewer", "adapter": spec["adapter"], "gates": []}
        verdict = dispatch(review, policy, runner=runner)
        return {"gate": "review", "ok": verdict.status == "done",
                "detail": verdict.reason or "review finished", "reviewer": verdict.worker_output[-500:]}
    return {"gate": gate, "ok": False, "detail": f"unknown gate '{gate}'"}
