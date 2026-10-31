"""Messages the owner sends while a turn runs: queued, then sent in order.

A turn that finishes starts the next queued message, as in Codex, Hermes'
``queue`` mode and pi's follow-ups. A turn that is cancelled or fails pauses
the queue instead: what the owner wrote next may no longer fit, so it waits
for them to send or drop it. The queue lives with the session, not the page,
so it still runs with the window closed; it is not kept across a restart.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime import SessionRuntime

logger = logging.getLogger(__name__)

#: More than this is a backlog, not a follow-up.
MAX_QUEUED = 10


class Queue:
    def queued_public(self: "SessionRuntime") -> list[dict]:
        return [{"id": item["id"], "text": item["text"], "attachments": len(item["attachments"] or [])} for item in self.queued]

    def _queue_changed(self: "SessionRuntime") -> None:
        self.emit("QueueUpdated", {"queued": self.queued_public()}, persist=False)

    async def enqueue(self: "SessionRuntime", text: str, attachments: list[dict] | None = None) -> None:
        """Queue a message for after this turn, or send it now when nothing runs."""
        text = (text or "").strip()
        if not text and not attachments:
            raise ValueError("Empty prompt.")
        if self.turn is None and not self.queued:
            await self.prompt(text, attachments)
            return
        if len(self.queued) >= MAX_QUEUED:
            raise ValueError(f"At most {MAX_QUEUED} messages wait at once.")
        self.queued.append({"id": uuid.uuid4().hex[:8], "text": text, "attachments": attachments})
        self._queue_changed()

    def unqueue(self: "SessionRuntime", item_id: str) -> None:
        self.queued = [item for item in self.queued if item["id"] != item_id]
        self._queue_changed()

    async def send_queued(self: "SessionRuntime", item_id: str) -> None:
        """Send one queued message now (a paused queue, or the owner skipping ahead)."""
        if self.turn is not None:
            raise ValueError("A turn is already running in this session.")
        item = next((item for item in self.queued if item["id"] == item_id), None)
        if item is None:
            raise ValueError("That message is no longer queued.")
        self.queued.remove(item)
        self._queue_changed()
        await self.prompt(item["text"], item["attachments"])

    async def _drain_queue(self: "SessionRuntime", status: str) -> None:
        if status != "finished" or not self.queued or self.turn is not None:
            return
        item = self.queued.pop(0)
        self._queue_changed()
        try:
            await self.prompt(item["text"], item["attachments"])
        except Exception:  # noqa: BLE001 - a queued message that cannot start goes back, not lost
            logger.exception("queued message failed to start")
            self.queued.insert(0, item)
            self._queue_changed()
