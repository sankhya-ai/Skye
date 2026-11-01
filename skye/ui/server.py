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
from skye import approvals, backlog, gateway, issues, projects, roles
from skye.contract import ADAPTERS, ROLES
from skye.mission import (Journal, MissionError, default_state_dir, load_mission,
                          mission_status, reset_mission, run_mission)
from skye import journey as journey_runner
from skye.policy import Policy

_VERIFY_LOCKS: dict[str, threading.Lock] = {}
_VERIFY_LOCKS_GUARD = threading.Lock()

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


ARTIFACTS_LIST_LIMIT = 200


def mission_artifacts(mission_dir: Path) -> dict:
    """The real file changes of a mission, diffed from the start/end repo manifests the engine
    recorded (skye.journey.repo_manifest). Derived from the tree, not from worker claims.
    complete=False means the mission has not settled done yet, so only the baseline exists."""
    artifacts_file = mission_dir / "artifacts.json"
    if not artifacts_file.exists():
        return {"recorded": False}
    record = json.loads(artifacts_file.read_text("utf-8"))
    start = record.get("start") or {}
    end = record.get("end")
    out = {"recorded": True, "repo": record.get("repo", ""),
           "start_truncated": bool(record.get("start_truncated")),
           "complete": end is not None}
    if end is None:
        return out
    from ..journey import diff_manifests
    diff = diff_manifests(start, end)
    for kind in ("added", "modified", "deleted"):
        paths = diff[kind]
        out[kind] = paths[:ARTIFACTS_LIST_LIMIT]
        if len(paths) > ARTIFACTS_LIST_LIMIT:
            out[f"{kind}_overflow"] = len(paths) - ARTIFACTS_LIST_LIMIT
    out["end_truncated"] = bool(record.get("end_truncated"))
    return out


ARTIFACT_FILE_TYPES = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
                       ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
                       ".md": "text/markdown; charset=utf-8"}
ARTIFACT_FILE_LIMIT = 5 * 1024 * 1024


def artifact_file(state_dir: Path, mission_id: str, rel_path: str) -> tuple[bytes, str]:
    """Serve one file the mission actually changed, for inline preview. Strictly confined:
    the path must be in the mission's recorded added/modified diff (never an arbitrary
    path), must resolve inside the mission's repo, and must be a previewable type."""
    mission_dir = state_dir / "missions" / mission_id
    artifacts = mission_artifacts(mission_dir)
    if not artifacts.get("complete"):
        raise KeyError(rel_path)
    changed = set(artifacts.get("added") or []) | set(artifacts.get("modified") or [])
    if rel_path not in changed:
        raise KeyError(rel_path)
    suffix = Path(rel_path).suffix.lower()
    if suffix not in ARTIFACT_FILE_TYPES:
        raise KeyError(rel_path)
    root = Path(artifacts.get("repo") or "").resolve()
    target = (root / rel_path).resolve()
    if root not in target.parents or not target.is_file():
        raise KeyError(rel_path)
    if target.stat().st_size > ARTIFACT_FILE_LIMIT:
        raise ValueError("file too large to preview")
    return target.read_bytes(), ARTIFACT_FILE_TYPES[suffix]


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
    # A needs-owner step whose journaled contract fingerprint is already in the approvals store
    # is approved and waiting for a resume - a different state from still-blocked, and the UI
    # shows it as such.
    approved_fps = {a.get("fingerprint") for a in approvals.pending(approvals_path_for(state_dir))}
    needs_owner_fps = {e.get("fingerprint") for e in journal.step_states().values()
                       if e.get("event") == "needs-owner"}
    approved = bool(needs_owner_fps & approved_fps)

    return {"mission": mission, "status": status, "artifacts": mission_artifacts(mission_dir),
            "journal": _read_jsonl(mission_dir / "journal.jsonl"),
            "dynamic_steps": dynamic, "pending_contracts": pending,
            "approved": approved and not pending}


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
            project_id = payload["mission"].get("project", "")
            # journal ts is numeric epoch; the API contract (and the sidebar's string sort)
            # expects a string - normalize at the boundary, never leak the raw type.
            last = str(journal[-1]["ts"]) if journal else None
            status = payload["status"]
            dynamic_ids = {str(s.get("id")) for s in payload["dynamic_steps"]}
            authored_ids = {s["id"] for s in status["steps"]}
            # lifecycle projection, not last-entry-per-step: proposal events are journaled under
            # the PROPOSING step's id and must not mask its settled state.
            states = Journal(mission_dir / "journal.jsonl").step_states()
            all_ids = authored_ids | dynamic_ids

            def _settled(sid, want_done):
                entry = states.get(sid, {})
                if entry.get("event") != "settled":
                    return False
                return (entry.get("status") == "done") == want_done

            rows.append({"id": mission_dir.name, "goal": payload["mission"].get("goal", ""),
                         "project": project_id,
                         "overall": status["status"],
                         "needs_owner": bool(payload["pending_contracts"]),
                         "approved": payload["approved"],
                         "steps": len(all_ids),
                         "done": sum(1 for sid in all_ids if _settled(sid, True)),
                         "failed": sum(1 for sid in all_ids if _settled(sid, False)),
                         "last_activity": last,
                         "live": _live_missions.get(mission_dir.name, {}).get("running", False)})
    rows.sort(key=lambda r: r["last_activity"] or "", reverse=True)
    return rows


