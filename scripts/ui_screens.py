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
(ws / "app.py").write_text('def checkout(items):\n    return round(sum(i["price"] * i["qty"] for i in items), 2)\n')
(ws / "journey.py").write_text('from app import checkout\nassert checkout([{"price": 10.0, "qty": 2}]) == 20.0\nprint("JOURNEY OK")\n')

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
# a delivered repair with bound verification evidence
issue, _ = issues.report("production-log", "checkout totals wrong in production",
                         detail="order 1042 billed 13.5 instead of 23.5", project=project["id"])
fixed = backlog.get(issue["feature_id"])
backlog.decide(fixed["id"], "approved")
backlog.mark_started(fixed["id"], "m-repair")
backlog.mark_delivered("m-repair", journey_ok=False)
backlog.record_verification(fixed["id"], True, evidence="JOURNEY OK: checkout 2x10.0 = 20.0",
                            repo_revision=journey.repo_revision(str(ws)),
                            journey_digest=journey.journey_digest("python3 journey.py", str(ws)))
# an awaiting-verification feature with failing evidence
pending_repair = backlog.propose(
    [{"title": "Cart badge count", "brief": "Show the item count on the cart icon."}],
    project=project["id"], mission="seed")[0]
backlog.decide(pending_repair["id"], "approved")
backlog.mark_started(pending_repair["id"], "m-badge")
backlog.mark_delivered("m-badge", journey_ok=False)
backlog.record_verification(pending_repair["id"], False,
                            evidence="JOURNEY FAIL: badge shows 0 with 3 items in the cart",
                            repo_revision=journey.repo_revision(str(ws)),
                            journey_digest=journey.journey_digest("python3 journey.py", str(ws)))
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
