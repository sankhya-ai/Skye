"""The cohesive team scenario, captured live in the real UI: ONE ticket through its whole
persistent lifecycle - propose, approve, mission with a REAL review send-back (round 1
request-changes journaled with feedback, repair re-run, round 2 pass), journey replay,
delivered, reopened with evidence, repair mission, delivered again.

The team-line posts come from the WORKERS THEMSELVES, mid-dispatch: the build and repair
dispatches run team_line.py through a Shell tool call, which reads the mission journal and
the team line via the real `skye hear` CLI and reacts through `skye say`/`skye ack` - the
same cross-process shape as a Claude Code Bash call (see tests/test_teamwork.py). What each
dispatch posts is decided by what it HEARS, not by the scenario script: the build dispatch
posts a contract-change; the repair dispatch (a second worker process) hears it and acks
it during its own run.

Real product boundaries this respects, stated plainly:
- reviewer sessions are read-only (no Shell/WriteFile tools), so the reviewer never posts
  to the team line; the line is carried by the two developer dispatches;
- unattended workers may only run commands the owner has approved, so the scenario
  writes a promotion for `python3` script runs into the throwaway demo tenant's trust
  ledger, acting AS that fixture tenant's owner through the real approval machinery
  (tura_code.trust). It is fixture authorization, never presented as a recorded real-owner
  approval or as evidence about the approval flow itself;
- the review verdict is INJECTED mechanism scaffolding (the nested verdict JSON is swapped
  to "pass" by a watcher when round 1 lands). This verifies UI + engine + team-line
  plumbing, never that a real model independently reviews or coordinates. Workers are the
  scripted echo at the adapter boundary; real Claude Code/Codex runs remain unverified.
"""
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
home.mkdir(exist_ok=True)
# Fixture authorization, NOT a recorded owner approval: unattended workers may only run
# owner-approved commands, so this throwaway demo tenant's trust ledger starts with a
# promotion for python3 script runs. The scenario writes it AS the demo tenant's owner
# (fixture setup through the real approval machinery, tura_code.trust); it is never
# presented as evidence of a real owner's approvals or of the approval flow itself.
(home / "trust.json").write_text(json.dumps({
    "schema_version": "root.trust_ledger.v1",
    "entries": {"run command:python3": {"approvals": 3, "rejections": 0, "promoted": True}}}))
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
# INJECTED verdicts (mechanism scaffolding): the reviewer process replays whichever nested
# verdict is in the script file at launch; the watcher swaps request-changes -> pass.
REVIEW_CHANGES = {"verdict": "request-changes", "feedback": ["add an empty-cart test"]}
REVIEW_PASS = {"verdict": "pass"}

# The worker-side team-line program every dispatch runs mid-flight. It discovers the active
# mission from the journal, then acts on what it HEARS on the line (state machine: first
# developer dispatch notes progress; the reviewer posts the contract-change; the repair
# dispatch acks it mid-repair; the re-review closes it out). All through the real CLI.
TEAM_LINE_PY = r'''
import json, os, subprocess, sys, fcntl
from pathlib import Path

STATE = "__STATE__"
sys.path.insert(0, "__REPO__")

def cli(*args):
    env = dict(os.environ, SKYE_STATE_DIR=STATE, PYTHONPATH="__REPO__")
    proc = subprocess.run([sys.executable, "-m", "skye.cli", *args],
                          capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise SystemExit(f"skye {args[0]} failed: {proc.stderr.strip()}")
    return proc.stdout

def entries_of(d):
    p = d / "journal.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []

active = None
for d in sorted((Path(STATE) / "missions").iterdir(), key=lambda p: p.stat().st_mtime):
    entries = entries_of(d)
    settled = {e.get("step") for e in entries if e.get("event") == "settled"}
    if any(e.get("event") == "worker-job" and e.get("step") not in settled for e in entries):
        active = (d.name, entries)
if not active:
    raise SystemExit(0)
mid, journal = active
# the plan step stays silent on the line: it runs before any proposed step exists
if not any(e.get("event") == "step-accepted" for e in journal):
    raise SystemExit(0)

# serialize check-then-post across concurrent worker processes
lock = open(Path(STATE) / "team.lock", "a+")
fcntl.flock(lock, fcntl.LOCK_EX)
heard = json.loads(cli("hear", mid, "--since", "0"))["entries"]
changes = [e for e in heard if e["kind"] == "contract-change"]
if not changes:
    # the build dispatch, mid-run: progress plus the contract the repair must ack
    cli("say", mid, "--as", "developer", "--kind", "note",
        "Empty-cart state implemented in app.py; the build is running and the diff is small "
        "enough to eyeball while the review comes back.")
    cli("say", mid, "--as", "developer", "--kind", "contract-change",
        "--ack-needed", "developer",
        "Contract for any repair pass: extend the empty-cart helper in place rather than "
        "reworking the layout; the review finding should land there.")
else:
    change = changes[0]
    acked = {(e.get("ack_of"), e.get("author")) for e in heard if e["kind"] == "ack"}
    if (change["id"], "developer") not in acked:
        # the repair dispatch - a second worker process - heard the change and reacts now
        cli("ack", mid, change["id"], "--as", "developer",
            "--note", "Heard mid-repair; extending the same empty-cart helper in this pass.")
        cli("say", mid, "--as", "developer", "--kind", "note",
            "Working the review finding into the repair now; no layout changes needed.")
print("team line ok")
'''

