"""Drive Skye from Telegram, through the owner's own bot.

The owner makes a bot with @BotFather and gives Skye its token (``SKYE_TELEGRAM_TOKEN``).
Skye long-polls Telegram for it - no public URL, no webhook, no server of ours in between -
and answers exactly one person: whoever sends ``/start <pairing code>`` first. The code is
``SKYE_TELEGRAM_PAIR_CODE`` when set, otherwise one Skye makes up and prints to its log.

From the chat the owner signs the machine's coding agents in (``/claude``, ``/github`` -
see ``logins``), attaches GitHub repositories (``/repo owner/name``), starts tasks (any
message), approves steps outside the policy and opens the pull request when a task is
done. Everything runs through the same engine, policy and approvals as the web UI; the
chat only presses the same buttons.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import logins, projects

TOKEN_ENV = "SKYE_TELEGRAM_TOKEN"
PAIR_ENV = "SKYE_TELEGRAM_PAIR_CODE"
API_ENV = "SKYE_TELEGRAM_API"  # tests point this at a fake
WORK_ENV = "SKYE_WORK_DIR"
GIT_BASE_ENV = "SKYE_GIT_BASE"  # where /repo clones from (tests use a local folder)
PUBLIC_URL_ENV = "SKYE_PUBLIC_URL"  # the web UI's address, when it has one (a Maritime VM)
POLL_SECONDS = 50
WATCH_SECONDS = 5.0
LIMIT = 3900  # Telegram caps a message at 4096 characters
REPO = re.compile(r"^(?:https://github\.com/)?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")

HELP = """I'm your Skye: a team of coding agents on your own machine.

Set up (once):
/claude - sign in to Claude Code
/github - sign in to GitHub
/repo owner/name - attach a repository

Work:
just write the goal - the team plans and builds it
/solo <goal> - one agent, no planning (fewest tokens)
/status - what's running
/projects, /use <name> - switch repository
/web - a link to the web UI

