"""The owner's terminal tab: a real shell in the session's folder, local installs only."""

from __future__ import annotations

import os
import time

import pytest
from starlette.websockets import WebSocketDisconnect


def _read_until(ws, marker: bytes, limit: int = 400) -> bytes:
    seen = b""
    for _ in range(limit):
        seen += ws.receive_bytes()
        if marker in seen:
            return seen
    raise AssertionError(f"{marker!r} never came; got {seen[-400:]!r}")


posix = pytest.mark.skipif(os.name == "nt", reason="a POSIX shell; test_windows.py drives PowerShell's")


@posix
def test_the_terminal_is_a_shell_in_the_session_folder_without_roots_keys(client, workspace, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-must-not-leak")
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{session_id}/terminal") as ws:
        ws.send_json({"type": "resize", "cols": 120, "rows": 40})
        ws.send_json({"type": "input", "data": "echo \"at:$(pwd -P):$(tput cols)\"; echo \"key:${OPENROUTER_API_KEY:-none}\"; echo done-$((40+2))\n"})
        out = _read_until(ws, b"done-42")
    assert f"at:{os.path.realpath(workspace)}:120".encode() in out
    assert b"key:none" in out and b"sk-or-must-not-leak" not in out


@posix
def test_closing_the_tab_ends_what_the_shell_started(client, workspace, tmp_path):
    marker = tmp_path / "pid"
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{session_id}/terminal") as ws:
        ws.send_json({"type": "input", "data": f"sleep 300 & echo $! > {marker}; echo started-$((1+1))\n"})
        _read_until(ws, b"started-2")
    pid = int(marker.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    raise AssertionError("the shell's background job outlived the tab")


def test_hosted_mode_has_no_terminal(home):
    from fastapi.testclient import TestClient

    from tura_code.app import create_app
    from tura_code.users import UserStore

    token = UserStore(home / "users.json").add("alice")
    with TestClient(create_app(home, mode="hosted")) as client:
        headers = {"authorization": f"Bearer {token}"}
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect("/api/sessions/anything/terminal", headers=headers) as ws:
                ws.receive_bytes()
        assert closed.value.code == 4403
