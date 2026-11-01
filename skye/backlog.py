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
import re
import secrets
import time
from pathlib import Path
from typing import Any

DEFAULT_PATH = "skye.backlog.json"
STATUSES = ("pending", "approved", "awaiting-verification", "delivered", "reopened",
            "rejected")

# Labels are how the team sorts a ticket without reading it: type:* (bug/feature/chore),
# area:* (frontend/backend/...), severity:*, or a plain owner word. Validated like everything
# else owner- or worker-supplied - a label is data other flows filter on, never free text.
LABEL = re.compile(r"^[a-z][a-z0-9-]{0,29}(:[a-z][a-z0-9-]{0,29})?$")
MAX_LABELS = 12
THREAD_KINDS = ("comment", "evidence", "verdict", "status", "discussion")
MAX_THREAD_BODY = 4000


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
                  "kind": str(entry.get("kind") or "feature"),
                  "labels": validate_labels(entry.get("labels") or []),
                  "proposed_by": mission, "proposed_at": time.time(),
                  "decided_at": None, "decision_note": "", "mission_id": "",
                  "missions": [], "thread": [], "reopen_count": 0}
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
            if feature.get("status") not in ("pending", "reopened"):
                raise BacklogError(
                    f"feature {feature_id} is {feature.get('status')}; only pending or "
                    "reopened tickets can be decided")
            if feature.get("status") == "reopened":
                _thread(feature, actor="owner", kind="status",
                        body=f"repair {decision}" + (f": {note}" if note else ""))
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
            missions = feature.setdefault("missions", [])
            if mission_id and mission_id not in missions:
                missions.append(mission_id)  # every attempt stays linked; the ticket outlives runs
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
            previous = feature.get("verification") or {}
            # None = "no replay has run yet" - a real False is only ever written by
            # record_verification after an actual replay failed. The UI relies on this
            # to tell "needs review" apart from "verification failed".
            feature["verification"] = {"journey_passed": True if journey_ok else None,
                                       "evidence": evidence[-2000:], "verified_at": time.time(),
                                       "journey_digest": previous.get("journey_digest", "")}
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
                        repo_revision: str = "", journey_digest: str = "",
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
            previous = feature.get("verification") or {}
            feature["verification"] = {
                "journey_passed": bool(passed),
                "evidence": evidence[-2000:], "verified_at": time.time(),
                # bound to the actual attempt: the finished mission, the repo tree replayed,
                # and the check's own digest - a changed digest vs the prior replay means the
                # CHECK changed between runs (worker could have rewritten it; surfaced, and the
                # docs say to keep checks outside the writable repo for hard integrity).
                "mission_id": feature.get("mission_id", ""),
                "repo_revision": repo_revision,
                "journey_digest": journey_digest,
                "journey_changed": bool(journey_digest and previous.get("journey_digest")
                                        and previous.get("journey_digest") != journey_digest)}
            feature["status"] = "delivered" if passed else "awaiting-verification"
            _write(target, data)
            return feature
    raise BacklogError(f"unknown feature {feature_id!r}")


def validate_labels(labels: list[Any]) -> list[str]:
    """Normalize and validate labels; bad labels fail loudly, they are filter data."""
    if not isinstance(labels, list):
        raise BacklogError("labels must be a list")
    out: list[str] = []
    for label in labels:
        clean = str(label or "").strip().lower()
        if not LABEL.match(clean):
            raise BacklogError(
                f"label {label!r} must be lowercase a-z 0-9 -, optionally namespaced (area:frontend)")
        if clean not in out:
            out.append(clean)
        if len(out) > MAX_LABELS:
            raise BacklogError(f"a ticket carries at most {MAX_LABELS} labels")
    return out


def set_labels(feature_id: str, labels: list[Any], *, actor: str = "owner",
               path: str | Path | None = None) -> dict[str, Any]:
    """Apply labels to a ticket (replaces the set), journaled on the thread."""
    clean = validate_labels(labels)
    target = Path(path or default_path())
    data = _read(target)
    for feature in data["features"]:
        if feature.get("id") == feature_id:
            old = feature.get("labels") or []
            feature["labels"] = clean
            if clean != old:
                _thread(feature, actor=actor, kind="status",
                        body=f"labels: {', '.join(clean) if clean else '(cleared)'}")
            _write(target, data)
            return feature
    raise BacklogError(f"unknown feature {feature_id!r}")


