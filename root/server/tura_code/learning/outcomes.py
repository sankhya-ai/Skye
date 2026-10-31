"""Whether a learned skill earns its place: outcomes of the turns that used it.

A promoted skill is a claim that it helps. This keeps the evidence: every turn
that did real work (edited a file or ran a check) is recorded with the learned
skills it read — the runtime lists skills by path, so "used" means the agent
read that ``SKILL.md`` — and whether the turn went cleanly:

    clean = finished, every edit exercised by a check after it, no approval refused

Tool errors are not held against a turn: red-then-green is a healthy cycle.

Each skill version (a hash of its text) is compared against a reference under
the same tenant: the version it replaced when that has enough evidence of its
own, otherwise the turns that used no learned skill. A version is
*underperforming* once the 95% Wilson intervals of the two clean rates no
longer overlap — the version's upper bound below the reference's lower bound,
confidently worse rather than unlucky — after at least :data:`MIN_USES` turns. The service then keeps the previous version (roll
back) or, with none, retires the skill; in `review` mode it only says so.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..sessions import TurnState

MIN_USES = 5
MIN_BASELINE = 10
READ_TOOLS = {"ReadFile"}
BASELINE = ""


def version_of(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def wilson(clean: int, uses: int, z: float = 1.96) -> tuple[float, float]:
    """The 95% Wilson score interval of a clean rate."""
    if uses == 0:
        return 0.0, 1.0
    p = clean / uses
    centre = p + z * z / (2 * uses)
    margin = z * math.sqrt(p * (1 - p) / uses + z * z / (4 * uses * uses))
    scale = 1 + z * z / uses
    return max(0.0, (centre - margin) / scale), min(1.0, (centre + margin) / scale)


def is_clean(turn: "TurnState", status: str) -> bool:
    return status == "finished" and turn.verified and turn.rejections == 0


def did_work(turn: "TurnState") -> bool:
    return bool(turn.files_touched) or turn.checks > 0


def skills_read(turn: "TurnState", skills_root: Path) -> list[str]:
    """Names of learned skills whose SKILL.md this turn read."""
    root = skills_root.resolve()
    names: list[str] = []
    for call in turn.tool_calls.values():
        if call.get("name") not in READ_TOOLS or call.get("status") != "completed":
            continue
        try:
            raw = str(json.loads(call.get("arguments") or "{}").get("path") or "")
        except (json.JSONDecodeError, AttributeError):
            continue
        if not raw:
            continue
        path = Path(raw).expanduser().resolve()
        if path.name == "SKILL.md" and path.parent.parent == root and path.parent.name not in names:
            names.append(path.parent.name)
    return names


class OutcomeLedger:
    """``{"skills": {name: {version: {uses, clean}}}, "order": {name: [versions]}, "baseline": {uses, clean}}``."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("skills", {})
        data.setdefault("order", {})
        data.setdefault("baseline", {"uses": 0, "clean": 0})
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), "utf-8")
        tmp.replace(self.path)

    def record(self, used: dict[str, str], clean: bool) -> None:
        """``used``: skill name -> version read this turn; empty for a baseline turn."""
        with self._lock:
            data = self._load()
            tallies = [data["baseline"]] if not used else []
            for name, version in used.items():
                order = data["order"].setdefault(name, [])
                if version not in order:
                    order.append(version)
                tallies.append(data["skills"].setdefault(name, {}).setdefault(version, {"uses": 0, "clean": 0}))
            for tally in tallies:
                tally["uses"] += 1
                tally["clean"] += int(clean)
            self._save(data)

    def assess(self, name: str, version: str) -> dict[str, Any]:
        data = self._load()
        tally = data["skills"].get(name, {}).get(version, {"uses": 0, "clean": 0})
        order = data["order"].get(name, [])
        earlier = order[: order.index(version)] if version in order else order
        reference, against = data["baseline"], "baseline"
        for previous in reversed(earlier):
            prior = data["skills"][name][previous]
            if prior["uses"] >= MIN_USES:
                reference, against = prior, f"version {previous}"
                break
        enough = tally["uses"] >= MIN_USES and (against != "baseline" or reference["uses"] >= MIN_BASELINE)
        reference_rate = reference["clean"] / reference["uses"] if reference["uses"] else None
        upper = wilson(tally["clean"], tally["uses"])[1]
        reference_lower = wilson(reference["clean"], reference["uses"])[0]
        return {
            "version": version,
            "uses": tally["uses"],
            "clean": tally["clean"],
            "rate": tally["clean"] / tally["uses"] if tally["uses"] else None,
            "against": against,
            "reference_rate": reference_rate,
            "underperforming": bool(enough and upper < reference_lower),
        }
