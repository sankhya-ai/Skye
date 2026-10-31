"""The Git operations of a coding flow, each bounded by a timeout."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
from pathlib import Path

GIT_TIMEOUT = 120.0
IDENTITY = ("-c", "user.name=root", "-c", "user.email=root@localhost")


class GitError(RuntimeError):
    pass


async def git(cwd: Path, *args: str, check: bool = True, identity: bool = False) -> str:
    """Run git. ``identity``: commit as root only when the repo has no identity of its own."""
    needs_identity = identity and not await git(cwd, "config", "--get", "user.email", check=False)
    argv = ["git", *(IDENTITY if needs_identity else ()), *args]
    process = await asyncio.create_subprocess_exec(*argv, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(process.communicate(), GIT_TIMEOUT)
    except asyncio.TimeoutError as error:
        process.kill()
        raise GitError(f"git {args[0]} timed out") from error
    if check and process.returncode != 0:
        raise GitError(f"git {' '.join(args[:3])} failed: {(out + err).decode(errors='replace').strip()[-600:]}")
    return out.decode(errors="replace").strip()


#: Untracked files a test or build leaves behind are not part of a patch, even
#: in a repository without a .gitignore for them.
ARTIFACTS = (
    "__pycache__/*", "*/__pycache__/*", "*.pyc", "*.pyo", ".pytest_cache/*", "*/.pytest_cache/*", ".mypy_cache/*",
    ".ruff_cache/*", "node_modules/*", "*/node_modules/*", ".coverage", "coverage/*", "*.egg-info/*", ".tox/*",
    "target/*", ".DS_Store", "*/.DS_Store",
)


def is_artifact(path: str) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in ARTIFACTS)


async def new_files(worktree: Path) -> list[str]:
    """Untracked files the candidate created, without ignored files or artifacts."""
    listed = await git(worktree, "ls-files", "--others", "--exclude-standard")
    return [path for path in listed.splitlines() if path and not is_artifact(path)]


async def stage_candidate(worktree: Path) -> None:
    """Stage exactly the candidate's change: tracked edits and its new files."""
    await git(worktree, "add", "-u")
    files = await new_files(worktree)
    if files:
        await git(worktree, "add", "--", *files)


async def diff_receipt(worktree: Path, base: str, *, include_patch: bool = False) -> dict:
    """The candidate's change against the base commit, identified by its hash."""
    files = await new_files(worktree)
    if files:
        await git(worktree, "add", "-N", "--", *files)
    patch = await git(worktree, "diff", "--binary", base)
    names = [line for line in (await git(worktree, "diff", "--name-only", base)).splitlines() if line]
    receipt = {
        "sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        "changed_paths": names,
        "stat": await git(worktree, "diff", "--stat", base),
        "bytes": len(patch.encode("utf-8")),
    }
    if include_patch:
        receipt["patch"] = patch
    return receipt


def common_dir(worktree: Path) -> Path | None:
    """The main repository's `.git` directory of a linked worktree, or None."""
    marker = worktree / ".git"
    if not marker.is_file():
        return None
    text = marker.read_text("utf-8", errors="replace").strip()
    if not text.startswith("gitdir:"):
        return None
    gitdir = Path(text.split(":", 1)[1].strip())
    # <repo>/.git/worktrees/<name> -> <repo>/.git
    return gitdir.parent.parent if gitdir.parent.name == "worktrees" else None
