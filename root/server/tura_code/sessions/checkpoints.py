"""Checkpoints: the workspace as it was before each turn, and a way back to it.

Before a turn that may change files, the session snapshots its workspace into a
shadow Git repository of its own (``<session>/checkpoints/shadow.git``, with
the workspace as its work tree). The project's own ``.git`` is never touched:
no commits, no index, no hooks. Ignored files and build artifacts stay out, so
a checkpoint is the source the owner cares about, not ``node_modules``.

Restoring puts the workspace back exactly: files the turns after it changed or
deleted come back, and files they created go. A restore snapshots first, so it
can itself be undone.

Git runs through the tenant's sandbox like any other command (hosted mode
binds the session directory and the workspace at their own paths).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

logger = logging.getLogger(__name__)

SNAPSHOT_TIMEOUT = 30.0
#: Kept out of every checkpoint, on top of the workspace's own .gitignore files.
EXCLUDES = (
    "node_modules/", ".venv/", "venv/", "__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/",
    ".tox/", "target/", ".next/", ".turbo/", ".cache/", ".DS_Store",
)
_SETTINGS = (
    "-c", "core.hooksPath=/dev/null", "-c", "core.autocrlf=false", "-c", "core.quotepath=off", "-c", "commit.gpgsign=false",
    "-c", "user.name=root", "-c", "user.email=root@localhost", "-c", "gc.auto=0",
)


class CheckpointError(RuntimeError):
    pass


@dataclass(frozen=True)
class Checkpoint:
    id: str
    created_at: float
    label: str

    def public(self) -> dict:
        return {"id": self.id, "created_at": self.created_at, "label": self.label}


class Checkpoints:
    def __init__(self, directory: Path, workspace: Path, *, wrap: Callable[[list[str]], list[str]] = list, env: dict[str, str] | None = None) -> None:
        self.directory = directory
        self.git_dir = directory / "shadow.git"
        self.workspace = workspace
        self._wrap = wrap
        self._env = env if env is not None else dict(os.environ)
        self._lock = asyncio.Lock()
        #: Set when a snapshot could not be taken (too large, git missing): later turns skip it.
        self.disabled = ""

    # ----- git ---------------------------------------------------------------

    async def _git(self, *args: str, check: bool = True, timeout: float = SNAPSHOT_TIMEOUT) -> str:
        env = {**self._env, "GIT_DIR": str(self.git_dir), "GIT_WORK_TREE": str(self.workspace), "GIT_TERMINAL_PROMPT": "0"}
        env.pop("GIT_INDEX_FILE", None)
        argv = self._wrap(["git", *_SETTINGS, *args])
        try:
            process = await asyncio.create_subprocess_exec(
                *argv, cwd=str(self.workspace), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
        except OSError as error:
            raise CheckpointError(f"git is not available: {error}") from error
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout)
        except asyncio.TimeoutError as error:
            process.kill()
            raise CheckpointError(f"git {args[0]} took longer than {int(timeout)}s") from error
        if check and process.returncode != 0:
            raise CheckpointError(f"git {args[0]} failed: {(out + err).decode(errors='replace').strip()[-400:]}")
        return out.decode(errors="replace")

    async def _ensure_repo(self) -> None:
        if (self.git_dir / "HEAD").exists():
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        await self._git("init", "--quiet")
        info = self.git_dir / "info"
        info.mkdir(parents=True, exist_ok=True)
        (info / "exclude").write_text("\n".join(EXCLUDES) + "\n", "utf-8")

    async def _head(self) -> str:
        return (await self._git("rev-parse", "--verify", "--quiet", "HEAD", check=False)).strip()

    # ----- the public surface ------------------------------------------------

    async def snapshot(self, label: str) -> Checkpoint | None:
        """Record the workspace now; the previous checkpoint when nothing changed. None when skipped."""
        if self.disabled:
            return None
        async with self._lock:
            try:
                await self._ensure_repo()
                await self._git("add", "--all", "--", ".")
                tree = (await self._git("write-tree")).strip()
                head = await self._head()
                if head and (await self._git("rev-parse", f"{head}^{{tree}}")).strip() == tree:
                    return await self._describe(head)
                parent = ("-p", head) if head else ()
                commit = (await self._git("commit-tree", tree, *parent, "-m", label[:300] or "checkpoint")).strip()
                await self._git("update-ref", "HEAD", commit)
                return await self._describe(commit)
            except CheckpointError as error:
                self.disabled = str(error)
                logger.info("checkpoints off for %s: %s", self.workspace, error)
                return None

    async def list(self, limit: int = 50) -> list[Checkpoint]:
        if not (self.git_dir / "HEAD").exists() or not await self._head():
            return []
        out = await self._git("log", f"--max-count={limit}", "--format=%H%x1f%ct%x1f%s", "HEAD")
        found = []
        for line in out.splitlines():
            commit, when, label = (line.split("\x1f") + ["", ""])[:3]
            if commit:
                found.append(Checkpoint(commit, float(when or 0), label))
        return found

    async def changes_since(self, checkpoint: str) -> list[dict]:
        """What restoring ``checkpoint`` would change: ``[{"path", "status"}]`` against the workspace now."""
        async with self._lock:
            await self._git("add", "--all", "--", ".")
            out = await self._git("diff", "--cached", "--no-renames", "--name-status", "-z", checkpoint)
        parts = [p for p in out.split("\0") if p]
        return [{"status": parts[i], "path": parts[i + 1]} for i in range(0, len(parts) - 1, 2)]

    async def restore(self, checkpoint: str) -> dict:
        """Put the workspace back to ``checkpoint``. Returns what changed and the checkpoint that undoes it."""
        if not (self.git_dir / "HEAD").exists():
            raise CheckpointError("This session has no checkpoints.")
        commit = (await self._git("rev-parse", "--verify", "--quiet", f"{checkpoint}^{{commit}}", check=False)).strip()
        if not commit:
            raise CheckpointError(f"No checkpoint {checkpoint!r} in this session.")
        target = await self._describe(commit)
        undo = await self.snapshot(f"Before restoring “{target.label[:120]}”")
        if undo is None:
            raise CheckpointError(f"Could not record the workspace before restoring: {self.disabled}")
        async with self._lock:
            out = await self._git("diff", "--no-renames", "--name-status", "-z", commit, undo.id)
            parts = [p for p in out.split("\0") if p]
            changes = [(parts[i], parts[i + 1]) for i in range(0, len(parts) - 1, 2)]
            created = [path for status, path in changes if status == "A"]
            restored = [path for status, path in changes if status != "A"]
            for path in created:
                self._remove(path)
            for start in range(0, len(restored), 200):
                await self._git("checkout", commit, "--", *restored[start : start + 200])
            await self._git("update-ref", "HEAD", undo.id)
        return {"checkpoint": target.public(), "undo": undo.public(), "restored": restored, "removed": created}

    # ----- helpers -------------------------------------------------------------

    async def _describe(self, commit: str) -> Checkpoint:
        line = (await self._git("log", "-1", "--format=%H%x1f%ct%x1f%s", commit)).strip()
        sha, when, label = (line.split("\x1f") + ["", ""])[:3]
        return Checkpoint(sha, float(when or 0), label)

    def _remove(self, relative: str) -> None:
        root = self.workspace.resolve()
        path = (self.workspace / relative).resolve()
        if not path.is_relative_to(root) or path == root:
            return
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
        except OSError as error:
            logger.info("could not remove %s: %s", path, error)
            return
        # Folders the removed files lived in, when nothing else is left in them.
        parent = path.parent
        while parent != root and parent.is_relative_to(root):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


class Rollback:
    """Mixed into ``SessionRuntime``: a checkpoint before each turn, and the way back."""

    _checkpoints: Checkpoints | None = None

    @property
    def checkpoints(self: "SessionRuntime") -> Checkpoints:
        if self._checkpoints is None:
            self._checkpoints = Checkpoints(
                self.dir / "checkpoints",
                Path(self.record.workspace),
                wrap=lambda argv: self.tenant.wrap_command(self, argv),
                env=self.tenant.command_environment(self),
            )
        return self._checkpoints

    async def checkpoint_turn(self: "SessionRuntime", turn: "TurnState") -> None:
        """Before a turn that may change files: remember the workspace as it is."""
        if self.record.kind != "session" or self.record.permission_mode == "plan":
            return
        checkpoint = await self.checkpoints.snapshot(f"Before: {' '.join(turn.prompt.split())[:200]}")
        if checkpoint is not None:
            turn.checkpoint = checkpoint.id
            self.emit("Checkpoint", checkpoint.public())

    async def rollback(self: "SessionRuntime", checkpoint: str | None = None) -> dict:
        """Restore the workspace to a checkpoint (default: before the latest turn)."""
        if self.turn is not None:
            raise CheckpointError("Wait for the turn to finish (or stop it) before restoring.")
        if checkpoint is None:
            found = await self.checkpoints.list(limit=1)
            if not found:
                raise CheckpointError("This session has no checkpoints yet.")
            checkpoint = found[0].id
        result = await self.checkpoints.restore(checkpoint)
        self.emit("Rollback", result)
        return result


if TYPE_CHECKING:
    from .record import TurnState
    from .runtime import SessionRuntime
