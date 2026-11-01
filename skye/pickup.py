"""Trusted automatic pickup: starting owner-approved tickets without a manual click.

This module is deliberately NOT authority. What it automates is the intake click the owner
already makes by hand; it never widens what a run may do:
  - OFF by default. policy.auto_pickup.enabled must be true, in the same owner-managed file
    that lists which contracts may run unattended. Switching it on starts intake only.
  - A ticket's owner approval authorizes creating and starting its mission. It is NOT an
    execution-contract approval: every step contract still passes the normal policy /
    approval gate at dispatch (bound to its exact contract fingerprint), so a mission that
    has no unattended rule still pauses for the owner, exactly as a manual start would.
  - Reopened tickets are excluded unless auto_pickup.include_reopened is true, and then only
    when the owner's approval is FRESH: recorded after the latest reopen on the thread.
  - Every pickup decision is journaled on the ticket thread (reserved/started/aborted), so a
    later owner can see exactly which rule fired and why a start did or did not happen.

Idempotency is enforced, not assumed: reservation is a flock-serialized read-modify-write on
the backlog file, so two pollers in separate processes cannot both reserve one ticket. A
reservation records its picker and time; a reservation older than stale_after with no mission
attached belongs to a crashed picker and may be taken over. The launch step re-reads the
ticket under the same lock right before starting, so an approval revoked (or a ticket
otherwise changed) between reservation and launch aborts cleanly instead of starting stale
work. Effects bound to inputs (the mission goal text, project, adapter) are snapshotted from
the ticket record at creation time and carried into the mission spec unchanged.
"""

from __future__ import annotations

import fcntl
import time
from pathlib import Path
from typing import Any, Callable

from . import backlog

DEFAULT_STALE_AFTER = 120.0  # seconds; a reservation older than this with no mission is a crashed picker


class PickupError(ValueError):
    pass


def config(policy: Any) -> dict[str, Any]:
    """The auto_pickup block of a Policy, normalized. Absent or malformed means fully off."""
    raw = getattr(policy, "auto_pickup", None)
    if not isinstance(raw, dict):
        return {"enabled": False, "include_reopened": False, "max_missions": 1, "adapter": ""}
    return {"enabled": bool(raw.get("enabled")),
            "include_reopened": bool(raw.get("include_reopened")),
            "max_missions": max(1, int(raw.get("max_missions") or 1)),
            "adapter": str(raw.get("adapter") or "")}


def _lock_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + ".pickup.lock")


def _fresh_approval(feature: dict[str, Any]) -> bool:
    """For a reopened ticket: the owner's decision came AFTER the latest reopen on the thread."""
    if not feature.get("reopen_count"):
        return True
    decided_at = float(feature.get("decided_at") or 0)
    last_reopen = 0.0
    for entry in feature.get("thread") or []:
        if entry.get("kind") == "status" and str(entry.get("body", "")).startswith("reopened (occurrence"):
            last_reopen = max(last_reopen, float(entry.get("ts") or 0))
    return decided_at > last_reopen


def eligible(feature: dict[str, Any], cfg: dict[str, Any]) -> str:
    """Why a ticket may not auto-start, or '' when it may. Checked again under the lock at
    reservation AND at launch; this is the pre-filter a poller uses before reserving."""
    if feature.get("status") != "approved":
        return f"status is {feature.get('status')}, not approved"
    if feature.get("mission_id"):
        return "already has a mission"
    if feature.get("reopen_count") and not cfg["include_reopened"]:
        return "reopened tickets stay manual (auto_pickup.include_reopened is off)"
    if feature.get("reopen_count") and not _fresh_approval(feature):
        return "the owner's approval predates the latest reopen"
    return ""


def reserve(feature_id: str, *, picker: str, path: str | Path | None = None,
            now: float | None = None, stale_after: float = DEFAULT_STALE_AFTER) -> dict[str, Any]:
    """Atomically reserve an approved, unstarted ticket for this picker process.

    flock-serialized against every other poller, so simultaneous pollers cannot both win.
    Raises PickupError when the ticket is not startable or another picker holds a live
    reservation. A stale reservation (older than stale_after, still no mission) is taken over:
    the previous picker crashed between reserving and launching."""
    target = Path(path or backlog.default_path())
    now = time.time() if now is None else now
    with open(_lock_path(target), "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = backlog._read(target)
        for feature in data["features"]:
            if feature.get("id") != feature_id:
                continue
            held = feature.get("pickup") or {}
            if held.get("picker") and held.get("picker") != picker:
                if now - float(held.get("at") or 0) < stale_after:
                    raise PickupError(f"ticket {feature_id} is reserved by {held['picker']}")
                # crashed picker's reservation: take it over
            if feature.get("status") != "approved" or feature.get("mission_id"):
                raise PickupError(
                    f"ticket {feature_id} is {feature.get('status')}"
                    + (" with a mission" if feature.get("mission_id") else ""))
            feature["pickup"] = {"picker": str(picker), "at": now}
            backlog._write(target, data)
            return dict(feature)
        raise PickupError(f"unknown ticket {feature_id!r}")


def launch(feature_id: str, *, picker: str, path: str | Path | None = None,
           create_mission: Callable[[dict[str, Any]], str] | None = None) -> dict[str, Any]:
    """Re-validate under the lock and attach the new mission. Revocation-safe: if the ticket
    stopped being approved (or changed otherwise) since reserve(), nothing starts and the
    reservation is cleared. create_mission receives the ticket snapshot and must return the
    new mission id; it runs INSIDE the lock so no second picker can interleave."""
    target = Path(path or backlog.default_path())
    with open(_lock_path(target), "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = backlog._read(target)
        for feature in data["features"]:
            if feature.get("id") != feature_id:
                continue
            held = feature.get("pickup") or {}
            if held.get("picker") != picker:
                raise PickupError(f"ticket {feature_id} is not reserved by {picker}")
            if feature.get("status") != "approved" or feature.get("mission_id"):
                feature.pop("pickup", None)
                backlog._write(target, data)
                raise PickupError(
                    f"ticket {feature_id} changed before launch "
                    f"(status {feature.get('status')}); start aborted")
            snapshot = {"goal": f"{feature['title']}: {feature['brief']}", "plan": True,
                        "project": feature.get("project", "")}
            mission_id = create_mission(snapshot) if create_mission else ""
            if not mission_id:
                feature.pop("pickup", None)
                backlog._write(target, data)
                raise PickupError("mission creation failed; reservation cleared")
            feature["mission_id"] = mission_id
            feature.pop("pickup", None)
            backlog._thread(feature, actor="auto-pickup", kind="status",
                            body=f"started mission {mission_id} automatically under "
                                 "auto_pickup (owner-approved ticket; step contracts still "
                                 "pass the normal approval gate)")
            backlog._write(target, data)
            return {"feature_id": feature_id, "mission_id": mission_id, "goal": snapshot["goal"],
                    "project": snapshot["project"]}
        raise PickupError(f"unknown ticket {feature_id!r}")


def abort(feature_id: str, *, picker: str, path: str | Path | None = None, reason: str = "") -> None:
    """Clear this picker's reservation without starting (error paths, revoked approval)."""
    target = Path(path or backlog.default_path())
    with open(_lock_path(target), "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = backlog._read(target)
        for feature in data["features"]:
            if feature.get("id") == feature_id and (feature.get("pickup") or {}).get("picker") == picker:
                feature.pop("pickup", None)
                if reason:
                    backlog._thread(feature, actor="auto-pickup", kind="status",
                                    body=f"automatic start aborted: {reason}")
                backlog._write(target, data)
            return
