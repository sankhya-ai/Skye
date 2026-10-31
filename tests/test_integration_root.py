"""The real slice, no paid model: Skye's dispatcher drives the vendored Root server (its actual
HTTP API, its actual job machinery) with Root's scripted model standing in for the provider.

What this proves: contract -> policy -> catalogue -> adapter -> a REAL Root job that REALLY writes
the file through Root's tool path -> the settled job result back in Skye -> the policy's test gate
-> lifecycle events. What it does not prove: any real model's behavior (scripted by design) and
isolation (no jail in this test; that is Root's own test matrix's job).
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT_SERVER = Path(__file__).resolve().parents[1] / "root" / "server"
if str(ROOT_SERVER) not in sys.path:
    sys.path.insert(0, str(ROOT_SERVER))

from tura_code import launcher  # noqa: E402


def _runtime_available() -> bool:
    try:
        launcher.find_runtime_binary()
        return True
    except launcher.RuntimeNotFound:
        return False


requires_runtime = pytest.mark.skipif(not _runtime_available(), reason="kimi-agent runtime binary not found")


def tool_call(call_id: str, name: str, arguments: dict) -> str:
    return "tool_call: " + json.dumps({"id": call_id, "name": name, "arguments": json.dumps(arguments)})


def text(value: str) -> str:
    return "text: " + value


@pytest.fixture
def root_server(tmp_path, monkeypatch):
    """The vendored Root, served over a real socket, fully scripted (no provider, no cost)."""
    home = tmp_path / "root-home"
    monkeypatch.setenv("TURA_CODE_HOME", str(home))
    monkeypatch.setenv("TURA_CODE_OFFLINE", "1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("TURA_CODE_TOKEN", raising=False)
    script = tmp_path / "script.json"
    script.write_text(json.dumps([
        tool_call("w", "WriteFile", {"path": "hello.py", "content": "print('hello from skye')\n"}),
        text('Wrote it. {"summary": "hello.py written"}'),  # the echo DSL is line-based: one line, inline JSON
    ]))
    monkeypatch.setenv(launcher.SCRIPTED_ECHO_ENV, str(script))

    import uvicorn  # noqa: PLC0415
    from tura_code.app import create_app  # noqa: PLC0415

    app = create_app(home)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError("vendored Root did not start")
    url = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("SKYE_ROOT_URL", url)
    monkeypatch.setenv("SKYE_ROOT_HOME", str(home))
    yield url, home
    server.should_exit = True
    thread.join(15)


@requires_runtime
def test_a_contract_runs_a_real_root_job_through_its_gates(root_server, tmp_path):
    from skye.dispatcher import dispatch
    from skye.events import FileBridge
    from skye.policy import Policy

    url, home = root_server
    repo = tmp_path / "ws"
    repo.mkdir()
    policy = Policy({"allow_unattended": [{"adapter": "root", "role": "developer"}],
                     "test_command": "python3 -c \"import pathlib; assert 'hello from skye' in pathlib.Path('hello.py').read_text()\""})
    log = tmp_path / "events.jsonl"
    outcome = dispatch(
        {"goal": "write hello.py printing hello from skye", "repo": str(repo), "role": "developer",
         "adapter": "root", "gates": ["tests"], "contract_keys": ["summary"], "budget": {"max_steps": 8}},
        policy, bridge=FileBridge(log),
        runner=None)  # no test double: the real RootAdapter
    assert outcome.status == "done", outcome.reason
    # The file exists in the repo because Root's runtime really wrote it.
    assert "hello from skye" in (repo / "hello.py").read_text()
    # The worker output is the settled Root job, not a "job started" receipt.
    result = json.loads(outcome.worker_output)
    assert result["status"] == "finished" and result["contract"]["ok"]
    job_record = json.loads((home / "jobs" / f"{result['job']}.json").read_text())
    assert job_record["status"] == "finished" and job_record["role"] == "developer"
    # The lifecycle events landed: accepted, the passing tests gate, done.
    kinds = [json.loads(line)["kind"] for line in log.read_text().splitlines()]
    assert kinds == ["accepted", "gate", "done"], kinds


@requires_runtime
def test_a_worker_that_breaks_its_contract_fails_the_dispatch(root_server, tmp_path, monkeypatch):
    from skye.dispatcher import dispatch
    from skye.events import FileBridge
    from skye.policy import Policy

    url, _home = root_server
    # Re-script: the model answers without the required JSON block, so Root's contract check fails.
    script = tmp_path / "script2.json"
    script.write_text(json.dumps([text("no json here")]))
    monkeypatch.setenv(launcher.SCRIPTED_ECHO_ENV, str(script))
    repo = tmp_path / "ws"
    repo.mkdir()
    policy = Policy({"allow_unattended": [{"adapter": "root", "role": "explorer"}],
                     "test_command": "true"})
    outcome = dispatch(
        {"goal": "summarise", "repo": str(repo), "role": "explorer", "adapter": "root",
         "gates": ["tests"], "contract_keys": ["summary"]},
        policy, bridge=FileBridge(tmp_path / "events.jsonl"))
    assert outcome.status == "failed" and "contract" in outcome.worker_output
    # The tests gate never ran: the worker failing ends the dispatch before gates.
    assert outcome.gates == []
