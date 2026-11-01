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
POSTER_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <rect width="1200" height="630" fill="#0b0d10"/>
  <rect x="40" y="40" width="1120" height="550" rx="24" fill="#12161b" stroke="#232a33" stroke-width="2"/>
  <circle cx="240" cy="315" r="120" fill="#1a2129"/>
  <g transform="translate(180,255)" stroke="#7dd3a8" stroke-width="10" fill="none" stroke-linecap="round" stroke-linejoin="round">
    <path d="M0 10 h18 l16 70 h78 l14 -50 h-100"/>
    <circle cx="52" cy="108" r="10" fill="#7dd3a8" stroke="none"/>
    <circle cx="108" cy="108" r="10" fill="#7dd3a8" stroke="none"/>
  </g>
  <text x="430" y="285" font-family="Inter, sans-serif" font-size="56" font-weight="700" fill="#f2f5f7">The cart that keeps</text>
  <text x="430" y="370" font-family="Inter, sans-serif" font-size="56" font-weight="700" fill="#f2f5f7">its own receipts.</text>
  <text x="430" y="440" font-family="Inter, sans-serif" font-size="20" fill="#9aa7b4">Line items, totals and a badge that counts what is there.</text>
  <rect x="430" y="490" width="120" height="44" rx="22" fill="#7dd3a8"/>
  <text x="490" y="520" font-family="Inter, sans-serif" font-size="22" font-weight="600" fill="#0b0d10" text-anchor="middle">Shop</text>
</svg>"""
CART_NOTES_MD = """# Cart launch notes

The **cart widget** keeps its own receipts: line items, totals and a badge count.

## What changed

- added `CartBadge` to the header
- totals computed in `cart.py`
- poster asset under `assets/`

