"""The recoverable team workflow, driven through the Skye UI in a real browser.

End-to-end, offline, no provider and no cost: the vendored Root server runs with a scripted
model (TURA_CODE_SCRIPTED_ECHO), a Skye mission runs, pauses needs-owner on the tester step,
the operator approves and resumes IN THE UI, and the mission finishes - with Playwright
screenshots of every beat. What this proves: the chain executes through the UI, the one-time
approval is recorded and consumed, the journal trace renders live, and a worker's proposed
follow-up step joins the workflow dynamically. What it does not prove: real model behavior.

Usage: python3 scripts/ui_scenario.py [output-dir]   (needs: uvicorn, playwright + chromium)
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "root" / "server"))
sys.path.insert(0, str(REPO))

import uvicorn  # noqa: E402
from tura_code import launcher  # noqa: E402
from tura_code.app import create_app  # noqa: E402


def tool_call(call_id: str, name: str, arguments: dict) -> str:
    return "tool_call: " + json.dumps({"id": call_id, "name": name, "arguments": json.dumps(arguments)})


def text(value: str) -> str:
    return "text: " + value


def start_root(demo: Path) -> tuple[str, Path]:
    home = demo / "root-home"
    script = demo / "script.json"
    # dev writes hello.py and proposes a follow-up docs step; every job replays this script
    # (the echo is shared), so the tester/docs proposals for an already-known id are rejected.
    script.write_text(json.dumps([
        tool_call("w", "WriteFile", {"path": "hello.py", "content": "print('hello from skye')\n"}),
        text('Wrote hello.py. {"summary": "hello.py written", "propose_steps": [{"id": "docs", '
             '"role": "developer", "adapter": "root", "goal": "write README notes", '
             '"needs": ["dev"], "gates": []}]}'),
    ]))
    os.environ["TURA_CODE_HOME"] = str(home)
    os.environ["TURA_CODE_OFFLINE"] = "1"
    os.environ.pop("OPENROUTER_API_KEY", None)
    os.environ[launcher.SCRIPTED_ECHO_ENV] = str(script)
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
    return f"http://127.0.0.1:{port}", home


def main() -> int:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/skye-ui-scenario").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    demo = out_dir / "workdir"
    demo.mkdir(exist_ok=True)
    (demo / "ws").mkdir(exist_ok=True)
    state_dir = demo / "state"

    root_url, _home = start_root(demo)
    print(f"root scripted server: {root_url}")

    mission_file = demo / "mission.json"
    mission_file.write_text(json.dumps({
        "id": "ui-demo", "goal": "build and verify hello.py, with a dynamic docs follow-up",
        "repo": str(demo / "ws"),
        "steps": [
            {"id": "dev", "role": "developer", "adapter": "root", "goal": "write hello.py",
             "gates": [], "budget": {"max_steps": 8}},
            {"id": "tester", "role": "tester", "adapter": "root", "goal": "verify hello.py",
             "needs": ["dev"], "gates": ["tests"], "budget": {"max_steps": 8}}]}))

    env = dict(os.environ)
    env.update({"SKYE_ROOT_URL": root_url, "SKYE_ROOT_HOME": str(demo / "root-home"),
                "SKYE_STATE_DIR": str(state_dir)})
    # the standing policy allows developers unattended; the tester needs the owner, once
    (demo / "skye.policy.json").write_text(json.dumps({
        "allow_unattended": [{"adapter": "root", "role": "developer"}],
        "test_command": "python3 -c \"import pathlib; assert 'hello from skye' in pathlib.Path('hello.py').read_text()\""}))

    run = subprocess.run([sys.executable, "-m", "skye.cli", "mission", "run", str(mission_file)],
                         cwd=demo, env=env, capture_output=True, text=True, timeout=300)
    print(run.stdout.strip().splitlines()[-1] if run.stdout.strip() else run.stderr[-300:])
    assert "needs-owner" in run.stdout, run.stdout + run.stderr

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        ui_port = probe.getsockname()[1]
    env["PYTHONUNBUFFERED"] = "1"
    ui_proc = subprocess.Popen([sys.executable, "-m", "skye.cli", "ui", "--port", str(ui_port),
                                "--state-dir", str(state_dir)],
                               cwd=demo, env=env)
    # the token file (0600, state-dir-local) is the robust handshake - no pipe buffering involved
    token_file = state_dir / "ui-token"
    deadline = time.monotonic() + 20
    while not token_file.exists() and time.monotonic() < deadline:
        assert ui_proc.poll() is None, "UI server exited early"
        time.sleep(0.1)
    token = token_file.read_text().strip()
    ui_url = f"http://127.0.0.1:{ui_port}/?token={token}"
    print(f"ui: {ui_url}")

    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        page.goto(ui_url)
        page.wait_for_selector('[data-testid="mission-row-ui-demo"]', timeout=15000)
        page.screenshot(path=str(out_dir / "01-overview-needs-owner.png"), full_page=True)

        page.click('[data-testid="mission-row-ui-demo"]')
        page.wait_for_selector('[data-testid="approval-card"]', timeout=15000)
        page.wait_for_timeout(400)
        page.screenshot(path=str(out_dir / "02-detail-approval-card.png"), full_page=True)

        page.click("text=Approve this step once")
        page.wait_for_timeout(600)
        page.screenshot(path=str(out_dir / "03-after-approve.png"), full_page=True)

        page.click("text=Resume mission")
        # the mission runs real (scripted) Root jobs: dev re-attach/settle is journaled already,
        # tester runs now, then the dynamic docs step
        for _ in range(600):
            page.wait_for_timeout(500)
            badge = page.locator("text=done").first
            detail = page.evaluate(
                "fetch('/api/missions/ui-demo', {headers: {'X-Skye-Token': new URLSearchParams(location.search).get('token')}}).then(r => r.json())")
            if detail["status"]["status"] == "done" and not detail["live"].get("running"):
                break
        page.wait_for_timeout(400)
        page.screenshot(path=str(out_dir / "04-mission-done.png"), full_page=True)
        final = page.evaluate(
            "fetch('/api/missions/ui-demo', {headers: {'X-Skye-Token': new URLSearchParams(location.search).get('token')}}).then(r => r.json())")
        print("final overall:", final["status"]["status"])
        print("steps:", [(s["id"], s["state"], s["status"]) for s in final["status"]["steps"]])
        print("dynamic steps:", [s["id"] for s in final["dynamic_steps"]])
        assert final["status"]["status"] == "done", final["status"]
        assert any(s["id"] == "docs" for s in final["dynamic_steps"]), "dynamic docs step missing"
        browser.close()
    ui_proc.terminate()
    print(f"screenshots in {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
