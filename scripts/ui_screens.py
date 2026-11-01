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
home.mkdir(exist_ok=True)
# Fixture authorization, NOT a recorded owner approval (same standing as the grant seeded in
# ui_team_scenario.py): unattended workers may only run owner-approved shell commands, and
# the multi-repo script's docs write goes through the Shell tool (classified
# "run command:mkdir"). This throwaway demo tenant's trust ledger starts promoted for that
# command; it is never presented as evidence of a real owner's approval or the approval flow.
(home / "trust.json").write_text(json.dumps({
    "schema_version": "root.trust_ledger.v1",
    "entries": {"run command:mkdir": {"approvals": 1, "rejections": 0, "promoted": True}}}))
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
    # a real running window: sleep classifies read-tier (auto-allowed, no trust grant) and
    # keeps the dispatch alive long enough for mid-run UI/API state checks - same technique
    # as ui_team_scenario.py's live capture.
    "tool_call: " + json.dumps({"id": "s0", "name": "Shell",
                                "arguments": json.dumps({"command": "sleep 4"})}),
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
# A worker-proposed agent awaiting the owner's promote decision (registry fixture: this is
# the state a mission's propose-agent flow writes; the UI must distinguish it from active).
(demo / "skye.roles.json").write_text(json.dumps({"roles": [
    {"name": "security-auditor", "description": "Reviews changes for common security flaws.",
     "instructions": "Read the diff, list concrete risks, never edit files.",
     "catalogue": {"tools": ["Read", "Grep", "Glob"], "writable": False},
     "status": "proposed", "proposed_by": "reviewer", "source": "worker",
     "extends": "reviewer", "version": 1}
]}, indent=2))
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
            g_live = json.loads(urllib.request.urlopen(urllib.request.Request(
                f"http://127.0.0.1:{ui_port}/api/missions/{mission_id}/graph",
                headers={"X-Skye-Token": token}), timeout=30).read())
            live_states = {n["id"]: n["state"] for n in g_live["nodes"]}
            print("GRAPH route shows the running step mid-run:",
                  "PASS" if "running" in live_states.values() else f"FAIL {live_states}")
            flow_badge = 0
            for _ in range(20):  # the flow polls on its own 3s cadence inside the run window
                flow_badge = page.locator("[data-flow-node]", has_text="running").count()
                if flow_badge:
                    break
                page.wait_for_timeout(400)
            print("FLOW view marks the running node mid-run:",
                  "PASS" if flow_badge else "FAIL")
            try:
                page.locator("[data-flow-card]").screenshot(
                    path=str(out_dir / "ui-flow-running.png"), timeout=5000)
            except Exception:
                pass
            shot(page, out_dir / "ui-mission-detail-running.png")
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
    # --- collaboration fixtures: the tickets view and the mission's team line ---
    # A reopened ticket: delivered work the owner sent back with evidence + labels. The row
    # must carry the whole thread and the reopen count - never a context-free fresh ticket.
    from skye import discuss
    reopen_feat = backlog.propose(
        [{"title": "Checkout totals formatting",
          "brief": "Format totals as currency on the cart page."}],
        project=project["id"])[0]
    backlog.decide(reopen_feat["id"], "approved")
    backlog.mark_started(reopen_feat["id"], "m-fmt")
    backlog.mark_delivered("m-fmt", journey_ok=True)
    backlog.reopen(reopen_feat["id"],
                   evidence="Totals show 3 decimals on narrow screens",
                   labels=["ui"], actor="owner")
    # A second reopened ticket the owner ALREADY re-approved: it lands in pickup_due - the
    # queue a human picks from. The UI lists it as information; nothing auto-starts.
    pickup_feat = backlog.propose(
        [{"title": "Cart badge aria-label",
          "brief": "Screen-reader label for the cart badge."}],
        project=project["id"])[0]
    backlog.decide(pickup_feat["id"], "approved")
    backlog.mark_started(pickup_feat["id"], "m-aria")
    backlog.mark_delivered("m-aria", journey_ok=True)
    backlog.reopen(pickup_feat["id"], evidence="Label reads only 'button'", actor="owner")
    backlog.decide(pickup_feat["id"], "approved")
    # The approval mission's discussion: an acked contract-change (caught up) and an
    # UNACKED one (waiting_on must stay visible until the ack lands).
    discuss.post(state_dir, approval_mission, author="kim", kind="note",
                 body="Sales source confirmed: warehouse export, not the dashboard.")
    contract = discuss.post(state_dir, approval_mission, author="owner",
                            kind="contract-change",
                            body="Scope: weekly only, no monthly rollup.",
                            requires_ack=["kim"])
    discuss.post(state_dir, approval_mission, author="kim", kind="ack",
                 body="Acked - weekly only.", ack_of=contract["id"])
    discuss.post(state_dir, approval_mission, author="owner", kind="contract-change",
                 body="Reviewer sign-off required before delivery.",
                 requires_ack=["reviewer"])
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
        shot(page, out_dir / f"ui-{name}.png")
        if name == "agents":
            proposed = page.locator("text=Needs approval").count()
            extends_badge = page.locator("text=extends senior-dev").count()
            # capability claims were removed: the tool list itself is the truth, and a
            # Bash-bearing role must never be labeled "no Write/Edit tools".
            capability_badges = page.locator("span", has_text="Write/Edit tools").count()
            print("AGENTS proposed-vs-active distinction:", "PASS" if proposed else "FAIL")
            print("AGENTS extends lineage badge:", "PASS" if extends_badge else "FAIL")
            print("AGENTS no contradictory capability badges:",
                  "PASS" if capability_badges == 0 else "FAIL")
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
                shot(page, out_dir / "ui-artifact-markdown.png")

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
            shot(page, out_dir / "ui-approval-pending.png")
            card = page.locator("[data-testid='approval-card']")
            approve = card.locator("button", has_text="Approve this step once")
            if approve.count():
                # keyboard-only flow: summary toggles, Tab reaches approve, ring visible, Enter activates
                summary = card.locator("summary", has_text="What you're approving")
                summary.focus()
                page.keyboard.press("Enter")
                page.wait_for_timeout(200)
                opened = card.locator("details").evaluate("el => el.open")
                print("APPROVAL details keyboard toggle:", "PASS" if opened else "FAIL")
                # Tab walks the card's real order (scrollable brief region is a legitimate stop)
                # until the approve button holds focus.
                reached = False
                for _ in range(4):
                    page.keyboard.press("Tab")
                    page.wait_for_timeout(150)
                    focused = page.evaluate(
                        "document.activeElement && document.activeElement.textContent || ''")
                    if "Approve" in focused:
                        reached = True
                        break
                print("APPROVAL tab reaches approve:", "PASS" if reached else "FAIL")
                ring = page.evaluate(
                    "document.activeElement ? getComputedStyle(document.activeElement).boxShadow : ''")
                print("APPROVAL focus ring:", "PASS" if reached and ring and ring != "none"
                      else f"FAIL boxShadow={ring!r}")
                page.keyboard.press("Enter")
                page.wait_for_timeout(1500)
                shot(page, out_dir / "ui-approval-approved.png")
                settled = card.count() == 0
                print("APPROVAL keyboard activation:", "PASS" if settled else "FAIL card still present")
                # the sidebar list polls on its own 3s cycle: after it lands, an approved
                # (resume-to-continue) mission must read "approved", never "needs you".
                page.wait_for_timeout(2600)
                row_text = page.evaluate(
                    "(() => { const r = [...document.querySelectorAll('aside a')]"
                    ".find(a => a.textContent.includes('Weekly sales report'));"
                    " return r ? r.textContent : ''; })()")
                print("APPROVAL sidebar label after list refresh:", repr(row_text),
                      "PASS" if "approved" in row_text and "needs you" not in row_text else "FAIL")
                print("APPROVAL card flow: captured pending + approved")
            else:
                print("APPROVAL card flow: NO approve button found - check seed")
            # team line: acked + unacked contract-changes render their real ack state
            page.goto(ui_url + f"#/mission/{approval_mission}")
            page.wait_for_timeout(3000)
            panel = page.locator("text=Team discussion").count()
            note = page.locator("text=Sales source confirmed").count()
            acked = page.locator("text=acknowledged").count()
            waiting = page.locator("text=waiting on: reviewer").count()
            print("DISCUSSION panel:", "PASS" if panel else "FAIL",
                  "| note:", "PASS" if note else "FAIL",
                  "| ack rendered:", "PASS" if acked else "FAIL",
                  "| unacked waiting_on:", "PASS" if waiting else "FAIL")
            shot(page, out_dir / "ui-mission-discussion.png")
        if name == "backlog":
            reopened_badge = page.locator("text=Reopened - needs your decision").count()
            reopen_meta = page.locator("text=reopened 1x").count()
            label_chip = page.get_by_text("ui", exact=True).count()
            thread_sum = page.locator("summary", has_text="Thread (").count()
            pickup = page.locator("text=reopened ticket is approved and waiting to start").count()
            print("TICKETS reopened badge:", "PASS" if reopened_badge == 1 else f"FAIL {reopened_badge}",
                  "| reopen count meta x2:", "PASS" if reopen_meta == 2 else f"FAIL {reopen_meta}",
                  "| label chip:", "PASS" if label_chip else "FAIL",
                  "| thread disclosures:", "PASS" if thread_sum >= 2 else f"FAIL {thread_sum}",
                  "| pickup-due info:", "PASS" if pickup else "FAIL")
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
    # SETTINGS automatic pickup: exact recorded state, and unknown is never "off"
    page.goto(ui_url + "#/settings")
    page.wait_for_timeout(1800)
    off_line = page.locator("text=Off - approved tickets wait for you").count()
    print("SETTINGS pickup shows recorded off:", "PASS" if off_line else "FAIL")
    # enable through the REAL scoped endpoint (the contract a future toggle will use)
    post("/api/policy/auto_pickup", {"auto_pickup": {"enabled": True,
                                                     "include_reopened": True,
                                                     "max_missions": 2}})
    policy_data = json.loads((demo / "skye.policy.json").read_text())
    page.reload()
    page.wait_for_timeout(1800)
    on_line = page.locator("text=On - starts approved tickets").count()
    limit_line = page.locator("text=up to 2 at a time").count()
    reopened_line = page.locator("text=Reopened tickets included").count()
    print("SETTINGS pickup shows on with limits:",
          "PASS" if on_line and limit_line and reopened_line else "FAIL")
    shot(page, out_dir / "ui-settings-pickup-on.png")
    # a change lands elsewhere AFTER the form loaded: the whole-policy save must conflict,
    # never silently overwrite it; after an explicit reload the save goes through
    post("/api/policy/auto_pickup", {"auto_pickup": {"enabled": True,
                                                     "include_reopened": True,
                                                     "max_missions": 3}})
    page.locator("button", has_text="Save policy").first.click()
    page.wait_for_timeout(900)
    conflict = page.locator("text=policy changed elsewhere").count()
    still3 = (json.loads((demo / "skye.policy.json").read_text())
              .get("auto_pickup", {}).get("max_missions") == 3)
    print("SETTINGS stale save conflicts, newer change untouched:",
          "PASS" if conflict and still3 else "FAIL")
    shot(page, out_dir / "ui-settings-conflict.png")
    page.reload()
    page.wait_for_timeout(1800)
    page.locator("button", has_text="Save policy").first.click()
    page.wait_for_timeout(900)
    saved = page.locator("text=Saved - the next run uses this policy").count()
    kept = (json.loads((demo / "skye.policy.json").read_text())
            .get("auto_pickup", {}).get("enabled") is True)
    print("SETTINGS save after explicit reload keeps the pickup block:",
          "PASS" if saved and kept else "FAIL")
    # a policy file that cannot be read: pickup state is UNKNOWN, never shown as off
    (demo / "skye.policy.json").write_text("{ not json")
    page.reload()
    page.wait_for_timeout(1800)
    # the scoped endpoint fails CLOSED on the unreadable file: 409, no rewrite
    try:
        post("/api/policy/auto_pickup", {"auto_pickup": {"enabled": False}})
        toggle_409 = False
    except Exception:
        toggle_409 = True
    print("SETTINGS pickup endpoint refuses an unreadable policy (409):",
          "PASS" if toggle_409 else "FAIL")
    unknown_line = page.locator("text=Pickup state unavailable").count()
    off_leak = page.locator("text=Off - approved tickets wait for you").count()
    print("SETTINGS pickup unknown distinct from off:",
          "PASS" if unknown_line and not off_leak else "FAIL")
    # a failed read must not leave a live Save over synthesized defaults, and the banner
    # must read as an explanation, not a raw parser dump
    save_btn = page.locator("button", has_text="Save policy").first
    save_disabled = save_btn.evaluate("el => el.matches(':disabled')") if save_btn.count() else False
    readable = page.locator("text=policy file could not be read").count()
    print("SETTINGS failed read locks the form with a readable banner:",
          "PASS" if save_disabled and readable else "FAIL")
    shot(page, out_dir / "ui-settings-pickup-unavailable.png")
    (demo / "skye.policy.json").write_text(json.dumps(policy_data))
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
        shot(page, out_dir / "ui-mission-interrupted.png")
        print("INTERRUPTED capture: taken")
        again = page.locator("button", has_text="Run this step again")
        print("INTERRUPTED re-run affordance:", "PASS" if again.count() else "FAIL no button")
        # the copy above it must not promise zero side effects
        note = page.locator("text=partially applied").count()
        print("INTERRUPTED partial-effects note:", "PASS" if note else "FAIL missing")
        # exercise the action itself against the scripted runtime: the step really re-runs.
        again.first.click()
        settled = None
        for _ in range(16):
            time.sleep(0.5)
            st2 = json.loads(urllib.request.urlopen(urllib.request.Request(
                f"http://127.0.0.1:{ui_port}/api/missions/{interrupt_mission}",
                headers={"X-Skye-Token": token}), timeout=30).read())
            if st2["status"]["status"] != "interrupted":
                settled = st2
                break
        evs = [(e.get("step"), e.get("event")) for e in (settled or st2)["journal"]]
        rerun_ev = [e for e in (settled or st2)["journal"]
                    if e.get("event") in ("redispatch", "reattached", "dispatched")]
        print("INTERRUPTED re-run leaves interrupted:", "PASS" if settled else "FAIL still interrupted")
        print("INTERRUPTED re-run journaled:", "PASS" if rerun_ev else "FAIL no re-run events",
              "| journal:", evs)
        page.wait_for_timeout(1000)
        shot(page, out_dir / "ui-mission-rerun.png")
    else:
        shot(page, out_dir / "ui-mission-interrupted.png")
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
    shot(page, out_dir / "ui-server-down-first-load.png")
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
    shot(page, out_dir / "ui-server-down.png")

    # (c) Home composer: submitting during the outage shows a human error, not a hang
    page.goto(ui_url + "#/")
    page.wait_for_timeout(1500)
    page.fill('textarea[placeholder="Describe the goal..."]', "Ship the weekly report")
    page.click('button[aria-label="Start mission"]')
    page.wait_for_timeout(3000)
    composer_err = page.locator("text=Connection lost").count()
    print("COMPOSER outage error:", "PASS" if composer_err else "FAIL")
    shot(page, out_dir / "ui-home-composer-error.png")

    # (d) Sidebar add-project during the outage: inline error, form stays put
    page.click('[aria-label="New project"]')
    page.fill('input[placeholder="project name"]', "Tmp")
    page.fill('input[placeholder="repo path (must exist)"]', "/tmp")
    page.click('button:has-text("Add project")')
    page.wait_for_timeout(3000)
    add_err = page.locator("text=Connection lost").count()
    print("ADD-PROJECT outage error:", "PASS" if add_err else "FAIL")
    shot(page, out_dir / "ui-sidebar-addproject-error.png")

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
    shot(page, out_dir / "ui-server-recovered.png")
    browser.close()


