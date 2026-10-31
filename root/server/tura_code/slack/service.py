"""root in Slack: answers when someone @mentions it or messages it directly.

    @root in #eng (or a DM) ─▶ read the thread, or the channel's last messages
                            ─▶ that thread's session (read-only, plan mode, kind ``slack``)
                            ─▶ a turn, visible in the UI like any session
                            ─▶ the reply, posted in the thread

One session per Slack thread, so a follow-up in the thread continues the
conversation; each channel has its own folder, and with it its own memory.
Nothing is read until root is called. Sessions are unattended: a step that
would need the owner's approval is declined, never left waiting.

Local mode only, like the line: the Slack app is one person's root.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import launcher
from . import chat
from .api import SlackAPI, SlackError, SocketMode

if TYPE_CHECKING:
    from ..tenancy import Server

logger = logging.getLogger(__name__)

NAME = "slack"
TURN_TIMEOUT = 600.0


class SlackService:
    def __init__(self, server: "Server") -> None:
        self.server = server
        self.api: SlackAPI | None = None
        self.socket: SocketMode | None = None
        self.bot_user = ""
        self.bot_name = "root"
        self.team = ""
        self.answers = False
        self.problem = ""
        self._task: asyncio.Task | None = None
        self._work: set[asyncio.Task] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self._names: dict[str, str] = {}
        #: Slack may deliver an event twice (a retry, a reconnect): each is answered once.
        self._seen: OrderedDict[str, None] = OrderedDict()
        #: The session answering in each thread right now, for "@root stop".
        self._busy: dict[str, Any] = {}
        self._threads_path = server.home / "slack" / "threads.json"

    # ----- lifecycle ------------------------------------------------------------------

    def start(self) -> None:
        if self.server.mode != "local" or self._task is not None:
            return
        tenant = self.server.tenant()
        secret = (tenant.connections.get(NAME) or {}).get("secret") or env_tokens()
        if not secret.get("bot_token") or not secret.get("app_token"):
            return
        self.answers = bool(tenant.api_key()) or launcher.scripted()
        self.api = SlackAPI(secret["bot_token"], secret["app_token"])
        self.socket = SocketMode(self.api, self.handle)
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        assert self.api is not None and self.socket is not None
        try:
            who = await self.api.call("auth.test")
        except Exception as error:  # noqa: BLE001 - shown in status; the owner fixes the token
            self.problem = f"Slack refused the bot token: {error}"
            return
        self.bot_user, self.team = str(who.get("user_id") or ""), str(who.get("team") or "")
        self.bot_name = str(who.get("user") or "root")
        await self.socket.run()

    async def close(self) -> None:
        for task in list(self._work):
            task.cancel()
        if self.socket is not None:
            await self.socket.close()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self.api is not None:
            await self.api.close()
        self.api, self.socket, self._task = None, None, None

    async def restart(self) -> None:
        await self.close()
        self.problem = ""
        self.start()

    def status(self) -> dict[str, Any]:
        entry = self.server.tenant().connections.public(NAME) if self.server.mode == "local" else None
        return {
            "configured": entry is not None or bool(env_tokens()),
            "team": self.team or (entry or {}).get("team", ""),
            "bot": self.bot_name if self.bot_user else (entry or {}).get("bot", ""),
            "connected": bool(self.socket and self.socket.connected.is_set()),
            "answers": self.answers,
            "problem": self.problem or (self.socket.problem if self.socket else ""),
        }

    # ----- answering --------------------------------------------------------------------

    async def handle(self, event: dict[str, Any]) -> None:
        """An event off the socket: answer it on its own task, so the socket keeps reading."""
        if not self.answers or not chat.called(event, self.bot_user):
            return
        if NAME in self.server.tenant().settings.get().disabled_plugins:
            return  # switched off in Settings → Plugins
        event_id = str(event.get("event_id") or event.get("client_msg_id") or event.get("ts") or "")
        if event_id in self._seen:
            return
        self._seen[event_id] = None
        while len(self._seen) > 2_000:
            self._seen.popitem(last=False)
        key = f"{event.get('channel')}:{chat.thread_of(event)}"
        if chat.is_stop(event) and key in self._busy:
            await self._busy[key].cancel()
            return
        task = asyncio.create_task(self.answer(event))
        self._work.add(task)
        task.add_done_callback(self._work.discard)

    async def answer(self, event: dict[str, Any]) -> None:
        assert self.api is not None
        channel, thread = str(event.get("channel") or ""), chat.thread_of(event)
        lock = self._locks.setdefault(f"{channel}:{thread}", asyncio.Lock())
        async with lock:
            await self._react(event, "eyes", add=True)
            try:
                text = await self._run_turn(event, channel, thread)
            except Exception as error:  # noqa: BLE001 - the asker always hears back
                logger.exception("answering in Slack failed")
                text = f"I could not answer: {error}"
            for piece in chat.chunks(chat.mrkdwn(text)):
                try:
                    await self.api.call("chat.postMessage", channel=channel, thread_ts=thread, text=piece, unfurl_links=False)
                except SlackError as error:
                    logger.warning("could not post to Slack: %s", error)
                    break
            await self._react(event, "eyes", add=False)

    async def _run_turn(self, event: dict[str, Any], channel: str, thread: str) -> str:
        names = {self.bot_user: self.bot_name}
        asker = await self._name(str(event.get("user") or ""))
        context = await self._context(event, channel, thread, names)
        question = chat.plain(str(event.get("text") or ""), names) + chat.files_note(event)
        question = re.sub(rf"^\s*@{re.escape(self.bot_name)}\b[:,]?\s*", "", question)
        place = "a direct message" if event.get("channel_type") == "im" else ("a Slack thread" if event.get("thread_ts") else "a Slack channel")
        prompt = chat.prompt_for(name=self.bot_name, place=place, asker=asker, question=question, context=context)

        runtime = self._session_for(channel, thread, question)
        try:
            await runtime.prompt(prompt)
        except ValueError:
            return "I'm still busy with the last question in this thread; ask again in a minute."
        turn = runtime.turn
        key = f"{channel}:{thread}"
        self._busy[key] = runtime
        try:
            await runtime.wait_turn(TURN_TIMEOUT)
        except asyncio.TimeoutError:
            await runtime.cancel()
            await runtime.wait_turn(30)
            return "That took too long, so I stopped. " + (turn.reply() if turn else "")
        finally:
            self._busy.pop(key, None)
        if runtime.record.last_status == "cancelled":
            return "Stopped."
        reply = turn.reply() if turn else ""
        if runtime.record.last_status != "finished" and not reply:
            return f"I couldn't finish that ({runtime.record.last_status})."
        return reply or "(no answer)"

    async def _context(self, event: dict[str, Any], channel: str, thread: str, names: dict[str, str]) -> list[chat.Line]:
        """The conversation before the call: its thread, or the channel's last messages."""
        assert self.api is not None
        ts = str(event.get("ts") or "")
        try:
            if event.get("thread_ts"):
                found = await self.api.call("conversations.replies", channel=channel, ts=thread, limit=chat.THREAD_CONTEXT + 1)
                messages = [m for m in found.get("messages") or [] if float(m.get("ts") or 0) < float(ts)]
            else:
                found = await self.api.call("conversations.history", channel=channel, latest=ts, inclusive="false", limit=chat.CHANNEL_CONTEXT)
                messages = list(reversed(found.get("messages") or []))  # newest first from Slack
        except SlackError as error:
            logger.info("no Slack context for %s: %s", channel, error)
            return []
        lines = []
        for message in messages[-chat.THREAD_CONTEXT :]:
            if not chat.talking(message):
                continue
            user = str(message.get("user") or "")
            for mentioned in re.findall(r"<@([A-Z0-9]+)", str(message.get("text") or "")):
                names.setdefault(mentioned, await self._name(mentioned))
            who = f"{self.bot_name} (you)" if user == self.bot_user or message.get("bot_id") and not user else await self._name(user)
            lines.append(chat.Line(who, chat.plain(str(message.get("text") or ""), names) + chat.files_note(message)))
        return lines

    async def _name(self, user: str) -> str:
        if not user:
            return "someone"
        if user in self._names:
            return self._names[user]
        name = user
        if self.api is not None:
            try:
                profile = (await self.api.call("users.info", user=user)).get("user") or {}
                name = (profile.get("profile") or {}).get("display_name") or profile.get("real_name") or profile.get("name") or user
            except SlackError:
                pass
        self._names[user] = name
        return name

    async def _react(self, event: dict[str, Any], emoji: str, *, add: bool) -> None:
        if self.api is None:
            return
        try:
            await self.api.call("reactions.add" if add else "reactions.remove", channel=event.get("channel"), timestamp=event.get("ts"), name=emoji)
        except SlackError:
            pass  # reactions are a courtesy

    def _session_for(self, channel: str, thread: str, question: str):
        tenant = self.server.tenant()
        threads = self._threads()
        key = f"{channel}:{thread}"
        known = threads.get(key)
        if known:
            try:
                return tenant.sessions.get(known)
            except KeyError:
                pass
        folder = self.server.home / "slack" / "channels" / re.sub(r"[^A-Za-z0-9_-]", "_", channel or "dm")
        folder.mkdir(parents=True, exist_ok=True)
        runtime = tenant.sessions.create(folder, title=f"Slack · {question[:60] or 'question'}", kind="slack", mode="plan")
        runtime.record.unattended = True  # nobody is at root's UI to approve: decline instead of waiting
        runtime.save_record()
        threads[key] = runtime.record.id
        self._threads_path.parent.mkdir(parents=True, exist_ok=True)
        self._threads_path.write_text(json.dumps(threads, indent=1), "utf-8")
        return runtime

    def _threads(self) -> dict[str, str]:
        try:
            return dict(json.loads(self._threads_path.read_text("utf-8")))
        except (OSError, ValueError, TypeError):
            return {}


