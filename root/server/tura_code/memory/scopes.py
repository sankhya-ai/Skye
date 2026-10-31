"""Memory scopes, the packet attached to a request, and its envelope."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The owner scope of the local, single-user install. A hosted install binds
#: each user to ``owner:<user id>`` instead (see ``MemoryView``).
OWNER_SCOPE = "owner"
PACKET_CHAR_LIMIT = 2_400
_WORD = re.compile(r"[a-z0-9_]{3,}")
_STOP = frozenset(
    "the and for with that this from have has was were are you your our can will not "
    "into when then than what which who how why use using used also just only more "
    "some any all its it's they them their there here about after before over under".split()
)


def project_scope(workspace: Path | str) -> str:
    digest = hashlib.sha1(str(Path(workspace).resolve()).encode("utf-8")).hexdigest()[:16]
    return f"project:{digest}"


@dataclass
class Packet:
    text: str
    items: list[dict[str, Any]] = field(default_factory=list)


def words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


def clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def packet_envelope(packet_text: str, request: str, workspace: Path) -> str:
    """The request with host context in the envelope the runtime's director reads.

    The Jev director splits `## System` from `## User` so it judges the step
    against the actual request, not against the memory attached to it.
    """
    from ..sessions.coding_context import detect_project_facts, format_context_block

    facts = detect_project_facts(workspace)
    facts_block = format_context_block(facts)
    host_parts = [f"You are root working in `{workspace}`."]
    if facts_block:
        host_parts.append(facts_block)
    if packet_text.strip():
        host_parts.append(packet_text.strip())
    body = "\n\n".join(host_parts)
    return (
        "## System\n"
        f"{body}\n\n"
        "## User\n"
        f"{request}"
    )


def split_envelope(text: str) -> tuple[str, str]:
    """(host context, request) for display; the inverse of ``packet_envelope``."""
    stripped = text.lstrip()
    if stripped.startswith("## System") and "\n## User\n" in stripped:
        head, request = stripped.split("\n## User\n", 1)
        return head[len("## System"):].strip(), request
    return "", text