def _thread(feature: dict[str, Any], *, actor: str, kind: str, body: str,
            refs: list[str] | None = None) -> dict[str, Any]:
    entry = {"ts": time.time(), "actor": str(actor or "owner")[:40], "kind": kind,
             "body": str(body or "")[:MAX_THREAD_BODY],
             "refs": [str(r)[:300] for r in (refs or [])][:16]}
    feature.setdefault("thread", []).append(entry)
    return entry


def add_thread(feature_id: str, *, actor: str, kind: str, body: str,
               refs: list[str] | None = None, path: str | Path | None = None) -> dict[str, Any]:
    """Append to the ticket's permanent thread: discussion, evidence, verdicts, status notes.
    The thread is the ticket's memory - later repairs read it instead of re-asking."""
    if kind not in THREAD_KINDS:
        raise BacklogError(f"thread kind must be one of {THREAD_KINDS}, got {kind!r}")
    if not str(body or "").strip():
        raise BacklogError("an empty thread entry says nothing")
    target = Path(path or default_path())
    data = _read(target)
    for feature in data["features"]:
        if feature.get("id") == feature_id:
            entry = _thread(feature, actor=actor, kind=kind, body=body, refs=refs)
            _write(target, data)
            return entry
    raise BacklogError(f"unknown feature {feature_id!r}")


def reopen(feature_id: str, *, evidence: str, labels: list[Any] | None = None,
           actor: str = "owner", path: str | Path | None = None) -> dict[str, Any]:
    """A bug on DELIVERED work reopens the SAME ticket - never a fresh, context-free one.

    The ticket keeps its whole thread (the original discussion, work and review), gains the
    bug evidence and labels, and moves delivered -> reopened. From there the owner decides
    (decide(): reopened -> approved) and the normal intake starts the repair mission - with
    the original context attached. Reopening any other status is an error: pending/approved
    work is not delivered yet, and a rejected ticket stays rejected."""
    target = Path(path or default_path())
    data = _read(target)
    for feature in data["features"]:
        if feature.get("id") == feature_id:
            if feature.get("status") != "delivered":
                raise BacklogError(
                    f"feature {feature_id} is {feature.get('status')}; only delivered work reopens "
                    "(use send_back for work awaiting verification)")
            feature["status"] = "reopened"
            feature["mission_id"] = ""  # the delivered attempt is history (missions[] keeps it)
            feature["reopen_count"] = int(feature.get("reopen_count") or 0) + 1
            if labels:
                merged = list(feature.get("labels") or [])
                for label in validate_labels(labels):
                    if label not in merged:
                        merged.append(label)
                feature["labels"] = merged[:MAX_LABELS]
            _thread(feature, actor=actor, kind="evidence",
                    body=str(evidence or "").strip() or "bug reported (no detail given)")
            _thread(feature, actor=actor, kind="status",
                    body=f"reopened (occurrence {feature['reopen_count']}); "
                         "the owner decides whether the repair runs")
            _write(target, data)
            return feature
    raise BacklogError(f"unknown feature {feature_id!r}")


def fold_discussion(mission_id: str, entries: list[dict[str, Any]], *,
                    path: str | Path | None = None, cap: int = 50) -> int:
    """Fold a settled mission's team-line discussion into its ticket's thread, so the ticket -
    the permanent home - keeps the team's working conversation, not just outcomes.
    Returns how many entries landed (0 when no ticket links this mission)."""
    target = Path(path or default_path())
    data = _read(target)
    folded = 0
    for feature in data["features"]:
        linked = feature.get("mission_id") == mission_id or mission_id in (feature.get("missions") or [])
        if not linked:
            continue
        thread = feature.setdefault("thread", [])
        already = {e.get("refs", [None])[0] for e in thread if e.get("kind") == "discussion"}
        for entry in entries[:cap]:
            marker = str(entry.get("id") or "")
            if marker and marker in already:
                continue
            thread.append({"ts": entry.get("ts", time.time()),
                           "actor": str(entry.get("author") or "?"),
                           "kind": "discussion",
                           "body": f"[{entry.get('kind', 'note')}] {entry.get('body', '')}"[:MAX_THREAD_BODY],
                           "refs": [marker] if marker else []})
            folded += 1
    if folded:
        _write(target, data)
    return folded


def pickup_due(*, path: str | Path | None = None) -> list[dict[str, Any]]:
    """Reopened tickets whose repair the owner APPROVED but that have not started: the queue a
    human (or a deliberately configured automation, outside this repo's defaults) picks from.
    Read-only - listing due work never starts it; starting rides the normal intake and policy."""
    return [f for f in list_features(path)
            if f.get("status") == "approved" and f.get("reopen_count") and not f.get("mission_id")]
