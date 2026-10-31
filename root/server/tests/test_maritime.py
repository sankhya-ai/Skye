"""root on Maritime: the platform's /health and /chat contract, and a cloud root that hands its token to no one."""

from __future__ import annotations

import pytest
from conftest import requires_runtime, text
from fastapi.testclient import TestClient

from tura_code import maritime

FRONT_DOOR = {"host": "my-root.agents.maritime.sh"}


@pytest.fixture
def cloud(home, monkeypatch):
    monkeypatch.setenv("TURA_CODE_MARITIME", "1")
    monkeypatch.setenv("TURA_CODE_BOOTSTRAP", "0")
    monkeypatch.setenv("TURA_CODE_TOKEN", "cloud-token")
    from tura_code.app import create_app

    with TestClient(create_app(home)) as client:
        yield client


@requires_runtime
def test_maritime_reaches_root_through_its_front_door_and_nothing_else_does(cloud, script):
    assert cloud.get("/health", headers=FRONT_DOOR).json() == {"ok": True}
    script([text("Hello from root on Maritime.")])
    answer = cloud.post("/chat", json={"message": "hi", "source": "front_door"}, headers=FRONT_DOOR).json()
    assert answer == {"response": "Hello from root on Maritime."}
    # Everything else still refuses a host it does not know, and needs the token.
    assert cloud.get("/api/health", headers=FRONT_DOOR).status_code == 403
    assert cloud.post("/api/auth/bootstrap").status_code == 403
    assert cloud.get("/api/sessions").status_code == 401
    assert cloud.post("/api/auth/login", json={"token": "cloud-token"}).status_code == 200
    sessions = cloud.get("/api/sessions").json()["sessions"]
    assert [(s["kind"], s["permission_mode"]) for s in sessions] == [("chat", "plan")]


@requires_runtime
def test_a_long_turn_answers_later_on_status(cloud, script, monkeypatch):
    monkeypatch.setattr(maritime, "REPLY_WITHIN", 0.0)
    script([text("Here is the long answer.")])
    first = cloud.post("/chat", json={"message": "audit the repo", "source": "telegram"}, headers=FRONT_DOOR).json()["response"]
    assert first.startswith("Working on it")
    monkeypatch.setattr(maritime, "REPLY_WITHIN", 30.0)
    assert cloud.post("/chat", json={"message": "status", "source": "telegram"}, headers=FRONT_DOOR).json()["response"] == "Here is the long answer."
    assert cloud.post("/chat", json={"message": "status", "source": "telegram"}, headers=FRONT_DOOR).json()["response"] == "Nothing running. Ask me anything."


def test_the_routes_exist_only_on_maritime(client):
    assert client.post("/chat", json={"message": "hi"}).status_code in (404, 405)
