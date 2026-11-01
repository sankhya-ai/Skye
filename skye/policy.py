"""Team policy: which contracts may run UNATTENDED, and how the test gate runs.

Owner-managed JSON (default: nothing runs unattended - every contract is returned for the owner's
go-ahead). Rules match checkable fields only; plain code cannot judge intent, so keep rules narrow.
The test gate's command comes from THIS file, never from a contract: a contract cannot turn the
gate into arbitrary execution.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_PATH = "skye.policy.json"


class Policy:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        self.allow_unattended: list[dict[str, Any]] = list(data.get("allow_unattended") or [])
        self.test_command: str = str(data.get("test_command") or "")
        self.journey_command: str = str(data.get("journey_command") or "")
        # Review-loop bound: how many times a reviewer may send a step back for changes inside
        # one mission before the failure stands. Bounded, journaled, never a silent retry loop.
        try:
            self.max_review_rounds: int = min(8, max(0, int(data.get("max_review_rounds") or 2)))
        except (TypeError, ValueError):
            self.max_review_rounds = 2
        try:
            self.journey_timeout_seconds: int = max(1, int(data.get("journey_timeout_seconds") or 600))
        except (TypeError, ValueError):
            self.journey_timeout_seconds = 600
        self.max_concurrent: int = int(data.get("max_concurrent") or 1)
        self.review_role: str = str(data.get("review_role") or "reviewer")
        # Per-role model overrides for the worker CLI (role -> model alias, e.g.
        # {"pm": "sonnet"}). Absent = every worker uses the CLI's own default model.
        models = data.get("models") if isinstance(data.get("models"), dict) else {}
        self.models: dict[str, str] = {str(k): str(v) for k, v in models.items()
                                       if isinstance(v, str) and v.strip()}
        # Trusted automatic pickup (skye/pickup.py). Absent or malformed means fully OFF;
        # enabling it automates the intake click only - step contracts still pass the normal
        # approval gate at dispatch.
        self.auto_pickup: dict[str, Any] = data.get("auto_pickup") if isinstance(data.get("auto_pickup"), dict) else {}
        # Per-mission workspaces (skye/workspaces.py): absent = off = shared-tree behavior.
        self.workspaces: dict[str, Any] = data.get("workspaces") if isinstance(data.get("workspaces"), dict) else {}

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Policy":
        # SKYE_POLICY names the owner's policy file (set by `skye start`); every caller -
        # CLI, dispatcher, mission engine, UI - resolves the same file.
        path = path or os.environ.get("SKYE_POLICY", "").strip() or DEFAULT_PATH
        try:
            return cls(json.loads(Path(path).read_text("utf-8")))
        except (OSError, ValueError):
            return cls()  # absent or broken: the conservative default

    def check(self, contract: dict[str, Any], running: int = 0) -> tuple[str, str]:
        """("ok"|"cap"|"rule", reason). The cap and the no-rule case are distinct: a one-time
        owner approval may override a missing RULE, but never the concurrency CAP - an approval
        is consent for one contract, not a raise of the team's parallelism limit."""
        if running >= self.max_concurrent:
            return ("cap",
                    f"the team already runs {running} contract(s); the cap is {self.max_concurrent}")
        for rule in self.allow_unattended:
            if rule.get("adapter") == contract["adapter"] and rule.get("role") in (contract["role"], "*"):
                return ("ok", "")
        return ("rule",
                f"{contract['adapter']}/{contract['role']} is not on the team's unattended "
                "list, so this run needs your approval before it starts")

    def unattended_ok(self, contract: dict[str, Any], running: int = 0) -> str:
        """Why a contract may not run unattended, or '' when it may. Adapter and role must both be
        named by one rule; the concurrency cap is team-wide."""
        return self.check(contract, running=running)[1]
