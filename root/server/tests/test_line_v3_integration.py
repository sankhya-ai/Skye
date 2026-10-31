"""Identity v3 with the REAL relay: the Python consumer as an actual v3 member.

Enrolled with a v3 certificate (signed here, chamber-compatible), the Python client receives an
admin-signed epoch distribution off the bootstrap channel, reads and writes under epoch keys
(the relay verifies its envelope signatures), survives a restart from the 0600 store, follows a
rotation, and is REFUSED for a stale-epoch send. Complements the byte-level fixture tests in
test_line_v3_compat.py: this is live enrollment/distribution/send/receive/rotation integration.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from tura_code.line import protocol as line_protocol
from tura_code.line.client import LineClient
from tura_code.line.protocol import EpochStore, Invite, LineError

LINE_REPO = Path(os.environ.get("CHAMBER_DIR") or Path(__file__).resolve().parents[3] / "chamber")
WRANGLER = LINE_REPO / "relay" / "node_modules" / ".bin" / "wrangler"
pytestmark = [
    pytest.mark.skipif(not WRANGLER.exists(), reason="chamber's relay not installed (npm install in chamber/relay)"),
    pytest.mark.skipif(os.environ.get("CHAMBER_DIR") is None and not (Path(__file__).resolve().parents[3] / "chamber").exists(), reason="chamber checkout not next to root"),
]

ADMIN_PUB, ADMIN_PRIV = line_protocol.new_admin_key()
E1 = os.urandom(32)
E2 = os.urandom(32)


@pytest.fixture(scope="module")
def relay_v3(tmp_path_factory):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    state = tmp_path_factory.mktemp("relay-v3-state")
    process = subprocess.Popen(
        [str(WRANGLER), "dev", "--local", "--ip", "127.0.0.1", "--port", str(port), "--persist-to", str(state), "--var", f"ADMIN_KEY:{ADMIN_PUB}"],
        cwd=LINE_REPO / "relay",
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


@pytest.fixture()
def member(tmp_path, monkeypatch):
    """A v3 member 'root': chamber-shaped key file (0600) in a fresh CHAMBER_HOME, v3 invite."""
    home = tmp_path / "chamber-home"
    monkeypatch.setenv("CHAMBER_HOME", str(home))
    base = Invite.new("")  # placeholder secret shape; replaced below with the real relay url
    return {"home": home, "base_secret": base.secret}


def _setup_member(relay: str, home: Path) -> tuple[Invite, dict]:
    invite = Invite.new(relay)
    keys = line_protocol.new_member_keypair()
    keydir = home / "keys"
    keydir.mkdir(parents=True)
    path = keydir / f"{invite.line_id}-root.json"
    path.write_text(json.dumps({"v": 2, "line": invite.line_id, "owner": "root", **keys}))
    path.chmod(0o600)
    cert = line_protocol.issue_certificate(ADMIN_PRIV, line=invite.line_id, owner="root", pubkey=keys["public"], enc=keys["encPublic"])
    return Invite(relay, invite.secret, cert, ADMIN_PUB), keys


async def _join(invite: Invite, agent: str) -> tuple[LineClient, asyncio.Task]:
    client = LineClient(invite, agent)
    task = asyncio.create_task(client.run())
    await asyncio.wait_for(client.connected.wait(), 15)
    return client, task


async def _distribute(sender: LineClient, invite: Invite, epoch: int, key: bytes, recipients: dict[str, str]) -> None:
    """The admin's distribution, sent by its member client over the epoch-0 bootstrap channel."""
    wraps = {owner: line_protocol.wrap_epoch_key(enc, line=invite.line_id, epoch=epoch, owner=owner, key=key) for owner, enc in recipients.items()}
    payload = {
        "type": "chamber-epoch",
        "text": "",
        "thread": "",
        "line": invite.line_id,
        "epoch": epoch,
        "wraps": wraps,
        "sig": line_protocol.sign_distribution(ADMIN_PRIV, line=invite.line_id, epoch=epoch, wraps=wraps),
    }
    message_id = os.urandom(16).hex()
    body = invite.seal(payload, sender=sender.agent, to="*", message_id=message_id, reply_to=None)
    ts = int(time.time() * 1000)
    frame = {"t": "send", "id": message_id, "to": "*", "body": body, "blobs": [], "mentions": [], "ts": ts, "epoch": 0}
    frame["sig"] = line_protocol.sign_envelope(
        line_protocol._unb64(sender._member_key["private"]),
        line=invite.line_id, from_=sender.agent, to="*", message_id=message_id, reply_to="", ts=ts, hash_=line_protocol.body_hash(body), epoch=0,
    )
    future = asyncio.get_running_loop().create_future()
    sender._acks[message_id] = future
    await sender._ws.send(json.dumps(frame))
    await asyncio.wait_for(future, 15)
    sender._take_distribution(payload)  # the relay never echoes to the sender: take your own wrap
    order = line_protocol.signed_order(ADMIN_PRIV, {"line": invite.line_id, "op": "rotate", "epoch": epoch})
    response = httpx.post(f"{invite.relay}/v1/lines/{invite.line_id}/admin", json={"order": order}, headers=invite_headers(invite))
    assert response.status_code == 200, response.text


