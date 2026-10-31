"""Edits must be exercised before a turn ends: the runtime's verify gate, and
what the host does with a turn whose edits were never run."""

from __future__ import annotations

import pytest
from conftest import of_type, requires_runtime, run_turn, text, tool_call

from tura_code.sessions.record import TurnState


def test_only_commands_that_run_code_check_an_edit():
    turn = TurnState(id="t", prompt="p")
    assert turn.verified
    turn.note_edit()
    for command in ("git diff HEAD~1", "cd app && git log --oneline", "ls -la", "cat a.py"):
        turn.note_command(command)
        assert not turn.verified, command
    turn.note_command("python3 repro.py")
    assert turn.verified and turn.checks == 1
    turn.note_edit()
    assert not turn.verified


@requires_runtime
def test_the_runtime_holds_open_a_turn_whose_edits_were_never_run(client, workspace, script, monkeypatch):
    monkeypatch.setenv("TURA_CODE_VERIFY_GATE", "1")
    script([
        tool_call("w", "WriteFile", {"path": "calc.py", "content": "print(2 + 3)\n"}),
        tool_call("c", "memory_checkpoint", {"summary": "Fixed calc."}),
        text("Done."),
        # The gate keeps the turn open; the next step runs the change.
        tool_call("s", "Shell", {"command": "python3 calc.py"}),
        text("Ran it: prints 5."),
    ])
    session = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    client.patch(f"/api/sessions/{session}", json={"permission_mode": "yolo"})
    with client.websocket_connect(f"/api/sessions/{session}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "make calc print the sum")
    assert of_type(events, "TurnFinished")[-1]["payload"]["status"] == "finished"
    calls = [e["payload"]["function"]["name"] for e in of_type(events, "ToolCall")]
    assert calls == ["WriteFile", "memory_checkpoint", "Shell"]
    results = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(events, "ToolResult")}
    assert "5" in str(results["s"]["output"])
    checkpoint = str(results["c"]["output"])
    if not results["c"]["is_error"]:  # memory can be off in a minimal install
        assert checkpoint.startswith("Saved as UNVERIFIED")
    said = "".join(e["payload"].get("text", "") for e in of_type(events, "ContentPart"))
    assert "Ran it" in said


@requires_runtime
def test_a_turn_that_ran_its_change_ends_when_it_says_so(client, workspace, script, monkeypatch):
    monkeypatch.setenv("TURA_CODE_VERIFY_GATE", "1")
    script([
        tool_call("w", "WriteFile", {"path": "calc.py", "content": "print(5)\n"}),
        tool_call("s", "Shell", {"command": "python3 calc.py"}),
        text("Done."),
        text("This reply is never reached."),
    ])
    session = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    client.patch(f"/api/sessions/{session}", json={"permission_mode": "yolo"})
    with client.websocket_connect(f"/api/sessions/{session}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "make calc print five")
    said = "".join(e["payload"].get("text", "") for e in of_type(events, "ContentPart"))
    assert said == "Done."
    finished = of_type(events, "TurnFinished")[-1]["payload"]
    assert finished["verified"] is True
    assert finished["checks"] == 1


def test_detect_project_facts_python(tmp_path):
    from tura_code.sessions.coding_context import detect_project_facts, format_context_block

    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test-pkg'\n")
    (tmp_path / "uv.lock").write_text("")
    facts = detect_project_facts(tmp_path)
    assert facts["stack"] == "Python"
    assert facts["package_manager"] == "uv"
    assert "uv run pytest" in facts["verify_commands"]

    block = format_context_block(facts)
    assert "Workspace Facts:" in block
    assert "Python (uv)" in block
    assert "uv run pytest" in block


def test_detect_project_facts_node(tmp_path):
    import json
    from tura_code.sessions.coding_context import detect_project_facts, format_context_block

    pkg = {
        "name": "web-app",
        "scripts": {
            "test": "vitest run",
            "typecheck": "tsc --noEmit",
            "build": "vite build"
        }
    }
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    (tmp_path / "pnpm-lock.yaml").write_text("")
    (tmp_path / "tsconfig.json").write_text("{}")

    facts = detect_project_facts(tmp_path)
    assert facts["stack"] == "TypeScript"
    assert facts["package_manager"] == "pnpm"
    assert "pnpm test" in facts["verify_commands"]
    assert "pnpm run typecheck" in facts["verify_commands"]

    block = format_context_block(facts)
    assert "TypeScript (pnpm)" in block
    assert "pnpm test" in block
