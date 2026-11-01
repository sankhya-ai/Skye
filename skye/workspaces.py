"""Per-mission workspaces: a clean git checkout for each mission's work.

What this IS: file separation. Each mission runs against its own `git worktree` of the
mission repo (branch skye/<mission-id>), so two missions never share a working tree and a
mission's edits never land in the owner's checkout by themselves. Declared-path claims stay
advisory - a worktree is NOT a sandbox and does not stop a worker from reaching outside it,
and nothing here is a security boundary.

What this is NOT: publication. Worker's changes stay on the mission's branch in the worktree.
The review gate and journey replay run INSIDE the worktree, so verification covers exactly
what the worker changed; merging the branch back into the owner's tree remains the owner's
explicit act, exactly as before. Nothing merges, pushes, or publishes automatically.

Safety semantics:
  - Dirty base: an uncommitted or untracked base repo is recorded in workspace.json
    (dirty_at_start) - the worktree comes from HEAD, so owner WIP never travels into the
    mission, and the record says so.
  - Restart recovery: prepare() is idempotent. An existing worktree for the mission is
    reused; a missing worktree with an existing branch is recreated from that branch
    (worker commits survive); a recorded workspace whose worktree AND branch are both gone
    fails closed - never silently redo work in a fresh tree after unknown partial effects.
  - Parallel ownership: one worktree + one branch per mission id, unique by construction.
  - Non-git repos: no worktree is possible; the mission runs against the shared tree exactly
    as before and workspace.json says why, in plain terms.
  - reset_mission() removes the worktree only when it is clean; a dirty worktree is kept and
    reported, never deleted with unrecorded worker output in it.

Gated by policy.workspaces.enabled (absent = off = today's shared-tree behavior).
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any


class WorkspaceError(ValueError):
    pass


def config(policy: Any) -> dict[str, Any]:
    raw = getattr(policy, "workspaces", None)
    if not isinstance(raw, dict):
        return {"enabled": False, "allow_shared_fallback": False}
    return {"enabled": bool(raw.get("enabled")),
            "allow_shared_fallback": bool(raw.get("allow_shared_fallback"))}


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=60)


def _is_git_repo(repo: Path) -> bool:
    return _git(repo, "rev-parse", "--is-inside-work-tree").returncode == 0


def _record_path(mission_dir: Path) -> Path:
    return mission_dir / "workspace.json"


def read_record(mission_dir: Path) -> dict[str, Any] | None:
    try:
        return json.loads((_record_path(mission_dir)).read_text("utf-8"))
    except (OSError, ValueError):
        return None


def prepare(mission: dict[str, Any], *, state_dir: Path, policy: Any) -> dict[str, Any]:
    """Idempotently prepare the mission's workspace. Returns the record; record["worktree"]
    is the path steps should run against, or '' when the mission stays on the shared tree."""
    mission_dir = Path(state_dir) / "missions" / mission["id"]
    mission_dir.mkdir(parents=True, exist_ok=True)
    repo = Path(mission.get("repo") or "").expanduser()
    existing = read_record(mission_dir)
    cfg = config(policy)
    if cfg["enabled"] and (not repo or not _is_git_repo(repo)):
        # Workspaces are ON but this mission cannot get one. Never quietly launch it on the
        # shared tree as if it were isolated: either the owner explicitly permits shared mode
        # (policy.workspaces.allow_shared_fallback) or the mission fails closed, reason on record.
        reason = ("mission has no repo" if not repo
                  else "repo is not a git repository")
        if not cfg["allow_shared_fallback"]:
            record = {"worktree": "", "repo": str(repo), "created_at": time.time(),
                      "reason": f"{reason}; workspaces are enabled and shared fallback is not "
                                "permitted - mission refused"}
            _record_path(mission_dir).write_text(json.dumps(record, indent=2) + "\n", "utf-8")
            raise WorkspaceError(
                f"{reason}; workspaces are enabled, so the mission will not run on the shared "
                "tree unless policy.workspaces.allow_shared_fallback is set")
        reason += "; shared fallback explicitly permitted"
        record = {"worktree": "", "repo": str(repo), "reason": reason,
                  "created_at": time.time()}
        _record_path(mission_dir).write_text(json.dumps(record, indent=2) + "\n", "utf-8")
        return record
    if not cfg["enabled"]:
        if existing is None:
            existing = {"worktree": "", "repo": str(repo),
                        "reason": "workspaces disabled in policy",
                        "created_at": time.time()}
            _record_path(mission_dir).write_text(json.dumps(existing, indent=2) + "\n", "utf-8")
        return existing

    branch = f"skye/{mission['id']}"
    worktree = Path(state_dir) / "worktrees" / mission["id"]
    if worktree.is_dir():
        # reuse: same branch, same repo - a resume keeps its earlier work
        existing = existing or {}
        existing.update({"worktree": str(worktree), "repo": str(repo), "branch": branch,
                         "reused": True})
        _record_path(mission_dir).write_text(json.dumps(existing, indent=2) + "\n", "utf-8")
        return existing
    branch_exists = _git(repo, "rev-parse", "--verify", branch).returncode == 0
    if existing is not None and existing.get("worktree") and not branch_exists:
        raise WorkspaceError(
            f"mission {mission['id']} recorded a workspace, but both its worktree and branch "
            f"{branch} are gone; not silently restarting its work in a fresh tree")

    dirty = _git(repo, "status", "--porcelain").stdout.splitlines()
    base_rev = _git(repo, "rev-parse", "HEAD").stdout.strip()
    worktree.parent.mkdir(parents=True, exist_ok=True)
    if branch_exists:
        # worktree lost (e.g. state dir moved) but the branch holds worker commits: rebuild
        # from the branch, not from HEAD. This recovers COMMITTED work only - anything the
        # worker left uncommitted in the lost tree is gone, and the record must say so
        # instead of reading as a full recovery.
        result = _git(repo, "worktree", "add", str(worktree), branch)
    else:
        result = _git(repo, "worktree", "add", "-b", branch, str(worktree), "HEAD")
    if result.returncode != 0:
        raise WorkspaceError(f"git worktree add failed: {result.stderr.strip()}")
    record = {"worktree": str(worktree), "repo": str(repo), "branch": branch,
              "base_rev": base_rev, "created_at": time.time(), "reused": branch_exists,
              "dirty_at_start": [line[3:] for line in dirty][:100]}
    if branch_exists:
        record["recovered_from_branch"] = True
        record["uncommitted_lost"] = True
        record["recovery_note"] = ("the previous worktree was lost; committed work survived on "
                                   "the branch, but any UNCOMMITTED worker changes in the lost "
                                   "tree are unrecoverable - review before trusting the state")
    _record_path(mission_dir).write_text(json.dumps(record, indent=2) + "\n", "utf-8")
    return record


def apply(mission: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """The mission dict with its repo pointed at the worktree, when one was prepared. Steps
    with an explicit repo override keep it - an override opts that step out of the mission
    workspace, by the mission author's own choice."""
    if record.get("worktree"):
        return {**mission, "repo": record["worktree"]}
    return mission


def clean(worktree: Path) -> bool:
    return not _git(worktree, "status", "--porcelain").stdout.strip()


def release(record: dict[str, Any]) -> dict[str, Any]:
    """Remove the mission's worktree - ONLY when it is clean. A dirty worktree (uncommitted
    worker output, or a journey replay's leftovers) is kept and reported. The branch always
    stays: it is the mergeable record of the work, and removing worktrees never touches it."""
    worktree = Path(record.get("worktree") or "")
    repo = Path(record.get("repo") or "")
    if not worktree or not worktree.is_dir():
        return {"removed": False, "reason": "no worktree"}
    if not clean(worktree):
        return {"removed": False,
                "reason": f"worktree {worktree} has uncommitted changes; kept for review"}
    result = _git(repo, "worktree", "remove", str(worktree))
    return {"removed": result.returncode == 0,
            "reason": "" if result.returncode == 0 else result.stderr.strip()}
