"""Autonomous coding agent driver & benchmark runner for Root.

Simulates a real developer driving Root's coding engine through a multi-step
coding task: exploring code, identifying root causes, applying surgical patches,
running automated verification suites, and inspecting verified diffs —
running 100% locally with zero external API credits.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from tura_code.app import create_app
from tura_code.sessions.coding_context import detect_project_facts, format_context_block


def tool_call_str(call_id: str, name: str, args: dict) -> str:
    return "tool_call: " + json.dumps({"id": call_id, "name": name, "arguments": json.dumps(args)})


def text_str(content: str) -> str:
    return "text: " + content


def run_benchmark():
    print("=" * 70)
    print("  ROOT CODING AGENT CAPABILITY BENCHMARK (ZERO-CREDIT DRIVER)")
    print("=" * 70)

    # 1. Setup temporary sandbox workspace
    sandbox = Path(tempfile.mkdtemp(prefix="root_bench_"))
    print(f"\n[1/5] Initializing sandbox workspace at:\n      {sandbox}")

    (sandbox / "pyproject.toml").write_text("[project]\nname = 'token-limiter'\nversion = '0.1.0'\n", "utf-8")
    (sandbox / "AGENTS.md").write_text("# Instructions\nRun verification using: pytest tests/ -v\n", "utf-8")

    # A rate limiter with a bug: allows requests even when tokens are exhausted!
    buggy_code = """class TokenBucket:
    def __init__(self, capacity: int, fill_rate: float):
        self.capacity = capacity
        self.fill_rate = fill_rate
        self.tokens = capacity
        self.last_update = 0.0

    def consume(self, tokens: int = 1) -> bool:
        # BUG: '<=' instead of '>=' allows negative token balances!
        if self.tokens <= tokens:
            self.tokens -= tokens
            return True
        return False
"""
    (sandbox / "limiter.py").write_text(buggy_code, "utf-8")

    tests_dir = sandbox / "tests"
    tests_dir.mkdir()
    test_code = """from limiter import TokenBucket

def test_initial_tokens():
    b = TokenBucket(capacity=5, fill_rate=1.0)
    assert b.consume(3) is True
    assert b.tokens == 2

def test_reject_when_exceeded():
    b = TokenBucket(capacity=2, fill_rate=1.0)
    assert b.consume(2) is True
    # Now empty: must reject!
    assert b.consume(1) is False
"""
    (sandbox / "tests" / "test_limiter.py").write_text(test_code, "utf-8")

    # 2. Verify facts detection
    print("\n[2/5] Inspecting workspace context & verification engine...")
    facts = detect_project_facts(sandbox)
    print(format_context_block(facts))
    assert facts["stack"] == "Python"
    assert "pytest" in str(facts["verify_commands"])

    # 3. Setup scripted driver steps (simulating the coding agent's thought process)
    script_steps = [
        # Step 1: Agent inspects codebase
        "\n".join([
            text_str("I am investigating the rate limiter implementation and running tests to diagnose the failure."),
            tool_call_str("c1", "ReadFile", {"path": "limiter.py"}),
            tool_call_str("c2", "ReadFile", {"path": "tests/test_limiter.py"}),
        ]),
        # Step 2: Agent runs test to reproduce symptom
        "\n".join([
            text_str("Reproducing the symptom via the test runner."),
            tool_call_str("c3", "Shell", {"command": f"{sys.executable} -m pytest tests/test_limiter.py"}),
        ]),
        # Step 3: Agent performs surgical patch and runs verification check
        "\n".join([
            text_str("Found root cause: boundary condition check on `self.tokens`. Applying surgical fix."),
            tool_call_str("c4", "StrReplaceFile", {
                "path": "limiter.py",
                "edit": [{"old": "if self.tokens <= tokens:", "new": "if self.tokens >= tokens:"}]
            }),
            tool_call_str("c5", "Shell", {"command": f"{sys.executable} -m pytest tests/test_limiter.py -q"}),
        ]),
        # Step 4: Final wrap-up summary
        text_str("All unit tests pass cleanly. Rate limiter token bucket fix is verified."),
    ]

    script_path = sandbox / "driver_script.json"
    script_path.write_text(json.dumps(script_steps), "utf-8")
    os.environ["TURA_CODE_SCRIPTED_ECHO"] = str(script_path)
    os.environ["TURA_CODE_VERIFY_GATE"] = "1"

    # 4. Run session through Root server runtime
    print("\n[3/5] Launching Root runtime process and executing turn...")
    home_dir = sandbox / "home"
    home_dir.mkdir()
    app = create_app(home_dir)

    with TestClient(app) as client:
        # Bootstrap session
        client.post("/api/auth/bootstrap")
        sess_resp = client.post("/api/sessions", json={"workspace": str(sandbox)})
        session_id = sess_resp.json()["id"]
        # Set yolo so tools can run unattended
        client.patch(f"/api/sessions/{session_id}", json={"permission_mode": "yolo"})

        with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
            ws.receive_json()  # hello
            ws.send_json({"type": "prompt", "text": "Fix the token consumption bug in limiter.py and verify with tests"})

            events = []
            while True:
                message = ws.receive_json()
                if message.get("type") == "error":
                    raise AssertionError(message.get("message"))
                if message.get("type") != "event":
                    continue
                event = message["event"]
                events.append(event)
                evt_type = event.get("type")
                if evt_type == "ApprovalRequest":
                    ws.send_json({"type": "approval", "request_id": event["payload"]["id"], "response": "approve"})
                elif evt_type == "ToolCall":
                    payload = event["payload"]
                    print(f"  -> Tool Call: {payload['function']['name']}({payload['function'].get('arguments', '')[:60]}...)")
                elif evt_type == "TurnFinished":
                    break

        print("\n[4/5] Evaluating turn outcome and verification telemetry...")
        finished = [e["payload"] for e in events if e.get("type") == "TurnFinished"][-1]
        print(f"  - Status:         {finished.get('status')}")
        print(f"  - Verified:       {finished.get('verified')}")
        print(f"  - Checks Run:     {finished.get('checks')}")
        print(f"  - Files Modified: {finished.get('files_touched')}")
        print(f"  - Duration:       {finished.get('duration')}s")

        assert finished.get("status") == "finished"
        assert finished.get("verified") is True
        assert "limiter.py" in finished.get("files_touched")

        # 5. Check actual disk file modification
        print("\n[5/5] Inspecting patched code on disk:")
        patched_code = (sandbox / "limiter.py").read_text("utf-8")
        print("-" * 50)
        print(patched_code.strip())
        print("-" * 50)
        assert "if self.tokens >= tokens:" in patched_code

        # Run pytest directly on the patched sandbox
        res = subprocess.run([sys.executable, "-m", "pytest", "tests/test_limiter.py", "-q"], cwd=str(sandbox), capture_output=True, text=True)
        print(f"Direct Pytest Output: {res.stdout.strip()}")
        assert res.returncode == 0

    print("\n" + "=" * 70)
    print("  ALL BENCHMARK CRITERIA PASSED: CODING AGENT VERIFIED 100%!")
    print("=" * 70)

    # Cleanup
    shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    run_benchmark()
