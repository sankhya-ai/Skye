"""Team policy: which contracts may run UNATTENDED, and how the test gate runs.

Owner-managed JSON (default: nothing runs unattended - every contract is returned for the owner's
go-ahead). Rules match checkable fields only; plain code cannot judge intent, so keep rules narrow.
The test gate's command comes from THIS file, never from a contract: a contract cannot turn the
gate into arbitrary execution.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_PATH = "skye.policy.json"


class Policy:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        self.allow_unattended: list[dict[str, Any]] = list(data.get("allow_unattended") or [])
        self.test_command: str = str(data.get("test_command") or "")
        self.journey_command: str = str(data.get("journey_command") or "")
        try:
            self.journey_timeout_seconds: int = max(1, int(data.get("journey_timeout_seconds") or 600))
        except (TypeError, ValueError):
            self.journey_timeout_seconds = 600
        self.max_concurrent: int = int(data.get("max_concurrent") or 1)
        self.review_role: str = str(data.get("review_role") or "reviewer")

    @classmethod
    def load(cls, path: str | Path = DEFAULT_PATH) -> "Policy":
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
                f"no policy rule allows unattended {contract['adapter']}/{contract['role']}: "
                "the owner confirms this contract first")

    def unattended_ok(self, contract: dict[str, Any], running: int = 0) -> str:
        """Why a contract may not run unattended, or '' when it may. Adapter and role must both be
        named by one rule; the concurrency cap is team-wide."""
        return self.check(contract, running=running)[1]