# --- lost-worktree recovery: genuine prepare/loss/rebuild records, then the UI review path ---
import shutil
from skye import policy as policy_module
from skye import workspaces as wsm

repo = demo / "recovery-repo"
repo.mkdir()
subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
subprocess.run(["git", "config", "user.email", "suite@example.invalid"], cwd=repo, check=True)
subprocess.run(["git", "config", "user.name", "Suite"], cwd=repo, check=True)
(repo / "app.py").write_text("def total(xs):\n    return sum(xs)\n")
subprocess.run(["git", "add", "."], cwd=repo, check=True)
subprocess.run(["git", "commit", "-qm", "initial"], cwd=repo, check=True)

policy_now = json.loads((demo / "skye.policy.json").read_text())
policy_now["workspaces"] = {"enabled": True}
(demo / "skye.policy.json").write_text(json.dumps(policy_now))

post("/api/missions", {"id": "recovery-mission", "goal": "Recovery probe: rebuild after loss",
                       "repo": str(repo), "run": False,
                       "steps": [{"id": "build", "role": "developer", "adapter": "root",
                                  "goal": "extend the totals module", "gates": [],
                                  "budget": {"max_steps": 4}}]})
mission_dir = state_dir / "missions" / "recovery-mission"
mission_def = json.loads((mission_dir / "mission.json").read_text())
policy_obj = policy_module.Policy.load(demo / "skye.policy.json")
rec1 = wsm.prepare(mission_def, state_dir=state_dir, policy=policy_obj)
wt = Path(rec1["worktree"])
# committed worker output survives on the branch; uncommitted output is what gets lost
(wt / "feature.py").write_text("def feature():\n    return 42\n")
subprocess.run(["git", "add", "."], cwd=wt, check=True)
subprocess.run(["git", "commit", "-qm", "worker: add feature"], cwd=wt, check=True)
(wt / "scratch.py").write_text("# uncommitted scratch\n")
# the lost tree: a raw directory wipe - the stale git worktree registration must be pruned
# by prepare() itself (B2c regression: real loss, not a clean git worktree remove)
shutil.rmtree(wt)
rec2 = wsm.prepare(mission_def, state_dir=state_dir, policy=policy_obj)
print("RECOVERY record marks uncommitted loss:",
      "PASS" if rec2.get("uncommitted_lost") and rec2.get("recovered_from_branch") else "FAIL")
