from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tura_code import launcher


def runtime_available() -> bool:
    try:
        launcher.find_runtime_binary()
        return True
    except launcher.RuntimeNotFound:
        return False


requires_runtime = pytest.mark.skipif(not runtime_available(), reason="kimi-agent runtime binary not built")


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "home"
    monkeypatch.setenv("TURA_CODE_HOME", str(root))
    monkeypatch.setenv("TURA_CODE_OFFLINE", "1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("TURA_CODE_TOKEN", raising=False)
    return root


@pytest.fixture
def workspace(tmp_path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "README.md").write_text("# demo\n")
    return ws


@pytest.fixture
def script(tmp_path, monkeypatch):
    """Install scripted model replies: a list of echo-DSL strings, one per model call."""

    def install(replies: list[str]) -> Path:
        path = tmp_path / f"script-{time.monotonic_ns()}.json"
        path.write_text(json.dumps(replies))
        monkeypatch.setenv(launcher.SCRIPTED_ECHO_ENV, str(path))
        return path

    return install


@pytest.fixture
def client(home):
    from tura_code.app import create_app

    app = create_app(home)
    with TestClient(app) as test_client:
        assert test_client.post("/api/auth/bootstrap").status_code == 200
        yield test_client


def tool_call(call_id: str, name: str, arguments: dict) -> str:
    return "tool_call: " + json.dumps({"id": call_id, "name": name, "arguments": json.dumps(arguments)})


def text(value: str) -> str:
    return "text: " + value


def run_turn(ws, prompt: str, *, approve: str | None = "approve", timeout: float = 30.0) -> list[dict]:
    """Send a prompt and collect events until the turn finishes."""
    ws.send_json({"type": "prompt", "text": prompt})
    events: list[dict] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        message = ws.receive_json()
        if message["type"] == "error":
            raise AssertionError(message["message"])
        if message["type"] != "event":
            continue
        event = message["event"]
        events.append(event)
        if event["type"] == "ApprovalRequest" and approve:
            ws.send_json({"type": "approval", "request_id": event["payload"]["id"], "response": approve})
        if event["type"] == "TurnFinished":
            return events
    raise AssertionError("turn did not finish")


def of_type(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e["type"] == kind]
