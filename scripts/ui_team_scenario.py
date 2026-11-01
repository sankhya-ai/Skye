"""The cohesive team scenario, captured live in the real UI: ONE ticket through its whole
persistent lifecycle - propose, approve, mission with a REAL review send-back (round 1
request-changes, repair re-run carrying the feedback, round 2 pass), journey replay,
delivered, reopened with evidence, repair mission, delivered again - while the team
discussion arrives DURING the run, with cursor-reset/reconnect dedupe and a long narrow
thread checked for layout. Workers are scripted at the adapter boundary (free, offline,
labeled); the engine loop, review gate, journal, discussion bus and UI are the real code."""
import json, os, socket, subprocess, sys, tempfile, threading, time
from pathlib import Path

REPO = Path.cwd()
sys.path.insert(0, str(REPO / "root" / "server"))
sys.path.insert(0, str(REPO))
demo = Path(tempfile.mkdtemp())
ws = demo / "ws"
ws.mkdir()
(ws / "app.py").write_text('def checkout(items):\n    return round(sum(i["price"] * i["qty"] for i in items), 2)\n')
(ws / "journey.py").write_text(
    'from app import checkout\n'
    'total = checkout([{"price": 10.0, "qty": 2}, {"price": 3.5, "qty": 1}])\n'
    'assert abs(total - 23.5) < 1e-9, f"totals: expected 23.5, got {total}"\n'
    'print("JOURNEY OK: 23.5")\n')

home = demo / "root-home"
os.environ["TURA_CODE_HOME"] = str(home)
os.environ["TURA_CODE_OFFLINE"] = "1"
for var in ("OPENROUTER_API_KEY", "TURA_CODE_TOKEN"):
    os.environ.pop(var, None)
os.environ["SKYE_PROJECTS"] = str(demo / "skye.projects.json")
os.environ["SKYE_BACKLOG"] = str(demo / "skye.backlog.json")
os.environ["SKYE_ISSUES"] = str(demo / "skye.issues.json")
(demo / "skye.policy.json").write_text(json.dumps({
    "allow_unattended": [{"adapter": "root", "role": "*"}],
    "test_command": "true", "journey_command": "python3 journey.py",
    # room for the scripted round swap: rounds fire ~1s apart, faster than a test poll,
    # so the pass verdict may only land for a LATER round - the loop must survive that
    "max_review_rounds": 4}))

EMPTY_CART_APP = ('def checkout(items):\n'
                  '    return round(sum(i["price"] * i["qty"] for i in items), 2)\n\n'
                  'def empty_cart_message():\n    return "Your cart is empty"\n')
PLAN = {"verdict": "pass", "reasons": [],
        "propose_steps": [{"id": "build", "role": "developer", "adapter": "root",
                           "goal": "add the empty-cart state",
                           "brief": "Implement the empty-cart state in app.py",
                           "gates": ["review"], "budget": {"max_steps": 8}}],
        "propose_features": []}
# One unified script every role replays (the runtime's per-role script keys only apply to
# role-tagged sessions, not gateway jobs): ONE JSON object carrying the plan fields plus a
# nested "review" verdict. The host's shape check needs a single parseable JSON with
# verdict/reasons; Skye's parse_verdict scans every "verdict" string and the LAST wins, so
# the nested review verdict is the one the review gate acts on. The scenario rewrites that
# nested verdict to "pass" the moment the first review round is journaled.
REVIEW_CHANGES = {"verdict": "request-changes", "feedback": ["add an empty-cart test"]}
REVIEW_PASS = {"verdict": "pass"}

def write_scripts(review_verdict):
    reply = dict(PLAN)
    reply["review"] = review_verdict
    (demo / "script.json").write_text(json.dumps([
        "tool_call: " + json.dumps({"id": "w", "name": "WriteFile",
                                    "arguments": json.dumps({"path": "app.py",
                                                             "content": EMPTY_CART_APP})}),
        "text: Work complete. " + json.dumps(reply)]))
write_scripts(REVIEW_CHANGES)
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

from skye import backlog, discuss, projects
project = projects.add({"name": "Shop", "repo": str(ws)})
feature = backlog.propose([{"title": "Empty-cart state",
                            "brief": "Show a friendly message when the cart is empty."}],
                          project=project["id"])[0]
