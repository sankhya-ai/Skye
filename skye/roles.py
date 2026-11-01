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
    """Every role a contract may name: builtins plus the owner's ACTIVE registry roles.
    A worker-PROPOSED agent (status "proposed") is deliberately absent: it becomes a
    capability only when the owner promotes it (agents.promote / `skye agents promote`)."""
    names = list(BUILTIN_ROLES)
    for entry in _read(path)["roles"]:
        if entry.get("status", "active") == "proposed":
            continue
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
               "description": str(entry.get("description") or ""),
               "status": str(entry.get("status") or "active"),
               "proposed_by": str(entry.get("proposed_by") or ""),
               "has_instructions": bool(str(entry.get("instructions") or "").strip())}
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
    # Definition fields (the agent schema): instructions are the capability text, extends
    # composes on a base definition, status gates usability ("proposed" cannot run),
    # source/proposed_by keep provenance. Missing fields default to an active owner role -
    # back-compatible with registries written before the schema existed.
    record["instructions"] = str(entry.get("instructions") or "").strip()
    record["extends"] = str(entry.get("extends") or "").strip()
    record["version"] = int(entry.get("version") or 1)
    record["status"] = str(entry.get("status") or "active")
    record["source"] = str(entry.get("source") or "owner")
    record["proposed_by"] = str(entry.get("proposed_by") or "")
    target = Path(path or default_path())
    data = _read(target)
    data["roles"] = [r for r in data["roles"] if r.get("name") != name]
    data["roles"].append(record)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    tmp.replace(target)  # atomic, like the approvals store
    return record


def instructions_for(role: str, path: str | Path | None = None) -> str:
    """Owner-authored guidance for a role, from the same owner-managed registry file.

    A registry entry may carry "instructions": taste/process guidance ("how our designer
    works") that Skye prepends to the worker's goal at dispatch. This is the owner's voice
    reaching the worker, never a worker editing its own behavior: the registry file is
    owner-managed like the policy, and the mission contract + approval fingerprint are
    untouched. May annotate a builtin role (the shipped "designer") or a registry one."""
    for entry in _read(path)["roles"]:
        if entry.get("name") == role:
            return str(entry.get("instructions") or "").strip()
    return ""


def promote(name: str, path: str | Path | None = None) -> dict[str, Any]:
    """Activate a worker-proposed agent: the owner's decision that this capability may run.
    Only proposed -> active; anything else is an error, never a silent no-op."""
    target = Path(path or default_path())
    data = _read(target)
    for entry in data["roles"]:
        if entry.get("name") == name:
            if entry.get("status", "active") != "proposed":
                raise RoleError(f"agent {name!r} is not waiting for promotion "
                                f"(status: {entry.get('status', 'active')})")
            entry["status"] = "active"
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
            tmp.replace(target)
            return entry
    raise RoleError(f"unknown agent {name!r}")
