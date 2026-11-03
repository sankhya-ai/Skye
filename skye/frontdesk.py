"""Skye's front desk: everyone who messages Skye (iMessage, Telegram, WhatsApp... through Keiki)
gets their own Skye, on their own Maritime machine, running their own Claude.

    their phone ─▶ Keiki ─▶ (inbox) front desk ─▶ their worker's POST /chat ─▶ the reply ─▶ them
    a worker's later news ─▶ POST /notify/<token> ─▶ Keiki notice ─▶ a turn ─▶ them

The desk does nobody's work and holds nobody's credentials: a person signs their worker in to
Claude and GitHub from the chat (``claude``, ``github``), on that worker. The desk only knows
which conversation belongs to which machine.

Run it with ``SKYE_ROLE=front-desk`` (``deploy/maritime/start.py``) and:

* ``KEIKI_HARNESS_KEY`` - the Keiki agent's harness API key (External harness, inbox mode);
* ``KEIKI_API_KEY`` + ``KEIKI_AGENT_ID`` - a developer key with agents:write, for notices
  (news a worker sends when nobody is mid-turn). Without them news waits for the next message;
* ``MARITIME_API_KEY`` - provision, deploy, secrets, manage;
* ``SKYE_WORKER_IMAGE`` - the image a worker runs (Skye's, as Maritime built it);
* ``SKYE_DESK_URL`` - this desk's public address (its Maritime publicUrl);
* ``SKYE_DESK_SECRET`` - signs each worker's notify token;
* ``SKYE_WORKER_IDLE_SECONDS`` - how long an idle worker stays awake (default 3600).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

KEIKI_BASE = os.environ.get("KEIKI_API_BASE", "").strip().rstrip("/") or "https://onkeiki.com/api/v1"
MARITIME_BASE = os.environ.get("MARITIME_API", "").strip().rstrip("/") or "https://api.maritime.sh"
READY_WITHIN = 420.0
TURN_BUDGET = 540.0  # inside Keiki's ten-minute turn deadline
LEASE_EVERY = 45.0
BACKOFF = (1, 2, 5, 10, 30, 60)
NOTE_LIMIT = 30

TRIAL_DAYS = 7
PAID_DAYS = 31
DAY = 86400.0
PAYWALL = ("Your 7-day Skye trial has ended. Your machine and your work are kept; subscribe to carry "
           "on: {url}")

WELCOME = ("Hi, I'm Skye: a team of coding agents on a cloud machine of your own, using your own "
           "Claude. Setting your machine up now. It takes a minute or two, only this once.")


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


class HttpError(RuntimeError):
    def __init__(self, what: str, status: int = 0) -> None:
        super().__init__(what)
        self.status = status


def http(method: str, url: str, *, token: str = "", body: Any = None, timeout: float = 60,
         params: dict | None = None) -> Any:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": "skye-frontdesk"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read()[:300].decode("utf-8", "replace")
        raise HttpError(f"{method} {urllib.parse.urlparse(url).path}: {error.code} {detail}", error.code) from None
    except (OSError, urllib.error.URLError) as error:
        raise HttpError(f"{method} {urllib.parse.urlparse(url).path}: {error}") from None
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


# -- Keiki ------------------------------------------------------------------------------------

class Keiki:
    def __init__(self, harness_key: str, api_key: str = "", agent_id: str = "",
                 base: str = KEIKI_BASE) -> None:
        self.harness_key, self.api_key, self.agent_id, self.base = harness_key, api_key, agent_id, base

    def next_turn(self, wait: int = 25) -> dict | None:
        data = http("GET", f"{self.base}/harness/inbox/next", token=self.harness_key,
                    params={"wait": max(0, min(30, wait))}, timeout=wait + 20)
        turn = (data or {}).get("turn")
        return turn if isinstance(turn, dict) else None

    def _turn(self, turn: dict, route: str, body: dict) -> Any:
        url = f"{str(turn['keiki']['turnUrl']).rstrip('/')}/{route}"
        try:
            return http("POST", url, token=turn["turnToken"], body=body, timeout=30)
        except HttpError as error:
            if error.status == 409:  # already done under that key: fine
                return None
            raise

    def send(self, turn: dict, text: str, key: str) -> None:
        self._turn(turn, "messages", {"text": text, "idempotencyKey": key})

    def typing(self, turn: dict) -> None:
        try:
            self._turn(turn, "typing", {})
        except HttpError:
            pass

    def lease(self, turn: dict) -> None:
        try:
            self._turn(turn, "lease", {})
        except HttpError:
            pass

    def complete(self, turn: dict, ok: bool, error: str = "") -> None:
        body: dict[str, Any] = {"status": "completed" if ok else "failed"}
        if error:
            body["error"] = error[:500]
        self._turn(turn, "complete", body)

    def notice(self, conversation: str) -> bool:
        """Ask Keiki to open a turn in that conversation, so queued news can be sent in it."""
        if not (self.api_key and self.agent_id):
            return False
        try:
            http("POST", f"{self.base}/developer/agents/{self.agent_id}/notice", token=self.api_key,
                 body={"text": "Skye has an update for this person.", "phone": conversation})
            return True
        except HttpError as error:
            print(f"desk: notice for a conversation failed: {error}", flush=True)
            return False


# -- Maritime ---------------------------------------------------------------------------------

class Maritime:
    def __init__(self, api_key: str, base: str = MARITIME_BASE) -> None:
        self.api_key, self.base = api_key, base

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        return http(method, f"{self.base}{path}", token=self.api_key, **kwargs)

    def get(self, agent_id: str) -> dict | None:
        try:
            agent = self.call("GET", f"/api/agents/{agent_id}")
        except HttpError:
            return None
        if isinstance(agent, dict) and isinstance(agent.get("results"), dict):
            agent = agent["results"]
        return agent if isinstance(agent, dict) and agent.get("id") else None

    def find(self, external_id: str) -> dict | None:
        found = self.call("GET", "/api/agents", params={"externalId": external_id})
        if isinstance(found, dict):
            found = found.get("results") or found.get("agents") or []
        found = [a for a in found or [] if isinstance(a, dict) and a.get("externalId") == external_id]
        return found[0] if found else None

    def chat(self, agent_id: str, message: str) -> str:
        data = self.call("POST", f"/api/agents/{agent_id}/chat", body={"message": message, "source": "skye-desk"},
                         timeout=90)
        if isinstance(data, dict) and data.get("error") and not data.get("response"):
            raise HttpError(str(data["error"])[:200])
        return str((data or {}).get("response") or "").strip()


LINK = re.compile(r"(?i)\s*link\s+([A-Z0-9]{6})\s*")
LINK_TTL = 1800.0
FIREBASE_KEY = "AIzaSyBmPJA94vIrRygxbvcmqxAghFkRQnQIrx0"  # Sankhya's public web key (not a secret)
SITE_ORIGINS = ("https://skye.sankhyaailabs.com", "http://localhost:8000", "http://127.0.0.1:8000")


def verify_firebase(id_token: str) -> dict | None:
    """A Firebase ID token -> {uid, email}, checked by Google (accounts:lookup), or None."""
    if not id_token or len(id_token) > 4096:
        return None
    key = _env("FIREBASE_API_KEY") or FIREBASE_KEY
    try:
        data = http("POST", f"https://identitytoolkit.googleapis.com/v1/accounts:lookup?key={key}",
                    body={"idToken": id_token}, timeout=20)
    except HttpError:
        return None
    users = (data or {}).get("users") or []
    if not users or not users[0].get("localId"):
        return None
    return {"uid": users[0]["localId"], "email": users[0].get("email") or ""}


# -- the desk ---------------------------------------------------------------------------------

def external_id(conversation: str) -> str:
    """Maritime's key for this conversation's machine: stable, no personal data in it."""
    return "skye-" + hashlib.sha256(conversation.encode()).hexdigest()[:32]


class Desk:
    def __init__(self, home: Path, keiki: Keiki, maritime: Maritime, *, image: str, desk_url: str,
                 secret: str, idle_seconds: int = 3600) -> None:
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.keiki, self.maritime = keiki, maritime
        self.image, self.desk_url, self.secret, self.idle = image, desk_url.rstrip("/"), secret, idle_seconds
        self.path = self.home / "desk.json"
        self._lock = threading.Lock()
        self._creating: dict[str, threading.Lock] = {}
        self._busy: dict[str, threading.Lock] = {}
        self.stop = threading.Event()
        self.state = self._load()
        self.telegram: "TelegramFront | None" = None

    # -- state -----------------------------------------------------------------------------
    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1), "utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def _update(self, change: Callable[[dict], Any]) -> Any:
        with self._lock:
            result = change(self.state)
            self._save()
            return result

    def token_for(self, key: str) -> str:
        return hmac.new(self.secret.encode(), key.encode(), hashlib.sha256).hexdigest()[:40]

    # -- the inbox -------------------------------------------------------------------------
    def run(self) -> None:
        attempt = 0
        while not self.stop.is_set():
            try:
                turn = self.keiki.next_turn(wait=25)
                attempt = 0
            except HttpError as error:
                print(f"desk: Keiki inbox: {error}", flush=True)
                self.stop.wait(BACKOFF[min(attempt, len(BACKOFF) - 1)])
                attempt += 1
                continue
            if turn is not None:
                threading.Thread(target=self.answer, args=(turn,), daemon=True,
                                 name=f"desk-turn-{turn.get('turnId')}").start()

    def answer(self, turn: dict) -> None:
        """Run one claimed Keiki turn and always settle it."""
        conversation = str(turn.get("conversationId") or "")
        done = threading.Event()
        sent = 0

        def send(text: str) -> None:
            nonlocal sent
            sent += 1
            self.keiki.send(turn, text, key=f"{turn.get('turnId')}:{sent}")

        text = "\n".join(str(m.get("text") or "").strip() for m in turn.get("messages") or []
                         if isinstance(m, dict) and not m.get("silent")).strip()
        threading.Thread(target=self._keep_alive, args=(turn, done), daemon=True).start()
        try:
            ok, error = self.handle(conversation, text, send)
        finally:
            done.set()
        try:
            self.keiki.complete(turn, ok=ok, error=error)
        except HttpError as failure:
            print(f"desk: turn {turn.get('turnId')} not settled: {failure}", flush=True)

    def handle(self, conversation: str, text: str, send: Callable[[str], None]) -> tuple[bool, str]:
        """One message from a person, on any channel: their news first, then their machine's answer."""
        lock = self._busy.setdefault(conversation, threading.Lock())
        with lock:
            try:
                for note in self._take_notes(conversation):
                    send(note)
                linked = LINK.fullmatch(text)
                if linked:
                    send(self.link(conversation, linked.group(1)))
                elif text and self.plan(self.key_for(conversation))["status"] == "expired":
                    send(PAYWALL.format(url=self.pay_url()))
                elif text:  # a notice-only turn (silent) carries just the notes above
                    reply = self._ask_worker(conversation, text, send)
                    if reply:
                        send(reply)
                return True, ""
            except Exception as failure:  # noqa: BLE001 - the person always hears back
                traceback.print_exc()
                try:
                    send("I couldn't reach your machine just now. Try again in a minute.")
                except Exception:  # noqa: BLE001
                    pass
                return False, str(failure)[:300]

    def _keep_alive(self, turn: dict, done: threading.Event) -> None:
        while not done.is_set():
            self.keiki.typing(turn)
            if done.wait(LEASE_EVERY):
                return
            self.keiki.lease(turn)

    def _ask_worker(self, conversation: str, text: str, send: Callable[[str], None]) -> str:
        agent_id = self.worker_for(conversation, send)
        deadline = time.monotonic() + TURN_BUDGET
        while True:
            try:
                return self.maritime.chat(agent_id, text)
            except HttpError as error:
                # A machine waking from sleep can refuse for a moment: try again for a while.
                if time.monotonic() > deadline - 60:
                    raise
                print(f"desk: worker {agent_id} not answering yet: {error}", flush=True)
                time.sleep(10)

    # -- workers ---------------------------------------------------------------------------
    def key_for(self, conversation: str) -> str:
        """A conversation linked to a website account uses that account's machine."""
        return (self.state.get("links") or {}).get(conversation) or external_id(conversation)

    def worker_for(self, conversation: str, send: Callable[[str], None]) -> str:
        return self.worker_by_key(self.key_for(conversation), send, conversation=conversation)

    def worker_by_key(self, key: str, send: Callable[[str], None], *, conversation: str = "") -> str:
        with self._creating.setdefault(key, threading.Lock()):
            known = (self.state.get("workers") or {}).get(key)
            agent = self.maritime.get(known) if known else None
            if agent is None:
                agent = self.maritime.find(key)
            if agent is not None and agent.get("status") == "error":
                try:
                    self.maritime.call("DELETE", f"/api/agents/{agent['id']}")
                except HttpError:
                    pass
                agent = None
            if agent is None:
                send(WELCOME)
                agent = self._create(key)
                self.start_trial(key)
            agent_id = str(agent["id"])
            def remember(state: dict) -> None:
                state.setdefault("workers", {})[key] = agent_id
                if conversation:
                    state.setdefault("conversations", {})[key] = conversation
            self._update(remember)
            if agent.get("status") not in ("active", "sleeping"):
                self._wait_ready(agent_id)
            return agent_id

    def _create(self, key: str) -> dict:
        if not self.image or not self.desk_url:
            raise HttpError("the desk has no SKYE_WORKER_IMAGE or SKYE_DESK_URL")
        body = {"name": f"skye-{key[len('skye-'):][:20]}", "externalId": key, "framework": "custom",
                "imageName": self.image, "exposedPort": 8080, "idleTtlSeconds": self.idle}
        created = self.maritime.call("POST", "/api/agents", body=body, timeout=120)
        if isinstance(created, dict) and isinstance(created.get("results"), dict):
            created = created["results"]
        if not isinstance(created, dict) or not created.get("id"):
            raise HttpError("Maritime did not create the machine")
        agent_id = str(created["id"])
        env = {"SKYE_ROLE": ("worker", False),
               "SKYE_DESK_NOTIFY_URL": (f"{self.desk_url}/notify/{key}/{self.token_for(key)}", True)}
        # Maritime takes variables after create; restart once up so Skye starts with them.
        for name, (value, secret) in env.items():
            self.maritime.call("POST", f"/api/agents/{agent_id}/env",
                               body={"key": name, "value": value, "isSecret": secret})
        self._wait_ready(agent_id)
        self.maritime.call("POST", f"/api/agents/{agent_id}/restart", body={})
        time.sleep(5)
        self._wait_ready(agent_id)
        return {**created, "status": "active"}

    def _wait_ready(self, agent_id: str) -> None:
        deadline = time.monotonic() + READY_WITHIN
        while time.monotonic() < deadline:
            status = (self.maritime.get(agent_id) or {}).get("status")
            if status in ("active", "sleeping"):
                return
            if status == "error":
                raise HttpError("your machine failed to start")
            time.sleep(5)
        raise HttpError("your machine is taking too long to start")

    # -- website accounts ------------------------------------------------------------------
    @staticmethod
    def web_key(uid: str) -> str:
        return external_id("web:" + uid)

    def machine(self, key: str) -> dict[str, Any]:
        """Where this account's machine is: none, creating, ready (or failed, with why)."""
        job = (self.state.get("jobs") or {}).get(key) or {}
        agent_id = (self.state.get("workers") or {}).get(key)
        if job.get("status") == "creating":
            return {"status": "creating", "since": job.get("since")}
        if agent_id:
            agent = self.maritime.get(agent_id)
            if agent is not None and agent.get("status") != "error":
                return {"status": "ready", "agent": agent_id, "vm": agent.get("status")}
        if job.get("status") == "failed":
            return {"status": "failed", "error": job.get("error", "")}
        return {"status": "none"}

    def create_machine(self, key: str) -> dict[str, Any]:
        current = self.machine(key)
        if current["status"] in ("creating", "ready"):
            return current
        self._update(lambda s: s.setdefault("jobs", {}).__setitem__(
            key, {"status": "creating", "since": time.time()}))

        def build() -> None:
            try:
                self.worker_by_key(key, lambda text: None)
                self._update(lambda s: s.setdefault("jobs", {}).pop(key, None))
            except Exception as error:  # noqa: BLE001 - reported to the page
                traceback.print_exc()
                self._update(lambda s: s.setdefault("jobs", {}).__setitem__(
                    key, {"status": "failed", "error": str(error)[:300]}))
        threading.Thread(target=build, daemon=True, name=f"desk-build-{key[:12]}").start()
        return {"status": "creating"}

    def ask(self, key: str, message: str, *, gated: bool = True) -> str:
        if gated and self.plan(key)["status"] == "expired":
            raise HttpError(PAYWALL.format(url=self.pay_url()))
        agent_id = (self.state.get("workers") or {}).get(key)
        if not agent_id:
            raise HttpError("no machine yet")
        return self.maritime.chat(agent_id, message)

    def set_telegram(self, key: str, bot_token: str) -> dict[str, Any]:
        """The owner's own bot on their machine: check the token with Telegram, set it, restart."""
        if not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{30,}", bot_token.strip()):
            raise HttpError("that doesn't look like a bot token from @BotFather")
        api = _env("SKYE_TELEGRAM_API") or "https://api.telegram.org"
        me = http("POST", f"{api}/bot{bot_token.strip()}/getMe", body={}, timeout=20)
        username = str(((me or {}).get("result") or {}).get("username") or "")
        if not username:
            raise HttpError("Telegram didn't accept that token")
        agent_id = (self.state.get("workers") or {}).get(key)
        if not agent_id:
            raise HttpError("no machine yet")
        code = uuid.uuid4().hex[:6].upper()
        for name, value in (("SKYE_TELEGRAM_TOKEN", bot_token.strip()), ("SKYE_TELEGRAM_PAIR_CODE", code)):
            self.maritime.call("POST", f"/api/agents/{agent_id}/env",
                               body={"key": name, "value": value, "isSecret": True})
        self.maritime.call("POST", f"/api/agents/{agent_id}/restart", body={})
        return {"bot": username, "pair_code": code, "link": f"https://t.me/{username}?start={code}"}

    def link_code(self, key: str) -> str:
        code = uuid.uuid4().hex[:6].upper()

        def add(state: dict) -> None:
            codes = state.setdefault("link_codes", {})
            now = time.time()
            for old in [c for c, v in codes.items() if v.get("until", 0) < now or v.get("key") == key]:
                codes.pop(old)
            codes[code] = {"key": key, "until": now + LINK_TTL}
        self._update(add)
        return code

    def link(self, conversation: str, code: str) -> str:
        entry = (self.state.get("link_codes") or {}).get(code.upper())
        if not entry or entry.get("until", 0) < time.time():
            return "That link code isn't valid any more. Get a new one at skye.sankhyaailabs.com."

        def apply(state: dict) -> None:
            state.setdefault("links", {})[conversation] = entry["key"]
            state.setdefault("conversations", {})[entry["key"]] = conversation
            state.setdefault("link_codes", {}).pop(code.upper(), None)
        self._update(apply)
        return "Linked. This chat now talks to the Skye you set up on the website. Send help to see what I can do."

    # -- the plan: a 7-day trial from the machine's first day, then paid ------------------
    def plan(self, key: str) -> dict[str, Any]:
        account = (self.state.get("accounts") or {}).get(key)
        if account is None and key in (self.state.get("workers") or {}):
            account = self._update(lambda s: s.setdefault("accounts", {}).setdefault(
                key, {"trial_started": time.time()}))
        if account is None or (not account.get("trial_started") and not account.get("paid_until")):
            return {"status": "new", "trial_days": TRIAL_DAYS}
        now = time.time()
        paid_until = float(account.get("paid_until") or 0)
        if paid_until > now:
            return {"status": "active", "ends_at": paid_until, "days_left": math.ceil((paid_until - now) / DAY)}
        trial_end = float(account.get("trial_started") or now) + TRIAL_DAYS * DAY
        if trial_end > now:
            return {"status": "trial", "ends_at": trial_end, "days_left": math.ceil((trial_end - now) / DAY)}
        return {"status": "expired", "ended_at": max(trial_end, paid_until)}

    def remember_account(self, key: str, email: str = "", uid: str = "") -> None:
        def note(state: dict) -> None:
            account = state.setdefault("accounts", {}).setdefault(key, {})
            if email:
                account["email"] = email.lower()
            if uid:
                account["uid"] = uid
        self._update(note)

    def start_trial(self, key: str) -> None:
        self._update(lambda s: s.setdefault("accounts", {}).setdefault(key, {}).setdefault(
            "trial_started", time.time()))

    def extend(self, email: str, days: float = PAID_DAYS, until: float | None = None) -> list[str]:
        """Mark the account(s) with this email paid: until a date, or for ``days`` from now (or
        from the end of what's already paid, so an early renewal loses nothing)."""
        email = email.strip().lower()
        changed: list[str] = []

        def apply(state: dict) -> None:
            for key, account in (state.get("accounts") or {}).items():
                if email and account.get("email") == email:
                    start = max(time.time(), float(account.get("paid_until") or 0))
                    account["paid_until"] = until if until else start + days * DAY
                    changed.append(key)
        self._update(apply)
        return changed

    @staticmethod
    def pay_url(email: str = "") -> str:
        base = _env("SKYE_PAY_URL") or "https://skye.sankhyaailabs.com/start.html#plan"
        return base

    # -- news from workers -----------------------------------------------------------------
    def notify(self, key: str, token: str, text: str) -> bool:
        if not (re.fullmatch(r"skye-[0-9a-f]{32}", key) and hmac.compare_digest(token, self.token_for(key))):
            return False
        conversation = (self.state.get("conversations") or {}).get(key)
        if not conversation or not text.strip():
            return bool(conversation)

        def add(state: dict) -> None:
            notes = state.setdefault("notes", {}).setdefault(conversation, [])
            notes.append(text.strip()[:3800])
            del notes[:-NOTE_LIMIT]
        if self.telegram is not None and conversation.startswith(TelegramFront.PREFIX):
            try:
                self.telegram.send(conversation, text.strip())
                return True
            except HttpError as error:
                print(f"desk: telegram news not sent: {error}", flush=True)
        self._update(add)
        self.keiki.notice(conversation)  # a turn opens and carries the note; else the next message does
        return True

    def _take_notes(self, conversation: str) -> list[str]:
        return self._update(lambda s: (s.setdefault("notes", {}).pop(conversation, None) or []))