print("RECOVERY rebuilt tree keeps committed work, scratch gone:",
      "PASS" if (Path(rec2["worktree"]) / "feature.py").exists()
      and not (Path(rec2["worktree"]) / "scratch.py").exists() else "FAIL")

post("/api/missions/recovery-mission/resume", {})
deadline = time.monotonic() + 30
pending_ev = []
while time.monotonic() < deadline:
    detail = json.loads(urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{ui_port}/api/missions/recovery-mission",
        headers={"X-Skye-Token": token}), timeout=30).read())
    pending_ev = [e for e in detail["journal"] if e.get("event") == "workspace-recovery-pending"]
    if pending_ev:
        break
    time.sleep(0.5)
print("RECOVERY resume pauses with journaled recovery context:",
      "PASS" if pending_ev else "FAIL")

policy_now.pop("workspaces")
(demo / "skye.policy.json").write_text(json.dumps(policy_now))

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(ui_url + "#/mission/recovery-mission")
    page.wait_for_timeout(2500)
    card = page.locator("text=Workspace recovery needs your review").count()
    loss = page.locator("text=uncommitted worker changes are").count()
    branch = page.locator("text=skye/recovery-mission").count()
    resume_btn = page.locator("button", has_text="Resume").first
    # no blind Resume through ambiguous state: the button is disabled, or withheld entirely
    # when the mission never dispatched (status not-started is not resumable on its own)
    gated = (not resume_btn.count()
             or resume_btn.evaluate("el => el.matches(':disabled')"))
    print("RECOVERY review card with loss warning + branch context:",
          "PASS" if card and loss and branch else "FAIL")
    print("RECOVERY Resume withheld or disabled until acknowledged:", "PASS" if gated else "FAIL")
    shot(page, out_dir / "ui-recovery-review.png")
    page.locator("button", has_text="I reviewed the rebuilt workspace").click()
    page.wait_for_timeout(1200)
    acked_rec = json.loads((mission_dir / "workspace.json").read_text())
    ack_line = page.locator("text=resume when ready").count()
    enabled = (resume_btn.count()
               and resume_btn.evaluate("el => !el.matches(':disabled')"))
    print("RECOVERY acknowledge records who/when + Resume offered:",
          "PASS" if acked_rec.get("recovery_acknowledged") and ack_line and enabled else "FAIL")
    shot(page, out_dir / "ui-recovery-acknowledged.png")
    page.reload()
    page.wait_for_timeout(2500)
    still_pending = page.locator("button", has_text="I reviewed the rebuilt workspace").count()
    resolved = page.locator("text=resume when ready").count()
    print("RECOVERY ack->reload resolves pending (authoritative payload):",
          "PASS" if resolved and not still_pending else "FAIL stale pending after reload")
    browser.close()