backlog.decide(feature["id"], "approved")

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
out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/skye-team-scenario")
out_dir.mkdir(parents=True, exist_ok=True)

import urllib.request
def get(path):
    return json.loads(urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{ui_port}{path}", headers={"X-Skye-Token": token}), timeout=30).read())
def post(path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{ui_port}{path}",
                                 data=json.dumps(body).encode(),
                                 headers={"X-Skye-Token": token,
                                          "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

def journal(mid):
    p = state_dir / "missions" / mid / "journal.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
def wait_event(mid, event, timeout=90, interval=0.25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(e.get("event") == event for e in journal(mid)):
            return True
        time.sleep(interval)
    return False
def wait_status(mid, want, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if get(f"/api/missions/{mid}")["status"]["status"] == want:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False

# start the real run through the server so the live overlay is the server's own truth
mission_id = post("/api/backlog/start", {"feature": feature["id"], "adapter": "root",
                                         "run": True})["id"]

# The review loop runs rounds ~1s apart, faster than the browser choreography below. A
# watcher thread swaps the scripted verdict to "pass" the instant the first send-back is
# journaled, so the NEXT reviewer process replays the pass - mirroring a repair that lands.
def _verdict_watcher():
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if any(e.get("event") == "review-round" for e in journal(mission_id)):
            write_scripts(REVIEW_PASS)
            return
        time.sleep(0.05)
threading.Thread(target=_verdict_watcher, daemon=True).start()

from playwright.sync_api import sync_playwright
pw = sync_playwright().start()
browser = pw.chromium.launch()
page = browser.new_page(viewport={"width": 1280, "height": 800})
page.goto(ui_url + f"#/mission/{mission_id}")
assert wait_event(mission_id, "dispatched"), "mission never dispatched"
page.wait_for_timeout(1500)
running = page.locator("text=running").count()
print("TEAM live running badge:", "PASS" if running else "FAIL")

# the team talks WHILE the work runs - same discuss API real workers use
discuss.post(state_dir, mission_id, author="developer", kind="note",
             body="Empty-cart state implemented in app.py; running the build now.")
discuss.post(state_dir, mission_id, author="developer", kind="question",
             body="Should the empty state link back to the catalog?")
page.wait_for_timeout(2600)
summary2 = page.locator("text=Team discussion (2)").count()
note = page.locator("text=Empty-cart state implemented").count()
print("TEAM discussion live mid-run:", "PASS" if summary2 and note else "FAIL")
page.screenshot(path=str(out_dir / "ui-team-live.png"), full_page=True)

discuss.post(state_dir, mission_id, author="reviewer", kind="answer",
             body="Yes - link to the catalog. Checking the diff next.")
page.wait_for_timeout(2600)
summary3 = page.locator("text=Team discussion (3)").count()
page.reload()
page.wait_for_timeout(2600)
summary3_after = page.locator("text=Team discussion (3)").count()
print("TEAM live arrival + reload dedupe:", "PASS" if summary3 and summary3_after else "FAIL")

# the review gate REALLY sends the step back: round journaled, repair re-run with feedback
assert wait_event(mission_id, "review-round"), "review round never journaled"
page.goto(ui_url + f"#/mission/{mission_id}")
page.wait_for_timeout(2200)
round_marker = page.locator("text=Review round").count()
feedback = page.locator("text=add an empty-cart test").count()
print("TEAM review send-back in trace:", "PASS" if round_marker and feedback else "FAIL")
page.screenshot(path=str(out_dir / "ui-team-review-round.png"), full_page=True)

# a long thread: contract-change + ack plus working notes, then the narrow layout check
contract = discuss.post(state_dir, mission_id, author="pm", kind="contract-change",
                        body="Scope: the empty state ships without the weekly-report link.",
                        requires_ack=["developer"])
discuss.post(state_dir, mission_id, author="developer", kind="ack",
             body="Acked - no weekly-report link in this change.", ack_of=contract["id"])
for i in range(3):
    discuss.post(state_dir, mission_id, author="developer" if i % 2 == 0 else "reviewer",
                 kind="note",
                 body=(f"Working note {i + 1}: the empty state wraps long catalog names "
                       "across lines without pushing the checkout button off screen, and "
                       "the spacing tokens match the rest of the cart page."))
assert wait_status(mission_id, "done"), "mission never settled done"
settled_done = get(f"/api/missions/{mission_id}")["status"]["status"] == "done"
print("TEAM mission settled done after repair re-review:", "PASS" if settled_done else "FAIL")

nctx = browser.new_context(viewport={"width": 430, "height": 800})
npage = nctx.new_page()
npage.goto(ui_url + f"#/mission/{mission_id}")
npage.wait_for_timeout(2600)
summary_narrow = npage.locator("text=Team discussion (8)").count()
overflow = npage.evaluate(
    "(() => { const m = document.querySelector('main');"
    " return m ? m.scrollWidth - m.clientWidth : -1; })()")
npage.screenshot(path=str(out_dir / "ui-team-discussion-narrow.png"), full_page=True)
print("TEAM narrow long thread:", "PASS" if summary_narrow and overflow == 0
      else f"FAIL summary={summary_narrow} overflow={overflow}")
nctx.close()

# reconnect: the server dies and returns - no duplicated or lost entries
ui_proc.terminate()
ui_proc.wait(timeout=10)
page.wait_for_timeout(5000)  # a full failed poll cycle: the panel must say paused, never empty
paused = page.locator("text=Updates paused").count()
print("TEAM outage note (not false empty):", "PASS" if paused else "FAIL")
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
        get("/api/missions")
        break
    except Exception:
        time.sleep(0.2)
page.goto(ui_url + f"#/mission/{mission_id}")
page.wait_for_timeout(2600)
summary8 = page.locator("text=Team discussion (8)").count()
dup = page.locator("text=Empty-cart state implemented").count()
print("TEAM reconnect dedupe:", "PASS" if summary8 and dup == 1
      else f"FAIL summary8={summary8} dup={dup}")
page.screenshot(path=str(out_dir / "ui-team-reconnect.png"), full_page=True)

# the SAME ticket's persistent lifecycle: replay -> delivered -> reopen -> repair -> delivered
verified = post("/api/backlog/verify", {"feature": feature["id"]})
print("TICKET journey replay passed:", "PASS" if verified.get("verified") else "FAIL")
reopened = backlog.reopen(feature["id"], evidence="Empty state flashes before items load",
                          labels=["ui"], actor="owner")
backlog.add_thread(feature["id"], actor="owner", kind="comment",
                   body="Seen twice on slower connections; repair should gate the render.")
page.goto(ui_url + "#/backlog")
page.wait_for_timeout(2200)
re_badge = page.locator("text=Reopened - needs your decision").count()
page.screenshot(path=str(out_dir / "ui-ticket-lifecycle-reopened.png"), full_page=True)
print("TICKET reopened state:", "PASS" if re_badge else "FAIL")

backlog.decide(feature["id"], "approved")
repair_id = post("/api/backlog/start", {"feature": feature["id"], "adapter": "root",
                                        "run": True})["id"]
assert wait_status(repair_id, "done"), "repair mission never settled done"
verified2 = post("/api/backlog/verify", {"feature": feature["id"]})
print("TICKET repair replay passed:", "PASS" if verified2.get("verified") else "FAIL")
page.goto(ui_url + "#/backlog")
page.wait_for_timeout(2200)
delivered = page.locator("li:has-text('Empty-cart state') >> text=Delivered").count()
reopen_meta = page.locator("text=reopened 1x").count()
runs_meta = page.locator("text=2 runs").count()
replay_meta = page.locator("text=journey replay passed").count()
thread_meta = page.locator("summary", has_text="Thread (").count()
print("TICKET final delivered row:", "PASS" if delivered else "FAIL",
      "| reopened 1x:", "PASS" if reopen_meta else "FAIL",
      "| 2 runs:", "PASS" if runs_meta else "FAIL",
      "| replay passed:", "PASS" if replay_meta else "FAIL",
      "| thread kept:", "PASS" if thread_meta else "FAIL")
page.screenshot(path=str(out_dir / "ui-ticket-lifecycle.png"), full_page=True)

browser.close()
pw.stop()
print("UI TEAM SCENARIO OK")
