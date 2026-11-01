"""The autonomous-team loop, browser-proven end to end over the real UI server and the real
vendored Root (scripted echo, no provider, no cost):

  proposal -> owner approves in the UI -> Start work -> planner mission runs REAL Root jobs
  -> mission settles done -> the approved feature flips DELIVERED
  -> the settled mission's own propose_features lands as the NEXT pending proposal.

Screenshots at each stage. The models are scripted; every other moving part (journal, waves,
backlog, delivery, UI) is the real engine."""
import json, os, socket, subprocess, sys, tempfile, threading, time
from pathlib import Path

REPO = Path.cwd()
sys.path.insert(0, str(REPO / "root" / "server"))
sys.path.insert(0, str(REPO))
demo = Path(tempfile.mkdtemp())
(demo / "ws").mkdir()
(demo / "ws" / "notes.txt").write_text("the cart feature brief\n")

# --- real Root server, scripted ---
home = demo / "root-home"
os.environ["TURA_CODE_HOME"] = str(home)
os.environ["TURA_CODE_OFFLINE"] = "1"
for var in ("OPENROUTER_API_KEY", "TURA_CODE_TOKEN"):
    os.environ.pop(var, None)
proposal = {"propose_steps": [
    {"id": "build", "role": "developer", "adapter": "root", "goal": "build the cart",
     "brief": "Implement the cart", "gates": [], "budget": {"max_steps": 8}},
    {"id": "check", "role": "tester", "adapter": "root", "goal": "verify the cart",
     "brief": "Verify the cart works", "needs": ["build"], "gates": [],
     "budget": {"max_steps": 8}}],
    "propose_features": [
        {"title": "Cart totals", "brief": "Show line-item totals and a grand total."}]}
script = demo / "script.json"
script.write_text(json.dumps([
    "tool_call: " + json.dumps({"id": "r", "name": "ReadFile",
                                "arguments": json.dumps({"path": "notes.txt"})}),
    # ONE JSON object: Root's pm contract reads verdict+reasons; Skye reads
    # propose_steps and propose_features from the same object.
    "text: Here is the plan. " + json.dumps({"verdict": "pass", "reasons": [], **proposal})]))
os.environ["TURA_CODE_SCRIPTED_ECHO"] = str(script)

import uvicorn
from tura_code.app import create_app
app = create_app(home)
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    root_port = probe.getsockname()[1]
root_server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=root_port, log_level="error"))
threading.Thread(target=root_server.run, daemon=True).start()
deadline = time.monotonic() + 30
while not root_server.started and time.monotonic() < deadline:
    time.sleep(0.1)
root_url = f"http://127.0.0.1:{root_port}"
print("root:", root_url)

# --- project, backlog proposal, policy ---
os.environ["SKYE_PROJECTS"] = str(demo / "skye.projects.json")
os.environ["SKYE_BACKLOG"] = str(demo / "skye.backlog.json")
from skye import backlog, projects
project = projects.add({"name": "Shop", "repo": str(demo / "ws")})
feature = backlog.propose([{"title": "Shopping cart", "brief": "Add a cart to the shop."}],
                          project=project["id"], mission="mission-seed")[0]
(demo / "skye.policy.json").write_text(json.dumps({
    "allow_unattended": [{"adapter": "root", "role": "*"}], "test_command": "true"}))

# --- UI server ---
state_dir = demo / "state"
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    ui_port = probe.getsockname()[1]
env = dict(os.environ, SKYE_ROOT_URL=root_url, SKYE_ROOT_HOME=str(home), PYTHONUNBUFFERED="1")
ui_proc = subprocess.Popen([sys.executable, "-m", "skye.cli", "ui", "--port", str(ui_port),
                            "--state-dir", str(state_dir)], cwd=demo, env=env)
token_file = state_dir / "ui-token"
deadline = time.monotonic() + 20
while not token_file.exists() and time.monotonic() < deadline:
    assert ui_proc.poll() is None, "UI server exited early"
    time.sleep(0.1)
token = token_file.read_text().strip()
ui_url = f"http://127.0.0.1:{ui_port}/?token={token}"

out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/skye-delivered-loop")
out_dir.mkdir(parents=True, exist_ok=True)

from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    # 1. the proposal waits for the owner
    page.goto(ui_url + "#/backlog")
    page.wait_for_timeout(1500)
    page.screenshot(path=str(out_dir / "1-pending.png"), full_page=True)
    # 2. owner approves in the browser
    page.locator("button", has_text="Approve").first.click()
    page.wait_for_timeout(1200)
    page.screenshot(path=str(out_dir / "2-approved.png"), full_page=True)
    # 3. owner starts work -> planner mission runs real Root jobs
    page.locator("button", has_text="Start work").first.click()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        page.wait_for_timeout(500)
        if "/mission/" in page.evaluate("location.hash"):
            break
    assert "/mission/" in page.evaluate("location.hash"), "Start work did not open the mission"
    mission_id = page.evaluate("decodeURIComponent(location.hash.split('/mission/')[1])")
    final = None
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        page.wait_for_timeout(500)
        detail = page.evaluate(
            "fetch('/api/missions/' + '" + mission_id + "', "
            "{headers: {'X-Skye-Token': '" + token + "'}}).then(r => r.json())")
        if detail["status"]["status"] in ("done", "failed") and not detail["live"].get("running"):
            final = detail
            break
    assert final, "mission did not settle in time"
    page.wait_for_timeout(800)
    page.reload()
    page.wait_for_timeout(1200)
    page.screenshot(path=str(out_dir / "3-mission-done.png"), full_page=True)
    # 4. delivered feature + the team's NEXT proposal waiting for the owner
    page.goto(ui_url + "#/backlog")
    page.wait_for_timeout(1500)
    page.screenshot(path=str(out_dir / "4-delivered-next.png"), full_page=True)
    browser.close()
ui_proc.terminate()
assert final["status"]["status"] == "done", final["status"]
features = {f["title"]: f for f in backlog.list_features()}
assert features["Shopping cart"]["status"] == "delivered", features["Shopping cart"]
assert features["Shopping cart"]["mission_id"] == mission_id
assert features["Cart totals"]["status"] == "pending", features["Cart totals"]
assert features["Cart totals"]["proposed_by"] == mission_id
jobs = list((home / "jobs").glob("*.json"))
assert len(jobs) == 3, f"expected 3 real Root jobs, got {len(jobs)}"
print("DELIVERED LOOP OK - approve -> real work -> delivered -> next proposal pending")