# --- multi-repo recovery: two declared repos, one lost worktree, per-repo UI context ---
from skye.mission import load_mission

def _git_repo(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "suite@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Suite"], cwd=path, check=True)
    (path / "README.md").write_text(f"# {path.name}\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=path, check=True)

repo_be = demo / "backend-repo"
repo_fe = demo / "frontend-repo"
_git_repo(repo_be)
_git_repo(repo_fe)

policy_now = json.loads((demo / "skye.policy.json").read_text())
policy_now["workspaces"] = {"enabled": True}
(demo / "skye.policy.json").write_text(json.dumps(policy_now))

multi_spec = demo / "multi-mission.json"
multi_spec.write_text(json.dumps({
    "id": "multi-recovery", "goal": "Multi-repo probe: backend + frontend",
    "repos": {"backend": str(repo_be), "frontend": str(repo_fe)},
    "steps": [{"id": "build", "role": "developer", "adapter": "root",
               "goal": "frontend docs", "repo": "frontend",
               "gates": [], "budget": {"max_steps": 4}},
              {"id": "build-be", "role": "developer", "adapter": "root",
               "goal": "backend docs", "repo": "backend", "needs": ["build"],
               "gates": [], "budget": {"max_steps": 4}}]}))
multi_def = load_mission(multi_spec)  # the real validation/normalization path
# Same relative path in BOTH repos, distinct content per repo: the docs write runs through
# the Shell tool with the worktree cwd, so each repo's file names its own repository. This
# is the identical-path ambiguity case the grouped Changed files review must disambiguate.
multi_script = demo / "multi-script.json"
multi_script.write_text(json.dumps([
    "tool_call: " + json.dumps({"id": "r", "name": "ReadFile",
                                "arguments": json.dumps({"path": "README.md"})}),
    "tool_call: " + json.dumps({"id": "w", "name": "WriteFile",
                                "arguments": json.dumps({"path": "assets/cart-launch.svg",
                                                         "content": POSTER_SVG})}),
    "tool_call: " + json.dumps({"id": "w2", "name": "Shell",
                                "arguments": json.dumps({"command":
                                    "mkdir -p docs && printf '# Cart launch notes\\n\\nRepo: %s\\n' "
                                    '\"$(basename \"$PWD\")\" > docs/cart-launch.md'})}),
    "text: Here is the plan. " + json.dumps(plan)]))
main_script = os.environ["TURA_CODE_SCRIPTED_ECHO"]
os.environ["TURA_CODE_SCRIPTED_ECHO"] = str(multi_script)
multi_dir = state_dir / "missions" / "multi-recovery"
multi_dir.mkdir(parents=True, exist_ok=True)
(multi_dir / "mission.json").write_text(json.dumps(multi_def, indent=2) + "\n")

policy_obj = policy_module.Policy.load(demo / "skye.policy.json")
mrec1 = wsm.prepare(multi_def, state_dir=state_dir, policy=policy_obj)
be_wt = Path(mrec1["repos"]["backend"]["worktree"])
fe_wt = Path(mrec1["repos"]["frontend"]["worktree"])
print("MULTI one worktree per repo:",
      "PASS" if be_wt.is_dir() and fe_wt.is_dir() and be_wt != fe_wt else "FAIL")
(fe_wt / "widget.ts").write_text("export const widget = 1\n")
subprocess.run(["git", "add", "."], cwd=fe_wt, check=True)
subprocess.run(["git", "commit", "-qm", "worker: add widget"], cwd=fe_wt, check=True)
(fe_wt / "scratch.ts").write_text("// uncommitted\n")
subprocess.run(["git", "worktree", "remove", "--force", str(fe_wt)], cwd=repo_fe, check=True)
mrec2 = wsm.prepare(multi_def, state_dir=state_dir, policy=policy_obj)
print("MULTI lost frontend tree flagged, backend intact:",
      "PASS" if mrec2["repos"]["frontend"].get("uncommitted_lost")
      and not mrec2["repos"]["backend"].get("uncommitted_lost") else "FAIL")

post("/api/missions/multi-recovery/resume", {})
deadline = time.monotonic() + 30
mpending = []
while time.monotonic() < deadline:
    detail = json.loads(urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{ui_port}/api/missions/multi-recovery",
        headers={"X-Skye-Token": token}), timeout=30).read())
    mpending = [e for e in detail["journal"] if e.get("event") == "workspace-recovery-pending"]
    if mpending:
        break
    time.sleep(0.5)