def env_tokens() -> dict[str, str]:
    """A headless root (on Maritime) takes its Slack app from SLACK_BOT_TOKEN and SLACK_APP_TOKEN."""
    bot, app = os.environ.get("SLACK_BOT_TOKEN", "").strip(), os.environ.get("SLACK_APP_TOKEN", "").strip()
    return {"bot_token": bot, "app_token": app} if bot and app else {}


async def connect(server: "Server", bot_token: str, app_token: str) -> dict[str, Any]:
    """Check both tokens with Slack, store them, and start answering."""
    api = SlackAPI(bot_token, app_token)
    try:
        who = await api.call("auth.test")
        await api.call("apps.connections.open", token=app_token)
    finally:
        await api.close()
    server.tenant().connections.put(NAME, secret={"bot_token": bot_token, "app_token": app_token}, team=str(who.get("team") or ""), bot=str(who.get("user") or ""))
    await server.slack.restart()
    return server.slack.status()


def manifest(name: str = "root") -> dict[str, Any]:
    """The Slack app to create (api.slack.com/apps → Create New App → From a manifest)."""
    return {
        "display_information": {"name": name, "description": "A coding agent that answers when you @mention it."},
        "features": {
            "bot_user": {"display_name": name, "always_online": True},
            "app_home": {"messages_tab_enabled": True, "messages_tab_read_only_enabled": False},
        },
        "oauth_config": {
            "scopes": {
                "bot": [
                    "app_mentions:read",
                    "chat:write",
                    "channels:history",
                    "groups:history",
                    "im:history",
                    "mpim:history",
                    "users:read",
                    "reactions:write",
                ]
            }
        },
        "settings": {
            "event_subscriptions": {"bot_events": ["app_mention", "message.im"]},
            "interactivity": {"is_enabled": False},
            "socket_mode_enabled": True,
            "token_rotation_enabled": False,
        },
    }
