"""Running a flow's candidates: implement, verify on the host, repair, judge.

Each candidate is a hidden session of the same runtime (``kind="candidate"``)
working in its own Git worktree, unattended: edits and the project's checks
are allowed, anything that would need the owner is refused and the agent
adapts. After each attempt the *host* runs the flow's verification commands
in the worktree; a failure feeds its exact output into the next attempt, up to
:data:`MAX_ATTEMPTS`. Candidates run concurrently.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import hostos, launcher, llm
from ..sandbox import Mounts
from . import judge
from .git import common_dir, diff_receipt
from .store import event

if TYPE_CHECKING:
    from .service import FlowService

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
TURN_TIMEOUT = 30 * 60.0
CHECK_TIMEOUT = 900.0
MAX_JUDGE_ATTEMPTS = 2


def candidate_prompt(flow: dict[str, Any], cand: dict[str, Any], attempt: int) -> str:
    acceptance = "\n".join(f"- {item}" for item in flow["acceptance"]) or "- Implement the requested behavior correctly."
    commands = "\n".join(f"- `{item}`" for item in flow["verification_commands"])
    repair = ""
    failures = [v for v in cand["verification"] if v["exit_code"] != 0]
    if attempt > 1 and failures:
        detail = "\n\n".join(f"Command: {v['command']}\nExit: {v['exit_code']}\n{v['output_tail'][-4_000:]}" for v in failures)
        repair = (
            f"\nThis is repair attempt {attempt} of {MAX_ATTEMPTS}. Keep the correct parts of the current patch and fix the "
            f"root cause shown by the host's verification:\n{detail}\n"
        )
    return (
        "You are working in one isolated candidate worktree of a coding flow. Do not commit, merge, push, or touch any "
        "other checkout; do not start another coding flow.\n\n"
        f"Task:\n{flow['task']}\n\nAcceptance:\n{acceptance}\n\n"
        f"After your turn the host runs:\n{commands}\n{repair}\n"
        "Inspect the actual code, make the smallest complete change, run the narrowest useful checks, and leave the patch "
        "uncommitted. Never weaken or delete tests. Finish by reviewing your own diff and stating any remaining risk."
    )


async def run_check(service: "FlowService", worktree: Path, command: str) -> dict[str, Any]:
    tenant = service.tenant
    git_dir = common_dir(worktree)
    mounts = Mounts(workspace=worktree, writable=(git_dir,) if git_dir else (), home=tenant.home)
    argv = tenant.sandbox.wrap(hostos.shell_argv(command), mounts)
    env = tenant.sandbox.environment({**launcher.scrubbed_environment(), "PYTHONDONTWRITEBYTECODE": "1"}, mounts)
    try:
        process = await asyncio.create_subprocess_exec(*argv, cwd=str(worktree), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **hostos.spawn_options())
        try:
            out, _ = await asyncio.wait_for(process.communicate(), CHECK_TIMEOUT)
            code, output = process.returncode, out.decode(errors="replace")
        except asyncio.TimeoutError:
            await asyncio.to_thread(hostos.stop_tree, process.pid)
            code, output = None, f"verification timed out after {CHECK_TIMEOUT:.0f}s"
    except OSError as error:
        code, output = None, f"could not run: {error}"
    return {"command": command, "exit_code": code, "output_tail": output[-8_000:]}


async def run_candidate(service: "FlowService", flow_id: str, cand_id: str) -> None:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if service.stopping(flow_id):
            service.update_candidate(flow_id, cand_id, status="paused")
            return
        flow, cand = service.candidate_state(flow_id, cand_id)
        if cand["status"] == "verified":
            return
        service.update_candidate(flow_id, cand_id, status="working")
        service.publish(flow_id, f"{cand_id}: attempt {attempt} of {MAX_ATTEMPTS}")
        runtime = service.candidate_session(flow, cand)
        try:
            await runtime.prompt(candidate_prompt(flow, cand, attempt))
            await runtime.wait_turn(TURN_TIMEOUT)
        except (asyncio.TimeoutError, Exception) as error:  # noqa: BLE001 - one candidate's failure is not the flow's
            logger.info("candidate %s/%s attempt failed: %s", flow_id, cand_id, error)
            await runtime.cancel()
        if service.stopping(flow_id):
            service.update_candidate(flow_id, cand_id, status="paused")
            return
        worktree = Path(cand["worktree"])
        results = [await run_check(service, worktree, command) for command in flow["verification_commands"]]
        diff = await diff_receipt(worktree, flow["base_commit"])
        passed = bool(diff["changed_paths"]) and all(r["exit_code"] == 0 for r in results)
        service.update_candidate(
            flow_id,
            cand_id,
            status="verified" if passed else "verification_failed",
            verification=results,
            diff=diff,
            attempt={"attempt": attempt, "passed": passed, "reply": runtime.record.last_status},
        )
        service.publish(flow_id, f"{cand_id}: {'passed verification' if passed else 'needs repair'}")
        if passed:
            return
    service.update_candidate(flow_id, cand_id, status="failed")


async def pick_winner(service: "FlowService", flow_id: str, verified: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A validated judgment, or None when the judge could not give one."""
    flow = service.store.load(flow_id)
    evidence = []
    for cand in verified:
        current = await diff_receipt(Path(cand["worktree"]), flow["base_commit"], include_patch=True)
        if current["sha256"] != cand["diff"]["sha256"]:
            raise RuntimeError(f"{cand['id']} changed after verification")
        evidence.append(
            {
                "candidate_id": cand["id"],
                "diff_sha256": current["sha256"],
                "changed_paths": current["changed_paths"],
                "diff_stat": current["stat"],
                "patch": current["patch"][: judge.MAX_PATCH_CHARS],
                "verification": [{"command": v["command"], "exit_code": v["exit_code"]} for v in cand["verification"]],
                "repair_count": max(0, len(cand["attempts"]) - 1),
            }
        )
    messages = judge.prompt(flow, evidence)
    hashes = {c["id"]: c["diff"]["sha256"] for c in verified}
    settings = service.tenant.settings.get()
    error = ""
    for _ in range(MAX_JUDGE_ATTEMPTS):
        if error:
            messages.append({"role": "user", "content": f"Your previous output violated the contract: {error}. Return a corrected JSON object only."})
        try:
            answer = await llm.complete(messages, base_url=settings.base_url, api_key=service.tenant.api_key(), model=settings.learning_model or settings.model, purpose="judge", max_tokens=2_500, timeout=300)
        except llm.LLMError as failure:
            error = str(failure)
            break
        try:
            return judge.parse(answer, hashes)
        except judge.JudgmentError as failure:
            error = str(failure)
            messages.append({"role": "assistant", "content": answer[:8_000]})
    flow = service.store.load(flow_id)
    flow["judge_error"] = error
    event(flow, "judge_failed", error)
    service.store.save(flow)
    return None
