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
        """Run one claimed turn and always settle it."""
        conversation = str(turn.get("conversationId") or "")
        lock = self._busy.setdefault(conversation, threading.Lock())
        with lock:
            done = threading.Event()
            threading.Thread(target=self._keep_alive, args=(turn, done), daemon=True).start()
            sent = 0

            def send(text: str) -> None:
                nonlocal sent
                sent += 1
                self.keiki.send(turn, text, key=f"{turn.get('turnId')}:{sent}")

            ok, error = True, ""
            try:
                for note in self._take_notes(conversation):
                    send(note)
                text = "\n".join(str(m.get("text") or "").strip() for m in turn.get("messages") or []
                                 if isinstance(m, dict) and not m.get("silent")).strip()
                if text:  # a notice-only turn (silent) carries just the notes above
                    reply = self._ask_worker(conversation, text, send)
                    if reply:
                        send(reply)
            except Exception as failure:  # noqa: BLE001 - the turn is always settled
                traceback.print_exc()
                ok, error = False, str(failure)[:300]
                try:
                    send("I couldn't reach your machine just now. Try again in a minute.")
                except HttpError:
                    pass
            finally:
                done.set()
            try:
                self.keiki.complete(turn, ok=ok, error=error)
            except HttpError as failure:
                print(f"desk: turn {turn.get('turnId')} not settled: {failure}", flush=True)

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
    def worker_for(self, conversation: str, send: Callable[[str], None]) -> str:
        key = external_id(conversation)
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
            agent_id = str(agent["id"])
            self._update(lambda s: (s.setdefault("workers", {}).__setitem__(key, agent_id),
                                    s.setdefault("conversations", {}).__setitem__(key, conversation)))
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
        self._update(add)
        self.keiki.notice(conversation)  # a turn opens and carries the note; else the next message does
        return True

    def _take_notes(self, conversation: str) -> list[str]:
        return self._update(lambda s: (s.setdefault("notes", {}).pop(conversation, None) or []))


class DeskHandler(BaseHTTPRequestHandler):
    server_version = "skye-desk"

    def log_message(self, fmt: str, *args) -> None:
        return

    def _json(self, payload: Any, code: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.split("?")[0] in ("/", "/health"):
            return self._json({"ok": True, "service": "skye front desk"})
        self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        length = min(int(self.headers.get("Content-Length") or 0), 200_000)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = {}
        parts = self.path.split("?")[0].strip("/").split("/")
        if parts[0] == "chat":
            return self._json({"response": "This is Skye's front desk. Message Skye on iMessage or Telegram."})
        if len(parts) == 3 and parts[0] == "notify":
            desk: Desk = self.server.desk  # type: ignore[attr-defined]
            text = str((body or {}).get("text") or "") if isinstance(body, dict) else ""
            if desk.notify(parts[1], parts[2], text):
                return self._json({"ok": True})
            return self._json({"error": "unknown worker"}, 403)
        self._json({"error": "not found"}, 404)


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
    missing = [n for n in ("KEIKI_HARNESS_KEY", "MARITIME_API_KEY", "SKYE_WORKER_IMAGE", "SKYE_DESK_URL")
               if not _env(n)]
    print(f"skye front desk on :{port}" + (f" - missing: {', '.join(missing)}" if missing else ""), flush=True)
    if _env("KEIKI_HARNESS_KEY"):
        threading.Thread(target=desk.run, name="desk-inbox", daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
