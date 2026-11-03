"""Skye from Telegram: pairing, sign-ins through the vendors' own CLIs, repositories, tasks,
notifications, approvals and the pull request - against fake `claude`, `gh` and Telegram."""

import json
import os
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from skye import logins, projects, telegram, workspaces
from skye.ui import server as ui

REAL_CLAUDE_STATUS = logins.claude_status


def _env(tmp_path, monkeypatch):
    for var, name in (("SKYE_PROJECTS", "projects.json"), ("SKYE_TEAMS", "teams.json"),
                      ("SKYE_ROLES", "roles.json"), ("SKYE_BACKLOG", "backlog.json"),
                      ("SKYE_ISSUES", "issues.json")):
        monkeypatch.setenv(var, str(tmp_path / name))
    monkeypatch.setenv("SKYE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("SKYE_TELEGRAM_PAIR_CODE", "ABC123")
    monkeypatch.setattr(logins, "claude_status",
                        lambda: {"installed": True, "logged_in": True, "plan": "max"})
    monkeypatch.setattr(logins, "github_status",
                        lambda: {"installed": True, "logged_in": True, "login": "octo"})


def _bot(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    state = tmp_path / "state"
    state.mkdir()
    bot = telegram.Bot(state, token="T")
    sent = []
    monkeypatch.setattr(bot, "call", lambda method, http_timeout=30, **p: sent.append((method, p)) or {})
    return bot, sent


def _msg(text, user=7, chat=70):
    return {"update_id": 1, "message": {"text": text, "from": {"id": user, "username": "me"},
                                        "chat": {"id": chat}}}


def _texts(sent):
    return [p["text"] for m, p in sent if m == "sendMessage"]


def _paired(tmp_path, monkeypatch):
    bot, sent = _bot(tmp_path, monkeypatch)
    bot.handle(_msg("/start abc123"))
    sent.clear()
    return bot, sent


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qm", "init"], check=True)
    return path


# --- pairing ---------------------------------------------------------------------------

def test_only_the_paired_owner_is_answered(tmp_path, monkeypatch):
    bot, sent = _bot(tmp_path, monkeypatch)
    bot.handle(_msg("hello"))
    assert "pairing code" in _texts(sent)[-1]
    bot.handle(_msg("/start WRONG"))
    assert bot.state.get("owner_user") is None
    bot.handle(_msg("/start abc123"))  # case-insensitive
    assert bot.state["owner_user"] == 7 and bot.state["owner_chat"] == 70
    assert "Paired" in _texts(sent)[-2]
    assert "Claude Code: signed in (max)" in _texts(sent)[-1]
    sent.clear()
    bot.handle(_msg("/start abc123", user=8, chat=80))  # a second person cannot take it over
    assert bot.state["owner_user"] == 7
    assert sent[-1][1]["chat_id"] == 80 and "someone else" in sent[-1][1]["text"]
    # the pairing survives a restart
    again = telegram.Bot(bot.state_dir, token="T")
    assert again.state["owner_user"] == 7


def test_pair_code_is_generated_when_not_configured(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    monkeypatch.delenv("SKYE_TELEGRAM_PAIR_CODE")
    bot = telegram.Bot(tmp_path, token="T")
    assert len(bot.state["pair_code"]) == 6
    assert telegram.Bot(tmp_path, token="T").state["pair_code"] == bot.state["pair_code"]


# --- repositories ----------------------------------------------------------------------

def test_repo_clones_and_becomes_the_active_project(tmp_path, monkeypatch):
    bot, sent = _paired(tmp_path, monkeypatch)
    _git_repo(tmp_path / "remote" / "acme" / "shop.git")
    monkeypatch.setenv("SKYE_GIT_BASE", str(tmp_path / "remote"))
    bot.handle(_msg("/repo https://github.com/acme/shop"))
    assert "acme/shop cloned" in _texts(sent)[-1]
    clone = tmp_path / "work" / "acme" / "shop"
    assert (clone / "README.md").exists()
    [project] = projects.list_projects()
    assert project["repo"] == str(clone) and bot.state["project"] == project["id"]
    bot.handle(_msg("/repo acme/shop"))  # again: updated, not a second project
    assert "updated" in _texts(sent)[-1]
    assert len(projects.list_projects()) == 1
    bot.handle(_msg("/repo not a repo"))
    assert "/repo owner/name" in _texts(sent)[-1]


def test_use_switches_between_projects(tmp_path, monkeypatch):
    bot, sent = _paired(tmp_path, monkeypatch)
    a = projects.add({"name": "acme/shop", "repo": str(_git_repo(tmp_path / "a"))})
    projects.add({"name": "acme/api", "repo": str(_git_repo(tmp_path / "b"))})
    bot.handle(_msg("/use shop"))
    assert bot.state["project"] == a["id"]
    bot.handle(_msg("/projects"))
    assert "• acme/shop" in _texts(sent)[-1]


# --- tasks -----------------------------------------------------------------------------

def test_a_message_starts_a_team_task_and_solo_starts_one_agent(tmp_path, monkeypatch):
    bot, sent = _paired(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(ui, "create_mission", lambda state, spec: calls.append(spec) or f"m{len(calls)}")
    monkeypatch.setattr(ui, "resume_in_thread", lambda state, mid: calls.append(("run", mid)))
    bot.handle(_msg("add a dark mode toggle"))
    assert "/repo" in _texts(sent)[-1] and not calls  # no repository yet
    projects.add({"name": "acme/shop", "repo": str(_git_repo(tmp_path / "shop"))})
    bot.handle(_msg("add a dark mode toggle"))
    bot.handle(_msg("/solo fix the typo"))
    assert calls[0]["goal"] == "add a dark mode toggle" and calls[0]["plan"] is True
    assert calls[1] == ("run", "m1")
    assert calls[2]["goal"] == "fix the typo" and calls[2]["solo"] is True
    assert "team is planning" in _texts(sent)[-2] and "one agent" in _texts(sent)[-1]


def test_no_task_starts_before_claude_is_signed_in(tmp_path, monkeypatch):
    bot, sent = _paired(tmp_path, monkeypatch)
    monkeypatch.setattr(logins, "claude_status", lambda: {"installed": True, "logged_in": False})
    projects.add({"name": "acme/shop", "repo": str(_git_repo(tmp_path / "shop"))})
    bot.handle(_msg("do something"))
    assert "/claude" in _texts(sent)[-1]


# --- notifications, approvals, pull requests -------------------------------------------

def _overview(rows):
    return lambda state: rows


def test_watch_reports_each_change_once_with_the_right_button(tmp_path, monkeypatch):
    bot, sent = _paired(tmp_path, monkeypatch)
    row = {"id": "m1", "goal": "ship it", "needs_owner": False, "live": True, "overall": "running",
           "done": 0, "steps": 1}
    old = {**row, "id": "m0", "live": False, "overall": "done"}
    rows = [row, old]
    monkeypatch.setattr(ui, "missions_overview", _overview(rows))
    payload = {"pending_contracts": [], "journal": [], "artifacts": {"files": []}}
    monkeypatch.setattr(ui, "mission_payload", lambda state, mid: payload)
    bot.watch_once()  # first look: history, not news
    assert _texts(sent) == []
    row.update(live=False, needs_owner=True)
    payload["pending_contracts"] = [{"step": "deploy", "role": "devops", "adapter": "claude-code"}]
    payload["journal"] = [{"event": "needs-owner", "reason": "devops is not on the unattended list"}]
    bot.watch_once()
    bot.watch_once()  # unchanged: nothing new
    [needs] = [p for m, p in sent if m == "sendMessage"]
    assert "Needs you: ship it" in needs["text"] and "unattended list" in needs["text"]
    assert needs["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "a|m1|deploy"
    row.update(needs_owner=False, overall="done")
    payload["journal"].append({"event": "settled", "answer": "Shipped the page."})
    payload["artifacts"]["files"] = [{"path": "a"}, {"path": "b"}]
    bot.watch_once()
    done = sent[-1][1]
    assert "Done: ship it" in done["text"] and "Shipped the page." in done["text"]
    assert "2 file(s) changed" in done["text"]
    assert done["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "p|m1"


def test_approve_button_records_a_one_time_approval_and_resumes(tmp_path, monkeypatch):
    bot, sent = _paired(tmp_path, monkeypatch)
    mission_dir = bot.state_dir / "missions" / "m1"
    mission_dir.mkdir(parents=True)
    contract = {"goal": "deploy", "repo": str(tmp_path), "role": "devops", "adapter": "claude-code"}
    (mission_dir / "pending-deploy.json").write_text(json.dumps(contract))
    resumed = []
    monkeypatch.setattr(ui, "resume_in_thread", lambda state, mid: resumed.append(mid))
    bot.handle({"update_id": 2, "callback_query": {"id": "q", "data": "a|m1|deploy", "from": {"id": 99}}})
    assert not resumed  # not the owner
    bot.handle({"update_id": 3, "callback_query": {"id": "q", "data": "a|m1|deploy", "from": {"id": 7}}})
    assert resumed == ["m1"]
    assert not (mission_dir / "pending-deploy.json").exists()
    stored = json.loads((bot.state_dir / "approvals.json").read_text())
    assert stored and "Approved deploy once" in _texts(sent)[-1]


def _fake_bin(tmp_path, name, body):
    path = tmp_path / "bin" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def test_pull_request_commits_pushes_the_branch_and_opens_it(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = _git_repo(tmp_path / "repo")
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(origin)], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "main"], check=True)
    worktree = tmp_path / "wt"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "skye/m1", str(worktree)], check=True)
    (worktree / "feature.py").write_text("x = 1\n")
    mission_dir = tmp_path / "missions" / "m1"
    mission_dir.mkdir(parents=True)
    workspaces._record_path(mission_dir).write_text(json.dumps(
        {"worktree": str(worktree), "repo": str(repo), "branch": "skye/m1"}))
    log = tmp_path / "gh.log"
    _fake_bin(tmp_path, "gh", f"""import sys, json
open({str(log)!r}, "a").write(json.dumps(sys.argv[1:]) + "\\n")
print("https://github.com/acme/shop/pull/7")
""")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    result = workspaces.open_pull_request(mission_dir, title="Add feature")
    assert result["ok"], result
    assert result["repos"]["default"]["url"] == "https://github.com/acme/shop/pull/7"
    pushed = subprocess.run(["git", "-C", str(origin), "log", "--format=%s", "skye/m1"],
                            capture_output=True, text=True).stdout
    assert "Add feature" in pushed  # the worker's change was committed and pushed
    argv = json.loads(log.read_text().splitlines()[0])
    assert argv[:2] == ["pr", "create"] and argv[argv.index("--base") + 1] == "main"
    # main itself was never pushed to
    assert subprocess.run(["git", "-C", str(origin), "rev-list", "--count", "main"],
                          capture_output=True, text=True).stdout.strip() == "1"


def test_pull_request_with_nothing_to_push_says_so(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    worktree = tmp_path / "wt"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "skye/m2", str(worktree)], check=True)
    mission_dir = tmp_path / "m2"
    mission_dir.mkdir()
    workspaces._record_path(mission_dir).write_text(json.dumps(
        {"worktree": str(worktree), "repo": str(repo), "branch": "skye/m2"}))
    result = workspaces.open_pull_request(mission_dir, title="nothing")
    assert not result["ok"] and "changed no files" in result["repos"]["default"]["detail"]


# --- sign-ins through the vendors' CLIs -------------------------------------------------

FAKE_CLAUDE = """import json, os, sys
mark = os.path.join(os.environ["FAKE_HOME"], "claude-ok")
if sys.argv[1:3] == ["auth", "status"]:
    ok = os.path.exists(mark)
    print(json.dumps({"loggedIn": ok, "subscriptionType": "max" if ok else None}))
    sys.exit(0)
if sys.argv[1:3] == ["auth", "login"]:
    url = "https://claude.com/cai/oauth/authorize?code=true&state=xyz"
    # the real CLI wraps the link in an OSC 8 terminal hyperlink as well
    print("Opening browser to sign in...")
    print("If the browser didn't open, visit: \\x1b]8;;" + url + "\\x07" + url + "\\x1b]8;;\\x07")
    sys.stdout.write("Paste code here if prompted > "); sys.stdout.flush()
    code = sys.stdin.readline().strip()
    if code == "good-code-123":
        open(mark, "w").write("1"); print("Login successful."); sys.exit(0)
    print("OAuth error: invalid code"); sys.exit(1)
"""

FAKE_GH = """import json, os, sys, time
mark = os.path.join(os.environ["FAKE_HOME"], "gh-ok")
args = sys.argv[1:]
if args[:2] == ["api", "user"]:
    if os.path.exists(mark):
        print("octo"); sys.exit(0)
    sys.exit(1)
if args[:2] == ["auth", "login"]:
    print("! First copy your one-time code: ABCD-1234")
    sys.stdout.write("Press Enter to open https://github.com/login/device in your browser... ")
    sys.stdout.flush()
    sys.stdin.readline()
    time.sleep(0.3)  # gh polls github until approved
    open(mark, "w").write("1"); sys.exit(0)
if args[:2] == ["auth", "setup-git"]:
    open(mark + "-git", "w").write("1"); sys.exit(0)
"""


def test_claude_sign_in_link_and_code_round_trip(tmp_path, monkeypatch):
    _fake_bin(tmp_path, "claude", FAKE_CLAUDE)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_HOME", str(tmp_path))
    assert logins.claude_status()["logged_in"] is False
    login, url = logins.claude_begin()
    assert url == "https://claude.com/cai/oauth/authorize?code=true&state=xyz"
    result = logins.claude_finish(login, "good-code-123", timeout=10)
    assert result["ok"] and result["plan"] == "max"
    bad, _ = logins.claude_begin()
    assert logins.claude_finish(bad, "wrong-code-999", timeout=10)["ok"] is True  # still signed in from before
    os.remove(tmp_path / "claude-ok")
    bad, _ = logins.claude_begin()
    failed = logins.claude_finish(bad, "wrong-code-999", timeout=10)
    assert not failed["ok"] and "invalid code" in failed["detail"]


def test_claude_sign_in_from_the_chat(tmp_path, monkeypatch):
    bot, sent = _paired(tmp_path, monkeypatch)
    monkeypatch.setattr(logins, "claude_status", REAL_CLAUDE_STATUS)  # the fake CLI decides
    _fake_bin(tmp_path, "claude", FAKE_CLAUDE)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_HOME", str(tmp_path))
    bot.handle(_msg("/claude"))
    assert "https://claude.com/cai/oauth/authorize" in _texts(sent)[-1]
    bot.handle(_msg("good-code-123"))  # the next message is the code
    assert "Claude Code is signed in (max plan)" in _texts(sent)[-1]
    assert bot.claude_login is None


def test_github_sign_in_reports_the_code_and_finishes_in_the_background(tmp_path, monkeypatch):
    _fake_bin(tmp_path, "gh", FAKE_GH)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_HOME", str(tmp_path))
    login, code, link = logins.github_begin()
    assert code == "ABCD-1234" and link == "https://github.com/login/device"
    status = logins.github_finish(login, timeout=10)
    assert status == {"installed": True, "logged_in": True, "login": "octo"}
    assert (tmp_path / "gh-ok-git").exists()  # git now uses the same account


# --- the HTTP side ---------------------------------------------------------------------

def test_bot_talks_the_telegram_bot_api(tmp_path, monkeypatch):
    """getUpdates long-polls with an offset; sendMessage posts JSON to /bot<token>/."""
    _env(tmp_path, monkeypatch)
    seen = []

    class Fake(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, body))
            if self.path.endswith("/getUpdates"):
                result = [_msg("/start ABC123")] if len(seen) == 1 else []
            else:
                result = {}
            data = json.dumps({"ok": True, "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("SKYE_TELEGRAM_API", f"http://127.0.0.1:{server.server_address[1]}")
    monkeypatch.setattr(telegram, "POLL_SECONDS", 0)
    bot = telegram.Bot(tmp_path, token="123:abc")
    bot.start()
    import time
    deadline = time.time() + 10
    while time.time() < deadline and not any(p.endswith("/sendMessage") for p, _ in seen):
        time.sleep(0.05)
    bot.stop_event.set()
    server.shutdown()
    path, body = seen[0]
    assert path == "/bot123:abc/getUpdates" and body["offset"] == 0
    assert any(p == "/bot123:abc/sendMessage" and b["chat_id"] == 70 for p, b in seen)
    assert bot.state["offset"] == 2 and bot.state["owner_user"] == 7
