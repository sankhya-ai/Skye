"""The one job contract every worker adapter accepts.

A contract is data a human or a plan produces; the dispatcher validates it before anything runs.
No field here is executed: command lines are built by adapters from known templates, and the test
gate runs a configured test command from the team policy, never from the contract itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROLES = ("developer", "reviewer", "designer", "artist", "pm", "tester", "explorer", "devops")
ADAPTERS = ("root", "claude-code", "codex")
GATES = ("tests", "review", "journey")


class ContractError(ValueError):
    pass


def validate(contract: dict[str, Any]) -> dict[str, Any]:
    """Validate a job contract; return a normalized copy. Rejects unknown roles/adapters/gates and
    missing fields - an invalid contract fails BEFORE anything runs, never mid-job."""
    if not isinstance(contract, dict):
        raise ContractError("a job contract is a JSON object")
    out = dict(contract)
    goal = str(out.get("goal") or "").strip()
    if not goal:
        raise ContractError("goal is required: what the worker should do")
    out["goal"] = goal
    repo = str(out.get("repo") or "").strip()
    if not repo:
        raise ContractError("repo is required: the path the worker operates on")
    out["repo"] = repo
    role = str(out.get("role") or "developer").strip()
    from .roles import known as known_roles  # late import: roles.py reads this module's ROLES
    known_roles_ = known_roles()
    if role not in known_roles_:
        raise ContractError(f"unknown role '{role}': one of {', '.join(known_roles_)}")
    out["role"] = role
    adapter = str(out.get("adapter") or "").strip()
    if adapter not in ADAPTERS:
        raise ContractError(f"adapter is required: one of {', '.join(ADAPTERS)}")
    out["adapter"] = adapter
    gates = out.get("gates", ["tests"])
    if not isinstance(gates, list) or any(g not in GATES for g in gates):
        raise ContractError(f"gates must be a list within {', '.join(GATES)}")
    out["gates"] = gates
    out["branch"] = str(out.get("branch") or "").strip()
    out["brief"] = str(out.get("brief") or "").strip()  # the worker's small, self-contained brief
    # Owner-authored role instructions (registry "instructions" field) snapshot into the
    # contract at VALIDATION time as their own field: the text the owner reviews and approves
    # IS the text the worker receives, and the approval fingerprint covers it (fingerprint()
    # validates, so a registry edit changes the fingerprint of future approvals - it can never
    # silently retarget an approved one). A separate field keeps re-validation idempotent.
    # Adapters prepend it to the worker's prompt; absent when the registry has none.
    from .roles import instructions_for
    guidance = instructions_for(out["role"])
    if guidance:
        out["role_instructions"] = guidance
    else:
        out.pop("role_instructions", None)
    # Owner-declared project references (DESIGN.md, PRODUCT.md, ...) snapshot into the contract
    # at VALIDATION, like role instructions: the text the owner reviews and approves IS the text
    # the worker receives, the approval fingerprint covers it (fingerprint() re-validates under
    # the current files, so an edit changes the fingerprint of future approvals - fail closed),
    # and re-validation stays idempotent. Paths must resolve INSIDE the repo, be regular files,
    # and never be symlinks (same posture as the artifact preview endpoint). Size is bounded so
    # a bloated doc fails loudly instead of silently truncating the worker's prompt.
    refs = out.get("reference_files") or []
    if not isinstance(refs, list) or any(not isinstance(r, str) or not r.strip() for r in refs):
        raise ContractError("reference_files must be a list of repo-relative paths")
    root = Path(out["repo"]).resolve()
    snapshots = []
    total = 0
    for rel in refs:
        target = (root / rel.strip()).resolve()
        if target != root and root not in target.parents:
            raise ContractError(f"reference file escapes the repo: {rel!r}")
        if target.is_symlink() or not target.is_file():
            raise ContractError(f"reference file is not a regular file in the repo: {rel!r}")
        text = target.read_text("utf-8", errors="replace")
        total += len(text)
        if len(text) > 32768 or total > 131072:
            raise ContractError(f"reference files too large ({rel!r} pushes past the 32KB/file, "
                                "128KB total bound): trim the doc, do not truncate it silently")
        snapshots.append({"path": rel.strip(), "content": text})
    if snapshots:
        out["reference_docs"] = snapshots
    else:
        out.pop("reference_docs", None)
    budget = out.get("budget") or {}
    if not isinstance(budget, dict):
        raise ContractError("budget must be an object")
    out["budget"] = {k: budget[k] for k in ("max_steps", "timeout_seconds", "max_cost_usd") if k in budget}
    return out


def load(path: str | Path) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError) as error:
        raise ContractError(f"cannot read the job contract: {error}") from error
    return validate(raw)
