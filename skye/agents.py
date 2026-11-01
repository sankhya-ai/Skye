"""Agent definitions: the standard, reusable schema that makes a role a real teammate.

A role NAME is a badge. An agent DEFINITION is the capability: instructions (the expertise the
worker receives) plus an explicit tool catalogue (what its workers may touch), versioned and
resolved from ONE registry, so every run reuses the same definition instead of rebuilding the
role in every brief. Definitions compose: an entry may EXTEND another and narrow it ("backend-dev
extends senior-dev with a tighter catalogue"), and a mission may PROPOSE a specialist at run time
({"propose_agents": [...]} in a step's output) - proposed agents land in the owner registry as
status "proposed", invisible to contracts until the owner promotes them. Defining a capability
stays the owner's call, exactly like the policy file; a worker can propose, never grant.

Sources, in precedence order (first win for a name):
  1. the owner registry file (skye.roles.json, override SKYE_ROLES) - entries may refine a
     builtin's instructions/catalogue or define new specialists;
  2. the shipped BUILTIN_AGENTS below.

The catalogue half resolves through gateway.resolve (team catalogue file > registry > builtin);
this module owns the DEFINITION half (identity, version, instructions, composition). The gateway
honest limit stands: a catalogue is CLI-level scoping, not an OS security boundary.
"""

from __future__ import annotations

import json
from typing import Any

from . import roles
from .contract import ROLES as BUILTIN_ROLE_NAMES

#: Every tool a definition may name. Kept in sync with gateway.BUILTIN's shipped tools; a team
#: widens a worker's real reach through owner-managed catalogue files, not through proposals.
TOOL_UNIVERSE = ("Read", "Glob", "Grep", "Write", "Edit", "Bash", "inspect_page")

MAX_INSTRUCTIONS = 8000   # chars; a definition is guidance, not a novel
MAX_DESCRIPTION = 280
MAX_PROPOSED_PER_MISSION = 8

#: The shipped standard team. Instructions are the capability carrier: they are prepended to the
#: worker's prompt at dispatch (snapshotted into the contract at validation, covered by the
#: approval fingerprint). Keep them tight - they ride every brief for that role.
BUILTIN_AGENTS: dict[str, dict[str, Any]] = {
    "developer": {"version": 1, "description": "General implementer: makes the change the brief asks for.",
                  "instructions":
                      "You are the team's developer. Make the change the brief asks for and nothing "
                      "beyond it. Read before you write. Match the codebase's existing style and "
                      "patterns. Run the relevant checks before you finish and say what you ran."},
    "senior-dev": {"version": 1, "description": "Senior implementer: design judgment, edge cases, leaves the codebase cleaner.",
                   "instructions":
                       "You are the team's senior developer. Before writing code, state the approach "
                       "in two or three sentences and name the edge cases you will handle. Prefer the "
                       "simplest design that survives those edge cases. Reuse existing abstractions "
                       "instead of adding parallel ones. When you touch a file, leave it cleaner: no "
                       "dead code, no half-renames. Run the relevant checks and report exactly what "
                       "you ran and what it did."},
    "backend-dev": {"version": 1, "description": "Backend specialist: APIs, data, services. Publishes contract changes on the team line.",
                    "extends": "senior-dev",
                    "instructions":
                        "You are the team's backend developer, responsible for APIs, data models and "
                        "services. When you change anything a consumer depends on (routes, request/"
                        "response shapes, schemas, events), post a contract-change on the team line "
                        "the moment you decide it - parallel teammates plan around your notice, not "
                        "around your finished diff. Keep handlers thin and validate at the boundary."},
    "frontend-dev": {"version": 1, "description": "Frontend specialist: UI against agreed contracts; adapts or objects when they move.",
                     "extends": "senior-dev",
                     "instructions":
                         "You are the team's frontend developer. Build against the agreed API "
                         "contracts, and check the team line before depending on one: when a "
                         "contract-change touches your work, ack it and adapt in the same pass, or "
                         "object with reasons instead of silently building on a moved contract. "
                         "Handle loading, empty and error states for everything you render."},
    "reviewer": {"version": 1, "description": "Reviews diffs against the job; verdict pass / block / request-changes.",
                 "instructions":
                     "You are the team's reviewer - a second pair of eyes, never the author grading "
                     "itself. Read the actual changes and judge them against the job. Approve work "
                     "you would ship. When the work is close but not there, request specific changes "
                     "the author can act on rather than blocking outright. Block only what is wrong, "
                     "unsafe or off-brief. End with the verdict block exactly as instructed."},
    "designer": {"version": 1, "description": "Design eye over real rendered output; blocks slop.",
                 "instructions":
                     "You are the team's designer. Judge the rendered product, not the code that made "
                     "it: hierarchy, spacing, contrast, states (loading, empty, error), and whether "
                     "a user would call it finished. Slop is a blocking finding. Name concrete fixes "
                     "with concrete values, not taste words."},
    "artist": {"version": 1, "description": "Produces visual assets (SVG/illustration) the product uses.",
               "instructions":
                   "You are the team's artist. Produce assets the product actually ships: simple, "
                   "recognizable, on-palette, and light enough to serve. An asset nobody references "
                   "is not done."},
    "pm": {"version": 1, "description": "Product manager: scopes work, verifies the running product like a user, files bugs with labels.",
           "instructions":
               "You are the team's product manager. Scope work into small verifiable pieces with "
               "acceptance criteria. Verify the running product like a user - with evidence (what "
               "you clicked, what you saw), not from code reading. When you find a bug on delivered "
               "work, report it with repro steps and proper labels (type:bug, area:*, severity:*) "
               "so the ticket reopens with evidence, not vibes."},
    "tester": {"version": 1, "description": "Breaks the work: real scenarios against the running product.",
               "instructions":
                   "You are the team's tester. Your job is to break the work, not to bless it: run "
                   "the scenarios a user would actually hit, including the unhappy paths. Report "
                   "each failure with repro steps and what you expected. A pass with no scenarios "
                   "run is not a pass."},
    "explorer": {"version": 1, "description": "Read-only scout: maps code, answers 'where does X live'.",
                 "instructions":
                     "You are the team's explorer. Read-only: map the territory and report back - "
                     "where things live, how they connect, what a change would touch. Cite file "
                     "paths and line numbers so the next role starts oriented."},
    "devops": {"version": 1, "description": "Runs the product: builds, serves, checks health; never deploys anywhere public.",
               "instructions":
                   "You are the team's devops. Build and run the product locally, check its health "
                   "endpoints and logs, and report what is actually running. You never deploy to or "
                   "touch shared infrastructure - local verification only."},
}


