"""The repair loop, browser-proven end to end over the real UI server + real vendored Root
(scripted echo, no provider, no cost):

  production signal -> proposal -> owner approves in the UI -> Start work -> the team's first
  repair lands in the real product repo (WriteFile) but is WRONG -> work done ->
  AWAITING-VERIFICATION (done is not delivered) -> owner replays the journey -> FAILS, evidence
  recorded -> owner sends it back -> second repair attempt fixes the product -> replay the SAME
  UNCHANGED journey -> passes -> DELIVERED -> the signal shows resolved only now.

The journey command is set once by the owner and never touched again; what changes between the
failing and passing replay is the PRODUCT, repaired by the team's work. Screenshots per stage.
The models are scripted; every other moving part is the real engine."""
import hashlib, json, os, socket, subprocess, sys, tempfile, threading, time
def stage(msg): print(f"[{time.monotonic():7.1f}] {msg}", flush=True)
from pathlib import Path

REPO = Path.cwd()
sys.path.insert(0, str(REPO / "root" / "server"))
sys.path.insert(0, str(REPO))
demo = Path(tempfile.mkdtemp())
ws = demo / "ws"
ws.mkdir()

# --- the product under repair: a tiny shop whose checkout total ignores quantity ---
(ws / "app.py").write_text(
    'def checkout(items):\n'
    '    # BUG: ignores quantity - every line bills a single unit.\n'
    '    return sum(i["price"] for i in items)\n')
# --- the owner-configured journey: a plain check of the real behavior. NEVER edited below. ---
(ws / "journey.py").write_text(
    'from app import checkout\n'
    'total = checkout([{"price": 10.0, "qty": 2}, {"price": 3.5, "qty": 1}])\n'
    'if abs(total - 23.5) > 1e-9:\n'
    '    print(f"JOURNEY FAIL: checkout journey expected 23.5, product computed {total}")\n'
    '    raise SystemExit(1)\n'
    'print("JOURNEY OK: checkout journey 2x10.0 + 1x3.5 = 23.5")\n')
journey_sha = hashlib.sha256((ws / "journey.py").read_bytes()).hexdigest()
broken_app = (ws / "app.py").read_text()

# --- real Root server, scripted ---
home = demo / "root-home"
os.environ["TURA_CODE_HOME"] = str(home)
os.environ["TURA_CODE_OFFLINE"] = "1"
for var in ("OPENROUTER_API_KEY", "TURA_CODE_TOKEN"):
    os.environ.pop(var, None)

WRONG_FIX = ('def checkout(items):\n'
             '    # attempt 1: counts quantity but adds a flat 5.0 "handling" fee - still wrong.\n'
             '    return sum(i["price"] * i["qty"] for i in items) + 5.0\n')
RIGHT_FIX = ('def checkout(items):\n'
             '    return round(sum(i["price"] * i["qty"] for i in items), 2)\n')

def write_script(fix_source: str) -> None:
    plan = {"verdict": "pass", "reasons": [],
            "propose_steps": [
                {"id": "repair", "role": "developer", "adapter": "root",
                 "goal": "repair checkout totals", "brief": "Fix the checkout math",
                 "gates": [], "budget": {"max_steps": 8}},
                {"id": "check", "role": "tester", "adapter": "root",
                 "goal": "verify the repair", "brief": "Read the repaired file",
                 "needs": ["repair"], "gates": [], "budget": {"max_steps": 8}}],
            "propose_features": []}
    (demo / "script.json").write_text(json.dumps([
        "tool_call: " + json.dumps({"id": "r", "name": "ReadFile",
                                    "arguments": json.dumps({"path": "app.py"})}),
        "tool_call: " + json.dumps({"id": "w", "name": "WriteFile",
                                    "arguments": json.dumps({"path": "app.py",
                                                             "content": fix_source})}),
        "text: Repair done. " + json.dumps(plan)]))

write_script(WRONG_FIX)
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

# --- project, policy, issue signal ---
os.environ["SKYE_PROJECTS"] = str(demo / "skye.projects.json")
os.environ["SKYE_BACKLOG"] = str(demo / "skye.backlog.json")
os.environ["SKYE_ISSUES"] = str(demo / "skye.issues.json")
from skye import backlog, issues, projects
project = projects.add({"name": "Shop", "repo": str(ws)})
(demo / "skye.policy.json").write_text(json.dumps({
    "allow_unattended": [{"adapter": "root", "role": "*"}],
    "test_command": "true",
    "journey_command": "python3 journey.py"}))
issue, _ = issues.report("production-log", "checkout totals wrong in production",
                         detail="order 1042: 2x10.0 + 1x3.5 billed 13.5 instead of 23.5",
                         project=project["id"])

# --- UI server ---
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

