"""Token-guarded browser workspace routes, called only after the UI's auth checks."""
from __future__ import annotations

import json
import re
import tempfile
import threading
import uuid
from pathlib import Path
from urllib.parse import unquote
from . import handoff
from .importer import import_video
from .model import (MediaError, files_for, load, lock, project_dir, read_json, revision,
                    safe_file, update, write_json)
from .renderer import render

_JOBS: dict[str, dict] = {}
_GATE = threading.Lock()  # one heavy job per Skye process; M1 8GB is the baseline


def job_start(state: Path, kind: str, task) -> dict:
    if not _GATE.acquire(blocking=False):
        raise MediaError("A video job is already running")
    job_id = uuid.uuid4().hex[:16]
    jobs = state / "media-jobs"
    try:
        jobs.mkdir(parents=True, exist_ok=True)
    except OSError:
        _GATE.release()
        raise
    current = {"id": job_id, "kind": kind, "status": "running"}
    _JOBS[job_id] = current
    try:
        write_json(jobs / (job_id + ".json"), current)
    except OSError:
        _JOBS.pop(job_id, None)
        _GATE.release()
        raise
    def progress(data):
        current.update(data)
        write_json(jobs / (job_id + ".json"), current)
    def worker():
        try:
            current["result"] = task(progress)
            current["status"] = "done"
        except Exception as exc:
            current.update(status="failed", error=str(exc)[-4000:])
        finally:
            try:
                write_json(jobs / (job_id + ".json"), current)
            finally:
                _JOBS.pop(job_id, None)
                _GATE.release()
    initial = dict(current)
    threading.Thread(target=worker, daemon=True, name="skye-video-" + kind).start()
    return initial


def snapshot(root: Path):
    with lock(root):
        p = load(root)
        p["revision"] = revision(root)
        latest = safe_file(root, "latest.json")
        p["latest"] = read_json(latest) if latest.exists() else None
        p["stale"] = bool(p["latest"] and p["latest"]["revision"] != p["revision"])
        return p


def blob(handler, path: Path, content_type: str):
    size = path.stat().st_size
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(size))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            handler.wfile.write(chunk)


def get(handler, path: str) -> bool:
    if not path.startswith("/api/media"):
        return False
    state = handler.state_dir
    try:
        parts = path.removeprefix("/api/media").strip("/").split("/")
        if parts == [""]:
            base = state / "media"
            items = []
            if base.exists():
                for folder in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                    if re.fullmatch(r"[a-f0-9]{16}", folder.name):
                        p = load(project_dir(state, folder.name))
                        items.append({k: p[k] for k in ("id", "name", "frames", "width", "height")})
            handler._json({"projects": items})
        elif parts[0] == "jobs" and len(parts) == 2:
            if not re.fullmatch(r"[a-f0-9]{16}", parts[1]):
                raise MediaError("Unknown video job")
            result = dict(_JOBS.get(parts[1]) or read_json(state / "media-jobs" / (parts[1] + ".json")))
            if result["status"] == "running" and parts[1] not in _JOBS:
                result.update(status="failed", error="Skye restarted. Start this job again")
            handler._json(result)
        else:
            root = project_dir(state, parts[0])
            if len(parts) == 1:
                handler._json(snapshot(root))
            elif len(parts) == 3 and parts[1] == "units":
                handler._json({"files": files_for(root, parts[2]), "revision": revision(root)})
            elif len(parts) == 2 and parts[1] == "history":
                from .history import summary
                handler._json(summary(root))
            elif len(parts) == 3 and parts[1] == "thumb":
                from .model import unit
                unit(root, parts[2])
                blob(handler, safe_file(root, f"units/{parts[2]}/thumb.jpg"), "image/jpeg")
            elif len(parts) == 2 and parts[1] == "source":
                blob(handler, safe_file(root, "source.webm"), "video/webm")
            elif len(parts) == 3 and parts[1] == "exports" and re.fullmatch(r"[a-f0-9]{64}-(preview|final)\.webm", parts[2]):
                blob(handler, safe_file(root, "exports/" + parts[2]), "video/webm")
            else:
                handler._json({"error": "Unknown media route"}, 404)
    except (OSError, ValueError, KeyError) as exc:
        handler._json({"error": str(exc)}, 400)
    return True


def post(handler, path: str) -> bool:
    if not path.startswith("/api/media"):
        return False
    state = handler.state_dir
    upload = None
    old_timeout = handler.connection.gettimeout()
    handler.connection.settimeout(60)
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if path == "/api/media/import":
            if not 0 < length <= 512 * 1024 * 1024:
                raise MediaError("Use a video under 512 MB")
            if _GATE.locked():
                raise MediaError("A video job is already running")
            state.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=state, prefix=".upload-", delete=False) as stream:
                upload = Path(stream.name)
                remaining = length
                while remaining:
                    chunk = handler.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise MediaError("Upload interrupted")
                    stream.write(chunk)
                    remaining -= len(chunk)
            name = unquote(handler.headers.get("X-Video-Name", "Video"))[:120]
            detection = handler.headers.get("X-Scene-Detection", "true") == "true"
            source = upload
            def work(progress):
                try:
                    progress({"stage": "Indexing video"})
                    return import_video(state, source, scene_detection=detection, name=name)
                finally:
                    source.unlink(missing_ok=True)
            result = job_start(state, "import", work)
            upload = None  # worker owns cleanup now
            handler._json(result, 202)
        else:
            if not 0 < length <= 1024 * 1024:
                raise MediaError("Video request exceeds 1 MB")
            body = json.loads(handler.rfile.read(length))
            parts = path.removeprefix("/api/media/").split("/")
            root = project_dir(state, parts[0])
            action = parts[1] if len(parts) == 2 else ""
            if action == "save":
                rev = update(root, body["shot"], body["revision"], body["files"])
                handler._json({"revision": rev})
            elif action == "render":
                profile = body.get("profile", "preview")
                handler._json(job_start(state, "render", lambda progress: render(root, profile, progress)), 202)
            elif action == "run-edit":
                from .worker import run_edit
                proposal_id = body["proposal"]
                handler._json(job_start(state, "edit", lambda progress: run_edit(state, root, proposal_id, progress)), 202)
            elif action == "split":
                from .structure import split
                handler._json(split(root, body["revision"], body["shot"], body["frame"]))
            elif action == "merge":
                from .structure import merge
                handler._json(merge(root, body["revision"], body["shot"]))
            elif action in ("undo", "redo"):
                from .history import step
                handler._json(step(root, body["revision"], -1 if action == "undo" else 1))
            elif action == "prepare":
                handler._json(handoff.prepare(root, body["shot"], body.get("goal", ""), body.get("adapter", "claude-code")))
            elif action == "apply":
                handler._json({"revision": handoff.apply(root, body["proposal"])})
            else:
                handler._json({"error": "Unknown media action"}, 404)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        handler._json({"error": str(exc)}, 400)
    finally:
        handler.connection.settimeout(old_timeout)
        if upload:
            upload.unlink(missing_ok=True)
    return True
