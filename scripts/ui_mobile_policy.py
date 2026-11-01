"""Mobile (390px) flow pass + SKYE_POLICY override regression - standalone: no worker
dispatches needed (the policy gate pauses before any dispatch). Real servers, real DOM."""
import json, os, socket, subprocess, sys, tempfile, time
from pathlib import Path

REPO = Path.cwd()
sys.path.insert(0, str(REPO))
demo = Path(tempfile.mkdtemp())
out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/skye-ui-mobile")
out_dir.mkdir(parents=True, exist_ok=True)
os.environ["SKYE_PROJECTS"] = str(demo / "skye.projects.json")
os.environ["SKYE_BACKLOG"] = str(demo / "skye.backlog.json")
os.environ["SKYE_ISSUES"] = str(demo / "skye.issues.json")

# the cwd policy ALLOWS root unattended; the override file does NOT - the regression is
# that a server must honor SKYE_POLICY over the cwd default (the original bug read cwd).
(demo / "skye.policy.json").write_text(json.dumps({
    "allow_unattended": [{"adapter": "root", "role": "*"}]}))
(demo / "policy-override.json").write_text(json.dumps({}))

import urllib.request

def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]

def boot(state_dir: Path, extra_env: dict | None = None) -> tuple:
    port = free_port()
    env = dict(os.environ, PYTHONUNBUFFERED="1", **(extra_env or {}))
    proc = subprocess.Popen([sys.executable, "-m", "skye.cli", "ui", "--port", str(port),
                             "--state-dir", str(state_dir)], cwd=demo, env=env)
    token_file = state_dir / "ui-token"
    deadline = time.monotonic() + 20
    while not token_file.exists() and time.monotonic() < deadline:
        assert proc.poll() is None
        time.sleep(0.1)
    return proc, port, token_file.read_text().strip()

def get(port: int, token: str, path: str):
    return json.loads(urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", headers={"X-Skye-Token": token}), timeout=30).read())

def post(port: int, token: str, path: str, body: dict):
    return json.loads(urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
        headers={"X-Skye-Token": token, "Content-Type": "application/json"}), timeout=30).read())

state1 = demo / "state1"
p1, port1, tok1 = boot(state1)
state2 = demo / "state2"
p2, port2, tok2 = boot(state2, {"SKYE_POLICY": str(demo / "policy-override.json")})

# --- SKYE_POLICY override regression ---
pol1 = get(port1, tok1, "/api/policy")["raw"]
pol2 = get(port2, tok2, "/api/policy")["raw"]
print("OVERRIDE servers read different policy files:",
      "PASS" if pol1.get("allow_unattended") and not pol2.get("allow_unattended") else "FAIL",
      f"(cwd={bool(pol1.get('allow_unattended'))} override={bool(pol2.get('allow_unattended'))})")
made = post(port2, tok2, "/api/missions",
            {"id": "override-gated", "goal": "Gated probe: policy override must pause this",
             "repo": str(demo), "run": False,
             "steps": [{"id": "build", "role": "developer", "adapter": "root",
                        "goal": "anything", "gates": [], "budget": {"max_steps": 2}}]})
post(port2, tok2, "/api/missions/override-gated/resume", {})
deadline = time.monotonic() + 25
paused = ""
while time.monotonic() < deadline:
    d = get(port2, tok2, "/api/missions/override-gated")
    if d["status"]["status"] == "needs-owner":
        paused = (d["status"]["steps"][0].get("reason") or "")
        break
    time.sleep(0.5)
print("OVERRIDE resume honors SKYE_POLICY (pauses for approval the cwd policy would skip):",
      "PASS" if paused else "FAIL", f"- {paused[:80]}")

# --- mobile pass: long data, 390px, every main route ---
LONG_GOAL = ("Ship the full checkout revamp across backend and frontend repositories with "
             "cart totals, discount codes, gift receipts, partial refunds, and a very long "
             "descriptive goal that keeps going so the layout has to wrap it properly "
             "without breaking the page on a small phone screen")
post(port1, tok1, "/api/missions",
     {"id": "long-mobile", "goal": LONG_GOAL, "repo": str(demo), "run": False,
      "steps": [{"id": "build", "role": "developer", "adapter": "root",
                 "goal": "long data probe", "gates": [], "budget": {"max_steps": 2}}]})

from playwright.sync_api import sync_playwright
def shot(page, path):
    """Full-content capture that renders FIXED chrome (the sidebar) where the user actually
    sees it. Playwright's full_page=True stitches scrolled segments and drops fixed elements
    mid-page; expanding the viewport to the content height lays the page out as a tall
    window instead, so top/bottom-pinned chrome pins correctly. Width is preserved."""
    size = page.viewport_size or {"width": 1280, "height": 800}
    h = page.evaluate("Math.max(document.documentElement.scrollHeight,"
                      " document.body ? document.body.scrollHeight : 0, window.innerHeight)")
    page.set_viewport_size({"width": size["width"], "height": max(400, min(int(h), 3000))})
    page.wait_for_timeout(250)
    page.screenshot(path=str(path))

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport={"width": 390, "height": 844})  # iPhone-class
    page = ctx.new_page()
    base = f"http://127.0.0.1:{port1}/?token={tok1}"
    for name, route in [("home", "#/"), ("missions", "#/missions"),
                        ("detail", "#/mission/long-mobile"), ("backlog", "#/backlog"),
                        ("settings", "#/settings")]:
        page.goto(base + route)
        page.wait_for_timeout(1800)
        overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        print(f"MOBILE {name} 390px no horizontal overflow:", "PASS" if not overflow else "FAIL")
        if name in ("missions", "detail", "settings", "home"):
            
            shot(page, out_dir / f"ui-mobile-{name}.png")
    long_visible = page.locator("text=Policy").count()
    browser.close()

p1.terminate(); p2.terminate()
print("MOBILE/POLICY OK")