When a task finishes I send you the result and a button to open the pull request."""


def enabled() -> bool:
    return bool(os.environ.get(TOKEN_ENV, "").strip())


class Bot:
    help_text = HELP

    def __init__(self, state_dir: Path, token: str | None = None) -> None:
        self.state_dir = Path(state_dir)
        self.token = token or os.environ.get(TOKEN_ENV, "").strip()
        self.api = os.environ.get(API_ENV, "").strip() or "https://api.telegram.org"
        self.state_path = self.state_dir / "telegram.json"
        self.state = self._load()
        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        self.claude_login: logins.PtyLogin | None = None
        self.github_login: logins.PtyLogin | None = None
        if not self.state.get("pair_code"):
            self.state["pair_code"] = (os.environ.get(PAIR_ENV, "").strip()
                                       or secrets.token_hex(3).upper())
            self._save()

    # -- state ---------------------------------------------------------------------------
    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        # The poller and the watcher both write: one at a time.
        with self._lock:
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, indent=2) + "\n", "utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(self.state_path)

    # -- Telegram API ----------------------------------------------------------------------
    def call(self, method: str, http_timeout: float = 30, **params: Any) -> Any:
        request = urllib.request.Request(
            f"{self.api}/bot{self.token}/{method}", data=json.dumps(params).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=http_timeout) as response:
            data = json.loads(response.read().decode())
        if not data.get("ok"):
            raise RuntimeError(f"telegram {method}: {data.get('description')}")
        return data.get("result")

    def say(self, text: str, buttons: list[list[tuple[str, str]]] | None = None,
            chat: int | None = None) -> None:
        chat = chat or self.state.get("owner_chat")
        if not chat:
            return
        params: dict[str, Any] = {"chat_id": chat, "text": text[:LIMIT],
                                  "disable_web_page_preview": True}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": [
                [{"text": label, "callback_data": data[:64]} for label, data in row]
                for row in buttons]}
        try:
            self.call("sendMessage", **params)
        except (OSError, RuntimeError, urllib.error.URLError) as error:
            print(f"telegram: could not send a message: {error}", flush=True)

    # -- loops -----------------------------------------------------------------------------
    def start(self) -> None:
        if not self.state.get("owner_user"):
            print(f"telegram: pair your bot - send it /start {self.state['pair_code']}", flush=True)
        threading.Thread(target=self._poll_loop, name="skye-telegram", daemon=True).start()
        threading.Thread(target=self._watch_loop, name="skye-telegram-watch", daemon=True).start()

    def _poll_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                updates = self.call("getUpdates", http_timeout=POLL_SECONDS + 10,
                                    offset=int(self.state.get("offset") or 0),
                                    timeout=POLL_SECONDS,
                                    allowed_updates=["message", "callback_query"])
            except Exception as error:  # noqa: BLE001 - network blips must not end the loop
                print(f"telegram: polling failed, retrying: {error}", flush=True)
                self.stop_event.wait(5)
                continue
            for update in updates or []:
                self.state["offset"] = int(update["update_id"]) + 1
                self._save()
                try:
                    self.handle(update)
                except Exception as error:  # noqa: BLE001 - one bad update must not stop the bot
                    import traceback
                    traceback.print_exc()
                    self.say(f"Something went wrong: {error}")

    def _watch_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.watch_once()
            except Exception:  # noqa: BLE001
                import traceback
                traceback.print_exc()
            self.stop_event.wait(WATCH_SECONDS)

    # -- updates ---------------------------------------------------------------------------
    def handle(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            query = update["callback_query"]
            try:
                self.call("answerCallbackQuery", callback_query_id=query["id"])
            except Exception:  # noqa: BLE001
                pass
            if self._is_owner(query.get("from") or {}):
                self.on_button(str(query.get("data") or ""))
            return
        message = update.get("message") or {}
        text = str(message.get("text") or "").strip()
        sender = message.get("from") or {}
        chat = (message.get("chat") or {}).get("id")
        if not text or chat is None:
            return
        if not self.state.get("owner_user"):
            words = text.split()
            if words[0].split("@")[0] == "/start" and len(words) > 1 \
                    and secrets.compare_digest(words[1].upper(), str(self.state["pair_code"]).upper()):
                self.state.update({"owner_user": sender.get("id"), "owner_chat": chat,
                                   "owner_name": sender.get("username") or sender.get("first_name") or ""})
                self._save()
                self.say("Paired - this Skye now answers only you.\n\n" + HELP)
                self.say(self._setup_line())
            else:
                self.say("Send /start followed by the pairing code from your Skye's log.", chat=chat)
            return
        if not self._is_owner(sender):
            self.say("This Skye belongs to someone else.", chat=chat)
            return
        self.on_text(text)

    def _is_owner(self, sender: dict[str, Any]) -> bool:
        return bool(self.state.get("owner_user")) and sender.get("id") == self.state.get("owner_user")

    def on_text(self, text: str) -> None:
        text = _as_command(text)
        word, _, rest = text.partition(" ")
        command = word.split("@")[0].lower() if word.startswith("/") else ""
        rest = rest.strip()
        if not command and self.claude_login is not None and self.claude_login.alive \
                and not self.claude_login.expired() and re.fullmatch(r"\S{8,}", text):
            return self._claude_code(text)
        if command in ("/start", "/help"):
            return self.say(self.help_text + "\n\n" + self._setup_line())
        if command == "/claude":
            return self._claude(rest)
        if command == "/github":
            return self._github(rest)
        if command == "/repo":
            return self._repo(rest)
        if command == "/projects":
            return self._projects()
        if command == "/use":
            return self._use(rest)
        if command == "/status":
            return self._status()
        if command == "/web":
            return self._web()
        if command == "/solo":
            return self._task(rest, solo=True)
        if command == "/team":
            return self._task(rest, solo=False)
        if command == "/approve":
            return self._approve_text(rest)
        if command == "/pr":
            return self._pull_request(rest.strip() or str(self.state.get("last_done") or ""))
        if command:
            return self.say(f"I don't know {command}. Send /help.")
        return self._task(text, solo=False)

    def on_button(self, data: str) -> None:
        kind, _, rest = data.partition("|")
        if kind == "a":
            mission, _, step = rest.partition("|")
            return self._approve(mission, step)
        if kind == "p":
            return self._pull_request(rest)

    # -- sign-ins --------------------------------------------------------------------------
    def _setup_line(self) -> str:
        claude = logins.claude_status()
        github = logins.github_status()
        project = self._active_project()
        parts = [
            f"Claude Code: {'signed in' + (' (' + claude['plan'] + ')' if claude.get('plan') else '') if claude['logged_in'] else 'not signed in - send /claude'}",
            f"GitHub: {'@' + github['login'] if github['logged_in'] else 'not signed in - send /github'}",
            f"Repository: {project['name'] if project else 'none yet - send /repo owner/name'}",
        ]
        return "\n".join(parts)

    def _claude(self, arg: str) -> None:
        if arg.lower() == "status":
            return self.say(self._setup_line())
        status = logins.claude_status()
        if not status["installed"]:
            return self.say("Claude Code is not installed on this machine.")
        if self.claude_login is not None:
            self.claude_login.stop()
        try:
            self.claude_login, url = logins.claude_begin()
        except RuntimeError as error:
            return self.say(f"I couldn't start the Claude sign-in: {error}")
        self.say("To sign in to Claude Code:\n1. Open this link and sign in with your Claude account:\n"
                 f"{url}\n2. Copy the code the page shows you.\n3. Send it here.")

    def _claude_code(self, code: str) -> None:
        login, self.claude_login = self.claude_login, None
        result = logins.claude_finish(login, code) if login else {"ok": False}
        if result.get("ok"):
            plan = f" ({result['plan']} plan)" if result.get("plan") else ""
            self.say(f"Claude Code is signed in{plan}.\n\n{self._setup_line()}")
        else:
            self.say(f"That didn't work: {result.get('detail') or 'unknown error'}. Send /claude to try again.")

    def _github(self, arg: str) -> None:
        if arg.lower() == "status":
            return self.say(self._setup_line())
        if not logins.github_status()["installed"]:
            return self.say("The GitHub CLI (gh) is not installed on this machine.")
        if self.github_login is not None:
            self.github_login.stop()
        try:
            login, code, link = logins.github_begin()
        except RuntimeError as error:
            return self.say(f"I couldn't start the GitHub sign-in: {error}")
        self.github_login = login
        self.say(f"To sign in to GitHub:\n1. Open {link}\n2. Enter the code {code}\n3. Approve it.\n\n"
                 "I'll tell you when it's done.")

        def _finish() -> None:
            status = logins.github_finish(login)
            if self.github_login is login:
                self.github_login = None
            if status.get("logged_in"):
                self.say(f"GitHub is signed in as @{status['login']}.\n\n{self._setup_line()}")
            else:
                self.say("The GitHub sign-in didn't complete. Send /github to try again.")
        threading.Thread(target=_finish, daemon=True).start()

    # -- repositories ----------------------------------------------------------------------
    def _work_dir(self) -> Path:
        return Path(os.environ.get(WORK_ENV, "").strip() or Path.home() / "skye-work")

    def _active_project(self) -> dict[str, Any] | None:
        known = projects.list_projects()
        chosen = self.state.get("project")
        return next((p for p in known if p["id"] == chosen), None) or (known[-1] if known else None)

    def _repo(self, arg: str) -> None:
        match = REPO.match(arg.strip())
        if not match:
            return self.say("Send /repo owner/name (or the github.com link).")
        owner, name = match.group(1), match.group(2)
        dest = self._work_dir() / owner / name
        self.say(f"Getting {owner}/{name}...")
        if (dest / ".git").exists():
            proc = subprocess.run(["git", "-C", str(dest), "pull", "--ff-only", "-q"],
                                  capture_output=True, text=True, timeout=300)
            note = "updated" if proc.returncode == 0 else "kept as it is (could not fast-forward)"
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            base = os.environ.get(GIT_BASE_ENV, "").strip().rstrip("/") or "https://github.com"
            proc = subprocess.run(["git", "clone", "-q", f"{base}/{owner}/{name}.git", str(dest)],
                                  capture_output=True, text=True, timeout=600,
                                  env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
            if proc.returncode != 0:
                hint = "" if logins.github_status()["logged_in"] else " If it's private, send /github first."
                return self.say(f"I couldn't clone {owner}/{name}: {(proc.stderr or '').strip()[-300:]}{hint}")
            note = "cloned"
        existing = next((p for p in projects.list_projects() if p["repo"] == str(dest)), None)
        project = existing or projects.add({"name": f"{owner}/{name}"[:60], "repo": str(dest)})
        self.state["project"] = project["id"]
        self._save()
        self.say(f"{owner}/{name} {note}. New tasks work on it now - just write what to do.")

    def _projects(self) -> None:
        known = projects.list_projects()
        if not known:
            return self.say("No repositories yet. Send /repo owner/name.")
        active = self._active_project()
        lines = [("• " if active and p["id"] == active["id"] else "  ") + p["name"] for p in known]
        self.say("Repositories (• is the one new tasks use):\n" + "\n".join(lines) + "\n\n/use <name> to switch.")

    def _use(self, arg: str) -> None:
        arg = arg.strip().lower()
        match = next((p for p in projects.list_projects()
                      if p["name"].lower() == arg or p["name"].lower().endswith("/" + arg)), None)
        if not match:
            return self.say("No repository by that name. /projects lists them.")
        self.state["project"] = match["id"]
        self._save()
        self.say(f"New tasks work on {match['name']}.")

    # -- tasks -----------------------------------------------------------------------------
    def _task(self, goal: str, *, solo: bool) -> None:
        if not goal:
            return self.say("Tell me the goal, e.g. /solo fix the failing test in the parser")
        if not logins.claude_status()["logged_in"] and not os.environ.get("SKYE_ALLOW_UNSIGNED"):
            return self.say("Claude Code isn't signed in yet. Send /claude first.")
        project = self._active_project()
        if project is None:
            return self.say("Attach a repository first: /repo owner/name")
        from .ui.server import create_mission, resume_in_thread
        spec: dict[str, Any] = {"goal": goal, "project": project["id"], "run": True}
        spec.update({"solo": True} if solo else {"plan": True})
        mission_id = create_mission(self.state_dir, spec)
        self.state.setdefault("seen", {})[mission_id] = "started"
        self._save()
        resume_in_thread(self.state_dir, mission_id)
        self.say(f"On it ({'one agent' if solo else 'the team is planning'}) in {project['name']}.\n"
                 f"Task {mission_id}. I'll message you when it needs you or is done.")

    def _web(self) -> None:
        base = os.environ.get(PUBLIC_URL_ENV, "").strip().rstrip("/")
        try:
            token = (self.state_dir / "ui-token").read_text("utf-8").strip()
        except OSError:
            token = ""
        if not base or not token:
            return self.say("The web UI isn't published on this machine (set SKYE_PUBLIC_URL).")
        self.say(f"Skye's web UI (this link signs you in - don't share it):\n{base}/?token={token}")

    def _status(self) -> None:
        from .ui.server import missions_overview
        rows = missions_overview(self.state_dir)[:8]
        if not rows:
            return self.say("No tasks yet. Write a goal to start one.\n\n" + self._setup_line())
        lines = []
        for row in rows:
            state = "running" if row["live"] else ("needs you" if row["needs_owner"] else row["overall"])
            lines.append(f"{_mark(state)} {row['goal'][:70]}\n   {state} · {row['done']}/{row['steps']} steps · {row['id']}")
        self.say("\n".join(lines))

    def watch_once(self) -> None:
        """Tell the owner when a task needs them, finishes or fails - once per change."""
        if not self.state.get("owner_chat"):
            return
        from .ui.server import mission_payload, missions_overview
        seen = self.state.setdefault("seen", {})
        first_run = "watching" not in self.state
        changed = False
        for row in missions_overview(self.state_dir):
            mission_id = row["id"]
            key = "needs-owner" if row["needs_owner"] else ("running" if row["live"] else row["overall"])
            if seen.get(mission_id) == key:
                continue
            previous, seen[mission_id] = seen.get(mission_id), key
            changed = True
            if first_run or previous is None and key in ("done", "failed"):
                continue  # history from before the bot was watching: not news
            if key == "needs-owner":
                payload = mission_payload(self.state_dir, mission_id)
                self.state["last_needs"] = mission_id
                buttons = [[(f"Approve {c['step']} once", f"a|{mission_id}|{c['step']}")]
                           for c in payload["pending_contracts"]]
                reasons = [f"• {c['step']} ({c.get('role')} · {c.get('adapter')})" for c in payload["pending_contracts"]]
                why = _last(payload["journal"], "needs-owner", "reason")
                self.say(f"⏸ Needs you: {row['goal'][:120]}\n" + "\n".join(reasons)
                         + (f"\n{why[:400]}" if why else ""), buttons or None)
            elif key == "done":
                payload = mission_payload(self.state_dir, mission_id)
                self.state["last_done"] = mission_id
                answer = _last(payload["journal"], "settled", "answer")
                files = len((payload.get("artifacts") or {}).get("files") or [])
                self.say(f"✅ Done: {row['goal'][:120]}\n\n{answer[:2500]}".rstrip()
                         + (f"\n\n{files} file(s) changed." if files else ""),
                         [[("Open pull request", f"p|{mission_id}")]])
            elif key == "failed":
                payload = mission_payload(self.state_dir, mission_id)
                why = _last(payload["journal"], "settled", "reason")
                self.say(f"❌ Failed: {row['goal'][:120]}\n{why[:600]}\n\nOpen Skye's web UI to follow up, "
                         "or write the goal again.")
        if first_run:
            self.state["watching"] = True
            changed = True
        if changed:
            self._save()

    def _approve_text(self, arg: str) -> None:
        """``approve`` (the latest task waiting on you, every step it waits for) or
        ``approve <task> [step]`` - the text form of the Approve button, for chats without buttons."""
        words = arg.split()
        mission_id = words[0] if words else str(self.state.get("last_needs") or "")
        if not mission_id or not re.fullmatch(r"[A-Za-z0-9_-]+", mission_id):
            return self.say("Nothing is waiting for your approval.")
        folder = self.state_dir / "missions" / mission_id
        steps = words[1:2] or sorted(p.name[len("pending-"):-len(".json")]
                                     for p in folder.glob("pending-*.json"))
        if not steps:
            return self.say("Nothing is waiting for your approval.")
        for step in steps:
            self._approve(mission_id, step)

    def _approve(self, mission_id: str, step: str) -> None:
        from . import approvals
        from .ui.server import approvals_path_for, resume_in_thread
        if not re.fullmatch(r"[A-Za-z0-9_-]+", mission_id) or not re.fullmatch(r"[A-Za-z0-9_.-]+", step):
            return
        path = self.state_dir / "missions" / mission_id / f"pending-{step}.json"
        if not path.exists():
            return self.say(f"{step} isn't waiting for approval any more.")
        contract = json.loads(path.read_text("utf-8"))
        approvals.approve(contract, path=approvals_path_for(self.state_dir), note="telegram")
        path.unlink(missing_ok=True)
        try:
            resume_in_thread(self.state_dir, mission_id)
        except Exception as error:  # noqa: BLE001 - e.g. already running
            return self.say(f"Approved {step}, but I couldn't resume the task: {error}")
        self.say(f"Approved {step} once. The task is running again.")

    def _pull_request(self, mission_id: str) -> None:
        from . import workspaces
        if not re.fullmatch(r"[A-Za-z0-9_-]+", mission_id):
            return
        mission_dir = self.state_dir / "missions" / mission_id
        if not (mission_dir / "mission.json").exists():
            return self.say("I can't find that task.")
        if not logins.github_status()["logged_in"]:
            return self.say("Sign in to GitHub first: /github")
        goal = str(json.loads((mission_dir / "mission.json").read_text("utf-8")).get("goal") or mission_id)
        self.say("Pushing the branch and opening the pull request...")
        result = workspaces.open_pull_request(
            mission_dir, title=goal[:120],
            body=f"{goal}\n\nMade by Skye (task {mission_id}): planned, built and checked by its agents.")
        lines = []
        for name, repo in result["repos"].items():
            label = "" if name == "default" else f"{name}: "
            lines.append(f"{label}{repo['url']}" if repo.get("ok") else f"{label}{repo.get('detail')}")
        self.say(("Pull request: " if result["ok"] else "") + "\n".join(lines))


# Chats without a "/" key (iMessage, SMS): a message that is exactly one of these words is
# read as the command, and so is one of the few shapes below that cannot be a goal.
BARE = {"help", "start", "claude", "github", "projects", "status", "web", "approve", "pr"}
ID = r"[A-Za-z0-9][A-Za-z0-9_-]{5,}"
BARE_SHAPES = (
    re.compile(r"(claude|github) status", re.I),
    re.compile(r"repo (\S+)", re.I),
    re.compile(rf"(approve) ({ID})(?: ([A-Za-z0-9_.-]+))?", re.I),
    re.compile(rf"(pr) ({ID})", re.I),
    re.compile(r"(solo) (.+)", re.I | re.S),
)


def _as_command(text: str) -> str:
    text = text.strip()
    if text.startswith("/"):
        return text
    if text.lower().rstrip(".!") in BARE:
        return "/" + text.lower().rstrip(".!")
    for shape in BARE_SHAPES:
        if shape.fullmatch(text) and (not text.lower().startswith("repo ")
                                      or REPO.match(text[5:].strip())):
            word, _, rest = text.partition(" ")
            return f"/{word.lower()} {rest.strip()}"
    return text


def _last(journal: list[dict], event: str, field: str) -> str:
    for entry in reversed(journal):
        if entry.get("event") == event and entry.get(field):
            return str(entry[field])
    return ""


def _mark(state: str) -> str:
    return {"done": "✅", "failed": "❌", "needs you": "⏸", "running": "⏳"}.get(state, "•")


def start(state_dir: Path) -> Bot | None:
    if not enabled():
        return None
    bot = Bot(state_dir)
    bot.start()
    return bot
