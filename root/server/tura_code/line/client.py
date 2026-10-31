"""One agent's connection to a line: join, see who is there, send, ask, receive.

    client = LineClient(invite, "ashish/root", kind="root", answers=True)
    task = asyncio.create_task(client.run())        # connects, reconnects, forever
    reply = await client.ask("@nitin", "Which port does the dev server use?")

Incoming messages that are not the reply to one of this client's own ``ask``
calls go to ``on_message`` when set, else to :attr:`inbox`. The relay keeps
what is sent while an agent is away; :attr:`cursor` (saved to ``state_path``)
is how far this agent has read, so a reconnect picks up exactly what it missed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from .config import shared_home
from .protocol import (
    AGENT,
    INLINE_FILE_BYTES,
    EpochStore,
    Invite,
    LineError,
    Message,
    body_hash,
    decode_file,
    encode_file,
    load_member_key,
    mentions_in,
    sign_envelope,
    unwrap_epoch_key,
    verify_distribution,
    _unb64,
)

logger = logging.getLogger(__name__)
BACKOFF = (1, 2, 5, 10, 30)
INBOX_LIMIT = 500


class LineClient:
    def __init__(
        self,
        invite: Invite,
        agent: str,
        *,
        kind: str = "agent",
        label: str = "",
        priority: int = 0,
        answers: bool = False,
        state_path: Path | None = None,
        on_message: Callable[[Message], Awaitable[None]] | None = None,
    ) -> None:
        if not AGENT.fullmatch(agent):
            raise LineError("an agent is named owner/name, lowercase, e.g. nitin/claude-code")
        self.invite = invite
        self.agent = agent
        self.hello = {"t": "hello", "agent": agent, "kind": kind, "label": label, "priority": priority, "answers": answers}
        self.state_path = state_path
        self.on_message = on_message
        self.roster: list[dict[str, Any]] = []
        # The line's protocol version, named by the welcome frame (1 shared key, 2 signed
        # attribution, 3 signed attribution + per-epoch read keys).
        self.line_version = 1
        # v3: this machine's member key (the 0600 file chamber enroll wrote) and the epoch keys
        # this member has unwrapped so far - both loaded from ~/.chamber/keys, never sent anywhere.
        owner = agent.split("/")[0]
        home = shared_home()
        self._member_key = load_member_key(home, self.invite.line_id, owner)
        self._epochs = EpochStore(home / "keys" / f"{self.invite.line_id}-{owner}-epochs.json")
        self.inbox: asyncio.Queue[Message] = asyncio.Queue(INBOX_LIMIT)
        self.connected = asyncio.Event()
        self.problem = ""
        self.cursor = self._load_cursor()
        self._ws = None
        self._acks: dict[str, asyncio.Future] = {}
        self._replies: dict[str, asyncio.Future] = {}
        self._stopped = False
        self._handlers: set[asyncio.Task] = set()

    # ----- the connection -----------------------------------------------------

    async def run(self) -> None:
        attempt = 0
        while not self._stopped:
            try:
                async with connect(self.invite.ws_url, additional_headers={"Authorization": f"Bearer {self.invite.token}"}, max_size=2**20, open_timeout=15) as ws:
                    self._ws = ws
                    pass_ = {"pass": self.invite.pass_} if self.invite.pass_ else {}
                    await ws.send(json.dumps({**self.hello, "since": self.cursor, **pass_}))
                    attempt = 0
                    async for raw in ws:
                        await self._frame(json.loads(raw))
            except asyncio.CancelledError:
                raise
            except InvalidStatus as error:
                self.problem = "the relay refused this invite" if error.response.status_code == 401 else f"relay answered {error.response.status_code}"
            except (OSError, ConnectionClosed, asyncio.TimeoutError, ValueError) as error:
                self.problem = f"relay unreachable: {error}"[:200]
            finally:
                self._ws = None
                self.connected.clear()
                for future in self._acks.values():
                    if not future.done():
                        future.set_exception(LineError("the connection to the relay dropped"))
                self._acks.clear()
            if self._stopped:
                break
            await asyncio.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
            attempt += 1

    async def close(self) -> None:
        self._stopped = True
        if self._ws is not None:
            await self._ws.close()

    async def _frame(self, frame: dict[str, Any]) -> None:
        kind = frame.get("t")
        if kind == "welcome":
            self.roster = frame.get("agents") or []
            self.line_version = int(frame.get("version") or 1)
            self.problem = ""
            self.connected.set()
        elif kind == "roster":
            self.roster = frame.get("agents") or []
        elif kind == "sent":
            future = self._acks.pop(str(frame.get("id")), None)
            if future and not future.done():
                future.set_result(frame)
        elif kind == "error":
            future = self._acks.pop(str(frame.get("id") or ""), None)
            if future and not future.done():
                future.set_exception(LineError(str(frame.get("error"))))
            else:
                self.problem = str(frame.get("error"))[:200]
        elif kind == "msg":
            await self._receive(frame)

    async def _receive(self, frame: dict[str, Any]) -> None:
        seq = int(frame.get("seq") or 0)
        to_owner = frame.get("to_owner")
        addressed = f"@{to_owner}" if to_owner else str(frame.get("to") or "")
        epoch_no = int(frame.get("epoch") or 0) if self.line_version == 3 else 0
        if self.line_version == 3 and epoch_no == 0:
            # Epoch 0 on a v3 line is the bootstrap channel ONLY: an admin-signed epoch
            # distribution. Anything else at epoch 0 is a downgrade attempt and is dropped.
            try:
                payload = self.invite.open(str(frame.get("body") or ""), sender=str(frame.get("from")), to=addressed, message_id=str(frame.get("id")), reply_to=frame.get("reply_to"))
                self._take_distribution(payload)
            except (LineError, ValueError):
                pass
            self._advance(seq)
            return
        epoch_arg = None
        if self.line_version == 3:
            key = self._epochs.key_for(epoch_no)
            if key is None:
                # No key for this epoch: history from before this member joined, or a rotation
                # they were left out of. Never an error - those messages simply don't open here.
                self._advance(seq)
                return
            epoch_arg = (epoch_no, key)
        try:
            payload = self.invite.open(
                str(frame.get("body") or ""), sender=str(frame.get("from")), to=addressed, message_id=str(frame.get("id")), reply_to=frame.get("reply_to"), epoch=epoch_arg
            )
        except (LineError, ValueError) as error:
            logger.warning("dropped a message from %s: %s", frame.get("from"), error)
            self._advance(seq)
            return
        message = Message(
            id=str(frame["id"]),
            sender=str(frame["from"]),
            to=addressed,
            text=str(payload.get("text") or ""),
            reply_to=frame.get("reply_to"),
            thread=str(payload["thread"]) if payload.get("thread") else (None if frame.get("reply_to") else str(frame["id"])),
            title=str(payload.get("title") or ""),
            mentions=[str(m) for m in frame.get("mentions") or []],
            seq=seq,
            ts=int(frame.get("ts") or 0),
            files=await self._files(payload.get("files"), epoch=epoch_arg),
        )
        self._advance(seq)
        waiting = self._replies.get(message.reply_to or "")
        if waiting is not None and not waiting.done():
            waiting.set_result(message)
        elif self.on_message is not None:
            # A task, not awaited here: a handler that replies needs this reader for its receipt.
            task = asyncio.create_task(self.on_message(message))
            self._handlers.add(task)
            task.add_done_callback(self._handlers.discard)
        else:
            if self.inbox.full():
                self.inbox.get_nowait()
            self.inbox.put_nowait(message)

    def _take_distribution(self, payload: dict[str, Any]) -> None:
        """An epoch distribution: accept only one signed by the admin key in THIS invite, for THIS
        line, with a wrap for THIS member that actually unwraps (mirrors chamber's #takeDistribution)."""
        if str(payload.get("type") or "") != "chamber-epoch":
            return
        try:
            epoch = int(payload.get("epoch") or 0)
        except (TypeError, ValueError):
            return
        wraps = payload.get("wraps")
        if payload.get("line") != self.invite.line_id or epoch < 1 or not isinstance(wraps, dict) or not self.invite.admin_pub:
            return
        if not verify_distribution(self.invite.admin_pub, line=self.invite.line_id, epoch=epoch, wraps=wraps, sig=str(payload.get("sig") or "")):
            return
        owner = self.agent.split("/")[0]
        wrap = wraps.get(owner)
        if not isinstance(wrap, dict) or not self._member_key or not self._member_key.get("encPrivate"):
            return
        key = unwrap_epoch_key(_unb64(str(self._member_key["encPrivate"])), wrap, line=self.invite.line_id, epoch=epoch, owner=owner)
        if key is not None:
            self._epochs.save(epoch, key)

    async def _files(self, raw: Any, *, epoch: tuple[int, bytes] | None = None) -> list[dict[str, Any]]:
        """Decode a message's files, fetching the big ones from the relay's storage."""
        out = []
        for item in filter(None, map(decode_file, raw if isinstance(raw, list) else [])):
            if "blob" in item:
                try:
                    async with httpx.AsyncClient(timeout=120) as http:
                        response = await http.get(self.invite.blob_url(item["blob"]), headers=self._auth)
                    response.raise_for_status()
                    item = {"name": item["name"], "data": self.invite.open_blob(response.content, item["blob"], epoch=epoch)}
                except (httpx.HTTPError, LineError) as error:
                    item = {"name": item["name"], "data": b"", "error": f"could not fetch: {error}"[:200]}
            out.append(item)
        return out

    @property
    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.invite.token}"}

    def _send_epoch(self) -> tuple[int, bytes] | None:
        """The current epoch's key for sending on a v3 line, or a refusal: never the shared key."""
        if self.line_version != 3:
            return None
        key = self._epochs.key_for(self._epochs.current) if self._epochs.current else None
        if key is None:
            raise LineError("this chamber is v3 and the admin has not shared an epoch key with you yet")
        return (self._epochs.current, key)

    async def _upload(self, name: str, data: bytes) -> tuple[dict[str, Any], str]:
        blob_id = uuid.uuid4().hex
        sealed = self.invite.seal_blob(data, blob_id, epoch=self._send_epoch())
        async with httpx.AsyncClient(timeout=300) as http:
            response = await http.put(self.invite.blob_url(blob_id), content=sealed, headers=self._auth)
        if response.status_code != 200:
            try:
                detail = response.json().get("error")
            except ValueError:
                detail = response.text[:200]
            raise LineError(f"could not upload {name}: {detail}")
        return {"name": name, "blob": blob_id, "size": len(data)}, blob_id

    # ----- the cursor ------------------------------------------------------------

    def _load_cursor(self) -> int:
        try:
            return int(json.loads(self.state_path.read_text("utf-8"))["cursor"]) if self.state_path else 0
        except (OSError, ValueError, KeyError, TypeError):
            return 0

    def _advance(self, seq: int) -> None:
        if seq <= self.cursor:
            return
        self.cursor = seq
        if self.state_path is not None:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps({"cursor": seq}), "utf-8")

    # ----- sending -------------------------------------------------------------------

    async def send(
        self, to: str, text: str, *, files: list[dict[str, Any]] | None = None, reply_to: str | None = None, thread: str | None = None, title: str = "",
        mentions: list[str] | None = None, timeout: float = 15,
    ) -> dict[str, Any]:
        """Send a message; returns the relay's receipt (``delivered`` agents, or ``held``) with its ``id`` and ``thread``.
        A message starts a conversation (its own id) unless ``thread`` says which."""
        to = to.strip().lower()
        try:
            await asyncio.wait_for(self.connected.wait(), timeout)
        except asyncio.TimeoutError as error:
            raise LineError(f"not connected to the line ({self.problem or 'connecting'})") from error
        message_id = uuid.uuid4().hex
        packed, blobs = [], []
        for item in files or []:
            if len(item["data"]) <= INLINE_FILE_BYTES:
                packed.append(encode_file(item["name"], item["data"]))
            else:
                entry, blob_id = await self._upload(item["name"], item["data"])
                packed.append(entry)
                blobs.append(blob_id)
        thread = thread or message_id
        epoch_arg = self._send_epoch()
        body = self.invite.seal({"text": text, "files": packed, "thread": thread, "title": title}, sender=self.agent, to=to, message_id=message_id, reply_to=reply_to, epoch=epoch_arg)
        future = asyncio.get_running_loop().create_future()
        self._acks[message_id] = future
        mentions = mentions_in(text) if mentions is None else mentions
        frame = {"t": "send", "id": message_id, "to": to, "body": body, "blobs": blobs, "mentions": mentions}
        if reply_to:
            frame["reply_to"] = reply_to
        if self.line_version >= 2:
            # The relay verifies this signature before storing or forwarding: sign the envelope
            # (line, from, to, id, reply_to, ts, [epoch,] body-hash) with the member key.
            if not self._member_key:
                raise LineError(f"this chamber is v{self.line_version} but this machine has no member key: run `chamber enroll`")
            frame["ts"] = int(time.time() * 1000)
            if self.line_version == 3:
                frame["epoch"] = epoch_arg[0]
            frame["sig"] = sign_envelope(
                _unb64(str(self._member_key["private"])),
                line=self.invite.line_id, from_=self.agent, to=to, message_id=message_id,
                reply_to=reply_to or "", ts=frame["ts"], hash_=body_hash(body), epoch=frame.get("epoch") or 0,
            )
        await self._ws.send(json.dumps(frame))
        receipt = await asyncio.wait_for(future, timeout)
        return {**receipt, "id": message_id, "thread": thread, "mentions": mentions}

    async def ask(self, to: str, text: str, *, files: list[dict[str, Any]] | None = None, timeout: float = 120) -> tuple[dict[str, Any], Message | None]:
        """Send and wait for the reply. Returns the receipt and the reply, or ``None`` on timeout
        (a later reply then lands in the inbox like any message)."""
        started = time.monotonic()
        receipt = await self.send(to, text, files=files)
        future = asyncio.get_running_loop().create_future()
        self._replies[receipt["id"]] = future
        try:
            return receipt, await asyncio.wait_for(future, max(1.0, timeout - (time.monotonic() - started)))
        except asyncio.TimeoutError:
            return receipt, None
        finally:
            self._replies.pop(receipt["id"], None)

    async def next_message(self, timeout: float = 0) -> Message | None:
        try:
            if timeout <= 0:
                return self.inbox.get_nowait()
            return await asyncio.wait_for(self.inbox.get(), timeout)
        except (asyncio.QueueEmpty, asyncio.TimeoutError):
            return None
