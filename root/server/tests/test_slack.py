"""root in Slack: called by an @mention or a DM, it reads the thread around the call and answers in it."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest
from conftest import requires_runtime, text

from tura_code.slack import chat
from tura_code.slack.api import SlackAPI, SocketMode

BOT = "UROOT"


class FakeSlack:
    """Slack's Web API for one workspace: a thread, two people, and what root posts."""

    def __init__(self) -> None:
        self.posted: list[dict] = []
        self.calls: list[str] = []
        self.reactions: list[tuple[str, str]] = []
        self.ws_url = ""
        self.thread = [
            {"user": "UASHISH", "ts": "100.0", "text": "deploys keep failing on friday"},
            {"user": "UNITIN", "ts": "101.0", "text": "it's the cache key, see <https://ci.example/run/7|run 7>"},
            {"subtype": "channel_join", "user": "UNEW", "ts": "101.5", "text": "joined"},
            {"user": "UNITIN", "ts": "102.0", "text": f"<@{BOT}> why does the cache key change?"},
            {"user": "UASHISH", "ts": "103.0", "text": "later message, not context"},
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.calls.append(method)
        if method == "auth.test":
            return httpx.Response(200, json={"ok": True, "user_id": BOT, "user": "root", "team": "Tura"})
        if method == "apps.connections.open":
            return httpx.Response(200, json={"ok": True, "url": self.ws_url})
        if method == "users.info":
            names = {"UASHISH": "Ashish", "UNITIN": "Nitin"}
            return httpx.Response(200, json={"ok": True, "user": {"profile": {"display_name": names.get(form["user"], "")}, "name": form["user"]}})
        if method == "conversations.replies":
            return httpx.Response(200, json={"ok": True, "messages": self.thread})
        if method == "conversations.history":
            newest_first = list(reversed([m for m in self.thread if float(m["ts"]) < float(form["latest"])]))
            return httpx.Response(200, json={"ok": True, "messages": newest_first})
        if method == "chat.postMessage":
            self.posted.append(form)
            return httpx.Response(200, json={"ok": True, "ts": "200.0"})
        if method.startswith("reactions."):
            self.reactions.append((method, form["name"]))
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"ok": False, "error": "unknown_method"})


@pytest.fixture
def slack(client):
    fake = FakeSlack()
    service = client.app.state.root.server.slack
    service.api = SlackAPI("xoxb-test", "xapp-test", transport=httpx.MockTransport(fake.handler))
    service.bot_user, service.bot_name, service.answers = BOT, "root", True
    yield service, fake
    service.api = None


def _mention(ts: str, words: str, *, thread: str = "100.0", user: str = "UNITIN") -> dict:
    return {"type": "app_mention", "event_id": f"Ev{ts}", "channel": "C1", "user": user, "ts": ts, "thread_ts": thread, "text": f"<@{BOT}> {words}"}


def _prompt_of(client, session_id: str) -> str:
    events = client.get(f"/api/sessions/{session_id}/events").json()["events"]
    return next(e["payload"]["text"] for e in events if e["type"] == "UserPrompt")


@requires_runtime
def test_a_mention_reads_its_thread_and_answers_in_it(client, script, slack):
    service, fake = slack
    script([text("The **cache key** includes the commit date; see [the docs](https://docs.example/cache).")])
    client.portal.call(service.handle, _mention("102.0", "why does the cache key change?"))
    client.portal.call(_settle, service)

    assert len(fake.posted) == 1
    post = fake.posted[0]
    assert post["channel"] == "C1" and post["thread_ts"] == "100.0", "the answer goes in the thread"
    assert post["text"] == "The *cache key* includes the commit date; see <https://docs.example/cache|the docs>."
    assert fake.reactions == [("reactions.add", "eyes"), ("reactions.remove", "eyes")]

    sessions = [s for s in client.get("/api/sessions").json()["sessions"] if s.get("kind") == "slack"]
    assert len(sessions) == 1 and sessions[0]["permission_mode"] == "plan"
    prompt = _prompt_of(client, sessions[0]["id"])
    # The thread before the call, with names and links people can read; nothing after it, no join notices.
    assert "Ashish: deploys keep failing on friday" in prompt
    assert "Nitin: it's the cache key, see run 7 (https://ci.example/run/7)" in prompt
    assert "later message" not in prompt and "joined" not in prompt
    assert "Nitin called you in a Slack thread" in prompt and "why does the cache key change?" in prompt

    # A follow-up in the same thread continues the same read-only session.
    script([text("Pin the date to the merge base.")])
    client.portal.call(service.handle, _mention("104.0", "how do we fix it?"))
    client.portal.call(_settle, service)
    again = [s for s in client.get("/api/sessions").json()["sessions"] if s.get("kind") == "slack"]
    assert [s["id"] for s in again] == [sessions[0]["id"]] and fake.posted[-1]["text"] == "Pin the date to the merge base."


