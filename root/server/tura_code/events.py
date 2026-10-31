"""A session's event log: ``events.jsonl``, appended, read back in pages.

Two things keep memory flat however long a session runs:

* **Coalescing.** A streamed answer arrives as thousands of ``ContentPart``
  deltas and a streamed tool call as many ``ToolCallPart`` fragments. They are
  merged in a one-event buffer and written as one whole message.
* **Paging from the end.** Nothing reads the whole file. The last sequence
  number comes from the file's tail; a reconnecting client gets the most recent
  page (bounded in events *and* bytes, starting at a turn boundary) and asks
  for older pages only if the owner scrolls up.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

PAGE_EVENTS = 1_500
PAGE_BYTES = 3 * 1024 * 1024
_CHUNK = 256 * 1024


def _copy(event: dict) -> dict:
    return json.loads(json.dumps(event))


class EventLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._buffer: dict | None = None
        self.last_seq = self._tail_seq()

    # ----- reading ---------------------------------------------------------

    def _reverse_lines(self):
        """Yield (line bytes) from the end of the file towards the start."""
        try:
            handle = self.path.open("rb")
        except FileNotFoundError:
            return
        with handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            pending = b""
            while position > 0:
                step = min(_CHUNK, position)
                position -= step
                handle.seek(position)
                pending = handle.read(step) + pending
                lines = pending.split(b"\n")
                pending = lines[0]
                for line in reversed(lines[1:]):
                    if line.strip():
                        yield line
            if pending.strip():
                yield pending

    def _tail_seq(self) -> int:
        for line in self._reverse_lines():
            try:
                return int(json.loads(line).get("seq") or 0)
            except (ValueError, json.JSONDecodeError):
                continue
        return 0

    def page(self, before: int | None = None, *, max_events: int = PAGE_EVENTS, max_bytes: int = PAGE_BYTES) -> dict[str, Any]:
        """The newest events (older than ``before``), oldest first.

        A page starts at a ``UserPrompt`` when one is in reach, so a client
        never renders a turn without its question. ``has_more`` says whether
        older events exist.
        """
        events: list[dict] = []
        size = 0
        has_more = False
        for line in self._reverse_lines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if before is not None and int(event.get("seq") or 0) >= before:
                continue
            if len(events) >= max_events or (size + len(line) > max_bytes and events):
                has_more = True
                break
            events.append(event)
            size += len(line)
        events.reverse()
        if has_more:
            start = next((i for i, e in enumerate(events) if e.get("type") == "UserPrompt"), None)
            if start:
                events = events[start:]
        if before is None and self._buffer is not None:
            events.append(_copy(self._buffer))
        return {"events": events, "has_more": has_more, "first_seq": events[0]["seq"] if events else None}

    # ----- writing ---------------------------------------------------------

    def _append(self, event: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def flush(self) -> None:
        if self._buffer is not None:
            self._append(self._buffer)
            self._buffer = None

    def record(self, event: dict) -> None:
        """Append an event, merging streaming deltas into whole messages."""
        self.last_seq = max(self.last_seq, int(event.get("seq") or 0))
        kind = event["type"]
        buffered = self._buffer
        if kind == "ContentPart":
            part = event["payload"].get("type")
            if buffered is not None and buffered["type"] == "ContentPart" and buffered["payload"].get("type") == part and part in ("text", "think"):
                key = "text" if part == "text" else "think"
                buffered["payload"][key] = (buffered["payload"].get(key) or "") + (event["payload"].get(key) or "")
                return
            self.flush()
            self._buffer = _copy(event)
            return
        if kind == "ToolCallPart":
            if buffered is not None and buffered["type"] == "ToolCall":
                function = buffered["payload"].setdefault("function", {})
                function["arguments"] = (function.get("arguments") or "") + (event["payload"].get("arguments_part") or "")
                return
            self.flush()
            self._append(event)
            return
        self.flush()
        if kind == "ToolCall":
            self._buffer = _copy(event)
            return
        self._append(event)
