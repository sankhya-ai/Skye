"""Current-UI screen captures at HEAD: Home, Backlog, Missions, mission detail, Agents,
Settings - real app renders over the real UI server with seeded realistic state (one quick
scripted Root mission so Missions/detail have content). Not mockups."""
import json, os, socket, subprocess, sys, tempfile, threading, time
from pathlib import Path

REPO = Path.cwd()
sys.path.insert(0, str(REPO / "root" / "server"))
sys.path.insert(0, str(REPO))
demo = Path(tempfile.mkdtemp())
ws = demo / "ws"
ws.mkdir()
(ws / "app.py").write_text('def checkout(items):\n    return round(sum(i["price"] * i["qty"] for i in items), 2)\n\ndef cart_badge(count):\n    return str(count)\n')
(ws / "journey.py").write_text(
    'from app import checkout, cart_badge\n'
    'total = checkout([{"price": 10.0, "qty": 2}, {"price": 3.5, "qty": 1}])\n'
    'assert abs(total - 23.5) < 1e-9, f"totals: expected 23.5, got {total}"\n'
    'assert cart_badge(3) == "3", "badge shows 0 with 3 items in the cart"\n'
    'print("JOURNEY OK: checkout 2x10.0 + 1x3.5 = 23.5; badge 3 items")\n')

home = demo / "root-home"
os.environ["TURA_CODE_HOME"] = str(home)
os.environ["TURA_CODE_OFFLINE"] = "1"
for var in ("OPENROUTER_API_KEY", "TURA_CODE_TOKEN"):
    os.environ.pop(var, None)
plan = {"verdict": "pass", "reasons": [],
        "propose_steps": [{"id": "build", "role": "developer", "adapter": "root",
                           "goal": "build the cart widget", "brief": "Implement the cart widget",
                           "gates": [], "budget": {"max_steps": 8}}],
        "propose_features": [{"title": "Cart totals", "brief": "Line-item and grand totals in the cart."}]}
(demo / "script.json").write_text(json.dumps([
    "tool_call: " + json.dumps({"id": "r", "name": "ReadFile",
                                "arguments": json.dumps({"path": "app.py"})}),
    "text: Here is the plan. " + json.dumps(plan)]))
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

os.environ["SKYE_PROJECTS"] = str(demo / "skye.projects.json")
os.environ["SKYE_BACKLOG"] = str(demo / "skye.backlog.json")
os.environ["SKYE_ISSUES"] = str(demo / "skye.issues.json")
from skye import backlog, issues, journey, projects
project = projects.add({"name": "Shop", "repo": str(ws)})
(demo / "skye.policy.json").write_text(json.dumps({
    "allow_unattended": [{"adapter": "root", "role": "*"}],
    "test_command": "true", "journey_command": "python3 journey.py"}))
def real_replay(feature_id: str, mission: str) -> dict:
    """Record a REAL journey replay for the feature: the actual command runs against the
    actual product files - no hand-written evidence strings."""
    backlog.mark_started(feature_id, mission)
    backlog.mark_delivered(mission, journey_ok=False)
    result = journey.run_journey("python3 journey.py", cwd=str(ws))
    return backlog.record_verification(
        feature_id, result["passed"], evidence=result["evidence"],
        repo_revision=journey.repo_revision(str(ws)),
        journey_digest=journey.journey_digest("python3 journey.py", str(ws)))

FIXED_APP = (ws / "app.py").read_text()
# a delivered repair: the real journey replays clean against the fixed product
issue, _ = issues.report("production-log", "checkout totals wrong in production",
                         detail="order 1042 billed 13.5 instead of 23.5", project=project["id"])
fixed = backlog.get(issue["feature_id"])
backlog.decide(fixed["id"], "approved")
recorded = real_replay(fixed["id"], "m-repair")
assert recorded["status"] == "delivered" and "23.5" in recorded["verification"]["evidence"]
# an awaiting-verification feature: replay REALLY FAILS while the badge repair is unshipped
(ws / "app.py").write_text(FIXED_APP.replace('return str(count)', 'return "0"  # repair not shipped'))
pending_repair = backlog.propose(
    [{"title": "Cart badge count", "brief": "Show the item count on the cart icon."}],
    project=project["id"], mission="seed")[0]
backlog.decide(pending_repair["id"], "approved")
recorded = real_replay(pending_repair["id"], "m-badge")
assert recorded["status"] == "awaiting-verification"
assert "badge shows 0" in recorded["verification"]["evidence"], recorded["verification"]
(ws / "app.py").write_text(FIXED_APP)  # the repair is still the team's pending work
# a fresh proposal awaiting the owner + a started mission for the Missions screens
fresh = backlog.propose([{"title": "Shopping cart", "brief": "Add a cart to the shop."}],
                        project=project["id"], mission="mission-seed")[0]

state_dir = demo / "state"
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    ui_port = probe.getsockname()[1]
env = dict(os.environ, SKYE_ROOT_URL=f"http://127.0.0.1:{root_port}",
           SKYE_ROOT_HOME=str(home), SKYE_ROOT_POLL_SECONDS="0.5", PYTHONUNBUFFERED="1")
ui_proc = subprocess.Popen([sys.executable, "-m", "skye.cli", "ui", "--port", str(ui_port),
                            "--state-dir", str(state_dir)], cwd=demo, env=env)
token_file = state_dir / "ui-token"
deadline = time.monotonic() + 20
while not token_file.exists() and time.monotonic() < deadline:
    assert ui_proc.poll() is None
    time.sleep(0.1)
token = token_file.read_text().strip()
ui_url = f"http://127.0.0.1:{ui_port}/?token={token}"
out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/skye-ui-screens")

import urllib.request
def post(path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{ui_port}{path}",
                                 data=json.dumps(body).encode(),
                                 headers={"X-Skye-Token": token,
                                          "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

# owner approves + starts the fresh proposal so Missions/detail have a real run
backlog.decide(fresh["id"], "approved")
started = post("/api/backlog/start", {"feature": fresh["id"], "adapter": "root", "run": True})
mission_id = started["id"]
from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    # open the mission detail first: viewing a not-started mission starts its in-process run
    page.goto(ui_url + f"#/mission/{mission_id}")
    deadline = time.monotonic() + 90
    final = "unknown"
    while time.monotonic() < deadline:
        page.wait_for_timeout(1000)
        detail = json.loads(urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{ui_port}/api/missions/{mission_id}",
            headers={"X-Skye-Token": token}), timeout=30).read())
        final = detail["status"]["status"]
        if final in ("done", "failed") and not detail["live"].get("running"):
            break
    for name, route in [("home", "#/"), ("backlog", "#/backlog"), ("missions", "#/missions"),
                        ("mission-detail", f"#/mission/{mission_id}"), ("agents", "#/agents"),
                        ("settings", "#/settings")]:
        page.goto(ui_url + route)
        page.wait_for_timeout(2200)
        page.screenshot(path=str(out_dir / f"ui-{name}.png"), full_page=True)
    browser.close()
ui_proc.terminate()
print("mission:", mission_id, final)
print("UI SCREENS OK")
