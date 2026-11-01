"""The Skye workflow UI server: stdlib-only, localhost-only, token-guarded.

Serves the built frontend (skye/ui/dist) and a small JSON API over the .skye
state dir: mission journals, pending approval contracts, and operator actions
(approve / resume / redispatch) that call the same functions the CLI uses.

Security posture:
  * binds 127.0.0.1 only; never accepts connections off-host.
  * every API call requires the per-run session token in the X-Skye-Token
    header (or ?token= for the initial page load only). The token is printed
    on the console and written to <state>/ui-token (0600). No cookies are set
    or read, so browsers cannot attach ambient credentials cross-origin.
  * POSTs additionally reject a cross-origin Origin/Referer, and no CORS
    headers are ever emitted, so other sites cannot read or trigger the API.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from skye import __version__
from skye import approvals
from skye.mission import (Journal, MissionError, default_state_dir, load_mission,
                          mission_status, reset_mission, run_mission)

DIST = Path(__file__).resolve().parent / "dist"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def mission_payload(state_dir: Path, mission_id: str) -> dict:
    """Live detail for one mission: definition snapshot, journal trace, pending approvals."""
    mission_dir = state_dir / "missions" / mission_id
    snapshot = mission_dir / "mission.json"
    if not snapshot.exists():
        raise KeyError(mission_id)
    mission = json.loads(snapshot.read_text("utf-8"))
    journal = Journal(mission_dir / "journal.jsonl")
    status = mission_status(mission, state_dir=state_dir)
    pending: list[dict] = []
    for contract_file in sorted(mission_dir.glob("pending-*.json")):
        contract = json.loads(contract_file.read_text("utf-8"))
        contract["step"] = contract_file.stem.removeprefix("pending-")
        pending.append(contract)
    dynamic = [s for s in journal.accepted_steps()]
    return {"mission": mission, "status": status, "journal": _read_jsonl(mission_dir / "journal.jsonl"),
            "dynamic_steps": dynamic, "pending_contracts": pending}


def missions_overview(state_dir: Path) -> list[dict]:
    """One row per journaled mission, newest activity first."""
    rows = []
    missions_root = state_dir / "missions"
    if missions_root.exists():
        for mission_dir in missions_root.iterdir():
            if not (mission_dir / "mission.json").exists():
                continue
            try:
                payload = mission_payload(state_dir, mission_dir.name)
            except (KeyError, json.JSONDecodeError):
                continue
            journal = payload["journal"]
            last = journal[-1]["ts"] if journal else None
            status = payload["status"]
            rows.append({"id": mission_dir.name, "goal": payload["mission"].get("goal", ""),
                         "overall": status["status"],
                         "needs_owner": bool(payload["pending_contracts"]) or any(
                             s["state"] == "needs-owner" for s in status["steps"]),
                         "steps": len(status["steps"]),
                         "done": sum(1 for s in status["steps"] if s["state"] == "done"),
                         "failed": sum(1 for s in status["steps"] if s["state"] == "failed"),
                         "last_activity": last,
                         "live": _live_missions.get(mission_dir.name, {}).get("running", False)})
    rows.sort(key=lambda r: r["last_activity"] or "", reverse=True)
    return rows


_live_missions: dict[str, dict] = {}
_live_lock = threading.Lock()

# Test/demo hook: the worker runner the in-process resume passes to run_mission. None means the
# real dispatcher path (the default, and what `skye ui` uses).
RUNNER = None


def resume_in_thread(state_dir: Path, mission_id: str, redispatch: str | None = None) -> None:
    """Resume a mission in-process from its snapshot; the UI polls the journal for progress.
    ``redispatch`` is the operator's explicit choice to re-run one interrupted step."""
    mission_dir = state_dir / "missions" / mission_id
    with _live_lock:
        if _live_missions.get(mission_id, {}).get("running"):
            raise MissionError(f"mission {mission_id} is already running")
        _live_missions[mission_id] = {"running": True, "started": time.time(), "error": None}

    def _run() -> None:
        try:
            mission = load_mission(mission_dir / "mission.json")
            run_mission(mission, state_dir=state_dir, approvals_path=approvals_path_for(state_dir),
                          redispatch=redispatch, runner=RUNNER)
        except Exception as exc:  # surfaced via GET /api/missions/<id> -> live.error
            with _live_lock:
                _live_missions[mission_id]["error"] = str(exc)
        finally:
            with _live_lock:
                _live_missions[mission_id]["running"] = False
                _live_missions[mission_id]["finished"] = time.time()

    threading.Thread(target=_run, name=f"skye-ui-resume-{mission_id}", daemon=True).start()