class AgentError(ValueError):
    pass


def _builtin(name: str) -> dict[str, Any] | None:
    entry = BUILTIN_AGENTS.get(name)
    if entry is None:
        return None
    return {"name": name, "version": int(entry.get("version") or 1),
            "description": str(entry.get("description") or ""),
            "instructions": str(entry.get("instructions") or ""),
            "extends": str(entry.get("extends") or ""), "source": "builtin"}


def definition_for(name: str, path: str | None = None) -> dict[str, Any] | None:
    """The effective definition for an ACTIVE agent name, registry first, then builtin.

    Registry entries may carry instructions/version/extends; an entry naming a builtin refines
    it (registry instructions win, since they are the owner's voice), an entry with "extends"
    composes on top of the named definition (base instructions first, then the refinement).
    Proposed agents are NOT returned: they are not capabilities yet."""
    base: dict[str, Any] | None = None
    for entry in roles._read(path)["roles"]:
        if entry.get("name") == name and entry.get("status", "active") != "proposed":
            base = {"name": name, "version": int(entry.get("version") or 1),
                    "description": str(entry.get("description") or ""),
                    "instructions": str(entry.get("instructions") or ""),
                    "extends": str(entry.get("extends") or ""),
                    "source": str(entry.get("source") or "owner") or "owner"}
            break
    if base is None:
        return _builtin(name)
    if base["extends"] and not base["instructions"]:
        parent = definition_for(base["extends"], path)
        if parent is not None:
            base["instructions"] = parent["instructions"]
            base["version"] = max(base["version"], parent["version"])
    return base


def list_definitions(path: str | None = None) -> list[dict[str, Any]]:
    """Every definition for display: builtins, active registry entries, and proposed agents
    (flagged, so the owner sees what is waiting for a promote decision)."""
    out = [dict(_builtin(name)) for name in BUILTIN_ROLE_NAMES if _builtin(name)]
    seen = {d["name"] for d in out}
    for entry in roles._read(path)["roles"]:
        name = str(entry.get("name") or "")
        if not name:
            continue
        status = str(entry.get("status") or "active")
        row = {"name": name, "version": int(entry.get("version") or 1),
               "description": str(entry.get("description") or ""),
               "instructions": str(entry.get("instructions") or ""),
               "extends": str(entry.get("extends") or ""),
               "status": status, "proposed_by": str(entry.get("proposed_by") or ""),
               "source": str(entry.get("source") or "owner") or "owner"}
        if name in seen:
            for i, existing in enumerate(out):
                if existing["name"] == name:
                    merged = {**out[i], **{k: v for k, v in row.items() if v}}
                    merged["source"] = "registry-override" if status != "proposed" else "proposed"
                    out[i] = merged
                    break
        else:
            out.append(row)
            seen.add(name)
    for d in out:
        d.setdefault("status", "active")
        d.setdefault("proposed_by", "")
    return out


