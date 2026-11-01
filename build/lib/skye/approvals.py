"""One-time owner approvals: the way past a needs-owner gate without widening the standing policy.

A standing policy rule says "this whole adapter/role class may run unattended, forever". That is
the wrong tool for "yes, THIS contract, once". An approval is bound to the exact normalized
contract by fingerprint (sha256 of its canonical JSON), recorded by the owner with
`skye approve job.json`, and CONSUMED when the dispatch starts - it never approves a second run,
and editing the contract invalidates it (the fingerprint no longer matches).

The store is a local JSON file (default skye.approvals.json, override with SKYE_APPROVALS),
owner-managed like the policy file. It grants nothing by itself: it only records what the owner
already decided.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from .contract import validate

DEFAULT_PATH = "skye.approvals.json"


def default_path() -> str:
    return os.environ.get("SKYE_APPROVALS", "").strip() or DEFAULT_PATH


def fingerprint(contract: dict[str, Any]) -> str:
    """The stable identity of a contract: sha256 over the canonical normalized form. Any edit -
    goal, repo, role, adapter, gates, brief, budget - produces a different fingerprint, so an
    approval can never drift onto a changed contract."""
    normalized = validate(contract)
    # tools/tools_writable are resolved by the dispatcher from the role catalogue at run time,
    # not contract content: exclude them so the same contract fingerprints identically before
    # and after a dispatch resolves its gateway.
    normalized.pop("tools", None)
    normalized.pop("tools_writable", None)
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return {"approvals": []}
    if not isinstance(data, dict) or not isinstance(data.get("approvals"), list):
        return {"approvals": []}
    return data


def _write(path: str | Path, data: dict[str, Any]) -> None:
    target = Path(path)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    tmp.replace(target)  # atomic: a crash never leaves a half-written store


def approve(contract: dict[str, Any], path: str | Path | None = None, note: str = "") -> dict[str, Any]:
    """Record the owner's one-time approval for exactly this contract. Returns the entry."""
    path = path or default_path()
    spec = validate(contract)
    fp = fingerprint(spec)
    data = _read(path)
    data["approvals"] = [a for a in data["approvals"] if a.get("fingerprint") != fp]
    entry = {"fingerprint": fp, "approved_at": time.time(), "note": note,
             "goal": spec["goal"], "role": spec["role"], "adapter": spec["adapter"]}
    data["approvals"].append(entry)
    _write(path, data)
    return entry


def consume(contract: dict[str, Any], path: str | Path | None = None) -> dict[str, Any] | None:
    """Take the approval for exactly this contract, removing it. Single-use: a second dispatch of
    the same contract needs a fresh approval. Returns the entry, or None when none exists."""
    path = path or default_path()
    fp = fingerprint(validate(contract))
    data = _read(path)
    kept = [a for a in data["approvals"] if a.get("fingerprint") != fp]
    taken = [a for a in data["approvals"] if a.get("fingerprint") == fp]
    if not taken:
        return None
    _write(path, {"approvals": kept})
    return taken[-1]


def peek(contract: dict[str, Any], path: str | Path | None = None) -> dict[str, Any] | None:
    """The approval for this contract without consuming it (used at the policy gate; consumption
    happens only when the worker actually starts)."""
    path = path or default_path()
    fp = fingerprint(validate(contract))
    for entry in _read(path)["approvals"]:
        if entry.get("fingerprint") == fp:
            return entry
    return None


def pending(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Every outstanding approval, for `skye approvals`."""
    return list(_read(path or default_path())["approvals"])