class TelegramFront:
    """Skye's own Telegram bot: everyone who messages it gets their own machine, as through Keiki."""

    PREFIX = "tg:"
    LIMIT = 4000
    START_LINK = re.compile(r"/start(?:@\w+)?\s+(?:link[-_])?([A-Za-z0-9]{6})")

    def __init__(self, desk: "Desk", token: str, base: str = "https://api.telegram.org") -> None:
        self.desk, self.token, self.base = desk, token, base.rstrip("/")
        self.offset = 0
        desk.telegram = self

    def api(self, method: str, body: dict | None = None, timeout: float = 40) -> Any:
        data = http("POST", f"{self.base}/bot{self.token}/{method}", body=body or {}, timeout=timeout)
        return (data or {}).get("result")

    def send(self, conversation: str, text: str) -> None:
        chat = conversation[len(self.PREFIX):]
        text = text or "..."
        for start in range(0, len(text), self.LIMIT):
            self.api("sendMessage", {"chat_id": chat, "text": text[start:start + self.LIMIT],
                                     "disable_web_page_preview": True})

    def poll_once(self, wait: int = 25) -> int:
        updates = self.api("getUpdates", {"offset": self.offset, "timeout": wait,
                                          "allowed_updates": ["message"]}, timeout=wait + 20) or []
        for update in updates:
            self.offset = max(self.offset, int(update.get("update_id", 0)) + 1)
            message = update.get("message") or {}
            chat = message.get("chat") or {}
            text = str(message.get("text") or "").strip()
            if chat.get("type") != "private" or not text or chat.get("id") is None:
                continue
            threading.Thread(target=self.on_message, args=(f"{self.PREFIX}{chat['id']}", text),
                             daemon=True, name=f"desk-tg-{chat['id']}").start()
        return len(updates)

    def on_message(self, conversation: str, text: str) -> None:
        started = self.START_LINK.fullmatch(text)
        if started:
            text = f"link {started.group(1).upper()}"
        elif re.fullmatch(r"/start(?:@\w+)?", text):
            text = "help"
        done = threading.Event()

        def typing() -> None:
            while not done.is_set():
                try:
                    self.api("sendChatAction", {"chat_id": conversation[len(self.PREFIX):],
                                                "action": "typing"}, timeout=10)
                except HttpError:
                    pass
                done.wait(5)
        threading.Thread(target=typing, daemon=True).start()
        try:
            self.desk.handle(conversation, text, lambda reply: self.send(conversation, reply))
        finally:
            done.set()

    def run(self) -> None:
        attempt = 0
        try:
            self.api("deleteWebhook", {})
        except HttpError:
            pass
        while not self.desk.stop.is_set():
            try:
                self.poll_once()
                attempt = 0
            except HttpError as error:
                print(f"desk: telegram: {error}", flush=True)
                self.desk.stop.wait(BACKOFF[min(attempt, len(BACKOFF) - 1)])
                attempt += 1


