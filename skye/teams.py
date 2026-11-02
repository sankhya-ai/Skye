"""Saved teams: a durable lead + member roster the owner creates once and reuses per job.

A team is DATA, owner-managed exactly like the roles registry and the policy file
(skye.teams.json, override with SKYE_TEAMS): a lead, the member agents the lead may delegate
to, an optional set of projects the team is assigned to, and an optional handoff note (the
owner's conventions for how work passes between members - it rides the lead's brief as text,
never code). Saving a team runs nothing and grants nothing.

What a saved team changes at run time, and what it deliberately does not:

- A Team task starts with the LEAD's planning session (the same nonwriting planner step an
  ad-hoc task starts with), and the lead's brief names the roster it may use.
- The roster is enforced, not just suggested: the mission engine rejects any lead-proposed
  step whose role is not a saved member (journaled as proposal-rejected), so team selection
  can never widen who runs - every proposed member session still passes the ordinary
  dispatcher path (policy rule or one-time owner approval, concurrency cap, role catalogue,
  gates, journal) before anything runs.
- Handoffs stay settled-outcome handoffs plus the async team line: a member's result feeds
  the next brief, and teammates read posts when they poll. The lead cannot push into a
  member's running session - live steering stays the owner's own follow-up channel.

The roster is snapshotted into the mission at load time (mission["team"]), so editing or
deleting the saved team afterwards cannot silently retarget a mission already defined.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any

DEFAULT_PATH = "skye.teams.json"
_NAME = re.compile(r"^.{1,60}$")
MAX_HANDOFF = 4000
MAX_MEMBERS = 24
MAX_PROJECTS = 24


class TeamError(ValueError):
    pass


def default_path() -> str:
    return os.environ.get("SKYE_TEAMS", "").strip() or DEFAULT_PATH


def _read(path: str | Path | None = None) -> dict[str, Any]:
    try:
        data = json.loads(Path(path or default_path()).read_text("utf-8"))
    except (OSError, ValueError):
        return {"teams": []}
    if not isinstance(data, dict) or not isinstance(data.get("teams"), list):
        return {"teams": []}
    return data


def _write(data: dict[str, Any], path: str | Path | None = None) -> None:
    target = Path(path or default_path())
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    tmp.replace(target)  # atomic, like the roles registry


def validate_team(entry: dict[str, Any], path: str | Path | None = None) -> dict[str, Any]:
    """Validate a team like a contract: a bad team fails before it reaches the registry.
    Returns the normalized record. Lead and members must be ACTIVE known roles (a
    worker-proposed agent is not a capability until the owner promotes it, so it cannot be
    saved into a roster either). The lead plans; the members are who it may delegate to."""
    if not isinstance(entry, dict):
        raise TeamError("a team is a JSON object")
    from . import roles
    known = set(roles.known())
    name = str(entry.get("name") or "").strip()
    if not _NAME.match(name):
        raise TeamError("a team needs a name (1-60 chars)")
    lead = str(entry.get("lead") or "").strip()
    if lead not in known:
        raise TeamError(f"team lead must be an active agent, got {lead!r}")
    from . import gateway
    try:
        catalogue = gateway.resolve(lead)
    except KeyError as error:
        raise TeamError(f"team lead: {error}") from error
    if catalogue.get("writable"):
        raise TeamError(
            f"team lead '{lead}' is writable (catalogue source: {catalogue['source']}); the "
            "lead runs the planning session, which is a nonwriting context step - narrow that "
            "role's catalogue or choose a read-only lead")
    members: list[str] = []
    for member in entry.get("members") or []:
        role = str(member or "").strip()
        if not role:
            continue
        if role not in known:
            raise TeamError(f"team member must be an active agent, got {role!r}")
        if role == lead:
            raise TeamError(f"the lead '{lead}' plans; it cannot also be its own member")
        if role in members:
            continue
        members.append(role)
    if not members:
        raise TeamError("a team needs at least one member the lead may delegate to")
    if len(members) > MAX_MEMBERS:
        raise TeamError(f"a team is bounded at {MAX_MEMBERS} members")
    projects: list[str] = []
    for project in entry.get("projects") or []:
        project_id = str(project or "").strip()
        if not project_id:
            continue
        from . import projects as projects_module
        if projects_module.get(project_id) is None:
            raise TeamError(f"team is assigned to unknown project {project_id!r}")
        if project_id not in projects:
            projects.append(project_id)
    if len(projects) > MAX_PROJECTS:
        raise TeamError(f"a team is bounded at {MAX_PROJECTS} project assignments")
    handoff = str(entry.get("handoff") or "").strip()
    if len(handoff) > MAX_HANDOFF:
        raise TeamError(f"handoff notes are {len(handoff)} chars; the bound is {MAX_HANDOFF}")
    return {"id": str(entry.get("id") or "").strip() or f"team-{secrets.token_hex(4)}",
            "name": name, "lead": lead, "members": members,
            "description": str(entry.get("description") or "").strip()[:280],
            "handoff": handoff, "projects": projects}


def list_teams(path: str | Path | None = None) -> list[dict[str, Any]]:
    return [t for t in _read(path)["teams"] if t.get("id") and t.get("name")]


def get(team: str, path: str | Path | None = None) -> dict[str, Any] | None:
    """A team by id OR name (the composer and mission files may carry either)."""
    wanted = str(team or "").strip()
    for record in list_teams(path):
        if record["id"] == wanted or record["name"] == wanted:
            return record
    return None


def _locked(path: str | Path | None, fn):
    """Run fn(data) -> result under an exclusive flock on a sibling lock file, writing the
    (possibly mutated) data back atomically before releasing. Two writers on this machine
    (the UI server and a CLI call) can no longer lose each other's update: the whole
    read-modify-write is serialized, not just the final replace."""
    target = Path(path or default_path())
    lock_path = target.with_suffix(target.suffix + ".lock")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            data = _read(target)
            result = fn(data)
            _write(data, target)
            return result
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def add(entry: dict[str, Any], path: str | Path | None = None) -> dict[str, Any]:
    """Record (or replace, by id) a saved team. Validated like a contract; the file write is
    atomic and the read-modify-write serialized. Saving runs nothing - the roster constrains
    missions created with it later."""
    record = validate_team(entry, path)
    expected = entry.get("revision") if isinstance(entry, dict) else None

    def _apply(data: dict[str, Any]) -> dict[str, Any]:
        existing = next((t for t in data["teams"] if t.get("id") == record["id"]), None)
        clash = next((t for t in data["teams"] if t.get("id") != record["id"]
                      and str(t.get("name") or "").strip().lower() == record["name"].lower()),
                     None)
        if clash is not None:
            raise TeamError(f"a team named {record['name']!r} already exists; team names are "
                            "unique (missions may refer to a team by name)")
        current = int((existing or {}).get("revision") or 0)
        # Optimistic concurrency: an editor that read revision N may only replace revision N.
        # A caller that sends no revision (a CLI/file edit) keeps last-writer-wins.
        if expected is not None and existing is not None and int(expected) != current:
            raise TeamError(f"team {record['name']!r} changed since it was opened "
                            f"(revision {current}, you edited {expected}); reload and retry")
        if existing is not None and "description" not in entry:
            record["description"] = str(existing.get("description") or "")
        record["revision"] = current + 1
        data["teams"] = [t for t in data["teams"] if t.get("id") != record["id"]]
        data["teams"].append(record)
        return record

    return _locked(path, _apply)


def remove(team_id: str, path: str | Path | None = None) -> dict[str, Any]:
    """Delete a saved team. Missions already created keep their snapshotted roster (the
    recorded definition of the run); only NEW missions can no longer pick the team."""

    def _apply(data: dict[str, Any]) -> dict[str, Any]:
        removed = [t for t in data["teams"] if t.get("id") == team_id]
        if not removed:
            raise TeamError(f"unknown team {team_id!r}")
        data["teams"] = [t for t in data["teams"] if t.get("id") != team_id]
        return removed[0]

    return _locked(path, _apply)