def write_scripts(review_verdict, slow):
    reply = dict(PLAN)
    reply["review"] = review_verdict
    team_line = TEAM_LINE_PY.replace("__STATE__", str(state_dir)).replace("__REPO__", str(REPO))
    lines = [
        "tool_call: " + json.dumps({"id": "w", "name": "WriteFile",
                                    "arguments": json.dumps({"path": "app.py",
                                                             "content": EMPTY_CART_APP})}),
        "tool_call: " + json.dumps({"id": "t", "name": "WriteFile",
                                    "arguments": json.dumps({"path": "team_line.py",
                                                             "content": team_line})})]
    if slow:
        lines.append("tool_call: " + json.dumps({"id": "s", "name": "Shell",
                                                 "arguments": json.dumps({"command": "sleep 6"})}))
    # plain relative script run: no env-prefixed absolute paths (the workspace fence scans
    # the command text), approved via the seeded owner trust grant for python3 script runs
    lines.append("tool_call: " + json.dumps({
        "id": "l", "name": "Shell",
        "arguments": json.dumps({"command": "python3 team_line.py"})}))
    if slow:
        # keep the dispatch genuinely running after the post so the live capture lands
        # inside a real running window, not a finished one
        lines.append("tool_call: " + json.dumps({"id": "s2", "name": "Shell",
                                                 "arguments": json.dumps({"command": "sleep 8"})}))
    lines.append("text: Work complete. " + json.dumps(reply))
    (demo / "script.json").write_text(json.dumps(lines))

from skye import backlog, projects
project = projects.add({"name": "Shop", "repo": str(ws)})
feature = backlog.propose([{"title": "Empty-cart state",
                            "brief": "Show a friendly message when the cart is empty."}],
                          project=project["id"])[0]
backlog.decide(feature["id"], "approved")

state_dir = demo / "state"
write_scripts(REVIEW_CHANGES, slow=True)
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

mission_id = post("/api/backlog/start", {"feature": feature["id"], "adapter": "root",
                                         "run": True})["id"]

# verdict injection scaffolding: swap the nested review verdict to "pass" the instant the
# first send-back is journaled, so the NEXT reviewer process replays pass
def _verdict_watcher():
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if any(e.get("event") == "review-round" for e in journal(mission_id)):
            write_scripts(REVIEW_PASS, slow=False)
            return
        time.sleep(0.05)
threading.Thread(target=_verdict_watcher, daemon=True).start()

from playwright.sync_api import sync_playwright
pw = sync_playwright().start()
browser = pw.chromium.launch()
page = browser.new_page(viewport={"width": 1280, "height": 800})
page.goto(ui_url + f"#/mission/{mission_id}")
assert wait_event(mission_id, "dispatched"), "mission never dispatched"