print("MULTI any lost tree pauses the mission for the owner:", "PASS" if mpending else "FAIL")

policy_now.pop("workspaces")
(demo / "skye.policy.json").write_text(json.dumps(policy_now))

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(ui_url + "#/mission/multi-recovery")
    page.wait_for_timeout(2000)
    card = page.locator("text=Workspace recovery needs your review").count()
    fe_lost = page.locator("text=frontend:").count() and page.locator(
        "text=uncommitted changes lost").count()
    be_listed = page.locator("text=backend:").count()
    mode = page.locator("text=across 2 repositories").count()
    print("MULTI recovery card names the affected repo with per-repo context:",
          "PASS" if card and fe_lost and be_listed else "FAIL")
    print("MULTI workspace mode surface lists both repositories:",
          "PASS" if mode else "FAIL")
    shot(page, out_dir / "ui-recovery-multi.png")
    page.locator("button", has_text="I reviewed the rebuilt workspace").click()
    page.wait_for_timeout(1200)
    page.reload()
    page.wait_for_timeout(2000)
    still_pending = page.locator("button", has_text="I reviewed the rebuilt workspace").count()
    resolved = page.locator("text=resume when ready").count()
    print("MULTI ack->reload resolves pending:", "PASS" if resolved and not still_pending else "FAIL")
    shot(page, out_dir / "ui-recovery-multi-acked.png")
    page.locator("button", has_text="Resume").first.click()
    deadline = time.monotonic() + 40
    multi_final = ""
    while time.monotonic() < deadline:
        d = json.loads(urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{ui_port}/api/missions/multi-recovery",
            headers={"X-Skye-Token": token}), timeout=30).read())
        multi_final = d["status"]["status"]
        if multi_final in ("done", "failed", "needs-owner", "gate-failed"):
            break
        time.sleep(1)
    os.environ["TURA_CODE_SCRIPTED_ECHO"] = main_script
    fe_docs = (Path(mrec2["repos"]["frontend"]["worktree"]) / "docs" / "cart-launch.md")
    be_docs = (Path(mrec2["repos"]["backend"]["worktree"]) / "docs" / "cart-launch.md")
    fe_txt = fe_docs.read_text() if fe_docs.exists() else ""
    be_txt = be_docs.read_text() if be_docs.exists() else ""
    own_only = ("Repo: frontend" in fe_txt and "backend" not in fe_txt
                and "Repo: backend" in be_txt and "frontend" not in be_txt)
    print("MULTI named-repo steps write the same relative path in their own worktree only:",
          "PASS" if multi_final == "done" and own_only else
          f"FAIL status={multi_final} fe={fe_txt[:40]!r} be={be_txt[:40]!r}")
    art = d.get("artifacts") or {}
    art_repos = art.get("repos") or {}
    per_repo_ok = (set(art_repos) == {"backend", "frontend"}
                   and all("docs/cart-launch.md" in (art_repos[r].get("added") or [])
                           and art_repos[r].get("complete") for r in ("backend", "frontend")))
    print("MULTI artifacts payload carries the same path per repo (artifacts.repos):",
          "PASS" if per_repo_ok else f"FAIL repos={list(art_repos)}")
    page.wait_for_timeout(3500)  # let the detail refresh show the settled run
    shot(page, out_dir / "ui-recovery-multi-resumed.png")
    # Grouped Changed files: each repo's section is captioned with its name and previews
    # that repo's own bytes for the SAME relative path - the ambiguity the flat list hid.
    captions = all(page.locator("div[data-repo=\"" + r + "\"]").count() for r in ("backend", "frontend"))
    rows_ok = all(page.locator("div[data-repo=\"" + r + "\"]")
                  .locator("span.font-mono", has_text="docs/cart-launch.md").count()
                  for r in ("backend", "frontend"))
    print("MULTI Changed files groups the same relative path under repo captions:",
          "PASS" if captions and rows_ok else "FAIL")
    for r in ("backend", "frontend"):
        section = page.locator("div[data-repo=\"" + r + "\"]")
        row = section.locator("div", has=page.locator("span.font-mono",
                                                      has_text="docs/cart-launch.md")).last
        row.locator("button", has_text="Preview").click()
        page.wait_for_timeout(1200)
    be_preview = page.locator("div[data-repo=\"backend\"]").locator("text=Repo: backend").count()
    fe_preview = page.locator("div[data-repo=\"frontend\"]").locator("text=Repo: frontend").count()
    cross = (page.locator("div[data-repo=\"backend\"]").locator("text=Repo: frontend").count()
             + page.locator("div[data-repo=\"frontend\"]").locator("text=Repo: backend").count())
    no_err = not page.locator("text=artifact preview failed").count()
    print("MULTI same-path previews show each repo's own content, never the other's:",
          "PASS" if be_preview and fe_preview and not cross and no_err else "FAIL")
    shot(page, out_dir / "ui-artifacts-multirepo.png")
    # ordinary-viewport scroll proof: the sidebar stays pinned at top/mid/bottom
    vp = browser.new_page(viewport={"width": 1280, "height": 800})
    vp.goto(ui_url + "#/mission/multi-recovery")
    vp.wait_for_timeout(2500)
    pin_ok = True
    for frac, name in ((0.0, "top"), (0.5, "mid"), (1.0, "bottom")):
        vp.evaluate(f"window.scrollTo(0, (document.documentElement.scrollHeight - window.innerHeight) * {frac})")
        vp.wait_for_timeout(400)
        r = vp.locator("aside").evaluate("el => {const b = el.getBoundingClientRect(); return {top: b.top, bottom: b.bottom}}")
        if abs(r["top"] - 8) > 2 or abs(r["bottom"] - 792) > 2:
            pin_ok = False
        vp.screenshot(path=str(out_dir / f"ui-scroll-{name}.png"))
    print("SCROLL sidebar pinned at top/mid/bottom in an ordinary viewport:",
          "PASS" if pin_ok else "FAIL")
    vp.close()
    g = json.loads(urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{ui_port}/api/missions/multi-recovery/graph",
        headers={"X-Skye-Token": token}), timeout=30).read())
    gnodes = {n["id"]: n for n in g["nodes"]}
    g_edge = any(e["from"] == "build" and e["to"] == "build-be" for e in g["edges"])
    g_repos = (gnodes.get("build", {}).get("repo") == "frontend"
               and gnodes.get("build-be", {}).get("repo") == "backend")
    g_states = all(gnodes.get(s, {}).get("state") == "done" for s in ("build", "build-be"))
    print("GRAPH route: real nodes with repo labels/states + the needs edge:",
          "PASS" if g_edge and g_repos and g_states and len(gnodes) == 2 else
          f"FAIL nodes={list(gnodes)} edges={g['edges']}")
    flow_nodes = page.locator("[data-flow-node]").count()
    flow_edge = page.locator("[data-flow-edge='build->build-be']").count()
    flow_repos = (page.locator("[data-flow-node='build']").locator("text=frontend").count()
                  and page.locator("[data-flow-node='build-be']").locator("text=backend").count())
    print("FLOW view: nodes, needs edge and repo chips render from the graph route:",
          "PASS" if flow_nodes == 2 and flow_edge and flow_repos else "FAIL")
    page.locator("[data-flow-node='build-be']").click()
    page.wait_for_timeout(600)
    sel = page.locator("text=backend docs").count() and page.locator("text=runs in backend").count()
    print("FLOW node select shows the step's real goal and repo assignment:",
          "PASS" if sel else "FAIL")
    page.locator("button", has_text="Show execution log").click()
    page.wait_for_timeout(600)
    log_rows = page.locator("li", has_text="settled").count()
    print("FLOW execution log lists redacted journal events:",
          "PASS" if log_rows >= 2 else f"FAIL rows={log_rows}")
    shot(page, out_dir / "ui-flow-multi.png")
    page.locator("[data-flow-card]").screenshot(path=str(out_dir / "ui-flow-multi-crop.png"))
    nctx = browser.new_context(viewport={"width": 430, "height": 800})
    npage = nctx.new_page()
    npage.goto(ui_url + "#/mission/multi-recovery")
    npage.wait_for_timeout(2500)
    overflow = npage.evaluate("document.documentElement.scrollWidth > window.innerWidth")
    ncard = npage.locator("text=Workspace was recovered").count()
    print("MULTI narrow 430px: no horizontal overflow, recovery card legible:",
          "PASS" if ncard and not overflow else "FAIL")
    shot(npage, out_dir / "ui-recovery-multi-narrow.png")
    nctx.close()
    browser.close()