@requires_runtime
def test_root_answers_only_when_called_and_each_event_once(client, script, slack):
    service, fake = slack
    script([text("Hi! Ask me anything.")])
    plain = {"type": "message", "event_id": "Ev1", "channel": "C1", "channel_type": "channel", "user": "UNITIN", "ts": "300.0", "text": "root is great"}
    own = {"type": "message", "event_id": "Ev2", "channel": "D1", "channel_type": "im", "user": BOT, "ts": "301.0", "text": "my own reply"}
    for event in (plain, own):
        client.portal.call(service.handle, event)
    assert fake.posted == [], "a message that does not call root is not read, let alone answered"

    dm = {"type": "message", "event_id": "Ev3", "channel": "D1", "channel_type": "im", "user": "UASHISH", "ts": "302.0", "text": "hello"}
    client.portal.call(service.handle, dm)
    client.portal.call(service.handle, dm)  # Slack delivered it twice
    client.portal.call(_settle, service)
    assert [p["text"] for p in fake.posted] == ["Hi! Ask me anything."]
    assert fake.posted[0]["thread_ts"] == "302.0"
    assert "conversations.history" in fake.calls


async def _settle(service) -> None:
    import asyncio

    while service._work:
        await asyncio.gather(*list(service._work), return_exceptions=True)


def test_slack_markup_reads_as_text_and_markdown_reads_as_slack():
    names = {"U1": "Nitin"}
    assert chat.plain("<@U1> see <#C9|eng> and <https://x.io|this> &amp; <!here>", names) == "@Nitin see #eng and this (https://x.io) & @here"
    assert chat.mrkdwn("## Fix\n**bold** ~~old~~ a < b & c\n```python\nx = 1 < 2\n```") == "*Fix*\n*bold* ~old~ a &lt; b &amp; c\n```\nx = 1 < 2\n```"
    long = ("para one. " * 300).strip() + "\n\n" + ("para two. " * 300).strip()
    pieces = chat.chunks(long, limit=3_500)
    assert len(pieces) == 2 and all(len(p) <= 3_500 for p in pieces)
    assert chat.is_stop({"text": f"<@{BOT}> stop"}) and chat.is_stop({"text": "cancel!"}) and not chat.is_stop({"text": "stop the deploy job"})
    assert not chat.called({"type": "app_mention", "bot_id": "B1", "user": "U2"}, BOT), "bots never summon root"


def test_socket_mode_acks_every_envelope_and_reconnects_when_told():
    import asyncio

    seen: list[dict] = []

    async def on_event(event: dict) -> None:
        seen.append(event)

    class Socket:
        sent: list[str] = []

        async def send(self, data: str) -> None:
            self.sent.append(data)

    async def run() -> tuple[bool, bool]:
        mode = SocketMode(SlackAPI("xoxb", "xapp"), on_event)
        mode._ws = Socket()
        hello = await mode._envelope({"type": "hello"})
        await mode._envelope({"type": "events_api", "envelope_id": "E1", "payload": {"event_id": "Ev9", "event": {"type": "app_mention", "text": "hi"}}})
        keep = await mode._envelope({"type": "disconnect", "reason": "refresh_requested"})
        await mode.api.close()
        return hello and mode.connected.is_set(), keep

    connected, keep = asyncio.run(run())
    assert connected and keep is False
    assert json.loads(Socket.sent[0]) == {"envelope_id": "E1"}
    assert seen == [{"type": "app_mention", "text": "hi", "event_id": "Ev9"}]


