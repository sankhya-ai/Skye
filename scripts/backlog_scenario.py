"""Backlog E2E in the real browser over the real UI server: a proposed feature appears in the
Backlog view, the owner approves it there, Start work creates a planner mission under the
feature's project. Proposals here are seeded through the real backlog module (the same intake
missions use); the run verifies the visible owner decision loop, not a mock."""
import os, socket, subprocess, sys, tempfile, time
from pathlib import Path

REPO = Path.cwd()
sys.path.insert(0, str(REPO))
demo = Path(tempfile.mkdtemp())
(demo / "ws").mkdir()
os.environ["SKYE_PROJECTS"] = str(demo / "skye.projects.json")
os.environ["SKYE_BACKLOG"] = str(demo / "skye.backlog.json")
from skye import backlog, projects
project = projects.add({"name": "Skye", "repo": str(demo / "ws")})
backlog.propose([{"title": "Full-text search",
                  "brief": "Search across every project file from the sidebar."}],
                project=project["id"], mission="mission-demo-1")

state_dir = demo / "state"
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    ui_port = probe.getsockname()[1]
env = dict(os.environ, PYTHONUNBUFFERED="1")
ui_proc = subprocess.Popen([sys.executable, "-m", "skye.cli", "ui", "--port", str(ui_port),
                            "--state-dir", str(state_dir)], cwd=demo, env=env)
token_file = state_dir / "ui-token"
deadline = time.monotonic() + 20
while not token_file.exists() and time.monotonic() < deadline:
    assert ui_proc.poll() is None, "UI server exited early"
    time.sleep(0.1)
token = token_file.read_text().strip()
ui_url = f"http://127.0.0.1:{ui_port}/?token={token}"

from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(ui_url + "#/backlog")
    page.wait_for_timeout(1500)
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/skye-backlog-scenario")
    out_dir.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out_dir / "backlog-pending.png"), full_page=True)
    assert page.locator("text=Full-text search").count() == 1
    assert page.locator("text=pending").count() >= 1
    # owner approves in the UI
    page.locator("button", has_text="Approve").first.click()
    page.wait_for_timeout(1500)
    assert page.locator("text=approved").count() >= 1
    page.screenshot(path=str(out_dir / "backlog-approved.png"), full_page=True)
    # owner starts work -> planner mission under the project, navigates to it
    page.locator("button", has_text="Start work").first.click()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        page.wait_for_timeout(500)
        if "/mission/" in page.evaluate("location.hash"):
            break
    page.wait_for_timeout(1000)
    assert "/mission/" in page.evaluate("location.hash"), "Start work did not open the mission"
    mission_id = page.evaluate("decodeURIComponent(location.hash.split('/mission/')[1])")
    detail = page.evaluate(
        "fetch('/api/missions/' + '" + mission_id + "', "
        "{headers: {'X-Skye-Token': '" + token + "'}}).then(r => r.json())")
    page.screenshot(path=str(out_dir / "backlog-mission.png"), full_page=True)
    browser.close()
ui_proc.terminate()
assert "Full-text search" in detail["mission"]["goal"], detail["mission"]["goal"]
assert detail["mission"]["project"] == project["id"]
stored = backlog.list_features()[0]
assert stored["mission_id"] == mission_id, stored
print("BACKLOG E2E OK - propose -> owner approve -> start -> planner mission under project")
