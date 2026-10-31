"""Mac apps: root's mac_* tools over root-driver — which tools a session sees, what asks, what comes back."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tura_code import driver
from tura_code.driver import ACTION_CLOSE, ACTION_OPERATE, DriverToolset

FAKE = r'''#!/usr/bin/env python3
import json, sys
TOOLS = [
    {"name": "get_window_state", "description": "Look.", "input_schema": {"type": "object"}, "risk": "read", "default_timeout": 5},
    {"name": "capture_window", "description": "Screenshot.", "input_schema": {"type": "object"}, "risk": "read", "default_timeout": 5},
    {"name": "click", "description": "Click.", "input_schema": {"type": "object"}, "risk": "write", "default_timeout": 5},
    {"name": "kill_app", "description": "Quit.", "input_schema": {"type": "object"}, "risk": "destructive", "default_timeout": 5},
]
if sys.argv[1] == "list-tools":
    print(json.dumps({"tools": TOOLS}))
elif sys.argv[1] == "call":
    params = json.loads(sys.stdin.read())
    tool = sys.argv[2]
    if tool == "capture_window":
        print(json.dumps({"ok": True, "image_ref": "data:image/jpeg;base64,AAAA"}))
    elif tool == "get_window_state" and params.get("pid") == 0:
        print(json.dumps({"ok": False, "code": "accessibility_permission_required", "error": "no"}))
        sys.exit(87)
    else:
        print(json.dumps({"ok": True, "tool": tool, "params": params}))
'''


@pytest.fixture
def fake_driver(tmp_path) -> Path:
    path = tmp_path / "root-driver"
    path.write_text(FAKE)
    path.chmod(0o755)
    return path


def make_runtime(*, mode="default", answering="", server_mode="local", approve=True):
    asked: list[tuple[str, str]] = []

    async def approve_host_action(call_id, action, description, display=None):
        asked.append((action, description))
        return approve

    runtime = SimpleNamespace(
        record=SimpleNamespace(permission_mode=mode, answering=answering),
        tenant=SimpleNamespace(server=SimpleNamespace(mode=server_mode)),
        approve_host_action=approve_host_action,
    )
    return runtime, asked


@pytest.fixture(autouse=True)
def on_a_mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")


def names(toolset, runtime):
    return sorted(d["name"] for d in toolset.definitions(runtime))


def test_a_local_session_gets_every_tool_and_plan_mode_only_looks(fake_driver):
    toolset = DriverToolset(fake_driver)
    assert names(toolset, make_runtime()[0]) == ["mac_capture_window", "mac_click", "mac_get_window_state", "mac_kill_app"]
    assert names(toolset, make_runtime(mode="plan")[0]) == ["mac_capture_window", "mac_get_window_state"]
    assert names(toolset, make_runtime(answering="line")[0]) == ["mac_capture_window", "mac_get_window_state"]
    assert toolset.handles("mac_click") and not toolset.handles("click") and not toolset.handles("mac_teleport")


def test_hosted_root_and_other_platforms_never_drive_a_desktop(fake_driver, monkeypatch):
    toolset = DriverToolset(fake_driver)
    assert toolset.definitions(make_runtime(server_mode="hosted")[0]) == []
    monkeypatch.setattr(sys, "platform", "linux")
    assert toolset.definitions(make_runtime()[0]) == []


def test_without_a_driver_there_are_no_tools(tmp_path):
    toolset = DriverToolset(tmp_path / "missing")
    assert toolset.definitions(make_runtime()[0]) == []


def test_looking_never_asks_operating_and_quitting_ask_by_class(fake_driver):
    toolset = DriverToolset(fake_driver)
    runtime, asked = make_runtime()
    looked = asyncio.run(toolset.call(runtime, "c1", "mac_get_window_state", {"bundle_id": "com.apple.calculator"}))
    assert not looked["is_error"] and asked == []
    clicked = asyncio.run(toolset.call(runtime, "c2", "mac_click", {"element_token": "t1", "snapshot_id": "s"}))
    assert not clicked["is_error"]
    assert json.loads(clicked["output"])["params"] == {"element_token": "t1", "snapshot_id": "s"}
    asyncio.run(toolset.call(runtime, "c3", "mac_kill_app", {"pid": 42}))
    assert [a for a, _ in asked] == [ACTION_OPERATE, ACTION_CLOSE]
    assert asked[1][1] == "kill app: pid=42"


def test_a_rejected_action_never_reaches_the_driver(fake_driver):
    toolset = DriverToolset(fake_driver)
    runtime, _ = make_runtime(approve=False)
    result = asyncio.run(toolset.call(runtime, "c1", "mac_click", {}))
    assert result["is_error"] and "not allowed" in result["message"]


def test_plan_mode_cannot_operate_even_by_name(fake_driver):
    toolset = DriverToolset(fake_driver)
    runtime, asked = make_runtime(mode="plan")
    result = asyncio.run(toolset.call(runtime, "c1", "mac_click", {}))
    assert result["is_error"] and asked == []


def test_screenshots_reach_the_model_as_images(fake_driver):
    result = asyncio.run(DriverToolset(fake_driver).call(make_runtime()[0], "c1", "mac_capture_window", {}))
    text, image = result["output"]
    assert "<image 1 attached>" in text["text"] and "base64" not in text["text"]
    assert image == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}


def test_missing_accessibility_says_how_to_fix_it(fake_driver):
    result = asyncio.run(DriverToolset(fake_driver).call(make_runtime()[0], "c1", "mac_get_window_state", {"pid": 0}))
    assert result["is_error"] and "allow Root Driver" in result["output"]


def test_the_plugin_is_listed(client):
    plugins = {p["id"]: p for p in client.get("/api/plugins").json()["plugins"]}
    assert plugins["driver"]["name"] == "Mac apps" and plugins["driver"]["enabled"] is True


def test_find_driver_prefers_the_env(fake_driver, monkeypatch, tmp_path):
    monkeypatch.setenv(driver.BIN_ENV, str(fake_driver))
    assert driver.find_driver() == fake_driver
    monkeypatch.setenv(driver.BIN_ENV, str(tmp_path / "nope"))
    assert driver.find_driver() is None
