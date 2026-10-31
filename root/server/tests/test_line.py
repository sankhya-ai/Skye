"""The team line, end to end: the real relay (workerd via `wrangler dev`), clients, root answering, Claude Code's tools."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from conftest import requires_runtime, text, tool_call

from tura_code.line import config as line_config
from tura_code.line.client import LineClient
from tura_code.line.protocol import Invite, LineError

#: The relay and the `chamber` tool live in chamber, checked out next to root.
LINE_REPO = Path(os.environ.get("CHAMBER_DIR") or Path(__file__).resolve().parents[3] / "chamber")
RELAY_DIR = LINE_REPO / "relay"
WRANGLER = RELAY_DIR / "node_modules" / ".bin" / "wrangler"
requires_relay = pytest.mark.skipif(not WRANGLER.exists(), reason="chamber's relay not installed (npm install in chamber/relay)")
WIPE_SECONDS = 2


@pytest.fixture(scope="module")
def relay(tmp_path_factory):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    state = tmp_path_factory.mktemp("relay-state")
    process = subprocess.Popen(
        [str(WRANGLER), "dev", "--local", "--ip", "127.0.0.1", "--port", str(port), "--persist-to", str(state), "--var", f"IDLE_WIPE_SECONDS:{WIPE_SECONDS}"],
        cwd=RELAY_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.3)
    else:
        os.killpg(process.pid, signal.SIGTERM)
        pytest.fail("relay did not start")
    yield url
    os.killpg(process.pid, signal.SIGTERM)
    process.wait(10)


async def _join(invite: Invite, agent: str, **kw) -> tuple[LineClient, asyncio.Task]:
    client = LineClient(invite, agent, **kw)
    task = asyncio.create_task(client.run())
    await asyncio.wait_for(client.connected.wait(), 15)
    return client, task


async def _leave(*pairs) -> None:
    for client, task in pairs:
        await client.close()
        task.cancel()


# ----- sealing -------------------------------------------------------------------


def test_the_relay_can_route_but_not_read_alter_or_forge():
    invite = Invite.new("https://relay.example")
    assert Invite.parse(str(invite)) == invite and invite.line_id == Invite.parse(str(invite)).line_id
    body = invite.seal({"text": "hi"}, sender="a/x", to="@b", message_id="m1", reply_to=None)
    # A sealed body is ciphertext: the plaintext survives only through the key.
    # (The literal words can turn up by chance in random bytes; the whole
    # message being in there cannot.)
    assert json.dumps({"text": "hi"}) not in body
    assert invite.open(body, sender="a/x", to="@b", message_id="m1", reply_to=None) == {"text": "hi"}
    for changed in ({"sender": "c/x"}, {"to": "@c"}, {"message_id": "m2"}, {"reply_to": "m0"}):
        with pytest.raises(LineError):
            invite.open(body, **{"sender": "a/x", "to": "@b", "message_id": "m1", "reply_to": None, **changed})
    with pytest.raises(LineError):
        Invite.new("https://relay.example").open(body, sender="a/x", to="@b", message_id="m1", reply_to=None)
    with pytest.raises(LineError):
        Invite.parse("https://relay.example/no-secret")
    with pytest.raises(LineError):
        LineClient(invite, "Not An Agent")


# ----- the relay ---------------------------------------------------------------------


@requires_relay
def test_a_person_gets_messages_sent_while_away_and_root_is_preferred(relay, tmp_path):
    cursor = tmp_path / "cursor.json"

    async def scenario():
        invite = Invite.new(relay)
        nitin = await _join(invite, "nitin/claude-code", kind="claude-code", priority=5, answers=True)
        receipt = await nitin[0].send("@ashish", "Which port does the dev server use?")
        assert receipt["held"] and receipt["to"] is None

        mine = await _join(invite, "ashish/claude-code", kind="claude-code", priority=5, answers=True, state_path=cursor)
        held = await mine[0].next_message(5)
        assert held.text == "Which port does the dev server use?" and held.sender == "nitin/claude-code" and held.to == "@ashish"
        await mine[0].send(held.sender, "8787", reply_to=held.id)
        answer = await nitin[0].next_message(5)
        assert answer.text == "8787" and answer.reply_to == held.id

        # With root up and answering, a person's messages go to root first.
        root = await _join(invite, "ashish/root", kind="root", priority=10, answers=True)
        assert {a["agent"] for a in nitin[0].roster} >= {"ashish/root", "ashish/claude-code", "nitin/claude-code"}
        assert (await nitin[0].send("@ashish", "again?"))["delivered"] == ["ashish/root"]
        assert (await root[0].next_message(5)).text == "again?"
        assert await mine[0].next_message(0.5) is None

        # A reconnect resumes from the cursor: nothing is delivered twice.
        await _leave(mine)
        await nitin[0].send("ashish/claude-code", "while you were out")
        again = await _join(invite, "ashish/claude-code", kind="claude-code", state_path=cursor)
        assert (await again[0].next_message(5)).text == "while you were out"
        assert await again[0].next_message(0.5) is None
        await _leave(nitin, root, again)

    asyncio.run(scenario())


@requires_relay
def test_the_relay_rejects_a_token_for_another_line(relay):
    other = Invite.new(relay)
    response = httpx.get(f"{relay}/v1/lines/{Invite.new(relay).line_id}/roster", headers={"Authorization": f"Bearer {other.token}"})
    assert response.status_code == 401
    assert httpx.get(f"{relay}/v1/lines/{other.line_id}/roster", headers={"Authorization": f"Bearer {other.token}"}).json() == {"agents": []}


@requires_relay
def test_big_files_go_through_storage_and_are_wiped_when_the_session_ends(relay):
    big = os.urandom(200 * 1024)

    async def scenario():
        invite = Invite.new(relay)
        a = await _join(invite, "a/claude-code")
        b = await _join(invite, "b/claude-code")
        await a[0].send("b/claude-code", "the fixture", files=[{"name": "fixture.bin", "data": big}, {"name": "note.txt", "data": b"small"}])
        got = await b[0].next_message(15)
        assert [f["name"] for f in got.files] == ["fixture.bin", "note.txt"] and got.files[0]["data"] == big
        _, probe = await a[0]._upload("probe.bin", big)
        stored = httpx.get(invite.blob_url(probe), headers={"Authorization": f"Bearer {invite.token}"})
        assert stored.status_code == 200 and big not in stored.content  # sealed at rest
        # A big file for someone offline must outlive the session's end.
        await a[0].send("c/claude-code", "for later", files=[{"name": "later.bin", "data": big}])
        await _leave(a, b)
        await asyncio.sleep(WIPE_SECONDS + 3)
        gone = httpx.get(invite.blob_url(probe), headers={"Authorization": f"Bearer {invite.token}"})
        assert gone.status_code == 404 and "wiped" in gone.json()["error"]
        c = await _join(invite, "c/claude-code")
        later = await c[0].next_message(15)
        assert later.text == "for later" and later.files[0]["data"] == big
        await _leave(c)

    asyncio.run(scenario())


# ----- root on the line -----------------------------------------------------------


def _on_line(monkeypatch, tmp_path, invite: Invite, owner: str) -> Path:
    """Join the way chamber does: the shared ~/.chamber/config.json."""
    shared = tmp_path / "chamber-home"
    shared.mkdir()
    (shared / "config.json").write_text(json.dumps({"invite": str(invite), "owner": owner}))
    monkeypatch.setenv("CHAMBER_HOME", str(shared))
    return shared


@requires_runtime
@requires_relay
def test_root_answers_a_teammates_agent_read_only(relay, home, workspace, script, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from tura_code.app import create_app

    invite = Invite.new(relay)
    _on_line(monkeypatch, tmp_path, invite, "ashish")
    line_config.save_workspace(home, str(workspace))
    script([tool_call("r", "ReadFile", {"path": "README.md"}), text("The README is a one-line demo heading."), text("Still a demo.")])
    app = create_app(home)
    with TestClient(app) as client:
        assert client.post("/api/auth/bootstrap").status_code == 200

        async def ask():
            nitin = await _join(invite, "nitin/claude-code", kind="claude-code")
            for _ in range(50):
                if any(a["agent"] == "ashish/root" and a["answers"] for a in nitin[0].roster):
                    break
                await asyncio.sleep(0.2)
            receipt, reply = await nitin[0].ask("@ashish", "What is in your README?", files=[{"name": "q.txt", "data": b"context"}], timeout=60)
            _, follow_up = await nitin[0].ask("ashish/root", "Anything else?", timeout=60)
            await _leave(nitin)
            return receipt, reply, follow_up

        receipt, reply, follow_up = asyncio.run(ask())
        assert receipt["delivered"] == ["ashish/root"]
        assert reply.text == "The README is a one-line demo heading." and follow_up.text == "Still a demo."
        status = client.get("/api/line").json()
        assert status["connected"] and status["answers"] and status["agent"] == "ashish/root"
        sessions = [s for s in client.get("/api/sessions").json()["sessions"] if s["kind"] == "line"]
        assert len(sessions) == 1  # one conversation per asking agent
        assert sessions[0]["title"] == "Line · nitin/claude-code" and sessions[0]["permission_mode"] == "plan"


# ----- the line tool (Node) and root (Python) on one line ---------------------------------


@requires_relay
def test_the_line_tool_and_root_understand_each_other(relay, monkeypatch, tmp_path):
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    invite = Invite.new(relay)
    shared = _on_line(monkeypatch, tmp_path, invite, "ashish")
    big = os.urandom(100 * 1024)
    (tmp_path / "big.bin").write_bytes(big)
    receive = (
        f"import {{ LineClient }} from {json.dumps(str(LINE_REPO / 'lib' / 'client.mjs'))};"
        f"import {{ Invite }} from {json.dumps(str(LINE_REPO / 'lib' / 'protocol.mjs'))};"
        f"const invite = await Invite.parse({json.dumps(str(invite))}).ready();"
        "const c = new LineClient(invite, 'nitin/claude-code.api', { answers: true, onMessage: async (m) => {"
        "  await c.send(m.from, `node read: ${m.text} + ${m.files.map((f) => f.name + ':' + f.data.length).join(',')}`,"
        "    { replyTo: m.id, files: [{ name: 'back.bin', data: m.files[0].data }] }); c.close(); } });"
        "c.run(); await c.ready(); console.log('ready');"
    )

    async def scenario():
        node = await asyncio.create_subprocess_exec("node", "--input-type=module", "-e", receive, stdout=asyncio.subprocess.PIPE)
        assert (await asyncio.wait_for(node.stdout.readline(), 20)).strip() == b"ready"
        root = await _join(invite, "ashish/root", kind="root")
        receipt, reply = await root[0].ask("@nitin", "hello from python", files=[{"name": "big.bin", "data": big}], timeout=20)
        assert receipt["delivered"] == ["nitin/claude-code.api"]
        assert reply.text == f"node read: hello from python + big.bin:{len(big)}" and reply.files[0]["data"] == big
        await asyncio.wait_for(node.wait(), 15)

        # And the other way: the `line send` command, to root.
        sent = await asyncio.create_subprocess_exec(
            "node", str(LINE_REPO / "bin" / "chamber.mjs"), "send", "ashish/root", "from", "the", "terminal", "--file", str(tmp_path / "big.bin"),
            env={**os.environ, "CHAMBER_HOME": str(shared)}, stdout=asyncio.subprocess.PIPE,
        )
        assert b"Delivered to ashish/root" in (await sent.communicate())[0]
        got = await root[0].next_message(10)
        assert got.sender == "ashish/terminal" and got.text == "from the terminal" and got.files[0]["data"] == big
        await _leave(root)

    asyncio.run(scenario())
