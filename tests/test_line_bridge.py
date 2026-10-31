"""The Chamber bridge: dispatcher lifecycle events really arrive on a Chamber line.

Runs the vendored Chamber relay exactly the way Root's own line tests do (workerd via
`wrangler dev`); skipped when no chamber checkout with its relay dependencies is available.
The observer is a second line member: events are sealed line messages it receives, not log lines.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKYE_ROOT = Path(__file__).resolve().parents[1]
ROOT_SERVER = SKYE_ROOT / "root" / "server"
if str(ROOT_SERVER) not in sys.path:
    sys.path.insert(0, str(ROOT_SERVER))

from tura_code.line.client import LineClient  # noqa: E402
from tura_code.line.protocol import Invite  # noqa: E402

#: A chamber checkout with the relay's npm dependencies installed: CHAMBER_DIR, a sibling
#: checkout, then the vendored copy (a fresh clone has no node_modules and is skipped).
def _relay_dir() -> Path | None:
    candidates = [Path(os.environ["CHAMBER_DIR"]) if os.environ.get("CHAMBER_DIR") else None,
                  SKYE_ROOT.parent / "chamber", SKYE_ROOT / "chamber"]
    for candidate in candidates:
        if candidate and (candidate / "relay" / "node_modules" / ".bin" / "wrangler").exists():
            return candidate / "relay"
    return None


RELAY_DIR = _relay_dir()
requires_relay = pytest.mark.skipif(RELAY_DIR is None,
                                    reason="chamber relay dependencies not installed (npm install in chamber/relay)")
WIPE_SECONDS = 2


@pytest.fixture(scope="module")
def relay(tmp_path_factory):
    import httpx  # noqa: PLC0415

    wrangler = RELAY_DIR / "node_modules" / ".bin" / "wrangler"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    state = tmp_path_factory.mktemp("relay-state")
    process = subprocess.Popen(
        [str(wrangler), "dev", "--local", "--ip", "127.0.0.1", "--port", str(port),
         "--persist-to", str(state), "--var", f"IDLE_WIPE_SECONDS:{WIPE_SECONDS}"],
        cwd=RELAY_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
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


async def _join(invite: Invite, agent: str, **kw):
    client = LineClient(invite, agent, **kw)
    task = asyncio.create_task(client.run())
    await asyncio.wait_for(client.connected.wait(), 15)
    return client, task


@requires_relay
def test_dispatch_events_arrive_on_the_line(relay, tmp_path):
    """A dispatch (scripted runner - this test is about the bridge, not the worker) publishes
    accepted/gate/done to the line; the owner member receives all three, sealed."""

    async def scenario():
        from skye.dispatcher import dispatch
        from skye.events import FanoutBridge, FileBridge, LineBridge
        from skye.policy import Policy

        invite = Invite.new(relay)
        observer = await _join(invite, "ashish/claude-code")
        bridge_pair = [None]

        def run_dispatch():
            bridge = LineBridge(str(invite), agent="skye/dispatcher")
            bridge_pair[0] = bridge
            outcome = dispatch(
                {"goal": "say hi", "repo": str(tmp_path), "role": "explorer", "adapter": "root",
                 "gates": ["tests"]},
                Policy({"allow_unattended": [{"adapter": "root", "role": "explorer"}],
                        "test_command": "true"}),
                runner=lambda spec: type("R", (), {"ok": True, "output": "hi", "returncode": 0})(),
                bridge=FanoutBridge(FileBridge(tmp_path / "events.jsonl"), bridge))
            return outcome

        loop = asyncio.get_running_loop()
        outcome = await loop.run_in_executor(None, run_dispatch)
        assert outcome.status == "done"
        received = [await asyncio.wait_for(observer[0].next_message(10), 20) for _ in range(3)]
        kinds = [m.text.split("]")[0].lstrip("[skye:") for m in received]
        assert kinds == ["accepted", "gate", "done"], kinds
        assert all("say hi" in m.text for m in received)
        await observer[0].close()
        observer[1].cancel()
        await loop.run_in_executor(None, bridge_pair[0].close)

    asyncio.run(scenario())


def test_the_file_sink_is_the_honest_default(tmp_path):
    """No line configured: events land in the local JSONL log, nothing is silently dropped."""
    from skye.dispatcher import dispatch
    from skye.events import FileBridge
    from skye.policy import Policy

    log = tmp_path / "events.jsonl"
    outcome = dispatch({"goal": "g", "repo": str(tmp_path), "role": "explorer", "adapter": "root", "gates": []},
                       Policy({"allow_unattended": [{"adapter": "root", "role": "explorer"}]}),
                       runner=lambda spec: type("R", (), {"ok": True, "output": "o", "returncode": 0})(),
                       bridge=FileBridge(log))
    assert outcome.status == "done"
    assert [json.loads(line)["kind"] for line in log.read_text().splitlines()] == ["accepted", "done"]


def test_a_dead_sink_never_loses_the_event(tmp_path, monkeypatch):
    from skye.dispatcher import dispatch
    from skye.events import FanoutBridge, FileBridge
    from skye.policy import Policy

    class Dead:
        def publish(self, event):
            raise ConnectionError("line down")

        def close(self):
            pass

    log = tmp_path / "events.jsonl"
    monkeypatch.chdir(tmp_path)
    outcome = dispatch({"goal": "g", "repo": str(tmp_path), "role": "explorer", "adapter": "root", "gates": []},
                       Policy({"allow_unattended": [{"adapter": "root", "role": "explorer"}]}),
                       runner=lambda spec: type("R", (), {"ok": True, "output": "o", "returncode": 0})(),
                       bridge=FanoutBridge(FileBridge(log), Dead()))
    assert outcome.status == "done"  # the dispatch is not held hostage by a dead sink
    fallback = Path("events.jsonl").read_text()
    assert "bridge-error" in fallback and "line down" in fallback
