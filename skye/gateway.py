"""The dynamic tool gateway: which tools a ROLE may use, resolved from data, not hard-coded into
the workers. A role catalogue is a JSON file the team owns; the dispatcher resolves the contract's
role to a catalogue and hands the ADAPTER the constraint (each adapter enforces it its own way:
Root scopes natively, Claude Code via --tools/--disallowedTools, Codex via its exec sandbox mode. Both external mechanisms are CLI-level configuration, not an OS security boundary.)"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Shipped defaults - read-only roles and a write-capable developer. Teams override per role with
#: catalogues/<role>.json; the file WINS over this table (that is the dynamic part).
BUILTIN: dict[str, dict[str, Any]] = {
    "explorer": {"tools": ["Read", "Glob", "Grep"], "writable": False},
    "reviewer": {"tools": ["Read", "Glob", "Grep"], "writable": False},
    "designer": {"tools": ["Read", "Glob", "Grep", "inspect_page"], "writable": False},
    "pm": {"tools": ["Read", "Glob", "Grep", "inspect_page"], "writable": False},
    "tester": {"tools": ["Read", "Glob", "Grep", "Bash"], "writable": False},
    "developer": {"tools": ["Read", "Glob", "Grep", "Write", "Edit", "Bash"], "writable": True},
    "devops": {"tools": ["Read", "Glob", "Grep", "Bash"], "writable": True},
}


def resolve(role: str, catalogues_dir: str | Path = "catalogues") -> dict[str, Any]:
    """The role's effective tool catalogue: a team file when present, else the shipped default.
    An unknown role fails loudly - a typo must not silently widen or narrow a worker's reach."""
    path = Path(catalogues_dir) / f"{role}.json"
    if path.exists():
        data = json.loads(path.read_text("utf-8"))
        return {"tools": list(data.get("tools") or []), "writable": bool(data.get("writable")), "source": str(path)}
    from .roles import catalogue_for
    registry = catalogue_for(role)
    if registry is not None:
        return {**registry, "source": "roles-registry"}
    if role in BUILTIN:
        return {**BUILTIN[role], "source": "builtin"}
    raise KeyError(f"no tool catalogue for role '{role}' (neither {path} nor a builtin)")
