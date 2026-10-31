"""The approval boundary of a session: the trust policy, grants and the ledger.

Every approval the runtime asks for — and every command a host tool is about
to run — passes through :meth:`Approvals._approve`: the policy in ``trust``
settles it (read-only command, accept-edits mode, a session grant, a promoted
ledger entry) or it goes to the owner. The owner's answer is recorded: a
session grant for "Allow for this session", and a vote in the ledger.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

from .. import trust

if TYPE_CHECKING:
    from .runtime import SessionRuntime


def approval_summary(payload: dict) -> dict:
    return {
        "tool_call_id": payload.get("tool_call_id"),
        "sender": payload.get("sender"),
        "action": payload.get("action"),
        "description": payload.get("description"),
    }


class Approvals:
    """Mixed into ``SessionRuntime``."""

    pending: dict[str, tuple[asyncio.Future, dict]]

    async def _approve(self: "SessionRuntime", payload: dict) -> str:
        # The session tool scope is a CEILING above the trust policy: a grant or ledger entry may settle an
        # approval, but nothing settles an action whose tool is outside the session's scope (e.g. after
        # ``adopt_role`` narrowed it). This is the same-turn enforcement point for native runtime tools, which
        # only consult the host here - reads inside the workspace do not ask and stay possible until the
        # runtime re-initializes next turn (see registry.adopt_role's stated limits).
        sender = str(payload.get("sender") or "").strip()
        if sender:
            from .. import catalogue

            if not catalogue.allowed(self.tenant, sender, self):
                summary = {**approval_summary(payload), "key": f"scope:{sender}"}
                self.emit(
                    "ApprovalResolved",
                    {"request_id": payload.get("id"), "response": "reject", "auto": True,
                     "reason": f"`{sender}` is outside this session's tool scope", "mode": self.record.permission_mode, **summary},
                )
                return "reject"
        mode = self.record.permission_mode
        ledger = self.tenant.trust
        decision = trust.decide(
            payload,
            mode=mode,
            ledger=ledger,
            grants=set(self.record.grants),
            ask_owner=self.record.kind != "candidate" and not self.record.unattended,
            workspace=self.record.workspace,
            own_roots=self.own_roots(),
            roam=self.tenant.server.mode == "local",
        )
        summary = {**approval_summary(payload), "key": decision.key}
        if decision.response is not None:
            self.emit(
                "ApprovalResolved",
                {"request_id": payload.get("id"), "response": decision.response, "auto": True, "reason": decision.reason, "mode": mode, **summary},
            )
            return decision.response
        request = {**payload, "trust": decision.trust_info(ledger)}
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        request_id = str(payload.get("id") or uuid.uuid4())
        request["id"] = request_id
        self.pending[request_id] = (future, request)
        self.emit("ApprovalRequest", request)
        try:
            response, source = await future
        finally:
            self.pending.pop(request_id, None)
        if source != "owner":
            # Settled by a mode switch, a cancel, or the runtime exiting: not an owner vote.
            self.emit("ApprovalResolved", {"request_id": request_id, "response": response, "auto": source == "mode", "reason": source, **summary})
            return response
        self.emit("ApprovalResolved", {"request_id": request_id, "response": response, "auto": False, **summary})
        return self._owner_decision(decision, response)

    def own_roots(self: "SessionRuntime") -> tuple[str, ...]:
        """Folders that belong to this session: its scratch folder and the owner's Python toolbox."""
        roots = [str(self.scratch_dir)]
        toolbox = getattr(self.tenant, "toolbox", None)
        if toolbox is not None:
            roots.append(str(toolbox.root))
        return tuple(roots)

    async def approve_host_command(
        self: "SessionRuntime", call_id: str, command: str, verb: str, sender: str = "process", display: list[dict[str, Any]] | None = None
    ) -> bool:
        """Put a command a host tool will run through the same policy as `Shell`."""
        payload = {
            "id": f"host-{uuid.uuid4().hex[:12]}",
            "tool_call_id": call_id,
            "sender": sender,
            "action": trust.ACTION_RUN_COMMAND,
            "description": f"{verb}: {command}",
            "display": display or [{"type": "shell", "language": "sh", "command": command}],
        }
        return await self._approve(payload) == "approve"

    async def approve_host_action(self: "SessionRuntime", call_id: str, action: str, description: str, display: list[dict[str, Any]] | None = None) -> bool:
        """Ask for a host action that is not a command (never ledger-promotable)."""
        payload = {
            "id": f"host-{uuid.uuid4().hex[:12]}",
            "tool_call_id": call_id,
            "sender": "root",
            "action": action,
            "description": description,
            "display": display or [],
        }
        return await self._approve(payload) == "approve"

    def _owner_decision(self: "SessionRuntime", decision: trust.Decision, response: str) -> str:
        """Record what the owner chose; return the runtime's answer."""
        approved = response in ("approve", "approve_for_session")
        if response == "approve_for_session" and decision.key not in self.record.grants:
            self.record.grants.append(decision.key)
            self.save_record()
        if decision.promotable:
            outcome = self.tenant.trust.record(decision.key, approved=approved)
            if outcome["just_promoted"] or outcome["just_demoted"]:
                self.emit(
                    "TrustChanged",
                    {"key": decision.key, "promoted": outcome["promoted"], "approvals": outcome["approvals"], "promote_after": trust.PROMOTE_AFTER},
                )
        # The runtime caches approve_for_session per action string ("run
        # command"), which would skip the owner for every later command. The
        # host holds the grant for exactly this action class instead.
        return "approve" if approved else "reject"

    def _settle_pending(self, response: str, source: str) -> None:
        for future, _ in list(self.pending.values()):
            if not future.done():
                future.set_result((response, source))

    def resolve_approval(self, request_id: str, response: str) -> bool:
        entry = self.pending.get(request_id)
        if entry is None or entry[0].done():
            return False
        if response not in ("approve", "approve_for_session", "reject"):
            response = "reject"
        entry[0].set_result((response, "owner"))
        return True

    def revoke_grant(self: "SessionRuntime", key: str) -> bool:
        if key not in self.record.grants:
            return False
        self.record.grants.remove(key)
        self.save_record()
        self.emit("SessionUpdated", self.public(), persist=False)
        return True

    def _settle_for_mode(self: "SessionRuntime", mode: str) -> None:
        """A more permissive mode also settles what is already waiting."""
        for future, payload in list(self.pending.values()):
            decision = trust.decide(payload, mode=mode, ledger=self.tenant.trust, grants=set(self.record.grants), workspace=self.record.workspace, own_roots=self.own_roots(), roam=self.tenant.server.mode == "local")
            if decision.response is not None and not future.done():
                future.set_result((decision.response, "mode"))
