"""Replaying the owner-configured journey command, with honest limits.

The journey command is a plain shell command the OWNER writes into the team policy
(Settings) - often just a smoke test. Exit 0 means the replay passed; anything else means
the product still fails that check. This module never invents a pass: no command, a
timeout, or a non-zero exit are all recorded as failures with evidence.

Evidence discipline: only a bounded, redacted output tail is recorded - never the full
output, and secret-looking assignments (TOKEN=..., Bearer ...) are masked before storage,
because journey output ends up in the backlog record and the UI.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any

EVIDENCE_LIMIT = 2000
DEFAULT_TIMEOUT_SECONDS = 600

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)[A-Z0-9_]*)"
    r"\s*[:=]\s*('[^']*'|\"[^\"]*\"|\S+)")
_BEARER = re.compile(r"(?i)\bBearer\s+\S+")


def redact(text: str) -> str:
    """Mask secret-looking values so recorded evidence cannot leak credentials."""
    text = _BEARER.sub("Bearer [redacted]", text)
    return _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}=[redacted]", text)


def run_journey(command: str, cwd: str = "",
                timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Run the journey command once and return a bounded, redacted verdict."""
    try:
        proc = subprocess.run(command, cwd=cwd or None, shell=True, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"passed": False, "timed_out": True, "returncode": None,
                "evidence": f"journey replay timed out after {timeout}s"}
    evidence = redact((proc.stdout + proc.stderr))[-EVIDENCE_LIMIT:]
    return {"passed": proc.returncode == 0, "timed_out": False,
            "returncode": proc.returncode, "evidence": evidence}
