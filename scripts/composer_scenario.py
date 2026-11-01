"""Browser composer E2E, honest stack: real vendored Root server (scripted echo, no provider,
no cost). The UI composer submits a goal -> planner Root job proposes the workflow at runtime
-> build/check Root jobs run -> mission settles done. Adapter labels read 'root', the truth."""
import json, os, socket, subprocess, sys, tempfile, threading, time
from pathlib import Path

REPO = Path.cwd()
sys.path.insert(0, str(REPO / "root" / "server"))
sys.path.insert(0, str(REPO))

demo = Path(tempfile.mkdtemp())
(demo / "ws").mkdir()

# --- real Root server, scripted ---
home = demo / "root-home"
os.environ["TURA_CODE_HOME"] = str(home)
os.environ["TURA_CODE_OFFLINE"] = "1"
for var in ("OPENROUTER_API_KEY", "TURA_CODE_TOKEN"):
    os.environ.pop(var, None)
proposal = {"propose_steps": [
    {"id": "build", "role": "developer", "adapter": "root", "goal": "write hello.py",
     "brief": "Write hello.py printing hello from skye", "gates": [], "budget": {"max_steps": 8}},
    {"id": "check", "role": "tester", "adapter": "root", "goal": "verify hello.py",
     "brief": "Check hello.py exists and mentions skye", "needs": ["build"], "gates": [],
     "budget": {"max_steps": 8}}]}
script = demo / "script.json"
(demo / "ws" / "notes.txt").write_text("the hello page brief\n")
# one script replayed per job; every role (pm included) has Read, so no job exceeds its scope
script.write_text(json.dumps([
    "tool_call: " + json.dumps({"id": "r", "name": "ReadFile",
                                "arguments": json.dumps({"path": "notes.txt"})}),
    # ONE JSON object: Root's pm contract reads verdict+reasons; Skye reads propose_steps from it
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

# --- project + policy the UI server will use ---
os.environ["SKYE_PROJECTS"] = str(demo / "skye.projects.json")
os.environ["SKYE_ROLES"] = str(demo / "skye.roles.json")
from skye import projects
projects.add({"name": "Skye", "repo": str(demo / "ws")})
(demo / "skye.policy.json").write_text(json.dumps({
    "allow_unattended": [{"adapter": "root", "role": "*"}], "test_command": "true"}))

# --- UI server subprocess (its cwd carries the policy) ---
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
ui_url = f"http://127.0.0.1:{ui_port}/?token={token_file.read_text().strip()}"
print("ui:", ui_url)

from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(ui_url)
    page.wait_for_timeout(1000)
    page.fill("textarea", "build and verify a hello page")
    page.select_option("select", label="Skye")
    page.click('button[aria-label="Start mission"]')
    # composer navigates to the mission conversation
    page.wait_for_url("**/mission/**", timeout=15000)
    token = ui_url.split("token=")[1]
    final = None
    for _ in range(240):
        page.wait_for_timeout(500)
        detail = page.evaluate(
            "fetch('/api/missions/' + decodeURIComponent(location.hash.split('/mission/')[1]), "
            "{headers: {'X-Skye-Token': '" + token + "'}}).then(r => r.json())")
        if detail["status"]["status"] in ("done", "failed") and not detail["live"].get("running"):
            final = detail
            break
    page.wait_for_timeout(800)  # let the UI's own poll render the settled state
    page.reload()
    page.wait_for_timeout(1200)
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/skye-composer-scenario")
    out_dir.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out_dir / "composer-settled.png"), full_page=True)
    browser.close()
ui_proc.terminate()
assert final, "mission did not settle in time"
print("overall:", final["status"]["status"])
print("steps:", [(s["id"], s["state"], s.get("adapter")) for s in final["status"]["steps"]])
assert final["status"]["status"] == "done", final["status"]
jobs = list((home / "jobs").glob("*.json"))
assert len(jobs) == 3, f"expected 3 real Root jobs, got {len(jobs)}"
print("E2E OK - real Root jobs, planner-proposed workflow, settled")
