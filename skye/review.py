"""The review gate's verdict: a reviewer that merely FINISHES has not reviewed anything.

The review gate dispatches a second worker (never the worker grading itself) and requires an
explicit verdict in its output: a JSON block {"verdict": "pass"} or {"verdict": "block",
"reasons": [...]} - the same verdict vocabulary Root's own contract check enforces natively
(present key, pass/block values, reasons on a block), so the shape is enforced by the worker's
contract check where the adapter has one AND parsed here for every adapter. Only "pass" opens
the gate. A cleanly exiting reviewer that says "block" - or says nothing parseable - FAILS the
gate. That is the whole point of a gate: silence is not consent.

For the Root adapter the generated review contract also sets contract_keys=["verdict"], so
Root's own output-contract check rejects a verdict-less or malformed review before this parsing
even runs.
"""

from __future__ import annotations

import json
import re
from typing import Any

VERDICT_INSTRUCTION = (
    'End your review with a JSON block on a single line: {"verdict": "pass"} or '
    '{"verdict": "block", "reasons": ["why"]}. Only an explicit "pass" verdict passes the '
    "review gate; a missing or malformed verdict fails it."
)

_JSON_VERDICT = re.compile(r'"verdict"\s*:\s*"(pass|block)"')
_LINE_VERDICT = re.compile(r"^\s*VERDICT\s*:\s*(pass|block)\s*$", re.IGNORECASE | re.MULTILINE)
_REASONS = re.compile(r'"reasons"\s*:\s*\[(.*?)\]', re.DOTALL)


def parse_verdict(output: str) -> tuple[str | None, str]:
    """Extract the reviewer's verdict from worker output. Returns (verdict, reasons) where
    verdict is 'pass', 'block', or None when the output carries no parseable verdict.

    Handles the Root adapter's JSON envelope (the model's reply is a string field inside the
    adapter's result JSON) as well as raw stdout from the CLI adapters. The LAST verdict wins -
    a reviewer may quote earlier text before concluding.
    """
    candidates = [output]
    try:
        data = json.loads(output)
    except ValueError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("reply"), str):
        candidates.insert(0, data["reply"])
    for text in candidates:
        found = _JSON_VERDICT.findall(text) or [m.lower() for m in _LINE_VERDICT.findall(text)]
        if found:
            reasons = ""
            blocks = _REASONS.findall(text)
            if blocks:
                try:
                    reasons = "; ".join(str(item) for item in json.loads(f"[{blocks[-1]}]"))
                except ValueError:
                    reasons = blocks[-1][:300]
            return found[-1], reasons
    return None, ""


def build_review_contract(spec: dict[str, Any], review_role: str) -> dict[str, Any]:
    """The reviewer contract dispatched by the review gate: a second worker, read-only role from
    the team policy, judging the diff against the original goal. contract_keys asks enforcing
    adapters (Root) to require a well-formed verdict block in the output."""
    return {
        "goal": f"Review the changes in {spec['repo']} for the job: {spec['goal']}",
        "repo": spec["repo"],
        "role": review_role,
        "adapter": spec["adapter"],
        "gates": [],
        "brief": (f"You are reviewing another worker's changes in {spec['repo']} for this job: "
                  f"{spec['goal']}. Read the actual changes, judge them against the job, then "
                  f"{VERDICT_INSTRUCTION}"),
        "contract_keys": ["verdict"],
    }