def test_connecting_checks_the_token_kinds_and_the_manifest_is_complete(client):
    bad = client.put("/api/slack", json={"bot_token": "xapp-wrong", "app_token": "xoxb-wrong"})
    assert bad.status_code == 400 and "xoxb" in bad.json()["detail"]
    manifest = client.get("/api/slack/manifest").json()
    assert manifest["settings"]["socket_mode_enabled"] is True
    assert set(manifest["settings"]["event_subscriptions"]["bot_events"]) == {"app_mention", "message.im"}
    assert {"app_mentions:read", "chat:write", "channels:history", "im:history"} <= set(manifest["oauth_config"]["scopes"]["bot"])
    assert client.get("/api/slack").json()["configured"] is False


def _wait(client, predicate, timeout: float = 30.0) -> None:
    import asyncio
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        client.portal.call(asyncio.sleep, 0.05)
    raise AssertionError("timed out waiting for the Slack socket")


@requires_runtime
def test_connecting_starts_the_socket_and_answers_an_event_end_to_end(client, script, monkeypatch):
    """The whole path: PUT the tokens, the connection is stored, Socket Mode opens, an event arrives, a turn runs, the reply goes back."""
    import asyncio

    from websockets.asyncio.server import serve

    fake = FakeSlack()
    queue = client.portal.call(asyncio.Queue)
    acks: list[dict] = []

    async def handler(ws) -> None:
        await ws.send(json.dumps({"type": "hello"}))
        while True:
            await ws.send(json.dumps(await queue.get()))
            acks.append(json.loads(await ws.recv()))

    ws_server = client.portal.call(serve, handler, "127.0.0.1", 0)
    fake.ws_url = f"ws://127.0.0.1:{ws_server.sockets[0].getsockname()[1]}"

    def patched(bot_token: str, app_token: str = "") -> SlackAPI:
        return SlackAPI(bot_token, app_token, transport=httpx.MockTransport(fake.handler))

    monkeypatch.setattr("tura_code.slack.service.SlackAPI", patched)
    service = client.app.state.root.server.slack
    script([text("Hello from the socket.")])
    try:
        connected = client.put("/api/slack", json={"bot_token": "xoxb-live", "app_token": "xapp-live"})
        assert connected.status_code == 200, connected.text
        assert connected.json() == {"configured": True, "team": "Tura", "bot": "root", "connected": False, "answers": True, "problem": ""}

        _wait(client, lambda: service.status()["connected"])
        assert "apps.connections.open" in fake.calls
        client.portal.call(
            queue.put,
            {
                "type": "events_api",
                "envelope_id": "E1",
                "payload": {"event_id": "Ev1", "event": {"type": "app_mention", "channel": "C1", "user": "UNITIN", "ts": "500.0", "text": f"<@{BOT}> hi"}},
            },
        )
        _wait(client, lambda: bool(fake.posted))
        assert acks == [{"envelope_id": "E1"}], "acknowledged before the turn ran"
        assert fake.posted[0]["channel"] == "C1" and fake.posted[0]["thread_ts"] == "500.0"
        assert fake.posted[0]["text"] == "Hello from the socket."
        assert service.status()["bot"] == "root"
    finally:
        client.portal.call(ws_server.close)


@requires_runtime
def test_a_plugin_switched_off_makes_root_silent_in_slack(client, script, slack):
    service, fake = slack
    client.put("/api/plugins/slack", json={"enabled": False})
    script([text("should not be said")])
    client.portal.call(service.handle, _mention("102.0", "are you there?"))
    client.portal.call(_settle, service)
    assert fake.posted == [] and not any(s.get("kind") == "slack" for s in client.get("/api/sessions").json()["sessions"])

    client.put("/api/plugins/slack", json={"enabled": True})
    script([text("I am back.")])
    client.portal.call(service.handle, _mention("104.0", "now?"))
    client.portal.call(_settle, service)
    assert [p["text"] for p in fake.posted] == ["I am back."]
