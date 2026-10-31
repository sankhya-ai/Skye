"""Coding flows for one person: create, run, review, apply, roll back.

Ported from Chotu's native coding runtime (``assistant_runtime/native_coding.py``).
The agent does the reasoning and editing; this module owns the deterministic
boundary the agent must not control — isolated worktrees on an immutable base
commit, host-run verification, the diff hash an approval is bound to, the
merge into the owner's checkout, and the exact revert.

    preparing ─start─▶ working ─▶ ready_to_apply ─apply─▶ applied ─rollback─▶ rolled_back
                          │   └─▶ needs_review (judge unavailable: owner selects)
                          └─▶ failed (no candidate verified)      any ─cancel─▶ cancelled
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..trust import TIER_READ, TIER_VERIFY, classify_command
from . import runner
from .git import GitError, diff_receipt, git, stage_candidate
from .store import TERMINAL, FlowError, FlowStore, candidate, event, public

if TYPE_CHECKING:
    from ..sessions import SessionRuntime
    from ..tenancy import Tenant

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 3
MAX_CHECKS = 8


class FlowService:
    def __init__(self, tenant: "Tenant") -> None:
        self.tenant = tenant
        self.store = FlowStore(tenant.root / "flows")
        self.worktrees = (tenant.workspace_root / ".root-flows") if tenant.workspace_root else tenant.root / "worktrees"
        self._tasks: dict[str, asyncio.Task] = {}
        self._stop: set[str] = set()
        self.recover()

    # ----- state helpers (used by the runner) --------------------------------

    def stopping(self, flow_id: str) -> bool:
        return flow_id in self._stop

    def candidate_state(self, flow_id: str, cand_id: str) -> tuple[dict, dict]:
        flow = self.store.load(flow_id)
        return flow, candidate(flow, cand_id)

    def update_candidate(self, flow_id: str, cand_id: str, *, status: str, verification=None, diff=None, attempt=None) -> None:
        flow = self.store.load(flow_id)
        cand = candidate(flow, cand_id)
        cand["status"] = status
        if verification is not None:
            cand["verification"] = verification
        if diff is not None:
            cand["diff"] = diff
        if attempt is not None:
            cand["attempts"].append(attempt)
        event(flow, f"candidate_{status}", cand_id)
        self.store.save(flow)

    def candidate_session(self, flow: dict[str, Any], cand: dict[str, Any]) -> "SessionRuntime":
        if cand.get("session_id"):
            try:
                return self.tenant.sessions.get(cand["session_id"])
            except KeyError:
                pass
        runtime = self.tenant.sessions.create(cand["worktree"], f"{flow['id']} · {cand['id']}", kind="candidate", parent=flow["owner_session"], mode="accept_edits", role=cand["id"])
        stored = self.store.load(flow["id"])
        candidate(stored, cand["id"])["session_id"] = runtime.record.id
        self.store.save(stored)
        return runtime

    def publish(self, flow_id: str, summary: str) -> None:
        flow = self.store.load(flow_id)
        try:
            owner = self.tenant.sessions.get(flow["owner_session"])
        except KeyError:
            return
        owner.emit("FlowUpdated", {**public(flow), "summary": summary})

    def _set_status(self, flow: dict[str, Any], status: str, detail: str = "") -> dict[str, Any]:
        flow["status"] = status
        event(flow, status, detail)
        return self.store.save(flow)

    # ----- lifecycle -----------------------------------------------------------

    async def create(self, repo: str | Path, task: str, *, owner_session: str, acceptance=None, verification_commands=None, candidate_count: int = 1) -> dict[str, Any]:
        repo_path = self.tenant.check_workspace(repo)
        task = str(task or "").strip()
        if not task:
            raise FlowError("a task is required")
        count = int(candidate_count or 1)
        if not 1 <= count <= MAX_CANDIDATES:
            raise FlowError(f"candidate_count must be between 1 and {MAX_CANDIDATES}")
        commands = [str(c).strip() for c in (verification_commands or ["git diff --check"]) if str(c).strip()]
        if not commands or len(commands) > MAX_CHECKS:
            raise FlowError(f"give 1 to {MAX_CHECKS} verification commands")
        for command in commands:
            verdict = classify_command(command)
            if verdict.tier not in (TIER_READ, TIER_VERIFY):
                raise FlowError(f"verification commands must only read or run the project's checks; `{command}` does not ({verdict.key})")
        try:
            if await git(repo_path, "rev-parse", "--is-inside-work-tree") != "true":
                raise FlowError("the workspace is not a Git repository")
            base = await git(repo_path, "rev-parse", "HEAD")
        except GitError as error:
            raise FlowError("coding flows need a Git repository with at least one commit") from error
        flow_id = self.store.next_id()
        candidates = []
        try:
            for index in range(1, count + 1):
                cand_id = f"candidate_{index}"
                worktree = self.worktrees / flow_id / cand_id
                branch = f"root/{flow_id}/{cand_id}"
                worktree.parent.mkdir(parents=True, exist_ok=True)
                await git(repo_path, "worktree", "add", "-b", branch, str(worktree), base)
                candidates.append({"id": cand_id, "branch": branch, "worktree": str(worktree), "status": "prepared", "attempts": [], "verification": [], "diff": None})
        except GitError as error:
            await self._remove_worktrees(repo_path, candidates)
            self.store.path(flow_id).unlink(missing_ok=True)
            raise FlowError(str(error)) from error
        flow = {
            "id": flow_id,
            "status": "preparing",
            "task": task,
            "acceptance": [str(a).strip() for a in (acceptance or []) if str(a).strip()][:40],
            "verification_commands": commands,
            "repo": str(repo_path),
            "base_commit": base,
            "owner_session": owner_session,
            "created_at": time.time(),
            "candidates": candidates,
            "selected": None,
            "judgment": None,
            "apply": None,
            "rollback": None,
        }
        event(flow, "created", f"{count} candidates at {base[:12]}")
        self.store.save(flow)
        self.publish(flow_id, "Prepared isolated candidates.")
        return public(flow)

    def start(self, flow_id: str) -> dict[str, Any]:
        flow = self.store.load(flow_id)
        if flow["status"] in TERMINAL or flow["status"] == "ready_to_apply":
            raise FlowError(f"the flow is already {flow['status']}")
        task = self._tasks.get(flow_id)
        if task is not None and not task.done():
            return {"status": "already_running", "flow_id": flow_id}
        self._stop.discard(flow_id)
        self._set_status(flow, "working")
        self._tasks[flow_id] = asyncio.create_task(self._run(flow_id))
        return {"status": "started", "flow_id": flow_id}

    async def _run(self, flow_id: str) -> None:
        try:
            flow = self.store.load(flow_id)
            self.publish(flow_id, "Building and verifying candidates.")
            await asyncio.gather(*(runner.run_candidate(self, flow_id, c["id"]) for c in flow["candidates"]))
            flow = self.store.load(flow_id)
            if self.stopping(flow_id):
                self._set_status(flow, "needs_review", "stopped")
                self.publish(flow_id, "Stopped. The candidate work is kept.")
                return
            verified = [c for c in flow["candidates"] if c["status"] == "verified"]
            if not verified:
                self._set_status(flow, "failed", "no candidate passed verification")
                self.publish(flow_id, "No candidate passed verification within the repair budget.")
                return
            judgment = await runner.pick_winner(self, flow_id, verified) if len(verified) > 1 else None
            flow = self.store.load(flow_id)
            if len(verified) > 1 and judgment is None:
                self._set_status(flow, "needs_review", "the judge could not decide; choose a candidate")
                self.publish(flow_id, "Several candidates passed; choose one to review.")
                return
            flow["judgment"] = judgment
            flow["selected"] = judgment["winner"] if judgment else verified[0]["id"]
            self._set_status(flow, "ready_to_apply", flow["selected"])
            self.publish(flow_id, "A verified patch is ready for your review. Nothing has been applied.")
        except Exception as error:  # noqa: BLE001 - a crashed run is a failed flow, not a crashed server
            logger.exception("coding flow %s failed", flow_id)
            flow = self.store.load(flow_id)
            self._set_status(flow, "failed", str(error))
            self.publish(flow_id, "The coding flow stopped unexpectedly.")
        finally:
            self._stop.discard(flow_id)

    async def stop(self, flow_id: str) -> dict[str, Any]:
        flow = self.store.load(flow_id)
        self._stop.add(flow_id)
        for cand in flow["candidates"]:
            if cand.get("session_id"):
                try:
                    await self.tenant.sessions.get(cand["session_id"]).cancel()
                except KeyError:
                    pass
        task = self._tasks.get(flow_id)
        if task is not None:
            await asyncio.wait([task], timeout=30)
        return public(self.store.load(flow_id))

    def select(self, flow_id: str, cand_id: str) -> dict[str, Any]:
        """The owner's choice when the judge could not make one."""
        flow = self.store.load(flow_id)
        if flow["status"] != "needs_review" or candidate(flow, cand_id)["status"] != "verified":
            raise FlowError("only a verified candidate of a flow awaiting review can be selected")
        flow["selected"] = cand_id
        self._set_status(flow, "ready_to_apply", f"owner selected {cand_id}")
        self.publish(flow_id, f"Selected {cand_id}.")
        return public(flow)

    async def review(self, flow_id: str) -> dict[str, Any]:
        flow = self.store.load(flow_id)
        if not flow.get("selected"):
            raise FlowError("the flow has no selected candidate")
        cand = candidate(flow, flow["selected"])
        current = await diff_receipt(Path(cand["worktree"]), flow["base_commit"], include_patch=True)
        if current["sha256"] != (cand.get("diff") or {}).get("sha256"):
            raise FlowError("the selected candidate changed after verification")
        return {"flow_id": flow_id, "candidate_id": cand["id"], "base_commit": flow["base_commit"], "verification": cand["verification"], "judgment": flow.get("judgment"), "diff": current}

    async def apply(self, flow_id: str, review_sha256: str) -> dict[str, Any]:
        flow = self.store.load(flow_id)
        if flow["status"] != "ready_to_apply":
            raise FlowError("the flow is not ready to apply")
        cand = candidate(flow, flow["selected"])
        repo, worktree = Path(flow["repo"]), Path(cand["worktree"])
        current = await diff_receipt(worktree, flow["base_commit"])
        if current["sha256"] != cand["diff"]["sha256"]:
            raise FlowError("the selected candidate changed after verification")
        if review_sha256 != current["sha256"]:
            raise FlowError("the approval is not for the current reviewed diff")
        if await git(repo, "status", "--porcelain", "--untracked-files=no"):
            raise FlowError("your checkout has uncommitted changes; commit or stash them, then apply")
        before = await git(repo, "rev-parse", "HEAD")
        if before != flow["base_commit"]:
            raise FlowError("your checkout moved on since the flow started; start a new flow from the current commit")
        await stage_candidate(worktree)
        await git(worktree, "commit", "-q", "-m", f"root: {flow['task'][:120]}", identity=True)
        cand_commit = await git(worktree, "rev-parse", "HEAD")
        try:
            await git(repo, "merge", "--no-ff", "-q", "-m", f"Apply root coding flow {flow_id}", cand_commit, identity=True)
        except GitError as error:
            await git(repo, "merge", "--abort", check=False)
            raise FlowError(f"the merge failed: {error}") from error
        flow["apply"] = {"before_commit": before, "candidate_commit": cand_commit, "applied_commit": await git(repo, "rev-parse", "HEAD"), "diff_sha256": current["sha256"], "applied_at": time.time()}
        self._set_status(flow, "applied", flow["apply"]["applied_commit"])
        await self._cleanup(flow)
        self.publish(flow_id, "Applied the exact reviewed patch. It can be rolled back.")
        return public(self.store.load(flow_id))

    async def rollback(self, flow_id: str, applied_commit: str) -> dict[str, Any]:
        flow = self.store.load(flow_id)
        applied = flow.get("apply") or {}
        if flow["status"] != "applied" or applied_commit != applied.get("applied_commit"):
            raise FlowError("the rollback is not for this flow's applied commit")
        repo = Path(flow["repo"])
        if await git(repo, "status", "--porcelain", "--untracked-files=no"):
            raise FlowError("your checkout has uncommitted changes; rollback is paused")
        if await git(repo, "rev-parse", "HEAD") != applied_commit:
            raise FlowError("your checkout moved on since the apply; revert the merge by hand")
        await git(repo, "revert", "--mainline", "1", "--no-edit", applied_commit, identity=True)
        flow["rollback"] = {"reverted_commit": applied_commit, "rollback_commit": await git(repo, "rev-parse", "HEAD"), "rolled_back_at": time.time()}
        self._set_status(flow, "rolled_back")
        self.publish(flow_id, "Rolled back the exact apply commit.")
        return public(flow)

    async def cancel(self, flow_id: str) -> dict[str, Any]:
        flow = self.store.load(flow_id)
        if flow["status"] in ("applied", "rolled_back"):
            raise FlowError("an applied flow cannot be cancelled; roll it back")
        await self.stop(flow_id)
        flow = self.store.load(flow_id)
        await self._cleanup(flow)
        self._set_status(flow, "cancelled")
        self.publish(flow_id, "Discarded the candidates. Your checkout was not changed.")
        return public(flow)

    async def _cleanup(self, flow: dict[str, Any]) -> None:
        for cand in flow["candidates"]:
            if cand.get("session_id"):
                try:
                    await self.tenant.sessions.delete(cand["session_id"])
                except KeyError:
                    pass
        await self._remove_worktrees(Path(flow["repo"]), flow["candidates"])

    @staticmethod
    async def _remove_worktrees(repo: Path, candidates: list[dict[str, Any]]) -> None:
        for cand in candidates:
            await git(repo, "worktree", "remove", "--force", cand["worktree"], check=False)
            await git(repo, "branch", "-D", cand["branch"], check=False)

    def recover(self) -> None:
        """Flows that were working when the server stopped wait for the owner."""
        for flow in self.store.list(limit=1000):
            if flow["status"] == "working":
                for cand in flow["candidates"]:
                    if cand["status"] == "working":
                        cand["status"] = "interrupted"
                self._set_status(flow, "needs_review", "interrupted by a restart")

    def list(self) -> list[dict[str, Any]]:
        return [public(flow) for flow in self.store.list()]

    def get(self, flow_id: str) -> dict[str, Any]:
        flow = public(self.store.load(flow_id))
        task = self._tasks.get(flow_id)
        flow["running"] = bool(task and not task.done())
        return flow

    async def close(self) -> None:
        for flow_id, task in list(self._tasks.items()):
            if not task.done():
                self._stop.add(flow_id)
                task.cancel()