def validate_definition(entry: dict[str, Any], path: str | None = None) -> dict[str, Any]:
    """Validate a proposed agent definition like a contract: bad definitions fail before they
    reach the registry. Returns the normalized record (status/proposed_by set by propose())."""
    if not isinstance(entry, dict):
        raise AgentError("an agent definition is a JSON object")
    name = str(entry.get("name") or "").strip()
    if not roles._NAME.match(name):
        raise AgentError(f"agent name must be 1-40 chars of a-z 0-9 -, got {name!r}")
    instructions = str(entry.get("instructions") or "").strip()
    if not instructions:
        raise AgentError("an agent definition needs instructions - the capability is the text")
    if len(instructions) > MAX_INSTRUCTIONS:
        raise AgentError(f"instructions are {len(instructions)} chars; the bound is {MAX_INSTRUCTIONS}")
    description = str(entry.get("description") or "").strip()[:MAX_DESCRIPTION]
    extends = str(entry.get("extends") or "").strip()
    if extends:
        if extends == name:
            raise AgentError("an agent cannot extend itself")
        if definition_for(extends, path) is None:
            raise AgentError(f"extends unknown or unpromoted agent {extends!r}")
    catalogue = entry.get("catalogue") or {}
    tools = catalogue.get("tools") or []
    if not isinstance(tools, list) or any(not isinstance(t, str) for t in tools):
        raise AgentError("catalogue.tools must be a list of tool names")
    unknown = [t for t in tools if t not in TOOL_UNIVERSE]
    if unknown:
        raise AgentError(f"catalogue names tool(s) outside the known universe: {', '.join(unknown)}. "
                         "Widening a worker's real reach is the owner's edit, not a proposal.")
    version = entry.get("version") or 1
    if not isinstance(version, int) or version < 1:
        raise AgentError("version must be a positive integer")
    return {"name": name, "description": description, "instructions": instructions,
            "extends": extends, "version": version,
            "catalogue": {"tools": [str(t) for t in tools],
                          "writable": bool(catalogue.get("writable"))}}


def propose(entries: list[dict[str, Any]], *, mission: str,
            path: str | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Record worker-proposed agent definitions as PROPOSED registry entries.

    Returns (accepted, rejected) where rejected rows carry {name, reason}. A proposed agent is
    invisible to contracts (roles.known excludes it) until the owner promotes it - the mission
    that proposed it cannot run it in the same breath. Bounded per mission so a runaway proposer
    cannot fill the registry."""
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    existing = {str(e.get("name")) for e in roles._read(path)["roles"]}
    prior = sum(1 for e in roles._read(path)["roles"]
                if e.get("proposed_by") == mission)
    for entry in entries:
        name = str((entry or {}).get("name") or "?")
        if len(accepted) + prior >= MAX_PROPOSED_PER_MISSION:
            rejected.append({"name": name,
                             "reason": f"the mission already proposed {MAX_PROPOSED_PER_MISSION} agents (the cap)"})
            continue
        try:
            record = validate_definition(entry, path)
        except AgentError as error:
            rejected.append({"name": name, "reason": str(error)})
            continue
        if record["name"] in existing or record["name"] in BUILTIN_AGENTS:
            rejected.append({"name": record["name"],
                             "reason": "an agent or builtin already has that name"})
            continue
        roles.add({**record, "status": "proposed", "source": "proposed",
                   "proposed_by": mission}, path)
        existing.add(record["name"])
        accepted.append(record)
    return accepted, rejected


def promote(name: str, path: str | None = None) -> dict[str, Any]:
    """The owner's go-ahead: a proposed agent becomes active and contracts may name it."""
    return roles.promote(name, path)


def extract_proposals(output: str) -> list[dict[str, Any]]:
    """A worker's proposed agent definitions: the last JSON object in its output carrying a
    "propose_agents" list. Same envelope handling as mission step proposals (the Root adapter
    wraps the model's reply in a JSON envelope)."""
    candidates = [output]
    try:
        data = json.loads(output)
    except ValueError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("reply"), str):
        candidates.insert(0, data["reply"])
    decoder = json.JSONDecoder()
    for text in candidates:
        found = None
        # Scan every '{' as a potential object start; raw_decode handles arbitrary nesting
        # (a proposal's catalogue object is two levels down), failures just advance.
        for index, char in enumerate(text):
            if char != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(text[index:])
            except ValueError:
                continue
            if isinstance(parsed, dict) and isinstance(parsed.get("propose_agents"), list):
                found = parsed  # the LAST one wins, like verdicts
        if found is not None:
            return [item for item in found["propose_agents"] if isinstance(item, dict)]
    return []
