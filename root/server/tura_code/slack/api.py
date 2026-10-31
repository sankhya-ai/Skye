"""Slack's Web API and Socket Mode, on root's own HTTP and WebSocket libraries.

Socket Mode means root opens the connection (``apps.connections.open`` with the
app-level token), so neither a laptop nor a cloud machine needs a public URL.
Every event arrives in an envelope that must be acknowledged within three
seconds; the work happens after the ack. Slack closes the socket now and then
(``disconnect``) and root opens a new one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, Awaitable, Callable

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

logger = logging.getLogger(__name__)

API = "https://slack.com/api"
BACKOFF = (1, 2, 5, 10, 30, 60)


class SlackError(RuntimeError):
    def __init__(self, method: str, error: str) -> None:
        super().__init__(f"Slack {method}: {error}")
        self.error = error


class SlackAPI:
    """The Web API methods root uses, with the bot token (or the app token for Socket Mode)."""

    def __init__(self, bot_token: str, app_token: str = "", *, transport: httpx.AsyncBaseTransport | None = None, base: str = API) -> None:
        self.bot_token = bot_token
        self.app_token = app_token
        self.base = base.rstrip("/")
        self._client = httpx.AsyncClient(timeout=30, transport=transport)

    async def call(self, method: str, *, token: str = "", **params: Any) -> dict[str, Any]:
        """One method call; raises ``SlackError`` when Slack answers ``ok: false``."""
        data = {k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v)) for k, v in params.items() if v is not None}
        headers = {"Authorization": f"Bearer {token or self.bot_token}"}
        for _ in range(3):
            response = await self._client.post(f"{self.base}/{method}", data=data, headers=headers)
            if response.status_code == 429:  # rate limited: Slack says how long to wait
                await asyncio.sleep(min(30, int(response.headers.get("Retry-After") or 1)))
                continue
            body = response.json()
            if not body.get("ok"):
                raise SlackError(method, str(body.get("error") or f"HTTP {response.status_code}"))
            return body
        raise SlackError(method, "rate_limited")

    async def close(self) -> None:
        await self._client.aclose()


class SocketMode:
    """The event stream: ``on_event`` gets each Events API event, after it is acknowledged."""

    def __init__(self, api: SlackAPI, on_event: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self.api = api
        self.on_event = on_event
        self.connected = asyncio.Event()
        self.problem = ""
        self._ws: Any = None
        self._stopped = False

    async def run(self) -> None:
        attempt = 0
        while not self._stopped:
            try:
                opened = await self.api.call("apps.connections.open", token=self.api.app_token)
                async with connect(opened["url"], max_size=2**22, open_timeout=15) as ws:
                    self._ws = ws
                    attempt = 0
                    async for raw in ws:
                        if not await self._envelope(json.loads(raw)):
                            break  # Slack asked us to reconnect
            except asyncio.CancelledError:
                raise
            except SlackError as error:
                self.problem = str(error)
                if error.error in ("invalid_auth", "not_authed", "not_allowed_token_type"):
                    logger.warning("Slack refused the app-level token: %s", error)
            except (OSError, ConnectionClosed, InvalidStatus, asyncio.TimeoutError, ValueError, httpx.HTTPError) as error:
                self.problem = f"Slack unreachable: {error}"[:200]
            finally:
                self._ws = None
                self.connected.clear()
            if self._stopped:
                break
            await asyncio.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
            attempt += 1

    async def _envelope(self, envelope: dict[str, Any]) -> bool:
        kind = envelope.get("type")
        if kind == "hello":
            self.problem = ""
            self.connected.set()
            return True
        if kind == "disconnect":
            return False
        if envelope.get("envelope_id") and self._ws is not None:
            await self._ws.send(json.dumps({"envelope_id": envelope["envelope_id"]}))
        if kind == "events_api":
            payload = envelope.get("payload") or {}
            event = {**(payload.get("event") or {}), "event_id": payload.get("event_id") or envelope.get("envelope_id")}
            with contextlib.suppress(Exception):
                await self.on_event(event)
        return True

    async def close(self) -> None:
        self._stopped = True
        if self._ws is not None:
            await self._ws.close()