def wait_text(selector, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if page.locator(selector).count():
            return True
        page.wait_for_timeout(500)
    return False

# LIVE evidence: the worker's own mid-dispatch posts while the mission is genuinely running
assert wait_text("text=Team discussion (2)", timeout=60), "worker mid-run posts never rendered"
live_state = get(f"/api/missions/{mission_id}")
running_now = bool(live_state["live"].get("running"))
print("TEAM live running badge:", "PASS" if page.locator("text=running").count() else "FAIL")
print("TEAM discussion posted by the running worker:", "PASS")
print("TEAM actually running at capture time (API live):", "PASS" if running_now else "FAIL")
page.screenshot(path=str(out_dir / "ui-team-live.png"), full_page=True)

# the review send-back: round journaled with feedback, reviewer posted the contract-change
assert wait_event(mission_id, "review-round"), "review round never journaled"
# the reviewer is read-only and never posts; the line still shows the build dispatch's two
assert wait_text("text=Team discussion (2)", timeout=60), "build dispatch posts missing"
page.goto(ui_url + f"#/mission/{mission_id}")
page.wait_for_timeout(2200)
round_marker = page.locator("text=Review round").count()
feedback = page.locator("text=add an empty-cart test").count()
waiting = page.locator("text=waiting on: developer").count()
print("TEAM review send-back in trace:", "PASS" if round_marker and feedback else "FAIL")
print("TEAM unacked change shows waiting_on:", "PASS" if waiting else "FAIL")
page.screenshot(path=str(out_dir / "ui-team-review-round.png"), full_page=True)

# the repair dispatch acks mid-run; the mission settles done after re-review
assert wait_status(mission_id, "done"), "mission never settled done"
assert wait_text("text=Team discussion (4)", timeout=30), "final discussion count wrong"
settled_done = get(f"/api/missions/{mission_id}")["status"]["status"] == "done"
print("TEAM mission settled done after repair re-review:", "PASS" if settled_done else "FAIL")
page.goto(ui_url + f"#/mission/{mission_id}")
page.wait_for_timeout(2200)
acked = page.locator("text=acknowledged").count()
heard_note = page.locator("text=Heard mid-repair").count()
print("TEAM repair acked mid-run:", "PASS" if acked and heard_note else "FAIL")
# the ack must clear the wait: no "waiting on" survives on the settled line
stale_wait = page.locator("text=waiting on:").count()
print("TEAM waiting_on clears after ack:", "PASS" if stale_wait == 0 else "FAIL")
page.screenshot(path=str(out_dir / "ui-team-discussion-full.png"), full_page=True)

# reload dedupe: cursor reset re-reads everything, seq-keyed, no duplicates
page.reload()
page.wait_for_timeout(2600)
still4 = page.locator("text=Team discussion (4)").count()
dup = page.locator("text=Empty-cart state implemented").count()
print("TEAM reload dedupe:", "PASS" if still4 and dup == 1 else f"FAIL 4={still4} dup={dup}")

# long-ish thread at narrow width: no horizontal overflow
nctx = browser.new_context(viewport={"width": 430, "height": 800})
npage = nctx.new_page()
npage.goto(ui_url + f"#/mission/{mission_id}")
npage.wait_for_timeout(2600)
summary_narrow = npage.locator("text=Team discussion (4)").count()
overflow = npage.evaluate(
    "(() => { const m = document.querySelector('main');"
    " return m ? m.scrollWidth - m.clientWidth : -1; })()")
npage.screenshot(path=str(out_dir / "ui-team-discussion-narrow.png"), full_page=True)
print("TEAM narrow thread:", "PASS" if summary_narrow and overflow == 0
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
summary4 = page.locator("text=Team discussion (4)").count()
dup = page.locator("text=Empty-cart state implemented").count()
print("TEAM reconnect dedupe:", "PASS" if summary4 and dup == 1
      else f"FAIL summary4={summary4} dup={dup}")
page.screenshot(path=str(out_dir / "ui-team-reconnect.png"), full_page=True)

# the SAME ticket's persistent lifecycle: replay -> delivered -> reopen -> repair -> delivered
verified = post("/api/backlog/verify", {"feature": feature["id"]})
print("TICKET journey replay passed:", "PASS" if verified.get("verified") else "FAIL")
reopened = backlog.reopen(feature["id"], evidence="Empty state flashes before items load",
                          labels=["ui"], actor="owner")
backlog.add_thread(feature["id"], actor="owner", kind="comment",
                   body="Seen twice on slower connections; repair should gate the render.")
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
# expanded thread evidence: open the disclosure before the capture
thread_summary = page.locator("summary", has_text="Thread (").first
thread_open = False
if thread_summary.count():
    thread_summary.click()
    page.wait_for_timeout(400)
    thread_open = page.locator("text=Empty state flashes before items load").count() > 0
run1 = page.locator("aside >> text=run 1").count()
run2 = page.locator("aside >> text=run 2").count()
print("TICKET final delivered row:", "PASS" if delivered else "FAIL",
      "| reopened 1x:", "PASS" if reopen_meta else "FAIL",
      "| 2 runs:", "PASS" if runs_meta else "FAIL",
      "| replay passed:", "PASS" if replay_meta else "FAIL",
      "| thread expanded:", "PASS" if thread_open else "FAIL",
      "| runs distinguishable:", "PASS" if run1 and run2 else "FAIL")
page.screenshot(path=str(out_dir / "ui-ticket-lifecycle.png"), full_page=True)

# reconcile the panel against the recorded line before claiming the counts
final_entries = get(f"/api/missions/{mission_id}/discussion")["entries"]
print("TEAM final discussion entries:", len(final_entries),
      json.dumps([(e["kind"], e["author"], e.get("body", "")[:40]) for e in final_entries]))

browser.close()
pw.stop()
print("UI TEAM SCENARIO OK")
