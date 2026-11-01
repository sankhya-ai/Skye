"""The design gate through the REAL Skye mission/rework/delivery path - not a standalone
script run (that is scripts/design_gate_scenario.py). Real engine, scripted model:

  production signal ("the launch poster reads like generated slop") -> proposal -> owner
  approves -> Start work -> the designer (scripted adapter) writes poster.html - with REAL
  anti-patterns -> done -> AWAITING-VERIFICATION -> owner replays the journey, whose command
  is the design gate (pinned impeccable@4.8.0, deterministic, no model) -> gate exits 2 with
  the detector's real findings as recorded evidence -> owner sends it back -> the designer's
  rework writes the repaired poster -> the SAME UNCHANGED gate command passes -> DELIVERED.

The gate command is set once by the owner and never touched again; what changes is the
artifact. Screenshots per stage, including the rendered failing and repaired posters."""
import hashlib, json, os, socket, subprocess, sys, tempfile, threading, time
def stage(msg): print(f"[{time.monotonic():7.1f}] {msg}", flush=True)
from pathlib import Path

REPO = Path.cwd()
GATE = REPO / "scripts" / "design_gate.py"
assert GATE.is_file()
sys.path.insert(0, str(REPO / "root" / "server"))
sys.path.insert(0, str(REPO))
demo = Path(tempfile.mkdtemp())
ws = demo / "ws"
ws.mkdir()

BAD_POSTER = (REPO / "scripts" / "design_gate_scenario.py").read_text()
# reuse the exact poster bodies from the standalone scenario so both tell one story
import re as _re
_bad = _re.search(r'BAD_POSTER = """(.*?)"""', BAD_POSTER, _re.S).group(1)
_fixed = _re.search(r'FIXED_POSTER = """(.*?)"""', BAD_POSTER, _re.S).group(1)

JOURNEY_CMD = f"python3 {GATE} poster.html"

home = demo / "root-home"
os.environ["TURA_CODE_HOME"] = str(home)
os.environ["TURA_CODE_OFFLINE"] = "1"
for var in ("OPENROUTER_API_KEY", "TURA_CODE_TOKEN"):
    os.environ.pop(var, None)

def write_script(poster_html: str) -> None:
    plan = {"verdict": "pass", "reasons": [],
            "propose_steps": [
                # the producing step is a developer: Root scopes roles natively and its
                # "designer" is a read-only judgment role (no WriteFile) by design - Skye's
                # catalogue cannot widen it through this adapter. Designer guidance still
                # reaches the worker as role_instructions when the owner registers them.
                {"id": "design", "role": "developer", "adapter": "root",
                 "goal": "design the launch poster", "brief": "Produce poster.html",
                 "gates": [], "budget": {"max_steps": 8}},
                {"id": "check", "role": "tester", "adapter": "root",
                 "goal": "verify the poster exists", "brief": "Read poster.html",
                 "needs": ["design"], "gates": [], "budget": {"max_steps": 8}}],
            "propose_features": []}
    (demo / "script.json").write_text(json.dumps([
        "tool_call: " + json.dumps({"id": "w", "name": "WriteFile",
                                    "arguments": json.dumps({"path": "poster.html",
                                                             "content": poster_html})}),
        "tool_call: " + json.dumps({"id": "r", "name": "ReadFile",
                                    "arguments": json.dumps({"path": "poster.html"})}),
        "text: Poster drafted. " + json.dumps(plan)]))

write_script(_bad)
os.environ["TURA_CODE_SCRIPTED_ECHO"] = str(demo / "script.json")

import uvicorn
from tura_code.app import create_app
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    root_port = probe.getsockname()[1]
root_server = uvicorn.Server(uvicorn.Config(create_app(home), host="127.0.0.1",
                                            port=root_port, log_level="error"))
threading.Thread(target=root_server.run, daemon=True).start()
deadline = time.monotonic() + 30
while not root_server.started and time.monotonic() < deadline:
    time.sleep(0.1)
print("root:", f"http://127.0.0.1:{root_port}")

os.environ["SKYE_PROJECTS"] = str(demo / "skye.projects.json")
os.environ["SKYE_BACKLOG"] = str(demo / "skye.backlog.json")
os.environ["SKYE_ISSUES"] = str(demo / "skye.issues.json")
from skye import backlog, issues, projects
project = projects.add({"name": "Shop", "repo": str(ws)})
(demo / "skye.policy.json").write_text(json.dumps({
    "allow_unattended": [{"adapter": "root", "role": "*"}],
    "test_command": "true",
    "journey_command": JOURNEY_CMD}))
issue, _ = issues.report("user-report", "the launch poster reads like generated slop",
                         detail="beige palette, 9px text, italic serif hero, vague headline",
                         project=project["id"])

state_dir = demo / "state"
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    ui_port = probe.getsockname()[1]
env = dict(os.environ, SKYE_ROOT_URL=f"http://127.0.0.1:{root_port}",
           SKYE_ROOT_POLL_SECONDS="0.5",
           SKYE_ROOT_HOME=str(home), PYTHONUNBUFFERED="1")