See [the shop README](https://example.invalid/docs) for context.
"""
(demo / "script.json").write_text(json.dumps([
    "tool_call: " + json.dumps({"id": "r", "name": "ReadFile",
                                "arguments": json.dumps({"path": "app.py"})}),
    "tool_call: " + json.dumps({"id": "w", "name": "WriteFile",
                                "arguments": json.dumps({"path": "assets/cart-launch.svg",
                                                         "content": POSTER_SVG})}),
    "tool_call: " + json.dumps({"id": "w2", "name": "WriteFile",
                                "arguments": json.dumps({"path": "docs/cart-launch.md",
                                                         "content": CART_NOTES_MD})}),
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
    # capture the LIVE state mid-run before waiting for the settle: running badge,
    # dispatched step indicator, live journal entries
    # race the run: poll the API and screenshot the moment the mission is actually live
    live_shot = False
    for _ in range(120):
        live_detail = json.loads(urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{ui_port}/api/missions/{mission_id}",
            headers={"X-Skye-Token": token}), timeout=30).read())
        if live_detail["live"].get("running"):
            # give the page's own polling a beat to render the live badge, then capture
            page.wait_for_timeout(1200)
            page.screenshot(path=str(out_dir / "ui-mission-detail-running.png"), full_page=True)
            live_shot = True
            print("LIVE running-state capture: taken")
            break
        page.wait_for_timeout(100)
    if not live_shot:
        print("LIVE running-state capture: mission never reported running during the race")
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
    # An approval-pending state: a proposal started with an adapter the policy does not
    # allow (only root:*). Seeded after the main mission settles so the POLICY RULE, not
    # the concurrency cap, pauses it. The dispatcher's policy gate fires before any
    # adapter process launches, so no model process ever starts.
    approval_feat = backlog.propose(
        [{"title": "Weekly sales report",
          "brief": "Compile this week's sales into a summary report."}],
        project=project["id"])[0]
    backlog.decide(approval_feat["id"], "approved")
    approval_mission = post("/api/backlog/start",
                            {"feature": approval_feat["id"], "adapter": "codex", "run": True})["id"]
    for name, route in [("home", "#/"), ("backlog", "#/backlog"), ("missions", "#/missions"),
                        ("mission-detail", f"#/mission/{mission_id}"), ("agents", "#/agents"),
                        ("settings", "#/settings")]:
        page.goto(ui_url + route)
        page.wait_for_timeout(2200)
        if name == "mission-detail":
            # open the real artifact previews (files the scripted mission actually wrote)
            preview = page.locator("text=Preview").first
            if preview.count():
                preview.click()
                page.wait_for_timeout(1500)
        page.screenshot(path=str(out_dir / f"ui-{name}.png"), full_page=True)
        if name == "mission-detail":
            # markdown artifact: rendered preview with the link shown as text, never navigation
            md_row = page.locator("div", has=page.locator("span.font-mono", has_text="docs/cart-launch.md")).last
            md_preview = md_row.locator("button", has_text="Preview")
            if md_preview.count():
                md_preview.click()
                page.wait_for_timeout(1200)
                rendered = page.locator("text=Cart launch notes").count()
                link_nav = page.locator('a[href="https://example.invalid/docs"]').count()
                print("MARKDOWN preview rendered:", "PASS" if rendered else "FAIL",
                      "| link not navigable:", "PASS" if not link_nav else "FAIL")
                page.screenshot(path=str(out_dir / "ui-artifact-markdown.png"), full_page=True)

            # sidebar layout: full-page captures misplace fixed elements, so verify with
            # real viewport shots - pinned at top before and after scrolling
            vpage = page
            vpage.screenshot(path=str(out_dir / "ui-viewport-top.png"))
            vpage.evaluate("window.scrollTo(0, 600)")
            vpage.wait_for_timeout(400)
            vpage.screenshot(path=str(out_dir / "ui-viewport-scrolled.png"))
            aside_top = vpage.evaluate(
                "document.querySelector('aside').getBoundingClientRect().top")
            print("SIDEBAR pinned while scrolled:", "PASS" if aside_top < 20 else f"FAIL top={aside_top}")
            vpage.evaluate("window.scrollTo(0, 0)")

            # narrow viewport: the product must still fit a small window
            nctx = browser.new_context(viewport={"width": 430, "height": 800})
            npage = nctx.new_page()
            npage.goto(ui_url + "#/missions")
            npage.wait_for_timeout(2500)
            npage.screenshot(path=str(out_dir / "ui-narrow-missions.png"))
            overlap = npage.evaluate(
                "document.querySelector('main').getBoundingClientRect().left")
            print("NARROW main starts after sidebar:", "INFO left=", overlap)
            collapsed_top = npage.evaluate(
                "document.querySelector('main').getBoundingClientRect().top")
            menu = npage.locator("button", has_text="Menu")
            if menu.count():
                menu.click()
                npage.wait_for_timeout(400)
                npage.screenshot(path=str(out_dir / "ui-narrow-expanded.png"))
                expanded_top = npage.evaluate(
                    "document.querySelector('main').getBoundingClientRect().top")
                print("NARROW menu toggle:", "PASS" if expanded_top > collapsed_top else "FAIL",
                      f"main top {collapsed_top}px collapsed, {expanded_top}px expanded")
            else:
                print("NARROW menu toggle: FAIL no Menu button at narrow width")
            nctx.close()
            # approval states: the codex-adapter mission paused at the policy gate
            page.goto(ui_url + f"#/mission/{approval_mission}")
            page.wait_for_timeout(3000)
            page.screenshot(path=str(out_dir / "ui-approval-pending.png"), full_page=True)
            approve = page.locator("button", has_text="Approve this step once")
            if approve.count():
                approve.click()
                page.wait_for_timeout(1500)
                page.screenshot(path=str(out_dir / "ui-approval-approved.png"), full_page=True)
                print("APPROVAL card flow: captured pending + approved")
            else:
                print("APPROVAL card flow: NO approve button found - check seed")
        if name == "backlog":
            # interaction capture + behavior checks: the Reject confirmation dialog
            reject = page.locator("button", has_text="Reject").first
            if reject.count():
                reject.click()
                page.wait_for_timeout(400)
                focused = page.evaluate("document.activeElement?.textContent?.trim()")
                print("DIALOG initial focus:", repr(focused), "PASS" if focused == "Keep it" else "FAIL")
                page.keyboard.press("Tab")
                t1 = page.evaluate("document.activeElement?.textContent?.trim()")
                page.keyboard.press("Tab")
                t2 = page.evaluate("document.activeElement?.textContent?.trim()")
                print("DIALOG tab cycle:", repr(t1), "->", repr(t2),
                      "PASS" if t1 == "Reject" and t2 == "Keep it" else "FAIL")
                page.screenshot(path=str(out_dir / "ui-backlog-reject-dialog.png"))
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
                gone = page.locator('[role="dialog"]').count() == 0
                back = page.evaluate("document.activeElement?.textContent?.trim()")
                print("DIALOG escape closes:", "PASS" if gone else "FAIL",
                      "| focus returned:", repr(back), "PASS" if back == "Reject" else "FAIL")

# error state: abort the next decide; dialog must stay open with the error
                page.route("**/api/backlog/decide", lambda route: route.abort())
                reject2 = page.locator("button", has_text="Reject").first
                reject2.click()
                page.wait_for_timeout(300)
                page.locator('[role="dialog"] button', has_text="Reject").click()
                page.wait_for_timeout(1000)
                still_open = page.locator('[role="dialog"]').count() == 1
                err_shown = page.locator('[role="dialog"] .text-destructive').count() == 1
                print("DIALOG error state:", "PASS" if still_open and err_shown else "FAIL",
                      f"(open={still_open}, error={err_shown})")
                page.screenshot(path=str(out_dir / "ui-backlog-reject-error.png"))
                page.unroute("**/api/backlog/decide")
                page.locator('[role="dialog"] button', has_text="Keep it").click()
                page.wait_for_timeout(400)

# double-submit guard: two fast clicks must produce exactly one decide call
                decide_calls = []
                page.on("request", lambda req: decide_calls.append(req.url)
                        if req.url.endswith("/api/backlog/decide") else None)
                reject.first.click()
                page.wait_for_timeout(400)
                dialog_reject = page.locator('[role="dialog"] button', has_text="Reject")
                dialog_reject.click()
                dialog_reject.click(force=True)
                page.wait_for_timeout(1500)
                print("DIALOG double-submit:", len(decide_calls), "request(s)",
                      "PASS" if len(decide_calls) == 1 else "FAIL")


        if name == "home":
            # reduced motion: animation durations collapse to ~0 under the preference
            ctx = browser.new_context(viewport={"width": 1280, "height": 800},
                                      reduced_motion="reduce")
            rpage = ctx.new_page()
            rpage.goto(ui_url + "#/backlog")
            rpage.wait_for_timeout(1500)
            r = rpage.locator("button", has_text="Reject").first
            if r.count():
                r.click()
                dur = rpage.evaluate(
                    "getComputedStyle(document.querySelector('[role=dialog] > div:last-child')).animationDuration")
                print("DIALOG reduced-motion animation-duration:", dur,
                      "PASS" if dur in ("0.01ms", "1e-05s", "0s", "0.00001s") else "FAIL")
                rpage.screenshot(path=str(out_dir / "ui-backlog-reject-dialog-reduced-motion.png"))
            ctx.close()
    browser.close()

# --- interrupted state: kill the server mid-run, restart, the mission cannot reattach ---
interrupt_started = post("/api/backlog/start",
                         {"feature": approval_feat["id"], "adapter": "root", "run": True})
interrupt_mission = interrupt_started["id"]
time.sleep(0.4)  # the first step is dispatched, not yet settled
old_token = token
ui_proc.terminate()
ui_proc.wait(timeout=10)
env2 = dict(env)
ui_proc = subprocess.Popen([sys.executable, "-m", "skye.cli", "ui", "--port", str(ui_port),
                            "--state-dir", str(state_dir)], cwd=demo, env=env2)
deadline = time.monotonic() + 20
while time.monotonic() < deadline:
    new_tok = (state_dir / "ui-token").read_text().strip()
    if new_tok != old_token:
        token = new_tok
        break
    time.sleep(0.1)
ui_url = f"http://127.0.0.1:{ui_port}/?token={token}"
deadline = time.monotonic() + 20
while ui_proc.poll() is None and time.monotonic() < deadline:
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{ui_port}/api/missions",
            headers={"X-Skye-Token": token}), timeout=2)
        break
    except Exception:
        time.sleep(0.2)
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(ui_url + f"#/mission/{interrupt_mission}")
    page.wait_for_timeout(2500)
    status = json.loads(urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{ui_port}/api/missions/{interrupt_mission}",
        headers={"X-Skye-Token": token}), timeout=30).read())
    st = status["status"]["status"]
    print("INTERRUPTED mission state after kill+restart:", st)
    if st == "interrupted":
        page.screenshot(path=str(out_dir / "ui-mission-interrupted.png"), full_page=True)
        print("INTERRUPTED capture: taken")
    else:
        page.screenshot(path=str(out_dir / "ui-mission-interrupted.png"), full_page=True)
        print("INTERRUPTED capture: taken but state was", st, "- inspect")

    # --- outage states, in honest order ---

    # (a) first-load failure: the app loads but every API call fails. Simulated by
    # aborting /api/ requests while the server is up - a genuinely unreachable
    # server cannot serve the SPA at all, so interception is the honest way to
    # render this state.
    page.route("**/api/**", lambda route: route.abort())
    page.goto("about:blank")
    page.goto(ui_url + "#/missions")
    page.wait_for_timeout(6000)  # one failed refresh cycle + render
    banner = page.locator("text=Can't reach the local server").count()
    skeleton = page.locator('[aria-label="loading missions"]').count()
    unavail = page.locator("text=Missions unavailable").count()
    false_empty = page.locator("text=No missions yet").count()
    side_unavail = page.locator("text=unavailable - retrying").count()
    false_side = page.locator("text=No workspaces yet").count() + page.locator("text=No conversations yet").count()
    print("FIRST-LOAD failure: banner", "PASS" if banner else "FAIL",
          "| skeleton gone", "PASS" if not skeleton else "FAIL",
          "| unavailable state", "PASS" if unavail else "FAIL",
          "| no false empty", "PASS" if not false_empty and not false_side else "FAIL",
          "| sidebar unavailable", "PASS" if side_unavail == 2 else "FAIL")
    page.screenshot(path=str(out_dir / "ui-server-down-first-load.png"), full_page=True)
    page.unroute("**/api/**")

    # (b) app open WITH data when the server dies: stale view + banner
    page.goto(ui_url + "#/missions")
    page.wait_for_timeout(4000)  # successful refresh fills the list
    ui_proc.terminate()
    ui_proc.wait(timeout=10)
    page.wait_for_timeout(5000)  # a refresh cycle fails against the dead server
    banner = page.locator("text=Can't reach the local server").count()
    rows = page.locator("text=Weekly sales report").count()
    print("SERVER-DOWN stale: banner", "PASS" if banner else "FAIL",
          "| last data kept", "PASS" if rows else "FAIL")
    page.screenshot(path=str(out_dir / "ui-server-down.png"), full_page=True)

    # (c) Home composer: submitting during the outage shows a human error, not a hang
    page.goto(ui_url + "#/")
    page.wait_for_timeout(1500)
    page.fill('textarea[placeholder="Describe the goal..."]', "Ship the weekly report")
    page.click('button[aria-label="Start mission"]')
    page.wait_for_timeout(3000)
    composer_err = page.locator("text=Connection lost").count()
    print("COMPOSER outage error:", "PASS" if composer_err else "FAIL")
    page.screenshot(path=str(out_dir / "ui-home-composer-error.png"), full_page=True)

    # (d) Sidebar add-project during the outage: inline error, form stays put
    page.click('[aria-label="New project"]')
    page.fill('input[placeholder="project name"]', "Tmp")
    page.fill('input[placeholder="repo path (must exist)"]', "/tmp")
    page.click('button:has-text("Add project")')
    page.wait_for_timeout(3000)
    add_err = page.locator("text=Connection lost").count()
    print("ADD-PROJECT outage error:", "PASS" if add_err else "FAIL")
    page.screenshot(path=str(out_dir / "ui-sidebar-addproject-error.png"), full_page=True)

    # (e) reconnect: server returns, the banner clears and data comes back
    old_token = token
    ui_proc = subprocess.Popen([sys.executable, "-m", "skye.cli", "ui", "--port", str(ui_port),
                                "--state-dir", str(state_dir)], cwd=demo, env=env)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        new_tok = (state_dir / "ui-token").read_text().strip()
        if new_tok != old_token:
            token = new_tok
            break
        time.sleep(0.1)
    ui_url = f"http://127.0.0.1:{ui_port}/?token={token}"
    deadline = time.monotonic() + 20
    while ui_proc.poll() is None and time.monotonic() < deadline:
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"http://127.0.0.1:{ui_port}/api/missions",
                headers={"X-Skye-Token": token}), timeout=2)
            break
        except Exception:
            time.sleep(0.2)
    page.goto(ui_url + "#/missions")  # fresh load against the revived server
    page.wait_for_timeout(5000)  # at least one successful 3s refresh cycle
    banner = page.locator("text=Can't reach the local server").count()
    rows = page.locator("text=Weekly sales report").count()
    print("RECONNECT banner cleared:", "PASS" if not banner else "FAIL",
          "| data back:", "PASS" if rows else "FAIL")
    page.screenshot(path=str(out_dir / "ui-server-recovered.png"), full_page=True)
    browser.close()

print("mission:", mission_id, final)
print("UI SCREENS OK")
