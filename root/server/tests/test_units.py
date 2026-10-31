from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from tura_code import launcher
from tura_code.events import EventLog
from tura_code.memory import MemoryGateway, MemoryView, packet_envelope, project_scope, split_envelope
from tura_code.sessions import build_parts, expand_mentions
from tura_code.settings import Settings, SettingsStore
from tura_code.tenancy import Server


def _spec(tmp_path: Path, **kw) -> launcher.LaunchSpec:
    defaults = dict(
        session_id="abc",
        workspace=tmp_path,
        settings=Settings(openrouter_api_key="sk-or-test"),
        permission_mode="default",
        memory_enabled=True,
    )
    defaults.update(kw)
    return launcher.LaunchSpec(**defaults)


def test_config_uses_the_openrouter_protocol_and_keeps_the_key_out_of_env(tmp_path, monkeypatch):
    monkeypatch.delenv(launcher.SCRIPTED_ECHO_ENV, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    monkeypatch.setenv("SOME_OTHER_API_KEY", "secret")
    spec = _spec(tmp_path, capabilities=["image_in"])
    path = launcher.write_runtime_config(spec, tmp_path / "rt")
    config = json.loads(path.read_text())
    if os.name != "nt":  # no mode bits on Windows: the profile folder's ACL keeps it private
        assert oct(path.stat().st_mode & 0o777) == "0o600"
    provider = config["providers"]["openrouter"]
    assert provider["api_key"] == "sk-or-env"  # the environment wins
    assert config["models"]["main"]["provider"] == "openrouter"
    assert config["models"]["main"]["capabilities"] == ["image_in"]
    # Web search is keyless by default; the paid plugin only when asked for.
    assert config["services"]["web_search"]["order"] == ["parallel", "firecrawl"]
    assert "openrouter_search" not in config["services"]
    paid = _spec(tmp_path, settings=Settings(web_search_paid_fallback=True))
    assert launcher.runtime_config(paid)["services"]["openrouter_search"]["model"]
    env = launcher.child_environment(spec, tmp_path / "share")
    assert not any(k.endswith("_API_KEY") for k in env)
    assert "OPENROUTER_API_KEY" not in env
    assert env["CHOTU_DIRECTOR"] == "on"
    assert env["KIMI_SHARE_DIR"] == str(tmp_path / "share")


def test_director_off_switches_decisions_off(tmp_path):
    spec = _spec(tmp_path, settings=Settings(director_mode="off"))
    env = launcher.child_environment(spec, tmp_path / "share")
    assert env["CHOTU_DIRECTOR"] == "off" and env["CHOTU_DECISIONS_ENABLED"] == "0"


def test_overlay_for_plan_mode_with_memory(tmp_path):
    spec = _spec(tmp_path, permission_mode="plan")
    overlay = launcher.write_agent_overlay(spec, tmp_path / "rt").read_text()
    assert f"extend: {json.dumps(str(launcher.base_agent_file()))}" in overlay
    assert "context_continuity_tool: memory_checkpoint" in overlay
    assert "PLAN MODE" in overlay and "memory_recall" in overlay
    assert '"kimi_cli.tools.file:WriteFile"' in overlay
    assert '"kimi_cli.tools.file:StrReplaceFile"' in overlay


def test_overlay_without_memory_or_plan_is_bare(tmp_path):
    spec = _spec(tmp_path, memory_enabled=False)
    overlay = launcher.write_agent_overlay(spec, tmp_path / "rt").read_text()
    assert "context_continuity_tool" not in overlay
    assert "exclude_tools" not in overlay
    assert "ROLE_ADDITIONAL" not in overlay


def test_fingerprint_changes_with_what_shapes_the_process(tmp_path):
    base = _spec(tmp_path).fingerprint()
    assert _spec(tmp_path, permission_mode="accept_edits").fingerprint() == base
    assert _spec(tmp_path, permission_mode="plan").fingerprint() != base
    assert _spec(tmp_path, settings=Settings(openrouter_api_key="sk-or-test", model="x/y")).fingerprint() != base


def test_settings_never_expose_the_key_and_blank_means_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    store = SettingsStore(tmp_path)
    store.update({"openrouter_api_key": "sk-or-v1-abcdefgh"})
    store.update({"openrouter_api_key": "", "model": "a/b"})
    settings = store.get()
    assert settings.openrouter_api_key == "sk-or-v1-abcdefgh" and settings.model == "a/b"
    public = settings.public()
    assert public["openrouter_api_key"] == "" and public["api_key_set"] and public["api_key_hint"] == "…efgh"
    if os.name != "nt":
        assert oct((tmp_path / "settings.json").stat().st_mode & 0o777) == "0o600"
    store.update({"clear_api_key": True})
    assert store.get().openrouter_api_key == ""


def test_envelope_round_trip(tmp_path):
    wrapped = packet_envelope("Project memory:\n- uses pytest", "fix the bug", tmp_path)
    context, request = split_envelope(wrapped)
    assert request == "fix the bug"
    assert "uses pytest" in context
    assert split_envelope("plain") == ("", "plain")


def test_mentions_attach_workspace_files_only(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n")
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("nope")
    text = expand_mentions("look at @src/app.py and @../secret.txt and email a@b.com", tmp_path)
    assert "print('hi')" in text
    assert "nope" not in text
    assert text.count("<system>Contents of") == 1


def test_build_parts_puts_images_after_text():
    import base64

    parts = build_parts(
        "see",
        [
            {"name": "a.png", "mime": "image/png", "data": base64.b64encode(b"png").decode()},
            {"name": "notes.txt", "mime": "text/plain", "data": base64.b64encode(b"hello").decode()},
        ],
    )
    assert parts[0]["type"] == "text" and "hello" in parts[0]["text"]
    assert parts[1]["type"] == "image_url" and parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_event_log_coalesces_stream_deltas(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    runtime = Server(tmp_path).tenant().sessions.create(str(ws))
    for chunk in ("Hel", "lo", "!"):
        runtime.emit("ContentPart", {"type": "text", "text": chunk})
    runtime.emit("ToolCall", {"type": "function", "id": "c1", "function": {"name": "Shell", "arguments": '{"comm'}})
    runtime.emit("ToolCallPart", {"arguments_part": 'and":"ls"}'})
    runtime.emit("ToolResult", {"tool_call_id": "c1", "return_value": {"is_error": False}})
    runtime.emit("ContentPart", {"type": "text", "text": "Done"})
    history = runtime.log.page()["events"]
    assert [e["type"] for e in history] == ["ContentPart", "ToolCall", "ToolResult", "ContentPart"]
    assert history[0]["payload"]["text"] == "Hello!"
    assert history[1]["payload"]["function"]["arguments"] == '{"command":"ls"}'
    runtime.log.flush()
    lines = (runtime.dir / "events.jsonl").read_text().splitlines()
    assert len(lines) == 4
    # A reopened log knows where it left off without reading the whole file.
    assert EventLog(runtime.dir / "events.jsonl").last_seq == runtime._seq


def test_event_log_pages_from_the_end_at_turn_boundaries(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    seq = 0
    for turn in range(30):
        for kind in ("UserPrompt", "ContentPart", "ToolCall", "ToolResult", "TurnFinished"):
            seq += 1
            payload = {"text": "x" * 200} if kind != "ToolCall" else {"function": {"name": "Shell", "arguments": "{}"}}
            log.record({"seq": seq, "ts": 0, "turn": str(turn), "type": kind, "payload": payload})
    log.flush()
    page = log.page(max_events=12)
    assert page["has_more"] is True
    assert page["events"][0]["type"] == "UserPrompt" and page["events"][-1]["seq"] == seq
    assert len(page["events"]) <= 12
    older = log.page(before=page["first_seq"], max_events=12)
    assert older["events"][-1]["seq"] == page["first_seq"] - 1
    everything = log.page(max_events=10_000)
    assert everything["has_more"] is False and len(everything["events"]) == seq
    # Bounded by bytes too: one huge result does not pull the whole log into memory.
    small = log.page(max_bytes=2_000)
    assert small["has_more"] and sum(len(json.dumps(e)) for e in small["events"]) < 4_000


@pytest.mark.asyncio
async def test_memory_gateway_local_mode(tmp_path):
    settings = Settings(memory_mode="local")
    root = MemoryGateway(tmp_path, lambda: settings)
    gateway = MemoryView(root)
    ws = tmp_path / "project"
    ws.mkdir()
    try:
        stored = await gateway.handle_tool(
            "memory_remember",
            {"content": "Database migrations live in db/migrations and run with alembic.", "kind": "convention"},
            workspace=ws,
            session_id="s",
        )
        assert stored["is_error"] is False, stored
        await gateway.handle_tool(
            "memory_remember",
            {"content": "Owner prefers tabs over spaces in Go code.", "scope": "owner"},
            workspace=ws,
            session_id="s",
        )
        recalled = await gateway.handle_tool("memory_recall", {"query": "where are database migrations"}, workspace=ws, session_id="s")
        assert "alembic" in recalled["output"]
        assert "tabs" not in recalled["output"]  # no shared words, not relevant

        packet = await gateway.compile_packet("add a database migrations step", ws, seen=set(), first_turn=True)
        assert packet is not None and "alembic" in packet.text
        again = await gateway.compile_packet("add a database migrations step", ws, seen={i["id"] for i in packet.items}, first_turn=False)
        assert again is None  # nothing new to show

        other = tmp_path / "other"
        other.mkdir()
        elsewhere = await gateway.recall("database migrations", other)
        assert elsewhere == []  # project memory stays in its project

        checkpoint = await gateway.handle_tool(
            "memory_checkpoint",
            {"summary": "Wired migrations into CI", "what_worked": "running alembic upgrade head in CI"},
            workspace=ws,
            session_id="s",
        )
        assert checkpoint["is_error"] is False and checkpoint["output"].startswith("Checkpoint saved.")
        assert project_scope(ws) != project_scope(other)

        # Two people on one hosted server share Dhee, not owner memory.
        alice, bob = MemoryView(root, "owner:alice"), MemoryView(root, "owner:bob")
        await alice.remember("Alice deploys with fly.io on Fridays.", ws, scope="owner")
        assert any("fly.io" in m["memory"] for m in await alice.recall("how does deploys work fly.io", ws, scope="owner"))
        assert await bob.recall("how does deploys work fly.io", ws, scope="owner") == []
        alice_ids = [m["id"] for m in await alice.list_memories(ws) if "fly.io" in m["memory"]]
        assert alice_ids and not await bob.forget(alice_ids[0], ws)
        assert await alice.forget(alice_ids[0], ws)
    finally:
        await root.close()


@pytest.mark.asyncio
async def test_memory_off_is_a_tool_error_not_a_crash(tmp_path):
    settings = Settings(memory_mode="off")
    root = MemoryGateway(tmp_path, lambda: settings)
    gateway = MemoryView(root)
    try:
        result = await gateway.handle_tool("memory_recall", {"query": "x"}, workspace=tmp_path, session_id="s")
        assert result["is_error"] is True and "unavailable" in result["message"].lower()
        assert await gateway.compile_packet("x", tmp_path, seen=set(), first_turn=True) is None
    finally:
        await root.close()


def test_security_guards(home, workspace):
    from fastapi.testclient import TestClient

    from tura_code.app import create_app

    with TestClient(create_app(home)) as client:
        assert client.get("/api/sessions").status_code == 401
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/health", headers={"host": "evil.example:8787"}).status_code == 403
        client.post("/api/auth/bootstrap")
        assert client.get("/api/sessions").status_code == 200
        token = (home / "token").read_text().strip()
        bare = TestClient(client.app)
        assert bare.get("/api/sessions", headers={"authorization": f"Bearer {token}"}).status_code == 200
        created = client.post("/api/sessions", json={"workspace": str(workspace)}).json()
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/api/sessions/{created['id']}/ws", headers={"origin": "https://evil.example"}
            ) as ws:
                ws.receive_json()
        assert client.post("/api/sessions", json={"workspace": str(workspace / "missing")}).status_code == 400


def test_user_mcp_config_is_passed_and_restarts_on_change(tmp_path):
    assert launcher.user_mcp_config(tmp_path) is None
    (tmp_path / "mcp.json").write_text('{"mcpServers": {}}')
    config = launcher.user_mcp_config(tmp_path)
    spec = _spec(tmp_path, mcp_config=config)
    argv = launcher.runtime_argv(Path("/bin/kimi-agent"), spec, tmp_path / "c.json", tmp_path / "a.yaml")
    assert argv[-2:] == ["--mcp-config-file", str(tmp_path / "mcp.json")]
    before = spec.fingerprint()
    import os as _os

    _os.utime(config, (1, 1))
    assert spec.fingerprint() != before


def test_fallback_models_join_a_chat_request_once():
    import json

    from tura_code.llm_proxy import with_fallbacks

    body = json.dumps({"model": "a/one", "messages": []}).encode()
    assert json.loads(with_fallbacks(body, ["b/two", "a/one", "c/three"]))["models"] == ["a/one", "b/two", "c/three"]
    assert with_fallbacks(body, []) == body
    explicit = json.dumps({"model": "a/one", "models": ["x/y"]}).encode()
    assert with_fallbacks(explicit, ["b/two"]) == explicit
    assert with_fallbacks(b"not json", ["b/two"]) == b"not json"


def test_every_assistant_tool_call_carries_reasoning_back():
    import json

    from tura_code.llm_proxy import with_reasoning_echo

    call = [{"id": "c1", "type": "function", "function": {"name": "Shell", "arguments": "{}"}}]
    body = json.dumps({"messages": [
        {"role": "user", "content": "push it"},
        {"role": "assistant", "content": "", "tool_calls": call},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        {"role": "assistant", "content": "", "tool_calls": call, "reasoning_content": "thought"},
        {"role": "assistant", "content": "done"},
    ]}).encode()
    messages = json.loads(with_reasoning_echo(body))["messages"]
    assert messages[1]["reasoning_content"] == ""
    assert messages[3]["reasoning_content"] == "thought"
    assert "reasoning_content" not in messages[4] and "reasoning_content" not in messages[0]
    complete = json.dumps({"messages": [messages[3]]}).encode()
    assert with_reasoning_echo(complete) == complete
    assert with_reasoning_echo(b"not json") == b"not json"


def test_one_root_per_data_directory(tmp_path):
    import subprocess
    import sys

    from tura_code.instance import AlreadyRunning, claim

    held = claim(tmp_path, "http://localhost:1111")
    # A second process on the same folder is refused and told where the first one is.
    probe = "import sys, pathlib; from tura_code.instance import claim\ntry:\n    claim(pathlib.Path(sys.argv[1]), 'x')\nexcept Exception as e:\n    print(e)"
    out = subprocess.run([sys.executable, "-c", probe, str(tmp_path)], capture_output=True, text=True).stdout
    assert "already running" in out and "http://localhost:1111" in out
    held.close()
    claim(tmp_path, "http://localhost:2222").close()  # released when its holder goes
    assert AlreadyRunning


def test_folder_dialog_returns_the_choice_or_none(client, tmp_path, monkeypatch):
    from tura_code import workspace

    import sys

    chosen = tmp_path / "picked"
    chosen.mkdir()
    # A stand-in for the dialog: prints the choice, or exits 1 as a cancelled dialog does.
    fake = "import os, sys\npick = os.environ.get('PICK')\nprint(pick + os.sep) if pick else sys.exit(1)\n"
    monkeypatch.setattr(workspace, "picker_command", lambda begin: ([sys.executable, "-c", fake], None))
    monkeypatch.setattr(workspace, "folder_picker", lambda: True)
    assert client.get("/api/health").json()["folder_picker"] is True
    monkeypatch.setenv("PICK", str(chosen))
    assert client.post("/api/fs/pick", json={"start": str(tmp_path)}).json() == {"path": str(chosen)}
    monkeypatch.delenv("PICK")
    assert client.post("/api/fs/pick", json={}).json() == {"path": None}
    monkeypatch.setattr(workspace, "folder_picker", lambda: False)
    assert client.post("/api/fs/pick", json={}).status_code == 404
    assert client.get("/api/health").json()["folder_picker"] is False
