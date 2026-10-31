"""root on the line: joins as ``<owner>/root`` and answers what is sent to it.

    message from nitin/claude-code ─▶ that sender's line session (read-only, plan mode)
                                   ─▶ a turn, visible in the UI like any session
                                   ─▶ the turn's reply, sent back as the answer

One session per asking agent, so a follow-up continues the conversation. The
session is kind ``line``: plan mode removes the editing tools, the line role
says the message is a question, not an instruction, and anything that still
needs an approval waits for the owner in the UI (a turn that waits too long is
cancelled and the asker told so). root says it ``answers`` only when it has a
model and a workspace; otherwise the relay routes ``@owner`` to the owner's
other agents, e.g. their Claude Code.

Local mode only: a hosted server is many people, and a line is one person's.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import launcher
from . import config as line_config
from .client import LineClient
from .log import AgentLog
from .protocol import LineError, Message

if TYPE_CHECKING:
    from ..tenancy import Server

logger = logging.getLogger(__name__)
TURN_TIMEOUT = 600.0
PROMPT_FILE_BYTES = 32 * 1024


def prompt_for(message: Message) -> str:
    lines = [f"Message over the team line from `{message.sender}`:", "", "<<<"]
    lines += [message.text.strip() or "(no text)", ">>>"]
    for item in message.files:
        if item.get("error"):
            lines += ["", f"(Attached `{item['name']}`: {item['error']})"]
            continue
        try:
            text = item["data"].decode("utf-8")
        except UnicodeDecodeError:
            text = None
        if text is not None and len(item["data"]) <= PROMPT_FILE_BYTES:
            lines += ["", f"Attached file `{item['name']}`:", "```", text, "```"]
        else:
            lines += ["", f"(Attached `{item['name']}`, {len(item['data'])} bytes, not shown: binary or too large.)"]
    return "\n".join(lines)


class LineService:
    def __init__(self, server: "Server") -> None:
        self.server = server
        self.config: line_config.LineConfig | None = None
        self.client: LineClient | None = None
        self.answers = False
        self.log: AgentLog | None = None
        self._task: asyncio.Task | None = None
        self._locks: dict[str, asyncio.Lock] = {}
        self._conversations_path = server.home / "line" / "conversations.json"

    # ----- lifecycle --------------------------------------------------------------

    def start(self) -> None:
        if self.server.mode != "local" or self._task is not None:
            return
        self.config = line_config.load(self.server.home)
        if self.config is None:
            return
        tenant = self.server.tenant()
        workspace_ok = bool(self.config.workspace) and Path(self.config.workspace).is_dir()
        self.answers = workspace_ok and (bool(tenant.api_key()) or launcher.scripted())
        if not self.answers:
            return  # nothing to say: @you goes to your Claude Code, or to your seat on the board
        agent = self.config.agent("root")
        self.log = AgentLog(agent)
        self.client = LineClient(
            self.config.invite,
            agent,
            kind="root",
            label=f"root · {Path(self.config.workspace).name}",
            priority=10,
            answers=self.answers,
            state_path=line_config.state_path(self.server.home, agent),
            on_message=self.answer,
        )
        self._task = asyncio.create_task(self.client.run())

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self.client, self._task = None, None

    async def restart(self) -> None:
        await self.close()
        self.start()

    def status(self) -> dict[str, Any]:
        if self.config is None or self.client is None:
            return {"configured": False}
        return {
            "configured": True,
            "agent": self.client.agent,
            "relay": self.config.invite.relay,
            "workspace": self.config.workspace,
            "answers": self.answers,
            "connected": self.client.connected.is_set(),
            "problem": self.client.problem,
            "agents": self.client.roster,
        }

    # ----- answering ------------------------------------------------------------------

    async def answer(self, message: Message) -> None:
        if self.log is not None:
            self.log.received(message)
        if message.to == "*":
            return  # a note to everyone, not a question to root
        lock = self._locks.setdefault(message.sender, asyncio.Lock())
        async with lock:
            try:
                text = await self._run(message)
            except Exception as error:  # noqa: BLE001 - the asker always hears back
                logger.exception("answering %s failed", message.sender)
                text = f"root could not answer: {error}"
        await self._reply(message, text)

    async def _run(self, message: Message) -> str:
        runtime = self._session_for(message.sender)
        try:
            await runtime.prompt(prompt_for(message))
        except ValueError:
            return "root is busy with another turn in this conversation; ask again in a minute."
        turn = runtime.turn
        if runtime.record.title != f"Line · {message.sender}":  # a first prompt names the session after itself
            runtime.record.title = f"Line · {message.sender}"
            runtime.save_record()
        try:
            await runtime.wait_turn(TURN_TIMEOUT)
        except asyncio.TimeoutError:
            await runtime.cancel()
            await runtime.wait_turn(30)
            return "root timed out (it may be waiting for its owner's approval). Partial answer: " + (turn.reply() if turn else "")
        reply = turn.reply() if turn else ""
        status = runtime.record.last_status
        if status != "finished" and not reply:
            return f"root could not finish ({status}). Its owner can see the session in root."
        return reply or "(root finished without a reply)"

    def _session_for(self, sender: str):
        tenant = self.server.tenant()
        conversations = self._conversations()
        known = conversations.get(sender)
        if known:
            try:
                return tenant.sessions.get(known)
            except KeyError:
                pass
        runtime = tenant.sessions.create(self.config.workspace, title=f"Line · {sender}", kind="line", mode="plan")
        conversations[sender] = runtime.record.id
        self._conversations_path.parent.mkdir(parents=True, exist_ok=True)
        self._conversations_path.write_text(json.dumps(conversations, indent=1), "utf-8")
        return runtime

    def _conversations(self) -> dict[str, str]:
        try:
            return dict(json.loads(self._conversations_path.read_text("utf-8")))
        except (OSError, ValueError, TypeError):
            return {}

    async def _reply(self, message: Message, text: str) -> None:
        thread = message.thread or message.id
        to = message.to if message.to.startswith("#") else message.sender  # a channel conversation stays in its channel
        try:
            receipt = await self.client.send(to, text, reply_to=message.id, thread=thread, title=message.title)
            if self.log is not None:
                self.log.sent(receipt, to=to, text=text, reply_to=message.id, title=message.title)
        except LineError as error:
            logger.warning("could not reply to %s: %s", message.sender, error)
