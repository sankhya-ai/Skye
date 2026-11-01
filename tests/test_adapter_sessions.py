"""Adapter session protocol: streaming, vendor session capture/resume, fail-closed resume
rejection, cancellation and timeouts - proven against fake CLIs that speak the REAL wire
shapes (Claude stream-json, Codex --json ThreadEvents), never against a paid model."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from skye.adapters import base
from skye.adapters.claude_code import ClaudeCodeAdapter
from skye.adapters.codex import CodexAdapter

FAKES = Path(__file__).parent / "fakes"


@pytest.fixture
def fake_path(monkeypatch, tmp_path):
    log = tmp_path / "invocations.jsonl"
    monkeypatch.setenv("PATH", f"{FAKES}:{__import__('os').environ['PATH']}")
    monkeypatch.setenv("CODEX_PROTOCOL", "exec")  # these tests pin the exec transport
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.delenv("FAKE_SLEEP", raising=False)
    monkeypatch.delenv("FAKE_MARK", raising=False)
    return log


def _argv_lines(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def _contract(tmp_path, **extra):
    return {"repo": str(tmp_path), "goal": "do the thing", **extra}


# --- Claude Code ---

def test_claude_session_streams_events_and_captures_session(fake_path, tmp_path):
    adapter = ClaudeCodeAdapter()
    seen = []
    result = adapter.run_session(_contract(tmp_path), on_event=seen.append)
    assert result.ok and result.continuity == "fresh"
    assert result.session_id == "sess-new-1"
    assert [e["kind"] for e in seen] == ["session", "message", "tool", "result"]
    assert seen[1]["text"] == "I worked on the task."
    assert seen[2]["name"] == "Read"
    assert result.usage["total_cost_usd"] == 0.01
    assert "I worked on the task." in result.output


def test_claude_resume_uses_vendor_flag_and_marks_continuity(fake_path, tmp_path):
    adapter = ClaudeCodeAdapter()
    result = adapter.run_session(_contract(tmp_path), resume_session_id="sess-9")
    argv = _argv_lines(fake_path)[0]
    assert argv[argv.index("--resume") + 1] == "sess-9"
    assert result.continuity == "resumed" and result.session_id == "sess-9" and result.ok


def test_claude_resume_rejection_fails_closed_no_fresh_fallback(fake_path, tmp_path):
    adapter = ClaudeCodeAdapter()
    result = adapter.run_session(_contract(tmp_path), resume_session_id="bad-session")
    assert not result.ok and result.resume_rejected
    assert result.continuity == "resumed"
    # exactly ONE invocation and it carried --resume: no silent fresh run happened
    assert len(_argv_lines(fake_path)) == 1


def test_claude_role_tools_and_forwarded_budget_flags(fake_path, tmp_path):
    adapter = ClaudeCodeAdapter()
    contract = _contract(tmp_path, tools=["Read", "Grep"],
                         budget={"max_steps": 3, "max_cost_usd": 2.5})
    result = adapter.run_session(contract)
    argv = _argv_lines(fake_path)[0]
    assert argv[argv.index("--tools") + 1] == "Read,Grep"
    assert "mcp__*" in argv
    assert argv[argv.index("--max-turns") + 1] == "3"
    assert argv[argv.index("--max-budget-usd") + 1] == "2.5"
    assert result.ok


def test_claude_cancel_stops_the_process_group(fake_path, tmp_path, monkeypatch):
    mark = tmp_path / "completed-marker"
    monkeypatch.setenv("FAKE_SLEEP", "30")
    monkeypatch.setenv("FAKE_MARK", str(mark))
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    started = time.monotonic()
    result = adapter_run_with_cancel(tmp_path, cancel)
    elapsed = time.monotonic() - started
    assert result.cancelled and not result.ok and elapsed < 15
    assert not mark.exists()  # the run never completed
    assert result.session_id == "sess-new-1"  # id survives for an owner-chosen resume


def adapter_run_with_cancel(tmp_path, cancel):
    return ClaudeCodeAdapter().run_session(_contract(tmp_path), timeout=60,
                                           cancel_event=cancel)


def test_claude_timeout_is_reported_not_silent(fake_path, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "30")
    result = ClaudeCodeAdapter().run_session(_contract(tmp_path), timeout=1)
    assert result.timed_out and not result.ok and result.returncode == 124


def test_cancel_live_registry_signals_the_running_step(fake_path, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "30")
    holder = {}

    def run():
        holder["result"] = ClaudeCodeAdapter().run_session(
            _contract(tmp_path), timeout=60, run_key="m-1:build")

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with base._LIVE_LOCK:
            live = "m-1:build" in base._LIVE
        if live:
            break
        time.sleep(0.05)
    assert base.cancel_live("m-1:build") == "stopped"  # exit confirmed, not just signalled
    thread.join(timeout=15)
    assert holder["result"].cancelled
    assert base.cancel_live("m-1:build") == "not-live"  # cleaned up after the run


# --- Codex ---

def test_codex_session_streams_events_and_captures_thread(fake_path, tmp_path):
    adapter = CodexAdapter()
    seen = []
    result = adapter.run_session(_contract(tmp_path), on_event=seen.append)
    assert result.ok and result.session_id == "thr-new-1"
    assert [e["kind"] for e in seen] == ["session", "message", "tool", "result"]
    assert seen[1]["text"] == "codex did the work"
    assert seen[2]["name"] == "pytest -q"
    assert result.usage["input_tokens"] == 10


def test_codex_resume_order_and_sandbox(fake_path, tmp_path):
    adapter = CodexAdapter()
    result = adapter.run_session(_contract(tmp_path, tools_writable=True),
                                 resume_session_id="thr-7")
    argv = _argv_lines(fake_path)[0]
    # options precede the resume subcommand, matching the CLI's clap layout
    assert argv[:2] == ["exec", "--json"]
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert argv[argv.index("resume") + 1] == "thr-7"
    assert result.continuity == "resumed" and result.session_id == "thr-7" and result.ok


def test_codex_default_sandbox_stays_read_only(fake_path, tmp_path):
    CodexAdapter().run_session(_contract(tmp_path))
    argv = _argv_lines(fake_path)[0]
    assert argv[argv.index("--sandbox") + 1] == "read-only"


def test_codex_resume_rejection_fails_closed(fake_path, tmp_path):
    result = CodexAdapter().run_session(_contract(tmp_path), resume_session_id="bad-session")
    assert not result.ok and result.resume_rejected
    assert len(_argv_lines(fake_path)) == 1


# ----- codex app-server transport (default; codex-rs/app-server JSON-RPC over stdio) -----

def _wait_for(pred, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_codex_app_server_fresh_run_streams_events_and_thread_id(fake_path, tmp_path,
                                                                 monkeypatch):
    monkeypatch.delenv("CODEX_PROTOCOL", raising=False)
    events: list = []
    result = CodexAdapter().run_session(_contract(tmp_path), on_event=events.append)
    assert result.ok, result.error or result.output
    assert result.session_id == "thr-new-1" and result.continuity == "fresh"
    assert result.protocol.startswith("app-server")
    rpcs = [json.loads(line)["rpc"] for line in fake_path.read_text().splitlines()
            if '"rpc"' in line]
    assert rpcs[0] == "initialize"
    assert "thread/start" in rpcs and "turn/start" in rpcs
    assert any(e.get("kind") == "message" and "codex did the work" in e.get("text", "")
               for e in events)


def test_codex_app_server_resume_uses_thread_resume(fake_path, tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_PROTOCOL", raising=False)
    result = CodexAdapter().run_session(_contract(tmp_path), resume_session_id="thr-old")
    assert result.ok and result.continuity == "resumed"
    assert result.session_id == "thr-old"
    rpcs = [json.loads(line)["rpc"] for line in fake_path.read_text().splitlines()
            if '"rpc"' in line]
    assert "thread/resume" in rpcs and "thread/start" not in rpcs


def test_codex_app_server_resume_rejection_fails_closed(fake_path, tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_PROTOCOL", raising=False)
    result = CodexAdapter().run_session(_contract(tmp_path), resume_session_id="bad-session")
    assert not result.ok and result.resume_rejected
    # no silent fallback to a fresh thread after a rejected resume
    rpcs = [json.loads(line)["rpc"] for line in fake_path.read_text().splitlines()
            if '"rpc"' in line]
    assert "thread/start" not in rpcs


def test_codex_app_server_bad_handshake_falls_back_to_exec_loudly(fake_path, tmp_path,
                                                                  monkeypatch):
    monkeypatch.delenv("CODEX_PROTOCOL", raising=False)
    monkeypatch.setenv("FAKE_BAD_HANDSHAKE", "1")
    result = CodexAdapter().run_session(_contract(tmp_path))
    assert result.ok  # the exec transport still delivers the work
    assert result.protocol.startswith("exec-json-fallback:"), result.protocol
    assert result.continuity == "fresh" and result.session_id


def test_codex_app_server_mid_turn_steer_reaches_the_turn(fake_path, tmp_path,
                                                          monkeypatch):
    # FAKE_EXPECT_STEER holds the turn open until a turn/steer request arrives, so the
    # steer lands deterministically mid-turn - no timing race.
    monkeypatch.delenv("CODEX_PROTOCOL", raising=False)
    monkeypatch.setenv("FAKE_EXPECT_STEER", "1")
    import threading as _t
    from skye.adapters import base
    adapter = CodexAdapter()
    box: dict = {}
    thread = _t.Thread(target=lambda: box.__setitem__(
        "result", adapter.run_session(_contract(tmp_path), run_key="m1:dev")),
        daemon=True)
    thread.start()
    assert _wait_for(lambda: "m1:dev" in base._LIVE)
    # Registration precedes turn/start: until the turn exists the adapter answers
    # no-active-turn (nothing sent), so retry until the steer actually lands.
    assert _wait_for(lambda: base.steer_live("m1:dev", "also add logging") == "steered")
    thread.join(timeout=15)
    assert not thread.is_alive()
    result = box["result"]
    assert result.ok and "Follow-up received: also add logging" in result.output
    rpcs = [json.loads(line)["rpc"] for line in fake_path.read_text().splitlines()
            if '"rpc"' in line]
    assert "turn/steer" in rpcs


def test_codex_cancel_during_handshake_fallback_returns_truthful_result(
        fake_path, tmp_path, monkeypatch):
    # Regression: the cancel-during-fallback path must return a truthful cancelled
    # SessionResult - never crash the constructor (a leaked TypeError would replace the
    # honest protocol state).
    import threading as _t
    monkeypatch.delenv("CODEX_PROTOCOL", raising=False)
    monkeypatch.setenv("FAKE_BAD_HANDSHAKE", "1")
    cancel = _t.Event()
    cancel.set()
    result = CodexAdapter().run_session(_contract(tmp_path), cancel_event=cancel)
    assert result.cancelled and not result.ok
    assert result.output and "cancelled" in result.output


def test_codex_approval_request_is_explicitly_declined(fake_path, tmp_path, monkeypatch):
    # Enforcement is at the decision point: a server approval request mid-turn gets an
    # explicit "decline" (never an implicit approve), journaled as approval-declined.
    monkeypatch.delenv("CODEX_PROTOCOL", raising=False)
    monkeypatch.setenv("FAKE_ASK_APPROVAL", "1")
    events: list = []
    result = CodexAdapter().run_session(_contract(tmp_path), on_event=events.append)
    assert result.ok, result.output[:200]
    responses = [json.loads(line)["srv_response"]
                 for line in fake_path.read_text().splitlines()
                 if '"srv_response"' in line]
    assert responses == [{"decision": "decline"}]
    assert any(e.get("kind") == "approval-declined" for e in events)


def test_codex_unknown_server_request_fails_closed(fake_path, tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_PROTOCOL", raising=False)
    monkeypatch.setenv("FAKE_UNKNOWN_REQUEST", "1")
    events: list = []
    result = CodexAdapter().run_session(_contract(tmp_path), on_event=events.append)
    assert result.ok, result.output[:200]  # the turn completes; the request was refused
    responses = [json.loads(line)["srv_response"]
                 for line in fake_path.read_text().splitlines()
                 if '"srv_response"' in line]
    assert responses and responses[0].get("code") == -32601
    assert any(e.get("kind") == "server-request-unknown" for e in events)


def test_codex_thread_start_error_fails_closed_no_exec_fallback(fake_path, tmp_path,
                                                                monkeypatch):
    # An explicit thread/start error arrives AFTER the request went out: thread state
    # is unknown, so the run fails closed with the truthful state - no exec retry.
    monkeypatch.delenv("CODEX_PROTOCOL", raising=False)
    monkeypatch.setenv("FAKE_THREAD_START_ERROR", "1")
    result = CodexAdapter().run_session(_contract(tmp_path))
    assert not result.ok
    assert "thread state unknown" in result.output
    assert result.protocol.startswith("app-server")
    rpcs = [json.loads(line)["rpc"] for line in fake_path.read_text().splitlines()
            if '"rpc"' in line]
    assert "thread/start" in rpcs and "turn/start" not in rpcs
