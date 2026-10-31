"""What a session persists, and what one turn accumulates while it runs."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MODES: tuple[str, ...] = ("default", "accept_edits", "plan", "yolo")


#: Sessions that answer someone else: a teammate's agent on the line, a team chat
#: (Slack), a conversation Keiki brings (WhatsApp, iMessage, Telegram…), or Maritime's chat.
ANSWERING_KINDS = frozenset({"line", "slack", "keiki", "chat"})


@dataclass
class SessionRecord:
    id: str
    workspace: str
    title: str = "New session"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    permission_mode: str = "default"
    turns: int = 0
    last_status: str = ""
    #: Action classes the owner allowed "for this session" (trust keys).
    grants: list[str] = field(default_factory=list)
    #: "session"; "candidate" for a coding-flow candidate run unattended; or one of
    #: ``ANSWERING_KINDS``, a read-only session that answers someone else.
    kind: str = "session"
    #: For a candidate: the session that owns its flow, and which candidate it is.
    parent: str = ""
    role: str = ""
    #: The registry role version ``role`` was adopted at (0 = built-in or none). Set by ``adopt_role``; pinned, so a
    #: newer role version never silently changes a session that adopted the old one.
    role_version: int = 0
    #: Recent ``find_tools`` misses ({"query", "at"}), newest last, capped - the deterministic capability-gap
    #: signal the owner is shown every few misses (``catalogue.capability_miss``). Set by the gateway, never the model.
    capability_misses: list[dict] = field(default_factory=list)
    #: Nobody is watching (a scheduled run): what would ask the owner is declined instead.
    unattended: bool = False
    #: The schedule that started this session, if any.
    schedule: str = ""
    #: Host-set tool-name patterns (fnmatch) this session may use, on top of the tenant's ``tool_scope``.
    #: Set when the session is created; the model can only NARROW it, one-way, by ``adopt_role`` (registry.py).
    #: Empty: no session limit. See ``catalogue.guarantee``.
    tool_scope: list[str] = field(default_factory=list)
    #: Per-job cap on model steps in a turn (``jobs``); 0 = the settings' limit.
    max_steps: int = 0
    #: Per-turn caps the runtime enforces itself (``jobs``); 0 = none. Tokens/cost are provider-reported.
    max_tokens: int = 0
    max_wall_seconds: int = 0
    max_cost_usd: float = 0.0
    #: Names of broker credentials this session's job may use (``broker``); set by the host, never by the model.
    credentials: list[str] = field(default_factory=list)
    #: Per-session jail chosen by the host: "" (the server's own sandbox), "bwrap" (files only) or "bwrap-model-only" (files and network). See ``sandbox``.
    isolation: str = ""
    #: The job role (``jobs``) when the session is a job: selects the review guides it may load. Set by the host.
    job_role: str = ""
    #: The job this session works (or worked) for, stamped by the HOST at creation - every resume worker too.
    #: Never model-supplied: reliable metadata for per-turn policy (e.g. how much memory a worker is shown).
    job: str = ""
    #: True when that job answers another job's proposal (``parent_job_id``): a DELEGATED worker, stamped by the
    #: host at creation from the accepted record. Least-context policy keys off this, not off model claims.
    delegated: bool = False
    #: What the owner authorized this job to do on pages (``visual.act``): {"interact": {origin, allow_writes}, "deployed": {urls}}. Host-set only.
    visual: dict = field(default_factory=dict)

    @property
    def answering(self) -> bool:
        """Answers someone other than the owner (another agent, a chat group): read-only, no owner accounts."""
        return self.kind in ANSWERING_KINDS

    @classmethod
    def load(cls, path: Path) -> "SessionRecord":
        data = json.loads(path.read_text("utf-8"))
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / "session.tmp"
        tmp.write_text(json.dumps(asdict(self), indent=2), "utf-8")
        tmp.replace(directory / "session.json")


@dataclass
class TurnState:
    id: str
    prompt: str
    started_at: float = field(default_factory=time.time)
    tool_calls: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_call_id: str = ""
    files_touched: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    tool_count: int = 0
    #: Tool calls that came back as errors, and approvals the owner refused.
    errors: int = 0
    rejections: int = 0
    #: Commands this turn that ran something (tests, a script, a build) rather
    #: than only read, and whether an edit has come after the last of them.
    checks: int = 0
    unchecked_edits: bool = False
    #: The workspace checkpoint taken before this turn (``checkpoints``), if any.
    checkpoint: str = ""
    #: The permission mode the turn ran in: a plan is only offered for approval after a plan-mode turn.
    mode: str = ""

    def note_edit(self) -> None:
        self.unchecked_edits = True

    def note_check(self) -> None:
        self.checks += 1
        self.unchecked_edits = False

    def note_command(self, command: str) -> None:
        """A command that exercises code checks the edits made before it."""
        from ..trust import classify_command

        if command.strip() and classify_command(command).tier != "read":
            self.note_check()

    @property
    def verified(self) -> bool:
        """No edit this turn is left without a check run after it."""
        return not self.unchecked_edits

    def reply(self) -> str:
        return " ".join("".join(self.text).split())

    def trace(self, limit: int = 40) -> list[dict[str, Any]]:
        """The tool calls of this turn, compact, in order: for reviewers."""
        out = []
        for call in list(self.tool_calls.values())[:limit]:
            out.append(
                {
                    "tool": call["name"],
                    "arguments": call["arguments"][:300],
                    "status": call.get("status", "no result"),
                    "result": call.get("brief", "")[:200],
                }
            )
        return out