# --- composer multi-repo setup: the owner declares repos in the real UI, never guessed ---
policy_now = json.loads((demo / "skye.policy.json").read_text())
policy_now["workspaces"] = {"enabled": True}
(demo / "skye.policy.json").write_text(json.dumps(policy_now))
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(ui_url + "#/")
    page.wait_for_timeout(2000)
    page.locator('textarea[placeholder="Describe the goal..."]').fill(
        "Composer probe: change backend + frontend together")
    page.locator("text=Multiple repositories").click()
    page.locator("text=+ Add repository").click()
    page.locator("text=+ Add repository").click()
    page.locator('input[placeholder="label (e.g. web)"]').nth(0).fill("backend")
    page.locator('input[placeholder="/absolute/path/to/repo"]').nth(0).fill(str(repo_be))
    page.locator('input[placeholder="label (e.g. web)"]').nth(1).fill("frontend")
    page.locator('input[placeholder="/absolute/path/to/repo"]').nth(1).fill(str(repo_fe))
    # no "Planning runs in" selection: the planner repo auto-defaults to the first declared
    # repo (previously a required manual choice), and step routing is fully automatic.
    shot(page, out_dir / "ui-composer-form.png")
    # validation is real: a relative path is refused before anything is sent
    page.locator('input[placeholder="/absolute/path/to/repo"]').nth(1).fill("relative/path")
    page.locator('button[aria-label="Start mission"]').click()
    refused = page.locator("text=needs an absolute path").count()
    page.locator('input[placeholder="/absolute/path/to/repo"]').nth(1).fill(str(repo_fe))
    print("COMPOSER refuses a non-absolute repo path before submit:", "PASS" if refused else "FAIL")
    # the scripted planner proposes steps already routed to the declared repos, each with a
    # recorded reason - the owner typed a goal and two folders, never an assignment.
    composer_plan = {"verdict": "pass", "reasons": [], "propose_steps": [
        {"id": "api-work", "role": "developer", "adapter": "root",
         "goal": "change the API", "repo": "backend",
         "repo_reason": "the API code lives in backend",
         "gates": [], "budget": {"max_steps": 4}},
        {"id": "web-work", "role": "developer", "adapter": "root",
         "goal": "update the web UI", "repo": "frontend",
         "repo_reason": "the web client lives in frontend",
         "gates": [], "budget": {"max_steps": 4}}]}
    composer_script = demo / "composer-script.json"
    composer_script.write_text(json.dumps([
        "tool_call: " + json.dumps({"id": "r", "name": "ReadFile",
                                    "arguments": json.dumps({"path": "README.md"})}),
        "tool_call: " + json.dumps({"id": "w2", "name": "Shell",
                                    "arguments": json.dumps({"command":
                                        "mkdir -p docs && printf '# Composer notes\\n\\nRepo: %s\\n' "
                                        '\"$(basename \"$PWD\")\" > docs/composer.md'})}),
        "text: Here is the plan. " + json.dumps(composer_plan)]))
    os.environ["TURA_CODE_SCRIPTED_ECHO"] = str(composer_script)
    page.locator('button[aria-label="Start mission"]').click()
    page.wait_for_timeout(3000)
    page.wait_for_selector("text=repositories", timeout=15000)
    mode = page.locator("text=separate Git worktrees across 2 repositories").count()
    names = page.locator("text=frontend").count() and page.locator("text=backend").count()
    print("COMPOSER multi-repo mission runs with per-repo worktrees shown:",
          "PASS" if mode and names else "FAIL")
    shot(page, out_dir / "ui-composer-multirepo.png")
    composer_id = page.url.rsplit("/", 1)[-1]
    deadline = time.monotonic() + 60
    cg = {}
    while time.monotonic() < deadline:
        cg = json.loads(urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{ui_port}/api/missions/{composer_id}/graph",
            headers={"X-Skye-Token": token}), timeout=30).read())
        if cg.get("meta", {}).get("status") in ("done", "failed", "needs-owner", "gate-failed"):
            break
        time.sleep(1)
    os.environ["TURA_CODE_SCRIPTED_ECHO"] = main_script
    proposed = {n["id"]: n for n in cg.get("nodes", []) if n.get("origin") == "proposed"}
    routed = (proposed.get("api-work", {}).get("repo") == "backend"
              and proposed.get("web-work", {}).get("repo") == "frontend"
              and all(proposed.get(s, {}).get("repo_reason") for s in ("api-work", "web-work"))
              and all(proposed.get(s, {}).get("state") == "done" for s in ("api-work", "web-work")))
    print("COMPOSER auto-routing: steps land in the right repos with recorded reasons:",
          "PASS" if cg.get("meta", {}).get("status") == "done" and routed else
          f"FAIL status={cg.get('meta', {}).get('status')} proposed={ {k: (v.get('repo'), v.get('state')) for k, v in proposed.items()} }")
    page.wait_for_timeout(1500)
    page.locator("[data-flow-node='api-work']").click()
    page.wait_for_timeout(600)
    why = page.locator("text=Why this repo: the API code lives in backend").count()
    prop = page.locator("text=Proposed by").count()
    print("FLOW shows the routed step's origin and repo reason:",
          "PASS" if why and prop else "FAIL")
    page.locator("[data-flow-card]").screenshot(path=str(out_dir / "ui-composer-routed.png"))
    page.wait_for_timeout(6000)  # two 3s poll cycles: the sidebar must catch up with done
    stale_running = page.locator("aside >> text=running").count()
    print("SIDEBAR consistent once the mission settles (no stale running):",
          "PASS" if not stale_running else "FAIL")
    # blocked state in the workflow view: the policy-paused codex mission waits on the owner
    page.goto(ui_url + f"#/mission/{approval_mission}")
    page.wait_for_timeout(2500)
    blocked_badge = page.locator("[data-flow-node]", has_text="needs you").count()
    print("FLOW view shows the policy-blocked step as needing the owner:",
          "PASS" if blocked_badge else "FAIL")
    try:
        page.locator("[data-flow-card]").screenshot(
            path=str(out_dir / "ui-flow-blocked.png"), timeout=5000)
    except Exception:
        pass
    # adapter availability in Settings: truthful installed/not-installed from the engine probe
    page.goto(ui_url + "#/settings")
    page.wait_for_timeout(2000)
    codex_line = page.locator("li", has_text="codex").first
    codex_unavail = codex_line.count() and "not available" in codex_line.inner_text()
    honest_auth = page.locator("text=only exercised by a real run").count()
    print("SETTINGS adapter availability: codex reported not available (no codex binary here):",
          "PASS" if codex_unavail else "FAIL")
    print("SETTINGS availability disclaims auth probing:",
          "PASS" if honest_auth else "FAIL")
    shot(page, out_dir / "ui-settings-adapters.png")
    browser.close()
policy_now.pop("workspaces")
(demo / "skye.policy.json").write_text(json.dumps(policy_now))

print("mission:", mission_id, final)
print("UI SCREENS OK")