def roles_payload(state_dir: Path) -> dict:
    """Skye's real dynamic roles: builtins plus the owner's registry specialists, each with its
    tool catalogue and observed usage from mission journals. No decorative personas - every
    role listed here can actually be dispatched, and a role added here really validates and
    runs (skye/roles.py)."""
    described = roles.describe()
    usage: dict[str, dict] = {row["name"]: {"steps": 0, "done": 0, "failed": 0}
                              for row in described}
    missions_root = state_dir / "missions"
    if missions_root.exists():
        for journal_path in missions_root.glob("*/journal.jsonl"):
            for entry in _read_jsonl(journal_path):
                role = entry.get("role")
                if role in usage and entry.get("event") == "settled":
                    usage[role]["steps"] += 1
                    if entry.get("status") == "done":
                        usage[role]["done"] += 1
                    else:
                        usage[role]["failed"] += 1
    rows = []
    for row in described:
        catalogue = roles.catalogue_for(row["name"]) if row["source"] != "builtin" else None
        rows.append({**row, **usage[row["name"]],
                     "catalogue": catalogue or {k: gateway.BUILTIN[row["name"]][k]
                                                for k in ("tools", "writable")}})
    return {"roles": rows, "adapters": list(ADAPTERS)}


def policy_path_default() -> Path:
    return Path(os.environ.get("SKYE_POLICY", "").strip() or "skye.policy.json")


PLANNER_BRIEF = """You are the planner for this goal:

{goal}

Break it into concrete specialist steps and reply with JSON exactly of the form
{{"propose_steps": [{{"id": "...", "role": "...", "adapter": "...", "goal": "...",
"brief": "...", "needs": ["..."]}}]}}.
Name roles from the team's role registry (builtins plus owner-defined specialists),
keep every step small and self-contained, and use needs for steps that depend on an
earlier step's outcome. Do not do the work yourself - your reply becomes the workflow.
If you see follow-up product work beyond this goal, also propose it for the owner's
backlog in the same reply: "propose_features": [{"title": "...", "brief": "..."}] -
proposals wait for the owner's approval before any work starts.
"""


