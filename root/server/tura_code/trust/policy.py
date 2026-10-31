"""The approval policy: settle a request by mode, grants and ledger, or ask."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import boundary
from .classify import (
    ACTION_EDIT_FILE,
    ACTION_EDIT_OUTSIDE,
    ACTION_READ_OUTSIDE,
    ACTION_RUN_COMMAND,
    TIER_READ,
    TIER_VERIFY,
    Classification,
    classify_command,
    command_of,
)
from .ledger import PROMOTE_AFTER, TrustLedger


@dataclass
class Decision:
    """What the host does with one approval request."""

    key: str
    #: "approve" or "reject" when the host settles it, None to ask the owner.
    response: str | None
    reason: str
    #: Whether the owner's answer counts toward the ledger.
    promotable: bool
    classification: Classification | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def trust_info(self, ledger: TrustLedger) -> dict[str, Any]:
        entry = ledger.entry(self.key) if self.promotable else {}
        info: dict[str, Any] = {
            "key": self.key,
            "promotable": self.promotable,
            "approvals": int(entry.get("approvals") or 0),
            "promote_after": PROMOTE_AFTER,
        }
        if self.classification is not None:
            info.update(tier=self.classification.tier, effects=list(self.classification.effects), reasons=list(self.classification.reasons))
        if self.extra.get("outside"):
            info["outside"] = self.extra["outside"]
        return info


def decide(
    payload: dict[str, Any],
    *,
    mode: str,
    ledger: TrustLedger,
    grants: set[str] | frozenset[str] = frozenset(),
    ask_owner: bool = True,
    workspace: str | None = None,
    own_roots: tuple[str, ...] = (),
    roam: bool = False,
) -> Decision:
    """Settle an approval request by policy, or say it needs the owner.

    ``ask_owner=False`` is for unattended work (coding-flow candidates): what
    would go to the owner is rejected instead, and the agent adapts.

    With ``workspace``, anything that reaches outside it — a command's paths,
    a read or an edit of an outside file — is settled only by a session grant
    for that folder, or by "Auto-approve all" when ``roam`` (a local install,
    where the machine is the owner's own): see ``boundary``.

    ``own_roots`` are folders that belong to the session itself (its scratch
    folder, root's Python toolbox): not outside anything. Reading and writing
    there is free; a command there is judged like one in the workspace.
    """
    action = str(payload.get("action") or "").strip()
    sender = str(payload.get("sender") or "").strip()

    def fence(path: str, reason: str, classification: Classification | None = None) -> Decision:
        key = boundary.grant_key(path)
        if boundary.granted(path, grants):
            return Decision(key, "approve", "allowed for this session", False, classification)
        if not ask_owner:
            return Decision(key, "reject", "outside the workspace, and this run is unattended", False, classification)
        if roam and mode == "yolo":
            return Decision(key, "approve", "auto-approve all", False, classification)
        return Decision(key, None, reason, False, classification, extra={"outside": path})

    if workspace and action in (ACTION_READ_OUTSIDE, ACTION_EDIT_OUTSIDE):
        path = boundary.path_in(str(payload.get("description") or ""))
        if path and any(boundary.inside(path, root) for root in own_roots):
            return Decision(boundary.grant_key(path), "approve", "the session's own folder", False)
        if path and action == ACTION_READ_OUTSIDE and boundary.allowed(path):
            return Decision(boundary.grant_key(path), "approve", "system or toolchain location", False)
        if path:
            return fence(path, "outside the workspace")

    def settle(key: str, reason: str, promotable: bool, classification: Classification | None = None) -> Decision:
        if mode == "yolo":
            return Decision(key, "approve", "auto-approve all", promotable, classification)
        if key in grants:
            return Decision(key, "approve", "allowed for this session", promotable, classification)
        if promotable and ledger.is_promoted(key):
            return Decision(key, "approve", f"trusted after {PROMOTE_AFTER} approvals", promotable, classification)
        if not ask_owner:
            return Decision(key, "reject", "needs the owner, and this run is unattended", promotable, classification)
        return Decision(key, None, reason, promotable, classification)

    if action == ACTION_RUN_COMMAND:
        classification = classify_command(command_of(payload))
        reaches = boundary.outside_paths(command_of(payload), workspace) if workspace else []
        reaches = [p for p in reaches if not any(boundary.inside(p, root) for root in own_roots)]
        ungranted = [p for p in reaches if not boundary.granted(p, grants)]
        if ungranted:
            return fence(ungranted[0], "reaches outside the workspace", classification)
        if classification.tier == TIER_READ:
            return Decision(classification.key, "approve", "read-only command", True, classification)
        if classification.tier == TIER_VERIFY and mode in ("accept_edits", "plan", "flow"):
            return Decision(classification.key, "approve", "project check", True, classification)
        return settle(classification.key, "command", True, classification)
    if action == ACTION_EDIT_FILE:
        if mode in ("accept_edits", "flow"):
            return Decision(ACTION_EDIT_FILE, "approve", "accept edits", False)
        return settle(ACTION_EDIT_FILE, "edit", False)
    # Edits outside the workspace, host actions (a background process, a
    # coding-flow apply) and anything unknown: the owner, or a session grant.
    key = action or f"{sender or 'tool'}:(unknown)"
    return settle(key, "action", False)
