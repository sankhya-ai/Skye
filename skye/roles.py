"""Dynamic agent roles: owner-defined specialists beyond the shipped vocabulary.

The shipped ROLES (developer, designer, pm, ...) are a starting set, not a ceiling: a team
defines its own specialists ("security-auditor", "release-captain") in a roles registry file
(skye.roles.json, override with SKYE_ROLES), owner-managed exactly like the policy file. A
registry role is REAL: it validates in contracts like a builtin and carries its own tool
catalogue, so the gateway can scope what its workers may touch. Workers cannot create roles -
defining a capability is the owner's call, like editing the policy.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .contract import ROLES as BUILTIN_ROLES

DEFAULT_PATH = "skye.roles.json"
_NAME = re.compile(r"^[a-z][a-z0-9-]{0,39}$")


class RoleError(ValueError):
    pass


def default_path() -> str:
    return os.environ.get("SKYE_ROLES", "").strip() or DEFAULT_PATH


def _read(path: str | Path | None = None) -> dict[str, Any]:
    try:
        data = json.loads(Path(path or default_path()).read_text("utf-8"))
    except (OSError, ValueError):
        return {"roles": []}
    if not isinstance(data, dict) or not isinstance(data.get("roles"), list):
        return {"roles": []}
    return data


def known(path: str | Path | None = None) -> tuple[str, ...]:
    """Every role a contract may name: builtins plus the owner's registry roles."""
    names = list(BUILTIN_ROLES)
    for entry in _read(path)["roles"]:
        name = str(entry.get("name") or "")
        if name and name not in names:
            names.append(name)
    return tuple(names)


def catalogue_for(role: str, path: str | Path | None = None) -> dict[str, Any] | None:
    """The registry role's inline tool catalogue, or None when the role is not registry-defined."""
    for entry in _read(path)["roles"]:
        if entry.get("name") == role:
            catalogue = entry.get("catalogue") or {}
            return {"tools": list(catalogue.get("tools") or []),
                    "writable": bool(catalogue.get("writable"))}
    return None


def describe(path: str | Path | None = None) -> list[dict[str, Any]]:
    """All roles, builtin and registry, for display."""
    out = [{"name": name, "source": "builtin", "description": ""} for name in BUILTIN_ROLES]
    for entry in _read(path)["roles"]:
        name = str(entry.get("name") or "")
        if not name:
            continue
        row = {"name": name, "source": "registry",
               "description": str(entry.get("description") or "")}
        for i, existing in enumerate(out):
            if existing["name"] == name:  # a registry entry REFINES a builtin's catalogue
                out[i] = {**row, "source": "registry-override"}
                break
        else:
            out.append(row)
    return out


def add(entry: dict[str, Any], path: str | Path | None = None) -> dict[str, Any]:
    """Record an owner-defined role. Validated like a contract: a bad role fails before it
    reaches any worker. A registry entry may carry an inline tool catalogue; a name matching a
    builtin REFINES that builtin's catalogue (the file wins, like catalogues/<role>.json)."""
    name = str(entry.get("name") or "").strip()
    if not _NAME.match(name):
        raise RoleError(f"role name must be 1-40 chars of a-z 0-9 -, got {name!r}")
    catalogue = entry.get("catalogue") or {}
    tools = catalogue.get("tools") or []
    if not isinstance(tools, list) or any(not isinstance(t, str) for t in tools):
        raise RoleError("catalogue.tools must be a list of tool names")
    record = {"name": name, "description": str(entry.get("description") or "").strip(),
              "catalogue": {"tools": [str(t) for t in tools],
                            "writable": bool(catalogue.get("writable"))}}
    target = Path(path or default_path())
    data = _read(target)
    data["roles"] = [r for r in data["roles"] if r.get("name") != name]
    data["roles"].append(record)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    tmp.replace(target)  # atomic, like the approvals store
    return record
