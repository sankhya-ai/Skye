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


def repo_map(mission: dict[str, Any]) -> dict[str, str]:
    """The mission's named repositories. A single-repo mission is {'default': repo}; a
    multi-repo mission declares repos: {name: path} and may still carry a default repo.
    A monorepo is just the one-entry case - this never reinterprets separate repos as paths
    inside one."""
    repos = mission.get("repos")
    out: dict[str, str] = {}
    if isinstance(repos, dict):
        for name, path in repos.items():
            out[str(name)] = str(path)
    default = str(mission.get("repo") or "").strip()
    if default:
        import os
        real = {os.path.realpath(v) for v in out.values()}
        if os.path.realpath(default) not in real:
            out["default"] = default
    return out


def prepare(mission: dict[str, Any], *, state_dir: Path, policy: Any) -> dict[str, Any]:
    """Idempotently prepare the mission's workspace. Returns the record; record["worktree"]
    is the path steps should run against, or '' when the mission stays on the shared tree.
    Multi-repo missions get one worktree PER repository under worktrees/<mission-id>/<name>,
    each on branch skye/<mission-id> in its own repo; the record carries a per-repo map and
    aggregates uncommitted_lost so the recovery pause covers any repo's lost tree."""
    if repo_map(mission) and mission.get("repos"):
        return _prepare_multi(mission, state_dir=state_dir, policy=policy)
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
    """The mission dict with its repo(s) pointed at the prepared worktree(s). Steps name a
    repo by its declared name or fall back to the mission default; a step with an explicit
    path override keeps it - an override opts that step out of the mission workspace, by the
    mission author's own choice."""
    if record.get("repos"):
        resolved = {name: (rec.get("worktree") or rec.get("repo", ""))
                    for name, rec in record["repos"].items()}
        out = {**mission, "repos_resolved": resolved}
        if resolved.get("default"):
            out["repo"] = resolved["default"]
        return out
    if record.get("worktree"):
        return {**mission, "repo": record["worktree"]}
    return mission


def acknowledge_recovery(mission_dir: Path, *, actor: str = "operator") -> dict[str, Any]:
    """The operator's explicit decision after a lost-tree recovery: they have reviewed the
    rebuilt workspace (committed work from the branch, uncommitted changes reported lost)
    and accept running from it. Recorded with the original base context, never implied."""
    record = read_record(Path(mission_dir))
    if not record or not record.get("uncommitted_lost"):
        raise WorkspaceError("no lost-tree recovery is pending for this mission")
    existing = record.get("recovery_acknowledged")
    if existing:
        # The first decision is the record. A repeated ack confirms it; it never rewrites
        # who decided or when - history is not editable by pressing the button twice.
        record["recovery_ack_repeats"] = int(record.get("recovery_ack_repeats", 0)) + 1
        _record_path(Path(mission_dir)).write_text(json.dumps(record, indent=2) + "\n", "utf-8")
        return record
    record["recovery_acknowledged"] = {"at": time.time(), "by": actor,
                                       "base_rev": record.get("base_rev", ""),
                                       "branch": record.get("branch", "")}
    _record_path(Path(mission_dir)).write_text(json.dumps(record, indent=2) + "\n", "utf-8")
    return record


def clean(worktree: Path) -> bool:
    return not _git(worktree, "status", "--porcelain").stdout.strip()


def release(record: dict[str, Any]) -> dict[str, Any]:
    """Remove the mission's worktree - ONLY when it is clean. A dirty worktree (uncommitted
    worker output, or a journey replay's leftovers) is kept and reported. The branch always
    stays: it is the mergeable record of the work, and removing worktrees never touches it.
    Multi-repo records release each repo's worktree under the same rule."""
    if record.get("repos"):
        outcomes = {name: release(rec) for name, rec in record["repos"].items()
                    if rec.get("worktree")}
        removed = all(o["removed"] for o in outcomes.values()) if outcomes else False
        kept = {n: o["reason"] for n, o in outcomes.items() if not o["removed"]}
        return {"removed": removed, "per_repo": outcomes,
                "reason": "; ".join(f"{n}: {r}" for n, r in kept.items())}
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


