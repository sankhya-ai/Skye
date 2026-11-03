"""Skye as one person's worker behind a front desk (iMessage, Telegram, ... through Keiki).

    their phone ─▶ Keiki ─▶ front desk ─▶ Maritime ─▶ POST /chat on THIS machine ─▶ ChatBot
                                       ◀─ the reply (within Maritime's 30 s)
    later news (needs you / done / a sign-in finished) ─▶ POST $SKYE_DESK_NOTIFY_URL ─▶ desk ─▶ them

The machine belongs to one person, and only the front desk (holding the operator's Maritime
key) can reach ``/chat`` - Maritime's front door is the gate - so there is no pairing: whoever
speaks here is the owner. The commands are the Telegram bot's (``telegram.Bot``); only the
transport differs: replies are collected for the length of one request, anything said after
that is pushed to the desk, and a message the desk could not take waits in an outbox that the
next reply carries.
"""
from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import logins
from .telegram import Bot

ROLE_ENV = "SKYE_ROLE"
NOTIFY_ENV = "SKYE_DESK_NOTIFY_URL"
REPLY_WITHIN = 20.0  # Maritime answers /chat within 30 s
OUTBOX_LIMIT = 20

HELP = """I'm your Skye: a team of coding agents on your own cloud machine, using your own Claude.

Set up (once):
• claude - sign in to your Claude account
• github - sign in to GitHub
• repo owner/name - attach a repository

Then just write the goal, e.g. "add a dark mode toggle". The team plans, builds and checks it.
• solo <goal> - one agent, no planning (fewest tokens)
• status - what's running
• approve - let a waiting step run, once
• pr - open the pull request for the last finished task
• projects - your repositories; /use <name> to switch"""


def enabled() -> bool:
    return os.environ.get(ROLE_ENV, "").strip().lower() == "worker"


class ChatBot(Bot):
    help_text = HELP
    state_file = "chat.json"  # its own: the machine may run the owner's Telegram bot as well

    def __init__(self, state_dir: Path) -> None:
        super().__init__(state_dir, token="-")
        self.notify_url = os.environ.get(NOTIFY_ENV, "").strip()
        self._collect: list[str] | None = None
        self._collect_lock = threading.Lock()
        self._turn_lock = threading.Lock()
        if not self.state.get("owner_chat"):
            # One person's machine: the desk is the only way in, so that person owns it.
            self.state.update({"owner_chat": "desk", "owner_user": "desk"})
            self._save()

    def start(self) -> None:  # no Telegram polling: only the watcher
        threading.Thread(target=self._watch_loop, name="skye-chat-watch", daemon=True).start()

    # -- one message from the desk ---------------------------------------------------------
    def reply(self, message: str) -> str:
        """Handle one message; return everything said within REPLY_WITHIN seconds. Whatever
        is said later (a slow sign-in, a task's news) goes to the desk on its own."""
        if message.strip() == "/setup.json":  # the website's status check: answers only that
            return json.dumps(self.setup_state())
        with self._turn_lock:
            earlier = self._take_outbox()
            with self._collect_lock:
                self._collect = []
            worker = threading.Thread(target=self._handle_safely, args=(message,), daemon=True)
            worker.start()
            worker.join(REPLY_WITHIN)
            with self._collect_lock:
                said, self._collect = self._collect or [], None
            if worker.is_alive() and not said:
                said = ["Working on it - I'll message you when it's done."]
            return "\n\n".join(earlier + said) or "OK."

    def _handle_safely(self, message: str) -> None:
        try:
            self.on_text(message.strip() or "/help")
        except Exception as error:  # noqa: BLE001 - the person gets told, the machine stays up
            import traceback
            traceback.print_exc()
            self.say(f"Something went wrong: {error}")

    def setup_state(self) -> dict[str, Any]:
        """What the website's setup page shows: which sign-ins are done, which repository."""
        from . import telegram
        claude, github = logins.claude_status(), logins.github_status()
        project = self._active_project()
        try:  # the owner's own Telegram bot, when this machine runs one
            tg = json.loads((self.state_dir / telegram.Bot.state_file).read_text("utf-8"))
        except (OSError, ValueError):
            tg = {}
        return {"claude": {"signed_in": bool(claude.get("logged_in")), "plan": claude.get("plan") or "",
                           "pending": bool(self.claude_login and self.claude_login.alive)},
                "github": {"signed_in": bool(github.get("logged_in")), "login": github.get("login") or "",
                           "pending": bool(self.github_login and self.github_login.alive)},
                "repository": project["name"] if project else "",
                "telegram": {"configured": bool(os.environ.get(telegram.TOKEN_ENV)),
                             "paired": bool(tg.get("owner_user")),
                             "pair_code": "" if tg.get("owner_user") else str(tg.get("pair_code") or "")}}

    # -- the transport ---------------------------------------------------------------------
    def say(self, text: str, buttons: list[list[tuple[str, str]]] | None = None,
            chat: Any = None) -> None:
        for row in buttons or []:
            for label, data in row:
                if data.startswith("a|"):
                    text += f"\n\nReply “approve” to {label[0].lower() + label[1:]}."
                elif data.startswith("p|"):
                    text += "\n\nReply “pr” to open the pull request."
        with self._collect_lock:
            if self._collect is not None:
                self._collect.append(text)
                return
        if not self._push(text):
            outbox = self.state.setdefault("outbox", [])
            outbox.append(text)
            del outbox[:-OUTBOX_LIMIT]
            self._save()

    def _push(self, text: str) -> bool:
        if not self.notify_url:
            return False
        request = urllib.request.Request(self.notify_url, data=json.dumps({"text": text}).encode(),
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return 200 <= response.status < 300
        except (OSError, urllib.error.URLError) as error:
            print(f"chat: the desk did not take a message: {error}", flush=True)
            return False

    def _take_outbox(self) -> list[str]:
        outbox = list(self.state.get("outbox") or [])
        if outbox:
            self.state["outbox"] = []
            self._save()
        return outbox


_bot: ChatBot | None = None


def start(state_dir: Path) -> ChatBot | None:
    global _bot
    if not enabled():
        return None
    _bot = ChatBot(state_dir)
    _bot.start()
    return _bot


def bot() -> ChatBot | None:
    return _bot
