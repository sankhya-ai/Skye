"""Presentation-layer redaction for display payloads.

The journal and mission records are the durable execution evidence - they are NEVER altered.
This module shapes only what API payloads show, in two independent layers:

1. Field selection lives with each payload builder (e.g. the graph timeline's allowlist).
2. VALUE redaction lives here: credential-shaped substrings are masked inside free text, so a
   secret riding inside a legitimate field (a reason, an error string, a rationale) is still
   not displayed. Legitimate prose passes through unchanged.

Patterns are deliberately conservative credential shapes (vendor token formats, key blocks,
JWTs, key=value secrets). Redaction is best-effort display hygiene, not a guarantee that no
string could ever be sensitive - the durable records stay owner-visible by design, and views
that need raw records (detail route) keep their fields with values masked, not removed.
"""

from __future__ import annotations

import re
from typing import Any

_PATTERNS = [
    # private key blocks first (multi-line, most specific)
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
                re.DOTALL), "[redacted-private-key]"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[redacted-aws-key]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"), "[redacted-github-token]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[redacted-github-token]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "[redacted-slack-token]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\b"),
     "[redacted-jwt]"),
    (re.compile(r"(?i)\bBearer[ \t]+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [redacted]"),
    # key=value / key: value secrets; the KEY stays visible, the value is masked
    (re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|"
                r"session[_-]?token|id[_-]?token|refresh[_-]?token|client[_-]?secret|"
                r"private[_-]?key)(\s*[=:]\s*)([\"']?)[^\s\"']{4,}\3"),
     None),  # handled in redact_text to preserve the key name
]

_ASSIGNMENT = _PATTERNS[-1][0]


def redact_text(text: str) -> str:
    """Mask credential-shaped substrings; everything else passes through byte-identical."""
    if not text or not isinstance(text, str):
        return text
    out = text
    for pattern, replacement in _PATTERNS[:-1]:
        out = pattern.sub(replacement, out)
    quote = lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}[redacted]{m.group(3)}"
    out = _ASSIGNMENT.sub(quote, out)
    return out


def redact_value(value: Any) -> Any:
    """Recursive redaction for payload projections: strings masked, structure preserved."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    return value