ui_proc = subprocess.Popen([sys.executable, "-m", "skye.cli", "ui", "--port", str(ui_port),
                            "--state-dir", str(state_dir)], cwd=demo, env=env)
token_file = state_dir / "ui-token"
deadline = time.monotonic() + 20
while not token_file.exists() and time.monotonic() < deadline:
    assert ui_proc.poll() is None, "UI server exited early"
    time.sleep(0.1)
token = token_file.read_text().strip()
ui_url = f"http://127.0.0.1:{ui_port}/?token={token}"

out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/skye-design-gate")
out_dir.mkdir(parents=True, exist_ok=True)
feature_id = issue["feature_id"]

def settle_current_mission(page) -> str:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        page.wait_for_timeout(500)
        if "/mission/" in page.evaluate("location.hash"):
            break
    assert "/mission/" in page.evaluate("location.hash"), "Start work did not open the mission"
    mission_id = page.evaluate("decodeURIComponent(location.hash.split('/mission/')[1])")
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        page.wait_for_timeout(500)
        detail = page.evaluate(
            "fetch('/api/missions/' + '" + mission_id + "', "
            "{headers: {'X-Skye-Token': '" + token + "'}}).then(r => r.json())")
        if detail["status"]["status"] in ("done", "failed") and not detail["live"].get("running"):
            assert detail["status"]["status"] == "done", detail["status"]
            return mission_id
    raise AssertionError("mission did not settle in time")

from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.on("pageerror", lambda e: stage("PAGEERROR: " + str(e)[:300]))
    # 1. signal -> proposal pending
    page.goto(ui_url + "#/backlog")
    page.wait_for_timeout(1400)
    page.screenshot(path=str(out_dir / "g1-signal-pending.png"), full_page=True)
    stage("g1")
    # 2. owner approves
    page.locator("button", has_text="Approve").first.click()
    page.wait_for_timeout(1200)
    stage("g2 approved")
    # 3. designer's first draft: real anti-patterns
    page.locator("button", has_text="Start work").first.click()
    settle_current_mission(page)
    stage("mission1 done")
    assert (ws / "poster.html").read_text() == _bad
    # rendered evidence of the failing artifact itself
    page.goto("file://" + str(ws / "poster.html"))
    page.wait_for_timeout(400)
    page.screenshot(path=str(out_dir / "g3-poster-failing-render.png"))
    page.goto(ui_url + "#/backlog")
    page.wait_for_timeout(1400)
    page.screenshot(path=str(out_dir / "g3-awaiting-verification.png"), full_page=True)
    stage("g3")
    f = backlog.get(feature_id)
    assert f["status"] == "awaiting-verification", f["status"]
    # 4. replay the SAME gate: FAILS with the detector's real findings as evidence
    page.locator("button", has_text="Replay journey").first.click()
    page.wait_for_timeout(25000)  # npx run; generous but the wait is real
    page.screenshot(path=str(out_dir / "g4-gate-rejected.png"), full_page=True)
    stage("g4")
    f = backlog.get(feature_id)
    assert f["status"] == "awaiting-verification", f["status"]
    ev = f["verification"]["evidence"]
    assert "low-contrast" in ev or "anti-pattern" in ev, ev
    (out_dir / "g4-gate-evidence.txt").write_text(ev)
    print("gate rejection evidence (recorded by Skye):\n" + ev[:600])
    # 5. owner sends it back
    page.locator("button", has_text="Send back").first.click()
    page.wait_for_timeout(1200)
    assert backlog.get(feature_id)["status"] == "approved"
    stage("g5 sent back")
    # 6. rework: designer writes the repaired poster; the gate command is untouched
    write_script(_fixed)
    page.locator("button", has_text="Start work").first.click()
    settle_current_mission(page)
    stage("mission2 done")
    assert (ws / "poster.html").read_text() == _fixed
    page.goto("file://" + str(ws / "poster.html"))
    page.wait_for_timeout(400)
    page.screenshot(path=str(out_dir / "g6-poster-repaired-render.png"))
    page.goto(ui_url + "#/backlog")
    page.wait_for_timeout(1400)
    # 7. replay the SAME UNCHANGED gate: passes -> delivered
    page.locator("button", has_text="Replay journey").first.click()
    page.wait_for_timeout(25000)
    page.screenshot(path=str(out_dir / "g7-gate-passed-delivered.png"), full_page=True)
    stage("g7")
    f = backlog.get(feature_id)
    assert f["status"] == "delivered", f["status"]
    assert f["verification"]["journey_passed"] is True, f["verification"]
    (out_dir / "g7-gate-evidence.txt").write_text(f["verification"]["evidence"])
    print("gate pass evidence (recorded by Skye):\n" + f["verification"]["evidence"][:400])
    browser.close()

ui_proc.terminate()
print("DESIGN GATE JOURNEY OK: signal -> design -> gate rejects (real findings) -> rework "
      "-> same gate passes -> delivered, evidence recorded at every step")
