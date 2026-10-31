"""root on Maritime (maritime.sh): the platform's contract for a custom agent, on when ``TURA_CODE_MARITIME=1``.

Maritime runs root in its own micro-VM, keeps ``/data`` across sleep and
redeploys, and reaches it through two routes:

* ``GET /health`` — 2xx while the server is up;
* ``POST /chat`` — ``{"message", "source"}`` from Maritime's dashboard, CLI,
  Telegram or a scheduled wake, answered within 30 seconds.

A root turn often takes longer than that. ``/chat`` waits up to 25 seconds; if
the turn is still going it says so and keeps working, and the next ``/chat``
from that source (``status``, or a new question) starts with the answer. Each
source has its own read-only session (kind ``chat``), unattended like every
chat: a step that would need the owner's approval is declined.

The VM is reached only through Maritime's front door, so these two routes skip
the host check and take no token. Deploy root as a private agent, never with
``--public``; for the web UI set ``TURA_CODE_TOKEN`` and ``TURA_CODE_BOOTSTRAP=0``.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request

if TYPE_CHECKING:
    from .tenancy import Server

ROUTES = ("/health", "/chat")
REPLY_WITHIN = 25.0
TURN_TIMEOUT = 540.0


def enabled() -> bool:
    return os.environ.get("TURA_CODE_MARITIME", "") == "1"


class MaritimeChat:
    """One read-only conversation per ``source``, with turns that may outlive a request."""

    def __init__(self, server: "Server") -> None:
        self.server = server
        self._sessions: dict[str, str] = {}
        self._running: dict[str, tuple[str, asyncio.Task]] = {}
        self._unseen: dict[str, str] = {}

    async def chat(self, message: str, source: str) -> str:
        source = re.sub(r"[^a-z0-9_-]", "", source.lower())[:32] or "chat"
        running = self._running.get(source)
        if running is not None:
            question, task = running
            done, _ = await asyncio.wait({task}, timeout=REPLY_WITHIN)
            if not done:
                return f"Still working on “{question[:120]}”. Ask again in a minute."
            self._running.pop(source, None)
            self._unseen[source] = task.result()
            if message.strip().lower() in ("", "status", "?"):
                return self._unseen.pop(source)
        earlier = self._unseen.pop(source, "")
        if message.strip().lower() in ("", "status", "?"):
            return earlier or "Nothing running. Ask me anything."
        task = asyncio.create_task(self._turn(source, message))
        done, _ = await asyncio.wait({task}, timeout=REPLY_WITHIN)
        prefix = f"(The answer to your last question: {earlier})\n\n" if earlier else ""
        if done:
            return prefix + task.result()
        self._running[source] = (message, task)
        return prefix + "Working on it — this one takes a while. Send “status” in a minute."

    async def _turn(self, source: str, message: str) -> str:
        tenant = self.server.tenant()
        runtime = None
        known = self._sessions.get(source)
        if known:
            try:
                runtime = tenant.sessions.get(known)
            except KeyError:
                runtime = None
        if runtime is None:
            folder = self.server.home / "chat" / source
            folder.mkdir(parents=True, exist_ok=True)
            runtime = tenant.sessions.create(folder, title=f"Maritime · {source}", kind="chat", mode="plan")
            runtime.record.unattended = True
            runtime.save_record()
            self._sessions[source] = runtime.record.id
        prompt = f"A message reached you through Maritime ({source}):\n<<<\n{message.strip()}\n>>>\n\nAnswer it."
        try:
            await runtime.prompt(prompt)
        except ValueError:
            return "I'm still busy with the last message here."
        turn = runtime.turn
        try:
            await runtime.wait_turn(TURN_TIMEOUT)
        except asyncio.TimeoutError:
            await runtime.cancel()
            await runtime.wait_turn(30)
        return (turn.reply() if turn else "") or f"I couldn't finish that ({runtime.record.last_status})."


def router(server: "Server") -> APIRouter:
    api = APIRouter(include_in_schema=False)
    chat = MaritimeChat(server)

    @api.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True}

    @api.post("/chat")
    async def chat_route(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except ValueError:
            body = {}
        message = str((body or {}).get("message") or "")
        return {"response": await chat.chat(message, str((body or {}).get("source") or "chat"))}

    return api
