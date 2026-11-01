"""The team backlog: features a team PROPOSES, the owner DECIDES, only then work starts.

This is the product loop: missions may emit {"propose_features": [...]} - each lands here as a
PENDING feature under its project, with provenance (which mission/team proposed it). Nothing
proposed ever runs: an owner decision (approve/reject) is recorded first, and only an APPROVED
feature can be turned into a mission (a planner mission under the same project - the engine's
existing intake). Persisted in skye.backlog.json (override with SKYE_BACKLOG), atomic writes,
owner-managed exactly like the policy and roles registry. No timers, no polling: proposals
arrive when a mission settles; the next step waits on the owner, not on a schedule.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

DEFAULT_PATH = "skye.backlog.json"
STATUSES = ("pending", "approved", "awaiting-verification", "delivered", "rejected")


class BacklogError(ValueError):
    pass


def default_path() -> str:
    return os.environ.get("SKYE_BACKLOG", "").strip() or DEFAULT_PATH


def _read(path: str | Path | None = None) -> dict[str, Any]:
    try:
        data = json.loads(Path(path or default_path()).read_text("utf-8"))
    except (OSError, ValueError):
        return {"features": []}
    if not isinstance(data, dict) or not isinstance(data.get("features"), list):
        return {"features": []}
    return data


def _write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    tmp.replace(path)


def list_features(path: str | Path | None = None,
                  project: str = "", status: str = "") -> list[dict[str, Any]]:
    rows = [f for f in _read(path)["features"] if f.get("id")]
    if project:
        rows = [f for f in rows if f.get("project") == project]
    if status:
        rows = [f for f in rows if f.get("status") == status]
    return rows


def get(feature_id: str, path: str | Path | None = None) -> dict[str, Any]:
    for feature in list_features(path):
        if feature.get("id") == feature_id:
            return feature
    raise BacklogError(f"unknown feature {feature_id!r}")


def propose(entries: list[dict[str, Any]], *, project: str = "", mission: str = "",
            path: str | Path | None = None) -> list[dict[str, Any]]:
    """Record team-proposed features as PENDING. Validated like contracts: a feature without a
    title or brief is rejected before it reaches the owner. Duplicates (same title+project
    already pending/approved) are skipped, not doubled."""
    target = Path(path or default_path())
    data = _read(target)
    existing = {(f.get("project"), str(f.get("title")).strip().lower())
                for f in data["features"] if f.get("status") in ("pending", "approved")}
    added = []
    for entry in entries:
        title = str(entry.get("title") or "").strip()
        brief = str(entry.get("brief") or "").strip()
        if not title or not brief:
            raise BacklogError("a proposed feature needs a title and a brief")
        if (project, title.lower()) in existing:
            continue
        record = {"id": f"feat-{secrets.token_hex(4)}", "project": project,
                  "title": title, "brief": brief, "status": "pending",
                  "proposed_by": mission, "proposed_at": time.time(),
                  "decided_at": None, "decision_note": "", "mission_id": ""}
        data["features"].append(record)
        existing.add((project, title.lower()))
        added.append(record)
    if added:
        _write(target, data)
    return added


def decide(feature_id: str, decision: str, *, note: str = "",
           path: str | Path | None = None) -> dict[str, Any]:
    """The owner's decision. Only pending -> approved/rejected; delivered is terminal, set by
    the engine when the feature's mission settles done."""
    if decision not in ("approved", "rejected"):
        raise BacklogError(f"decision must be approved or rejected, got {decision!r}")
    target = Path(path or default_path())
    data = _read(target)
    for feature in data["features"]:
        if feature.get("id") == feature_id:
            if feature.get("status") != "pending":
                raise BacklogError(
                    f"feature {feature_id} is {feature.get('status')}; only pending features can be decided")
            feature["status"] = decision
            feature["decided_at"] = time.time()
            feature["decision_note"] = note
            _write(target, data)
            return feature
    raise BacklogError(f"unknown feature {feature_id!r}")


def mark_started(feature_id: str, mission_id: str, path: str | Path | None = None) -> None:
    target = Path(path or default_path())
    data = _read(target)
    for feature in data["features"]:
        if feature.get("id") == feature_id:
            feature["mission_id"] = mission_id
            _write(target, data)
            return


def mark_delivered(mission_id: str, *, journey_ok: bool = False,
                   evidence: str = "", path: str | Path | None = None) -> None:
    """A settled-done mission ends its feature's work - called from the mission path on done.

    'Done' is NOT 'delivered': the user-facing delivered state requires the affected user
    journey to have been replayed and passed. With journey evidence the feature lands
    DELIVERED; without it the feature waits as AWAITING-VERIFICATION until a journey replay
    passes (verify() below)."""
    target = Path(path or default_path())
    data = _read(target)
    changed = False
    for feature in data["features"]:
        if feature.get("mission_id") == mission_id and feature.get("status") == "approved":
            feature["status"] = "delivered" if journey_ok else "awaiting-verification"
            feature["verification"] = {"journey_passed": bool(journey_ok),
                                       "evidence": evidence[-2000:], "verified_at": time.time()}
            changed = True
    if changed:
        _write(target, data)


def send_back(feature_id: str, *, note: str = "",
              path: str | Path | None = None) -> dict[str, Any]:
    """A failed journey replay sends the work back: awaiting-verification -> approved, so the
    owner can start the next repair attempt. The failing evidence stays on the record."""
    target = Path(path or default_path())
    data = _read(target)
    for feature in data["features"]:
        if feature.get("id") == feature_id:
            if feature.get("status") != "awaiting-verification":
                raise BacklogError(
                    f"feature {feature_id} is {feature.get('status')}; "
                    "only work awaiting verification can be sent back")
            feature["status"] = "approved"
            feature["mission_id"] = ""
            if note:
                feature["decision_note"] = note
            _write(target, data)
            return feature
    raise BacklogError(f"unknown feature {feature_id!r}")


def record_verification(feature_id: str, passed: bool, *, evidence: str = "",
                        path: str | Path | None = None) -> dict[str, Any]:
    """The outcome of replaying the user journey for a feature. Only a PASS moves a feature
    to delivered; a failure records the evidence and leaves the feature awaiting
    verification. Verification can only run against work that was owner-approved AND
    actually started (awaiting-verification, with its mission recorded): a pending,
    rejected, or merely approved-but-unstarted feature has no delivered attempt to
    verify, and a delivered feature stays delivered. Verification never skips the owner
    approval that started the work - it gates DELIVERY, not authorization."""
    target = Path(path or default_path())
    data = _read(target)
    for feature in data["features"]:
        if feature.get("id") == feature_id:
            if feature.get("status") != "awaiting-verification" or not feature.get("mission_id"):
                raise BacklogError(
                    f"feature {feature_id} is {feature.get('status')}"
                    f"{' with no mission' if not feature.get('mission_id') else ''}; "
                    "only completed work awaiting verification can be replayed")
            feature["verification"] = {"journey_passed": bool(passed),
                                       "evidence": evidence[-2000:], "verified_at": time.time()}
            feature["status"] = "delivered" if passed else "awaiting-verification"
            _write(target, data)
            return feature
    raise BacklogError(f"unknown feature {feature_id!r}")