def invite_headers(invite: Invite) -> dict[str, str]:
    return {"authorization": f"Bearer {invite.token}", "content-type": "application/json"}


async def _wait_epoch(client: LineClient, epoch: int) -> None:
    for _ in range(100):
        if client._epochs.current >= epoch:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"epoch {epoch} never reached {client.agent}")


@pytest.mark.asyncio
async def test_v3_consumer_against_the_real_relay(relay_v3, tmp_path, monkeypatch) -> None:
    home = tmp_path / "chamber-home"
    monkeypatch.setenv("CHAMBER_HOME", str(home))
    invite, keys = _setup_member(relay_v3, home)

    admin_client, admin_task = await _join(invite, "root/admin")
    consumer, consumer_task = await _join(invite, "root/agent")
    assert consumer.line_version == 3, "the first v3 admit fixed the line at v3"

    # Distribution over the bootstrap channel: the consumer unwraps its wrap and saves it (0600).
    await _distribute(admin_client, invite, 1, E1, {"root": keys["encPublic"]})
    await _wait_epoch(consumer, 1)
    assert consumer._epochs.key_for(1) == E1, "the consumer unwrapped the admin's epoch key"
    store_file = home / "keys" / f"{invite.line_id}-root-epochs.json"
    assert store_file.stat().st_mode & 0o777 == 0o600

    # Read and write under the epoch key: the relay verifies the Python client's v3 signature.
    await admin_client.send("*", "admin under epoch one")
    message = await consumer.next_message(timeout=10)
    assert message is not None and message.text == "admin under epoch one"
    await consumer.send("*", "python consumer under epoch one")
    back = await admin_client.next_message(timeout=10)
    assert back is not None and back.text == "python consumer under epoch one", "relay accepted the Python client's signed epoch frame"

    # Restart: a fresh client on the same machine sends from the 0600 store, no new distribution.
    await consumer.close()
    consumer_task.cancel()
    restarted, restarted_task = await _join(invite, "root/agent")
    assert restarted._epochs.current == 1 and restarted._epochs.key_for(1) == E1
    await restarted.send("*", "after restart")
    back = await admin_client.next_message(timeout=10)
    assert back is not None and back.text == "after restart"

    # Rotation: epoch 2 distributed; the consumer follows and writes under it.
    await _distribute(admin_client, invite, 2, E2, {"root": keys["encPublic"]})
    await _wait_epoch(restarted, 2)
    await restarted.send("*", "under epoch two")
    back = await admin_client.next_message(timeout=10)
    assert back is not None and back.text == "under epoch two"

    # Fail closed: a stale-epoch send (as a just-woken offline client would attempt) is refused.
    stale_id = "c" * 32
    stale_body = invite.seal({"text": "stale", "files": [], "thread": stale_id, "title": ""}, sender=restarted.agent, to="*", message_id=stale_id, reply_to=None, epoch=(1, E1))
    ts = int(time.time() * 1000)
    frame = {"t": "send", "id": stale_id, "to": "*", "body": stale_body, "blobs": [], "mentions": [], "ts": ts, "epoch": 1}
    frame["sig"] = line_protocol.sign_envelope(
        line_protocol._unb64(keys["private"]),
        line=invite.line_id, from_=restarted.agent, to="*", message_id=stale_id, reply_to="", ts=ts, hash_=line_protocol.body_hash(stale_body), epoch=1,
    )
    future = asyncio.get_running_loop().create_future()
    restarted._acks[stale_id] = future
    await restarted._ws.send(json.dumps(frame))
    with pytest.raises(LineError, match="stale epoch 1"):
        await asyncio.wait_for(future, 15)

    # Nothing from before the rotation leaks forward: the epoch-2 store key differs and old
    # ciphertext semantics stay as documented (the member's own held keys open their own past).
    assert EpochStore(store_file).key_for(1) == E1, "held epochs stay held - rotation is not retroactive"
    assert restarted._epochs.key_for(2) == E2

    await restarted.close()
    restarted_task.cancel()
    await admin_client.close()
    admin_task.cancel()