def create_mission(state_dir: Path, spec: dict) -> str:
    """Validate a UI-authored mission exactly like a mission file, then snapshot it so
    resume/run read the recorded definition. With {"plan": true} the mission starts as a
    single PLANNER step whose worker proposes the real workflow at runtime through the
    mission engine's validated propose-steps machinery - nothing is hardcoded here.
    Returns the mission id."""
    import tempfile
    goal = str(spec.get("goal") or "").strip()
    steps = spec.get("steps") or []
    if spec.get("plan"):
        steps = [{"id": "plan", "role": str(spec.get("planner_role") or "pm"),
                  "adapter": str(spec.get("adapter") or "root"), "goal": goal,
                  "brief": PLANNER_BRIEF.replace("{goal}", goal), "gates": [],
                  "budget": {"max_steps": 12}}]
    project_id = str(spec.get("project") or "").strip()
    repo = str(spec.get("repo") or "").strip()
    if project_id:
        project = projects.get(project_id)
        if project is None:
            raise MissionError(f"unknown project '{project_id}'")
        repo = repo or project["repo"]
    mission_id = str(spec.get("id") or "").strip() or f"mission-{secrets.token_hex(4)}"
    mission = {"id": mission_id, "goal": goal, "repo": repo or os.getcwd(),
               "steps": steps}
    if project_id:
        mission["project"] = project_id
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(mission, handle)
        temp_path = handle.name
    try:
        validated = load_mission(temp_path)  # the same validation `skye mission run` applies
    finally:
        os.unlink(temp_path)
    mission_dir = state_dir / "missions" / mission_id
    if mission_dir.exists():
        raise MissionError(f"mission {mission_id} already exists")
    mission_dir.mkdir(parents=True)
    (mission_dir / "mission.json").write_text(
        json.dumps(validated, indent=2, sort_keys=True) + "\n", "utf-8")
    return mission_id


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
                if url.path == "/api/backlog":
                    return self._json({"features": backlog.list_features()})
                if url.path == "/api/issues":
                    return self._json({"issues": issues.list_issues()})
                if url.path == "/api/projects":
                    return self._json({"projects": projects.list_projects()})
                if url.path == "/api/roles":
                    return self._json(roles_payload(self.state_dir))
                if url.path == "/api/policy":
                    path = policy_path_default()
                    raw = json.loads(path.read_text("utf-8")) if path.exists() else {}
                    policy = Policy.load(path)
                    return self._json({"raw": raw, "path": str(path.resolve()),
                                       "max_concurrent": policy.max_concurrent})
                if url.path.startswith("/api/mission-artifact/"):
                    mission_id = url.path.rsplit("/", 1)[-1]
                    rel_path = (parse_qs(url.query).get("path") or [""])[0]
                    data, content_type = artifact_file(self.state_dir, mission_id, rel_path)
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if url.path.startswith("/api/missions/"):
                    mission_id = url.path.rsplit("/", 1)[-1]
                    payload = mission_payload(self.state_dir, mission_id)
                    payload["live"] = _live_missions.get(mission_id, {})
                    return self._json(payload)
            except KeyError:
                return self._json({"error": "unknown mission or artifact"}, 404)
            except ValueError as error:
                return self._json({"error": str(error)}, 400)
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
            if url.path == "/api/issues":
                # Issue intake: an owner-wired error feed (production logs, frontend error
                # hook, user report) files a PENDING backlog proposal. Recording a signal
                # never starts work - the owner decides on the proposal as usual.
                try:
                    issue, created = issues.report(
                        str(body.get("kind") or ""), str(body.get("summary") or ""),
                        detail=str(body.get("detail") or ""),
                        project=str(body.get("project") or ""),
                        source=str(body.get("source") or "api"))
                except issues.IssueError as error:
                    return self._json({"error": str(error)}, 400)
                return self._json({"recorded": created, "issue": issue})
            if url.path == "/api/backlog/decide":
                try:
                    feature = backlog.decide(str(body.get("feature") or ""),
                                             str(body.get("decision") or ""),
                                             note=str(body.get("note") or ""))
                except backlog.BacklogError as error:
                    return self._json({"error": str(error)}, 400)
                return self._json({"decided": True, "feature": feature})
            if url.path == "/api/backlog/verify":
                # Replay the user journey for a feature: the owner-configured journey command
                # runs in the feature's project repo; only a pass delivers. The evidence
                # (output tail) is recorded either way. This gates DELIVERY - the owner
                # approval that started the work is untouched.
                # Only the feature id is accepted. The command, scope and policy come from
                # the owner-managed policy file alone - anything the caller supplies
                # (command, verdicts, "evidence") is untrusted and ignored.
                feature_id = str(body.get("feature") or "")
                features = {f["id"]: f for f in backlog.list_features()}
                feature = features.get(feature_id)
                if feature is None:
                    return self._json({"error": "unknown feature"}, 404)
                if feature.get("status") != "awaiting-verification" or not feature.get("mission_id"):
                    return self._json(
                        {"error": "only completed work awaiting verification can be replayed"},
                        409)
                repo = ""
                if feature.get("project"):
                    try:
                        repo = projects.get(feature["project"]).get("repo", "")
                    except Exception:
                        repo = ""
                if not repo:
                    return self._json(
                        {"error": "the feature's project has no repo to replay the journey in"}, 400)
                policy = Policy.load(policy_path_default())
                if not policy.journey_command:
                    return self._json(
                        {"error": "the team policy names no journey command; "
                                  "set journey_command in Settings first"}, 400)
                with _VERIFY_LOCKS_GUARD:
                    lock = _VERIFY_LOCKS.setdefault(feature_id, threading.Lock())
                if not lock.acquire(blocking=False):
                    return self._json(
                        {"error": "a journey replay for this work is already running"}, 409)
                try:
                    result = journey_runner.run_journey(
                        policy.journey_command, cwd=repo,
                        timeout=policy.journey_timeout_seconds)
                    updated = backlog.record_verification(
                        feature_id, result["passed"], evidence=result["evidence"],
                        repo_revision=journey_runner.repo_revision(repo),
                        journey_digest=journey_runner.journey_digest(policy.journey_command, repo))
                finally:
                    lock.release()
                verification = updated.get("verification") or {}
                return self._json({"verified": result["passed"], "feature": updated,
                                   "command": policy.journey_command,
                                   "evidence": result["evidence"],
                                   "timed_out": result["timed_out"],
                                   "repo_revision": verification.get("repo_revision", ""),
                                   "journey_changed": verification.get("journey_changed", False)})
            if url.path == "/api/backlog/rework":
                try:
                    feature = backlog.send_back(str(body.get("feature") or ""),
                                                note=str(body.get("note") or ""))
                except backlog.BacklogError as error:
                    return self._json({"error": str(error)}, 409)
                return self._json({"sent_back": True, "feature": feature})
            if url.path == "/api/backlog/start":
                feature_id = str(body.get("feature") or "")
                features = {f["id"]: f for f in backlog.list_features()}
                feature = features.get(feature_id)
                if feature is None:
                    return self._json({"error": "unknown feature"}, 404)
                if feature["status"] != "approved":
                    return self._json(
                        {"error": "only an owner-approved feature can start work"}, 409)
                mission_id = create_mission(
                    self.state_dir,
                    {"goal": f"{feature['title']}: {feature['brief']}", "plan": True,
                     "project": feature.get("project", ""), "adapter": body.get("adapter")})
                backlog.mark_started(feature_id, mission_id)
                if body.get("run"):
                    resume_in_thread(self.state_dir, mission_id)
                return self._json({"id": mission_id, "running": bool(body.get("run"))})
            if url.path == "/api/projects":
                record = body.get("project")
                if not isinstance(record, dict):
                    return self._json({"error": "project must be a JSON object"}, 400)
                try:
                    saved = projects.add(record)
                except projects.ProjectError as error:
                    return self._json({"error": str(error)}, 400)
                return self._json({"saved": True, "project": saved})
            if url.path == "/api/roles":
                record = body.get("role")
                if not isinstance(record, dict):
                    return self._json({"error": "role must be a JSON object"}, 400)
                try:
                    saved = roles.add(record)  # validated; a bad role fails before any worker sees it
                except roles.RoleError as error:
                    return self._json({"error": str(error)}, 400)
                return self._json({"saved": True, "role": saved})
            if url.path == "/api/policy":
                data = body.get("policy")
                if not isinstance(data, dict):
                    return self._json({"error": "policy must be a JSON object"}, 400)
                Policy(data)  # validate before touching disk
                path = policy_path_default()
                path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
                return self._json({"saved": True, "path": str(path.resolve())})
            if url.path == "/api/missions":
                mission_id = create_mission(self.state_dir, body)
                if body.get("run"):
                    resume_in_thread(self.state_dir, mission_id)
                return self._json({"id": mission_id, "running": bool(body.get("run"))})
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