URL = re.compile(r"https://\S+")
GH_CODE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b")


class DeskHandler(BaseHTTPRequestHandler):
    """Health, workers' news (/notify), and the website's setup API (/api/*).

    The website calls with ``Content-Type: text/plain`` and the Firebase ID token in the JSON
    body - a "simple" cross-origin request (no preflight), and nothing rides in a header that a
    proxy in front of the desk might drop."""
    server_version = "skye-desk"

    def log_message(self, fmt: str, *args) -> None:
        return

    @property
    def desk(self) -> "Desk":
        return self.server.desk  # type: ignore[attr-defined]

    def _json(self, payload: Any, code: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin") or ""
        allowed = [o.strip() for o in (_env("SKYE_SITE_ORIGINS") or "").split(",") if o.strip()] or SITE_ORIGINS
        if origin in allowed:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        origin = self.headers.get("Origin") or ""
        if origin in SITE_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in ("/", "/health"):
            return self._json({"ok": True, "service": "skye front desk"})
        if path == "/api/config":
            return self._json({"imessage": _env("SKYE_IMESSAGE"), "telegram": _env("SKYE_TELEGRAM_BOT")})
        self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        length = min(int(self.headers.get("Content-Length") or 0), 200_000)
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        parts = self.path.split("?")[0].strip("/").split("/")
        if parts == ["billing", "razorpay"]:
            return self._razorpay(raw)
        if parts == ["admin", "activate"]:
            secret = _env("SKYE_ADMIN_KEY")
            if not secret or not hmac.compare_digest(str(body.get("key") or ""), secret):
                return self._json({"error": "forbidden"}, 403)
            changed = self.desk.extend(str(body.get("email") or ""), float(body.get("days") or PAID_DAYS))
            return self._json({"ok": bool(changed), "accounts": len(changed)})
        if parts[0] == "chat":
            return self._json({"response": "This is Skye's front desk. Message Skye on iMessage or Telegram."})
        if len(parts) == 3 and parts[0] == "notify":
            if self.desk.notify(parts[1], parts[2], str(body.get("text") or "")):
                return self._json({"ok": True})
            return self._json({"error": "unknown worker"}, 403)
        if parts[0] == "api":
            user = verify_firebase(str(body.get("idToken") or ""))
            if user is None:
                return self._json({"error": "Sign in again."}, 401)
            try:
                return self._json(self.api("/".join(parts[1:]), user, body))
            except HttpError as error:
                return self._json({"error": str(error)}, 400)
        self._json({"error": "not found"}, 404)

    def _razorpay(self, raw: bytes) -> None:
        """Razorpay's webhook (payment captured, payment link paid, subscription charged):
        signed with the webhook secret; the payer's email finds the account."""
        secret = _env("RAZORPAY_WEBHOOK_SECRET")
        given = self.headers.get("X-Razorpay-Signature") or ""
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest() if secret else ""
        if not secret or not hmac.compare_digest(given, expected):
            return self._json({"error": "bad signature"}, 400)
        try:
            event = json.loads(raw)
        except ValueError:
            return self._json({"error": "bad body"}, 400)
        payload = event.get("payload") or {}
        payment = (payload.get("payment") or {}).get("entity") or {}
        subscription = (payload.get("subscription") or {}).get("entity") or {}
        notes = {**(subscription.get("notes") or {}), **(payment.get("notes") or {})}
        email = str(notes.get("email") or payment.get("email") or "")
        if event.get("event") not in ("payment.captured", "payment_link.paid", "subscription.charged",
                                      "subscription.activated") or not email:
            return self._json({"ok": True, "ignored": True})
        until = float(subscription.get("current_end") or 0) or None
        changed = self.desk.extend(email, until=until + DAY if until else None)
        print(f"desk: payment for {len(changed)} account(s)", flush=True)
        return self._json({"ok": True})

    def api(self, route: str, user: dict, body: dict) -> dict[str, Any]:
        desk, key = self.desk, Desk.web_key(user["uid"])
        desk.remember_account(key, user["email"], user["uid"])
        if route == "me":
            machine = desk.machine(key)
            out: dict[str, Any] = {"email": user["email"], "machine": machine,
                                   "plan": desk.plan(key), "pay_url": desk.pay_url(),
                                   "price": _env("SKYE_PRICE") or "$5 / month",
                                   "linked": key in (desk.state.get("conversations") or {})}
            if machine["status"] == "ready":
                try:
                    out["setup"] = json.loads(desk.ask(key, "/setup.json", gated=False))
                except (HttpError, ValueError):
                    out["setup"] = None  # waking up: the page asks again
            return out
        if route == "machine":
            if desk.plan(key)["status"] == "expired":
                raise HttpError(PAYWALL.format(url=desk.pay_url()))
            return desk.create_machine(key)
        if route == "claude":
            reply = desk.ask(key, "/claude")
            link = URL.search(reply)
            return {"link": link.group(0) if link else "", "reply": reply}
        if route == "claude/code":
            code = str(body.get("code") or "").strip()
            if not re.fullmatch(r"\S{8,}", code):
                raise HttpError("paste the whole code the Claude page showed you")
            return {"reply": desk.ask(key, f"/claude code {code}")}
        if route == "github":
            reply = desk.ask(key, "/github")
            code = GH_CODE.search(reply)
            return {"code": code.group(1) if code else "", "link": "https://github.com/login/device",
                    "reply": reply}
        if route == "repo":
            repo = str(body.get("repo") or "").strip()
            if not re.fullmatch(r"(?:https://github\.com/)?[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?(?:\.git)?/?", repo):
                raise HttpError("use owner/name, e.g. sankhya-ai/Skye")
            return {"reply": desk.ask(key, f"/repo {repo}")}
        if route == "telegram":
            return desk.set_telegram(key, str(body.get("token") or ""))
        if route == "link":
            return {"code": desk.link_code(key), "imessage": _env("SKYE_IMESSAGE"),
                    "telegram": _env("SKYE_TELEGRAM_BOT")}
        raise HttpError("unknown request")


def from_env() -> Desk:
    home = Path(_env("SKYE_DESK_HOME") or Path.home() / "skye-desk")
    secret = _env("SKYE_DESK_SECRET")
    if not secret:
        secret_file = home / "secret"
        home.mkdir(parents=True, exist_ok=True)
        if not secret_file.exists():
            secret_file.write_text(uuid.uuid4().hex + uuid.uuid4().hex, "utf-8")
            os.chmod(secret_file, 0o600)
        secret = secret_file.read_text("utf-8").strip()
    try:
        idle = int(_env("SKYE_WORKER_IDLE_SECONDS") or 3600)
    except ValueError:
        idle = 3600
    return Desk(home, Keiki(_env("KEIKI_HARNESS_KEY"), _env("KEIKI_API_KEY"), _env("KEIKI_AGENT_ID")),
                Maritime(_env("MARITIME_API_KEY")), image=_env("SKYE_WORKER_IMAGE"),
                desk_url=_env("SKYE_DESK_URL"), secret=secret, idle_seconds=idle)


def main() -> None:
    desk = from_env()
    port = int(_env("PORT") or 8080)
    server = ThreadingHTTPServer(("0.0.0.0", port), DeskHandler)
    server.desk = desk  # type: ignore[attr-defined]
    missing = [n for n in ("MARITIME_API_KEY", "SKYE_WORKER_IMAGE", "SKYE_DESK_URL")
               if not _env(n)]
    print(f"skye front desk on :{port}" + (f" - missing: {', '.join(missing)}" if missing else ""), flush=True)
    if _env("KEIKI_HARNESS_KEY"):
        threading.Thread(target=desk.run, name="desk-inbox", daemon=True).start()
    if _env("SKYE_DESK_TELEGRAM_TOKEN"):
        front = TelegramFront(desk, _env("SKYE_DESK_TELEGRAM_TOKEN"))
        threading.Thread(target=front.run, name="desk-telegram", daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