class UiHandler(BaseHTTPRequestHandler):
    server_version = f"skye-ui/{__version__}"

    @property
    def state_dir(self) -> Path:  # set on the server instance
        return self.server.state_dir  # type: ignore[attr-defined]

    @property
    def approvals_path(self) -> Path:
        return approvals_path_for(self.state_dir)

    @property
    def token(self) -> str:
        return self.server.token  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:  # keep the console clean
        return

    # --- auth ---
    def _authorized(self, query: dict, allow_query_token: bool) -> bool:
        header = self.headers.get("X-Skye-Token")
        if header and secrets.compare_digest(header, self.token):
            return True
        if allow_query_token:
            token = query.get("token", [""])[0]
            return bool(token) and secrets.compare_digest(token, self.token)
        return False

    def _deny(self, code: int = 403) -> None:
        body = json.dumps({"error": "unauthorized"}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, code: int = 200) -> None:
        body = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path: Path) -> None:
        dist = DIST.resolve()
        target = (dist / path).resolve()
        if dist not in target.parents and target != dist:
            self._deny(404)
            return
        if not target.is_file():
            target = dist / "index.html"  # SPA fallback
        if not target.is_file():
            self._json({"error": "frontend not built; run: cd skye/ui && pnpm build"}, 404)
            return
        ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
                 ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png",
                 ".woff2": "font/woff2"}.get(target.suffix, "application/octet-stream")
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # --- routes ---
    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path.startswith("/api/"):
            if not self._authorized(query, allow_query_token=False):
                return self._deny()
            try:
                if url.path == "/api/missions":
                    return self._json({"missions": missions_overview(self.state_dir),
                                       "live": {k: v for k, v in _live_missions.items()}})
                if url.path == "/api/approvals":
                    return self._json({"approvals": approvals.pending(self.approvals_path)})
                if url.path.startswith("/api/missions/"):
                    mission_id = url.path.rsplit("/", 1)[-1]
                    payload = mission_payload(self.state_dir, mission_id)
                    payload["live"] = _live_missions.get(mission_id, {})
                    return self._json(payload)
            except KeyError:
                return self._json({"error": "unknown mission"}, 404)
            return self._json({"error": "not found"}, 404)
        # Static SPA assets carry no state, so they are served without the token (the browser
        # fetches them with no header and no query token). Every /api/ call still requires it.
        rel = url.path.lstrip("/") or "index.html"
        self._static(Path(rel))

    def do_POST(self) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if not self._authorized(query, allow_query_token=False):
            return self._deny()
        origin = self.headers.get("Origin") or self.headers.get("Referer")
        if origin:
            origin_host = urlparse(origin).netloc.split("@")[-1]
            if origin_host not in (self.headers.get("Host"),):
                return self._json({"error": "cross-origin POST rejected"}, 403)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "invalid JSON body"}, 400)
        try:
            if url.path == "/api/approve":
                mission_id, step = body.get("mission"), body.get("step")
                if not mission_id or not step:
                    return self._json({"error": "mission and step are required"}, 400)
                mission_dir = self.state_dir / "missions" / mission_id
                contract_path = mission_dir / f"pending-{step}.json"
                if not contract_path.exists():
                    return self._json({"error": "no pending contract for that step"}, 404)
                contract = json.loads(contract_path.read_text("utf-8"))
                entry = approvals.approve(contract, path=self.approvals_path, note="skye ui")
                contract_path.unlink(missing_ok=True)
                return self._json({"approved": True, "fingerprint": entry["fingerprint"]})
            if url.path.startswith("/api/missions/"):
                mission_id, _, action = url.path.removeprefix("/api/missions/").partition("/")
                mission_dir = self.state_dir / "missions" / mission_id
                mission_file = mission_dir / "mission.json"
                if not mission_file.exists():
                    return self._json({"error": "unknown mission"}, 404)
                if action == "resume":
                    resume_in_thread(self.state_dir, mission_id)
                    return self._json({"resumed": True})
                if action == "redispatch":
                    step = body.get("step")
                    if not step:
                        return self._json({"error": "step is required"}, 400)
                    resume_in_thread(self.state_dir, mission_id, redispatch=step)
                    return self._json({"redispatched": step, "resumed": True})
                if action == "reset":
                    reset_mission(load_mission(mission_file), state_dir=self.state_dir)
                    return self._json({"reset": True})
                return self._json({"error": "unknown action"}, 404)
        except (MissionError, KeyError, FileNotFoundError) as exc:
            return self._json({"error": str(exc)}, 400)
        return self._json({"error": "not found"}, 404)


def approvals_path_for(state_dir: Path) -> Path:
    """The UI-scoped approvals store: state-dir-local so `skye ui` approves what its resumes read."""
    return state_dir / "approvals.json"


def serve(state_dir: Path | None = None, port: int = 8787) -> None:
    state_dir = Path(state_dir or default_state_dir()).resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    token_file = state_dir / "ui-token"
    token_file.write_text(token + "\n", "utf-8")
    os.chmod(token_file, 0o600)
    server = ThreadingHTTPServer(("127.0.0.1", port), UiHandler)
    server.state_dir = state_dir  # type: ignore[attr-defined]
    server.token = token  # type: ignore[attr-defined]
    url = f"http://127.0.0.1:{port}/?token={token}"
    print(f"skye ui {__version__} serving {state_dir}")
    print(f"open: {url}")
    print("localhost only; the URL carries this run's session token.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        token_file.unlink(missing_ok=True)
