"""Keiki's external harness, inbox mode: root claims turns by polling, and answers through each turn's API.

Inbox mode needs no public URL and no signing secret — only a harness API key —
so it works from a laptop and from a Maritime machine alike. Keiki keeps the
channels (WhatsApp, iMessage, Slack, Telegram, SMS, email), steering, delivery
and history; root runs the turn. Payload shapes follow ``@onkeiki/channels``'
``KeikiTurn``; inbox claims add ``turnToken``.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx

DEFAULT_BASE = "https://onkeiki.com/api/v1"


class KeikiError(RuntimeError):
    def __init__(self, what: str, status: int = 0, code: str = "") -> None:
        super().__init__(what)
        self.status = status
        self.code = code


class KeikiClient:
    def __init__(self, api_key: str, base: str = DEFAULT_BASE, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.api_key = api_key
        self.base = base.rstrip("/")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=15.0), transport=transport)

    async def next_turn(self, wait: int = 25) -> dict[str, Any] | None:
        """Claim the next queued turn, waiting up to ``wait`` seconds; None when there is none."""
        response = await self._client.get(f"{self.base}/harness/inbox/next", params={"wait": max(0, min(30, wait))}, headers={"Authorization": f"Bearer {self.api_key}"})
        if response.status_code in (401, 403):
            raise KeikiError("Keiki refused the harness API key", response.status_code, "unauthorized")
        if response.status_code >= 400:
            raise KeikiError(f"Keiki answered {response.status_code}", response.status_code)
        turn = (response.json() or {}).get("turn")
        return turn if isinstance(turn, dict) else None

    async def _turn(self, turn: dict[str, Any], route: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{str(turn['keiki']['turnUrl']).rstrip('/')}/{route}"
        response = await self._client.post(url, json=body, headers={"Authorization": f"Bearer {turn['turnToken']}"})
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400 and response.status_code != 409:
            raise KeikiError(f"Keiki {route}: {response.status_code} {str(data)[:200]}", response.status_code, str(data.get("code") or ""))
        return data if isinstance(data, dict) else {}

    async def send(self, turn: dict[str, Any], text: str, key: str | None = None) -> dict[str, Any]:
        """Deliver one message to the contact; the key makes a retry safe (Keiki never sends twice under it)."""
        return await self._turn(turn, "messages", {"text": text, "idempotencyKey": key or f"{turn['turnId']}:{uuid.uuid4().hex}"})

    async def typing(self, turn: dict[str, Any]) -> None:
        try:
            await self._turn(turn, "typing", {})
        except (KeikiError, httpx.HTTPError):
            pass  # a courtesy

    async def lease(self, turn: dict[str, Any]) -> None:
        await self._turn(turn, "lease", {})

    async def complete(self, turn: dict[str, Any], *, ok: bool, error: str = "") -> None:
        body: dict[str, Any] = {"status": "completed" if ok else "failed"}
        if error:
            body["error"] = error[:500]
        await self._turn(turn, "complete", body)

    async def close(self) -> None:
        await self._client.aclose()


async def keep_alive(client: KeikiClient, turn: dict[str, Any], every: float = 45.0) -> None:
    """While root works: refresh the claim's lease and show typing where the channel can."""
    while True:
        await client.typing(turn)
        await asyncio.sleep(every)
        try:
            await client.lease(turn)
        except (KeikiError, httpx.HTTPError):
            pass
