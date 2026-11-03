"""The worker's chat transport (skye.chat) and the front desk (skye.frontdesk), against fake
Keiki and Maritime servers on localhost."""
from __future__ import annotations

import json
import threading
import urllib.error
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from skye import chat, frontdesk, logins


class Fake:
    """One server playing Keiki (inbox, turn API, notices) and Maritime (agents, env, chat)."""

    def __init__(self) -> None:
        self.turns: list[dict] = []
        self.sent: list[tuple[str, str]] = []  # (turnId, text)
        self.completed: list[tuple[str, str]] = []
        self.notices: list[dict] = []
        self.agents: dict[str, dict] = {}
        self.env: dict[str, dict] = {}
        self.chats: list[tuple[str, str]] = []
        self.answer = lambda agent_id, message: f"echo: {message}"
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def _out(self, payload, code=200):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}")

            def do_GET(self):
                path, _, query = self.path.partition("?")
                if path == "/keiki/harness/inbox/next":
                    assert self.headers["Authorization"] == "Bearer harness-key"
                    deadline = time.time() + 1
                    while time.time() < deadline:
                        if fake.turns:
                            return self._out({"turn": fake.turns.pop(0)})
                        time.sleep(0.02)
                    return self._out({"turn": None})
                if path == "/api/agents":
                    ext = query.split("externalId=")[-1]
                    return self._out([a for a in fake.agents.values() if a["externalId"] == ext])
                if path.startswith("/api/agents/"):
                    agent = fake.agents.get(path.split("/")[3])
                    return self._out(agent) if agent else self._out({"error": "nf"}, 404)
                self._out({"error": "nf"}, 404)

            def do_POST(self):
                path = self.path.split("?")[0]
                body = self._body()
                if path.startswith("/keiki/turns/"):
                    turn_id, route = path.split("/")[3], path.split("/")[4]
                    if route == "messages":
                        fake.sent.append((turn_id, body["text"]))
                    elif route == "complete":
                        fake.completed.append((turn_id, body["status"]))
                    return self._out({"ok": True})
                if path.endswith("/notice"):
                    assert self.headers["Authorization"] == "Bearer dev-key"
                    fake.notices.append(body)
                    fake.turns.append(turn(f"n{len(fake.notices)}", body["phone"], "", silent=True))
                    return self._out({"ok": True})
                if path == "/api/agents":
                    agent_id = f"agent-{len(fake.agents) + 1}"
                    fake.agents[agent_id] = {**body, "id": agent_id, "status": "active"}
                    return self._out(fake.agents[agent_id])
                if path.endswith("/env"):
                    fake.env.setdefault(path.split("/")[3], {})[body["key"]] = body["value"]
                    return self._out({"ok": True})
                if path.endswith("/restart"):
                    return self._out({"ok": True})
                if path.startswith("/bot") and path.endswith("/getMe"):
                    if "good" not in path:
                        return self._out({"ok": False}, 401)
                    return self._out({"ok": True, "result": {"username": "MySkyeBot"}})
                if path.endswith("/chat"):
                    agent_id = path.split("/")[3]
                    fake.chats.append((agent_id, body["message"]))
                    return self._out({"response": fake.answer(agent_id, body["message"])})
                self._out({"error": "nf"}, 404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def turn(turn_id: str, conversation: str, text: str, *, silent: bool = False) -> dict:
    fake_base = turn.base  # type: ignore[attr-defined]
    return {"turnId": turn_id, "conversationId": conversation, "turnToken": "tt",
            "silent": silent,
            "messages": [{"text": text or "notice", "silent": silent}],
            "keiki": {"turnUrl": f"{fake_base}/keiki/turns/{turn_id}"}}


@pytest.fixture
def fake():
    server = Fake()
    turn.base = server.url  # type: ignore[attr-defined]
    yield server
    server.close()


def make_desk(tmp_path: Path, fake: Fake, desk_url: str = "https://desk.example") -> frontdesk.Desk:
    return frontdesk.Desk(tmp_path / "desk", frontdesk.Keiki("harness-key", "dev-key", "kagent", base=f"{fake.url}/keiki"),
                          frontdesk.Maritime("m-key", base=fake.url), image="img:1", desk_url=desk_url,
                          secret="s3cret", idle_seconds=60)


def wait_until(check, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return False


# -- the desk ---------------------------------------------------------------------------------

def test_first_message_creates_a_machine_and_answers(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(frontdesk.time, "sleep", lambda s: None)
    desk = make_desk(tmp_path, fake)
    desk.answer(turn("t1", "+15550001", "help"))
    texts = [t for _, t in fake.sent]
    assert texts[0] == frontdesk.WELCOME
    assert texts[-1] == "echo: help"
    assert fake.completed == [("t1", "completed")]
    (agent_id, agent), = fake.agents.items()
    assert agent["externalId"] == frontdesk.external_id("+15550001")
    assert agent["imageName"] == "img:1" and agent["exposedPort"] == 8080
    env = fake.env[agent_id]
    assert env["SKYE_ROLE"] == "worker"
    key = agent["externalId"]
    assert env["SKYE_DESK_NOTIFY_URL"] == f"https://desk.example/notify/{key}/{desk.token_for(key)}"

    # The second message reuses the machine and says no welcome.
    fake.sent.clear()
    desk.answer(turn("t2", "+15550001", "status"))
    assert [t for _, t in fake.sent] == ["echo: status"]
    assert len(fake.agents) == 1


def test_each_conversation_gets_its_own_machine(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(frontdesk.time, "sleep", lambda s: None)
    desk = make_desk(tmp_path, fake)
    desk.answer(turn("t1", "+15550001", "hi"))
    desk.answer(turn("t2", "telegram:42", "hi"))
    assert len(fake.agents) == 2
    assert {a for a, _ in fake.chats} == set(fake.agents)


def test_worker_news_reaches_the_person_through_a_notice(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(frontdesk.time, "sleep", lambda s: None)
    desk = make_desk(tmp_path, fake)
    desk.answer(turn("t1", "+15550001", "hi"))
    key = frontdesk.external_id("+15550001")
    fake.sent.clear()

    assert not desk.notify(key, "wrong-token", "x")
    assert not desk.notify("skye-" + "0" * 32, desk.token_for("skye-" + "0" * 32), "x")
    assert desk.notify(key, desk.token_for(key), "✅ Done: the thing")
    assert fake.notices == [{"text": "Skye has an update for this person.", "phone": "+15550001"}]
    # The notice opens a silent turn; the desk sends the note in it and asks the worker nothing.
    chats_before = len(fake.chats)
    desk.answer(fake.turns.pop(0))
    assert [t for _, t in fake.sent] == ["✅ Done: the thing"]
    assert len(fake.chats) == chats_before


def test_news_waits_for_the_next_message_without_notices(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(frontdesk.time, "sleep", lambda s: None)
    desk = make_desk(tmp_path, fake)
    desk.keiki.api_key = ""
    desk.answer(turn("t1", "+15550001", "hi"))
    key = frontdesk.external_id("+15550001")
    fake.sent.clear()
    assert desk.notify(key, desk.token_for(key), "⏸ Needs you")
    assert fake.notices == []
    desk.answer(turn("t2", "+15550001", "status"))
    assert [t for _, t in fake.sent] == ["⏸ Needs you", "echo: status"]


def test_inbox_loop_and_notify_endpoint(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(frontdesk.time, "sleep", lambda s: None)
    desk = make_desk(tmp_path, fake)
    server = ThreadingHTTPServer(("127.0.0.1", 0), frontdesk.DeskHandler)
    server.desk = desk  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=desk.run, daemon=True).start()
    try:
        fake.turns.append(turn("t1", "+15550009", "hello"))
        assert wait_until(lambda: ("t1", "completed") in fake.completed)
        key = frontdesk.external_id("+15550009")
        request = urllib.request.Request(f"{base}/notify/{key}/{desk.token_for(key)}",
                                         data=json.dumps({"text": "done!"}).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            assert json.loads(response.read())["ok"]
        assert wait_until(lambda: ("n1", "done!") in fake.sent)
        with urllib.request.urlopen(f"{base}/health", timeout=5) as response:
            assert json.loads(response.read())["ok"]
    finally:
        desk.stop.set()
        server.shutdown()


def test_a_failure_still_settles_the_turn(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(frontdesk.time, "sleep", lambda s: None)
    desk = make_desk(tmp_path, fake)
    desk.image = ""  # cannot create a machine
    desk.answer(turn("t1", "+15550001", "hi"))
    assert fake.completed == [("t1", "failed")]
    assert "couldn't reach your machine" in fake.sent[-1][1]


# -- the worker's side ------------------------------------------------------------------------

@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(chat.ROLE_ENV, "worker")
    monkeypatch.setattr(logins, "claude_status", lambda: {"installed": True, "logged_in": False})
    monkeypatch.setattr(logins, "github_status", lambda: {"installed": True, "logged_in": False})
    state = tmp_path / "state"
    state.mkdir()
    return state


class Inbox:
    def __init__(self):
        self.got: list[str] = []
        inbox = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                inbox.got.append(json.loads(self.rfile.read(n))["text"])
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/notify/k/t"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def test_worker_replies_in_the_request_and_pushes_later_news(worker, monkeypatch):
    inbox = Inbox()
    monkeypatch.setenv(chat.NOTIFY_ENV, inbox.url)
    monkeypatch.setattr(chat, "REPLY_WITHIN", 0.5)
    bot = chat.ChatBot(worker)
    reply = bot.reply("help")
    assert "claude - sign in" in reply and "Claude Code: not signed in" in reply

    release = threading.Event()
    monkeypatch.setattr(bot, "_status", lambda: (release.wait(5), bot.say("late news")))
    assert bot.reply("status") == "Working on it - I'll message you when it's done."
    release.set()
    assert wait_until(lambda: inbox.got == ["late news"])
    inbox.server.shutdown()


def test_worker_keeps_news_it_could_not_push(worker, monkeypatch):
    monkeypatch.setenv(chat.NOTIFY_ENV, "http://127.0.0.1:9/notify/k/t")
    bot = chat.ChatBot(worker)
    bot.say("⏸ Needs you: deploy", [[("Approve deploy once", "a|m1|deploy")]])
    bot.say("✅ Done: x", [[("Open pull request", "p|m1")]])
    reply = bot.reply("projects")
    assert reply.startswith("⏸ Needs you: deploy\n\nReply “approve” to approve deploy once.")
    assert "Reply “pr” to open the pull request." in reply
    assert "No repositories yet" in reply
    assert bot.reply("projects").startswith("No repositories yet")  # the outbox went out once


def test_approve_and_pr_words_use_the_latest_task(worker, monkeypatch):
    bot = chat.ChatBot(worker)
    called = []
    monkeypatch.setattr(bot, "_approve", lambda m, s: called.append(("approve", m, s)))
    monkeypatch.setattr(bot, "_pull_request", lambda m: called.append(("pr", m)))
    folder = worker / "missions" / "m-123456"
    folder.mkdir(parents=True)
    (folder / "pending-deploy.json").write_text("{}")
    bot.state.update({"last_needs": "m-123456", "last_done": "m-654321"})
    bot.reply("approve")
    bot.reply("PR")
    assert called == [("approve", "m-123456", "deploy"), ("pr", "m-654321")]
    assert "Nothing is waiting" in bot.reply("approve nothing-here")


def test_maritime_chat_route(worker, monkeypatch):
    from skye.ui import server as ui
    bot = chat.start(worker)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), ui.UiHandler)
        httpd.state_dir = worker  # type: ignore[attr-defined]
        httpd.token = "tok"  # type: ignore[attr-defined]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        request = urllib.request.Request(f"{base}/chat", data=json.dumps({"message": "help"}).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            assert "I'm your Skye" in json.loads(response.read())["response"]
        with urllib.request.urlopen(f"{base}/health", timeout=5) as response:
            assert json.loads(response.read()) == {"ok": True}
        httpd.shutdown()
    finally:
        bot.stop_event.set()
        chat._bot = None


# -- the website's setup API ------------------------------------------------------------------

def call_api(base: str, route: str, body: dict | None = None, origin: str = "https://skye.sankhyaailabs.com"):
    request = urllib.request.Request(f"{base}/api/{route}", data=json.dumps({"idToken": "tok", **(body or {})}).encode(),
                                     headers={"Content-Type": "text/plain", "Origin": origin})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read()), response.headers
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read()), error.headers


def test_website_sets_up_a_machine_and_signs_in(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(frontdesk.time, "sleep", lambda s: None)
    monkeypatch.setattr(frontdesk, "verify_firebase",
                        lambda token: {"uid": "u1", "email": "a@b.c"} if token == "tok" else None)
    monkeypatch.setenv("SKYE_TELEGRAM_API", fake.url)
    replies = {"/setup.json": json.dumps({"claude": {"signed_in": False}}),
               "/claude": "To sign in:\nhttps://claude.com/cai/oauth/authorize?x=1\nsend it here",
               "/github": "Open https://github.com/login/device\n2. Enter the code AB12-CD34"}
    fake.answer = lambda agent_id, message: replies.get(message, f"echo: {message}")
    desk = make_desk(tmp_path, fake)
    server = ThreadingHTTPServer(("127.0.0.1", 0), frontdesk.DeskHandler)
    server.desk = desk  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, data, headers = call_api(base, "me", {"idToken": "bad"})
        assert status == 401
        status, data, headers = call_api(base, "me")
        assert data["machine"] == {"status": "none"} and data["email"] == "a@b.c"
        assert headers["Access-Control-Allow-Origin"] == "https://skye.sankhyaailabs.com"
        assert call_api(base, "machine")[1]["status"] in ("creating", "ready")
        assert wait_until(lambda: call_api(base, "me")[1]["machine"]["status"] == "ready")
        (agent_id, agent), = fake.agents.items()
        assert agent["externalId"] == frontdesk.Desk.web_key("u1")
        assert call_api(base, "me")[1]["setup"] == {"claude": {"signed_in": False}}
        assert call_api(base, "claude")[1]["link"] == "https://claude.com/cai/oauth/authorize?x=1"
        assert call_api(base, "claude/code", {"code": "abc"})[0] == 400
        assert call_api(base, "claude/code", {"code": "good-code-123"})[1]["reply"] == "echo: /claude code good-code-123"
        assert call_api(base, "github")[1]["code"] == "AB12-CD34"
        assert call_api(base, "repo", {"repo": "a/b; rm -rf"})[0] == 400
        assert call_api(base, "repo", {"repo": "sankhya-ai/Skye"})[1]["reply"] == "echo: /repo sankhya-ai/Skye"

        assert call_api(base, "telegram", {"token": "nope"})[0] == 400
        status, data, _ = call_api(base, "telegram", {"token": "12345:good" + "x" * 30})
        assert data["bot"] == "MySkyeBot" and data["link"] == f"https://t.me/MySkyeBot?start={data['pair_code']}"
        assert fake.env[agent_id]["SKYE_TELEGRAM_PAIR_CODE"] == data["pair_code"]

        # Linking an iMessage conversation to this account: it then talks to the same machine.
        code = call_api(base, "link")[1]["code"]
        desk.answer(turn("t1", "+15550001", f"link {code.lower()}"))
        assert fake.sent[-1][1].startswith("Linked.")
        desk.answer(turn("t2", "+15550001", "status"))
        assert fake.chats[-1] == (agent_id, "status") and len(fake.agents) == 1
        assert call_api(base, "me")[1]["linked"] is True
        desk.answer(turn("t3", "+15550002", f"link {code}"))  # used up
        assert "isn't valid" in fake.sent[-1][1]
    finally:
        server.shutdown()


def test_worker_setup_state_and_explicit_claude_code(worker, monkeypatch):
    bot = chat.ChatBot(worker)
    bot.say("queued news")  # no desk: waits in the outbox
    state = json.loads(bot.reply("/setup.json"))
    assert state["claude"] == {"signed_in": False, "plan": "", "pending": False}
    assert state["telegram"]["configured"] is False
    assert bot.reply("/claude code some-code-123").startswith("queued news")  # outbox kept for a person
    assert "didn't work" in bot.reply("/claude code some-code-123")
    started = []
    monkeypatch.setattr(bot, "_task", lambda goal, solo: started.append(goal))
    bot.reply("/claude code another-code")
    assert started == []
