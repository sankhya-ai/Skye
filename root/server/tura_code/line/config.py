"""Which line this machine is on, and where root answers from.

The line itself — invite and your name — is shared with chamber in ``~/.chamber/config.json``, so joining once puts root and
every project's Claude Code in the same chamber. root adds only where it answers
from: ``<root data dir>/line.json`` ``{"workspace": "/path/to/repo"}``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .protocol import Invite

FILE = "line.json"


def shared_home() -> Path:
    raw = os.environ.get("CHAMBER_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".chamber"


@dataclass
class LineConfig:
    invite: Invite
    owner: str
    workspace: str = ""

    def agent(self, name: str) -> str:
        return f"{self.owner}/{name}"


def load(home: Path) -> LineConfig | None:
    """The shared line, with root's workspace; None when this machine is on no line."""
    try:
        shared = json.loads((shared_home() / "config.json").read_text("utf-8"))
        invite, owner = Invite.parse(shared["invite"]), str(shared["owner"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    try:
        workspace = str(json.loads((home / FILE).read_text("utf-8")).get("workspace") or "")
    except (OSError, ValueError, AttributeError):
        workspace = ""
    return LineConfig(invite, owner, workspace)


def save_workspace(home: Path, workspace: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / FILE).write_text(json.dumps({"workspace": workspace}), "utf-8")


def state_path(home: Path, agent: str) -> Path:
    """Where an agent keeps how far it has read the line."""
    return home / "line" / f"{agent.replace('/', '__')}.json"