def _prepare_multi(mission: dict[str, Any], *, state_dir: Path, policy: Any) -> dict[str, Any]:
    """Per-repo workspaces for a mission that declares repos. Each repo gets the same safety
    semantics as a single-repo mission (dirty-base record, idempotent reuse, rebuild from a
    surviving branch with honest loss flags, fail-closed when both are gone); any repo that
    cannot be isolated fails the whole preparation closed unless shared fallback is permitted.
    """
    mission_dir = Path(state_dir) / "missions" / mission["id"]
    mission_dir.mkdir(parents=True, exist_ok=True)
    existing = read_record(mission_dir)
    cfg = config(policy)
    if not cfg["enabled"]:
        if existing is None:
            existing = {"worktree": "", "repo": str(mission.get("repo") or ""),
                        "reason": "workspaces disabled in policy",
                        "created_at": time.time()}
            _record_path(mission_dir).write_text(json.dumps(existing, indent=2) + "\n", "utf-8")
        return existing
    if existing and existing.get("repos"):
        # resume: verify every recorded tree still stands, rebuild lost ones from branches
        # (with honest loss flags), fail closed on a tree+branch both gone
        records = existing["repos"]
        for name, rec in records.items():
            if not rec.get("worktree"):
                continue
            if Path(rec["worktree"]).is_dir():
                continue
            repo = Path(rec["repo"])
            if _git(repo, "rev-parse", "--verify", rec["branch"]).returncode == 0:
                result = _git(repo, "worktree", "add", rec["worktree"], rec["branch"])
                if result.returncode != 0:
                    raise WorkspaceError(f"git worktree add failed for {name}: "
                                         f"{result.stderr.strip()}")
                rec["recovered_from_branch"] = True
                rec["uncommitted_lost"] = True
                rec["recovery_note"] = ("the previous worktree was lost; committed work "
                                        "survived on the branch, but any UNCOMMITTED worker "
                                        "changes in the lost tree are unrecoverable")
            else:
                raise WorkspaceError(
                    f"mission {mission['id']} repo {name}: both worktree and branch "
                    f"{rec['branch']} are gone; not silently restarting its work")
        existing["uncommitted_lost"] = any(r.get("uncommitted_lost") for r in records.values())
        _record_path(mission_dir).write_text(json.dumps(existing, indent=2) + "\n", "utf-8")
        return existing
    records = {}
    for name, path in repo_map(mission).items():
        repo = Path(path).expanduser()
        if not repo or not _is_git_repo(repo):
            if not cfg["allow_shared_fallback"]:
                raise WorkspaceError(
                    f"repo '{name}' ({path}) is not a git repository; workspaces are enabled, "
                    "so the mission will not run it on the shared tree unless "
                    "policy.workspaces.allow_shared_fallback is set")
            records[name] = {"worktree": "", "repo": str(repo),
                             "reason": "not a git repository; shared fallback explicitly "
                                       "permitted"}
            continue
        branch = f"skye/{mission['id']}"
        worktree = Path(state_dir) / "worktrees" / mission["id"] / name
        dirty = _git(repo, "status", "--porcelain").stdout.splitlines()
        base_rev = _git(repo, "rev-parse", "HEAD").stdout.strip()
        worktree.parent.mkdir(parents=True, exist_ok=True)
        if _git(repo, "rev-parse", "--verify", branch).returncode == 0:
            # A branch with no record means an earlier prepare died partway. Rebuild from
            # the branch and say the state is uncertain - never pretend a fresh start.
            result = _git(repo, "worktree", "add", str(worktree), branch)
            if result.returncode != 0:
                raise WorkspaceError(f"git worktree add failed for {name}: "
                                     f"{result.stderr.strip()}")
            records[name] = {"worktree": str(worktree), "repo": str(repo), "branch": branch,
                             "base_rev": base_rev, "reused": True,
                             "recovered_from_branch": True, "uncommitted_lost": True,
                             "recovery_note": ("rebuilt from a pre-existing branch left by an "
                                               "interrupted preparation; its state is "
                                               "uncertain - review before trusting it"),
                             "dirty_at_start": [line[3:] for line in dirty][:100]}
            continue
        result = _git(repo, "worktree", "add", "-b", branch, str(worktree), "HEAD")
        if result.returncode != 0:
            raise WorkspaceError(f"git worktree add failed for {name}: {result.stderr.strip()}")
        records[name] = {"worktree": str(worktree), "repo": str(repo), "branch": branch,
                         "base_rev": base_rev, "reused": False,
                         "dirty_at_start": [line[3:] for line in dirty][:100]}
    record = {"worktree": "", "repo": str(mission.get("repo") or ""),
              "created_at": time.time(), "repos": records,
              "uncommitted_lost": any(r.get("uncommitted_lost") for r in records.values())}
    _record_path(mission_dir).write_text(json.dumps(record, indent=2) + "\n", "utf-8")
    return record
