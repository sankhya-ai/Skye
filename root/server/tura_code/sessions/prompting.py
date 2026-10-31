"""Turning what the owner typed into what the runtime receives."""

from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path

MENTION = re.compile(r"(?<![\w/])@([\w./\-]+[\w\-])")
MENTION_BYTES = 60_000


def title_of(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= 60 else line[:57].rstrip() + "…"


def attachment_meta(attachments: list[dict] | None) -> list[dict]:
    return [
        {"name": item.get("name") or "attachment", "mime": item.get("mime") or "", "size": len(item.get("data") or "") * 3 // 4}
        for item in attachments or []
    ]


def expand_mentions(text: str, workspace: Path) -> str:
    """Attach the contents of ``@path`` mentions that name files in the workspace."""
    root = workspace.resolve()
    budget = MENTION_BYTES
    blocks: list[str] = []
    seen: set[str] = set()
    for match in MENTION.finditer(text):
        rel = match.group(1)
        if rel in seen:
            continue
        seen.add(rel)
        candidate = (root / rel).resolve()
        if not candidate.is_file() or not candidate.is_relative_to(root):
            continue
        try:
            raw = candidate.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:4096]:
            continue
        body = raw[:budget].decode("utf-8", errors="replace")
        budget -= len(body)
        truncated = "\n… (truncated)" if len(raw) > len(body) else ""
        blocks.append(f"<system>Contents of `{rel}` (mentioned by the user):</system>\n```\n{body}{truncated}\n```")
        if budget <= 0:
            break
    if not blocks:
        return text
    return text + "\n\n" + "\n\n".join(blocks)


def build_parts(text: str, attachments: list[dict]) -> list[dict]:
    """User input as content parts: text, then images; other files inline as text."""
    parts: list[dict] = []
    texts = [text] if text else []
    for item in attachments:
        name = str(item.get("name") or "attachment")
        mime = str(item.get("mime") or mimetypes.guess_type(name)[0] or "application/octet-stream")
        data = str(item.get("data") or "")
        if mime.startswith("image/"):
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
            continue
        try:
            decoded = base64.b64decode(data).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            texts.append(f"<system>Attached file `{name}` is binary ({mime}) and was not included.</system>")
            continue
        texts.append(f"<system>Attached file `{name}`:</system>\n```\n{decoded[:MENTION_BYTES]}\n```")
    return [{"type": "text", "text": "\n\n".join(texts)}] + parts