out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/skye-journey")
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
    deadline = time.monotonic() + 75
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
    page.on("console", lambda m: stage("CONSOLE-" + m.type + ": " + m.text[:200])
            if m.type == "error" else None)
    # 1. the production signal became a proposal waiting on the owner
    page.goto(ui_url + "#/backlog")
    page.wait_for_timeout(1400)
    page.screenshot(path=str(out_dir / "j1-signal-pending.png"), full_page=True)
    stage("j1")
    # 2. owner approves
    page.locator("button", has_text="Approve").first.click()
    page.wait_for_timeout(1200)
    page.screenshot(path=str(out_dir / "j2-approved.png"), full_page=True)
    stage("j2")
    # 3. first repair attempt: the team writes a STILL-WRONG fix into the product
    page.locator("button", has_text="Start work").first.click()
    stage("mission1 started")
    settle_current_mission(page)
    stage("mission1 done")
    assert (ws / "app.py").read_text() == WRONG_FIX, "the team's first repair did not land"
    page.goto(ui_url + "#/backlog")
    page.wait_for_timeout(1400)
    page.screenshot(path=str(out_dir / "j3-awaiting-verification.png"), full_page=True)
    stage("j3")
    f = backlog.get(feature_id)
    assert f["status"] == "awaiting-verification", f
    # 4. owner replays the UNCHANGED journey against the still-broken product: FAILS
    page.locator("button", has_text="Replay journey").first.click()
    page.wait_for_timeout(2000)
    page.screenshot(path=str(out_dir / "j4-replay-failed.png"), full_page=True)
    stage("j4")
    f = backlog.get(feature_id)
    assert f["status"] == "awaiting-verification", f
    assert "JOURNEY FAIL" in f["verification"]["evidence"], f["verification"]
    fail_evidence = f["verification"]["evidence"]
    # 5. owner sends the work back for another attempt
    page.locator("button", has_text="Send back").first.click()
    page.wait_for_timeout(1200)
    page.screenshot(path=str(out_dir / "j5-sent-back.png"), full_page=True)
    stage("j5")
    assert backlog.get(feature_id)["status"] == "approved"
    # 6. second attempt: the team ships the real fix; the journey command is untouched
    write_script(RIGHT_FIX)
    page.locator("button", has_text="Start work").first.click()
    stage("mission1 started")
    settle_current_mission(page)
    stage("mission1 done")
    assert (ws / "app.py").read_text() == RIGHT_FIX, "the team's second repair did not land"
    assert hashlib.sha256((ws / "journey.py").read_bytes()).hexdigest() == journey_sha, \
        "the journey was weakened - scenario invalid"
    page.goto(ui_url + "#/backlog")
    page.wait_for_timeout(2500)
    stage("backlog state: " + json.dumps(
        [(f["title"], f["status"], bool(f.get("mission_id"))) for f in backlog.list_features()]))
    stage("page buttons: " + page.evaluate(
        "Array.from(document.querySelectorAll('button')).map(b => b.textContent).join(' | ')"))
    page.reload()
    page.wait_for_timeout(2000)
    stage("after reload body: " + page.evaluate("document.body.innerText.slice(0, 400)"))
    stage("api backlog: " + page.evaluate(
        "fetch('/api/backlog', {headers: {'X-Skye-Token': '" + token + "'}})"
        ".then(r => r.text()).then(t => t.slice(0, 300))"))
    page.screenshot(path=str(out_dir / "j6-debug.png"), full_page=True)
    # 7. replay the same journey: passes -> delivered; the signal resolves only now
    page.locator("button", has_text="Replay journey").first.click()
    page.wait_for_timeout(2000)
    page.screenshot(path=str(out_dir / "j6-delivered-resolved.png"), full_page=True)
    stage("j6")
    browser.close()
ui_proc.terminate()

f = backlog.get(feature_id)
assert f["status"] == "delivered", f
assert f["verification"]["journey_passed"] is True
assert "JOURNEY OK" in f["verification"]["evidence"]
assert hashlib.sha256((ws / "journey.py").read_bytes()).hexdigest() == journey_sha
assert (ws / "app.py").read_text() != broken_app  # the PRODUCT changed, not the check
issue_row = [i for i in issues.list_issues() if i["id"] == issue["id"]][0]
assert issue_row["feature_id"] == feature_id
jobs = list((home / "jobs").glob("*.json"))
print(f"fail evidence kept: {fail_evidence.splitlines()[0]!r}")
print(f"pass evidence: {f['verification']['evidence'].splitlines()[0]!r}")
print(f"root jobs run: {len(jobs)}")
print("JOURNEY LOOP OK - broken product -> wrong repair -> replay FAILS (evidence) -> "
      "send back -> real repair -> SAME journey passes -> delivered -> signal resolved")
