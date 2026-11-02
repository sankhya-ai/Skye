"""Live preview of a task's result: run the project's own dev server inside the task's
worktree, on a free localhost port, so the owner sees the changed site before bringing it in.

Owner-initiated only (a button in the UI) - Skye never starts a project's code on its own.
What runs is the project's own script (package.json "dev"/"start"), the same code its
developers run; a plain static site is served read-only. One preview per task; every
preview is stopped when Skye stops.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_PREVIEWS: dict[str, dict[str, Any]] = {}
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
START_TIMEOUT = 180  # seconds for the dev server to start listening
INSTALL_TIMEOUT = 600  # seconds for a first dependency install


def _free_port(start: int = 5300) -> int:
    for port in range(start, start + 200):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port for a preview")


def _package_manager(root: Path) -> str:
    for lock, pm in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"),
                     ("bun.lockb", "bun"), ("bun.lock", "bun")):
        if (root / lock).exists() and shutil.which(pm):
            return pm
    return "npm"


def detect(root: Path) -> dict[str, Any]:
    """How this tree previews: {kind: node|static|none, label, script?}."""
    pkg = root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text("utf-8"))
        except (OSError, ValueError):
            data = {}
        scripts = data.get("scripts") or {}
        script = next((s for s in ("dev", "start", "preview") if s in scripts), None)
        if script:
            deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
            framework = ("vite" if "vite" in deps else "next" if "next" in deps
                         else "astro" if "astro" in deps else "")
            pm = _package_manager(root)
            return {"kind": "node", "script": script, "framework": framework, "pm": pm,
                    "label": f"{pm} run {script}" + (f" ({framework})" if framework else "")}
    for candidate in ("index.html", "public/index.html", "dist/index.html", "site/index.html"):
        if (root / candidate).exists():
            return {"kind": "static", "dir": str((root / candidate).parent),
                    "label": f"static site ({candidate})"}
    return {"kind": "none", "label": "nothing to run"}


def _command(info: dict[str, Any], port: int) -> list[str]:
    if info["kind"] == "static":
        return [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
                "--directory", info["dir"]]
    pm, script, fw = info["pm"], info["script"], info["framework"]
    base = [pm, "run", script]
    if fw in ("vite", "astro"):
        return base + ["--", "--port", str(port), "--strictPort", "--host", "127.0.0.1"]
    if fw == "next":
        return base + ["--", "-p", str(port), "-H", "127.0.0.1"]
    return base  # generic: PORT env is set too


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _tail(entry: dict[str, Any], text: str) -> None:
    lines = entry.setdefault("log", [])
    lines.extend(l for l in text.splitlines() if l.strip())
    del lines[:-60]


def _run(mission_id: str, entry: dict[str, Any], root: Path, info: dict[str, Any]) -> None:
    env = {**os.environ, "PORT": str(entry["port"]), "BROWSER": "none", "FORCE_COLOR": "0"}
    try:
        if info["kind"] == "node" and not (root / "node_modules").exists():
            entry["phase"] = "installing dependencies"
            # Tracked like the server itself, so Stop (or Skye exiting) ends an install too.
            install = subprocess.Popen([info["pm"], "install"], cwd=root, env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, start_new_session=True)
            entry["proc"] = install
            if entry.get("stop"):
                _kill(entry)
                return
            try:
                out, _ = install.communicate(timeout=INSTALL_TIMEOUT)
            except subprocess.TimeoutExpired:
                _kill(entry)
                entry.update(status="failed", error=f"{info['pm']} install took too long")
                return
            _tail(entry, out or "")
            entry["proc"] = None  # the install is over; the server takes its place below
            if entry.get("stop"):
                return
            if install.returncode != 0:
                entry.update(status="failed", error=f"{info['pm']} install failed")
                return
        if entry.get("stop"):
            return
        entry["phase"] = "starting"
        proc = subprocess.Popen(_command(info, entry["port"]), cwd=root, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                start_new_session=True)
        entry["proc"] = proc
        if entry.get("stop"):
            # Stop arrived between launch and tracking: never leave an untracked server.
            _kill(entry)
            return

        def _drain() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                _tail(entry, line)
        threading.Thread(target=_drain, daemon=True).start()
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline and not entry.get("stop"):
            if proc.poll() is not None:
                entry.update(status="failed", error=f"the dev server exited (code {proc.returncode})")
                return
            if _port_open(entry["port"]):
                entry.update(status="ready", phase="")
                return
            time.sleep(0.4)
        if entry.get("stop"):
            _kill(entry)
        else:
            entry.update(status="failed", error="the dev server did not start listening in time")
            _kill(entry)
    except (OSError, subprocess.SubprocessError) as error:
        entry.update(status="failed", error=str(error))


def _kill(entry: dict[str, Any]) -> None:
    proc = entry.get("proc")
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass


def _public(mission_id: str, entry: dict[str, Any] | None, info: dict[str, Any],
            isolated: bool) -> dict[str, Any]:
    # isolated: the preview runs in the task's own copy (a git worktree). When False it runs
    # in the project folder itself (a task without a separate copy works there directly), so
    # an install or build writes into the owner's folder - the UI must say so.
    out: dict[str, Any] = {"mission": mission_id, "kind": info["kind"], "how": info["label"],
                           "isolated": isolated}
    if entry:
        alive = entry.get("proc") is None or entry["proc"].poll() is None
        status = entry["status"]
        if status == "ready" and not alive:
            status, entry["error"] = "failed", "the dev server stopped"
        out.update(status=status, phase=entry.get("phase", ""), error=entry.get("error", ""),
                   url=f"http://127.0.0.1:{entry['port']}/" if status == "ready" else "",
                   port=entry["port"], log=entry.get("log", [])[-25:])
    else:
        out["status"] = "stopped"
    return out


def _pick(rec: dict[str, Any]) -> tuple[str, bool]:
    """(path, isolated) for one workspace record. A recorded worktree that no longer exists
    (cleaned up after delivery) is an error, never a quiet switch to the project folder."""
    worktree = str(rec.get("worktree") or "")
    if worktree:
        if not Path(worktree).is_dir():
            raise ValueError("this task's own copy of the project was cleaned up, so there is "
                             "nothing of its own left to preview; run your project directly")
        return worktree, True
    return str(rec.get("repo") or ""), False


def _tree(state_dir: Path, mission_id: str) -> tuple[Path, bool]:
    if not _SAFE_ID.match(mission_id) or ".." in mission_id:
        raise KeyError(mission_id)
    mission_dir = Path(state_dir) / "missions" / mission_id
    if not (mission_dir / "mission.json").exists():
        raise KeyError(mission_id)
    from . import workspaces
    record = workspaces.read_record(mission_dir) or {}
    path, isolated = "", False
    if isinstance(record.get("repos"), dict) and record["repos"]:
        # multi-repo: preview the first repo with something to run
        for rec in record["repos"].values():
            candidate, cand_isolated = _pick(rec or {})
            if candidate and Path(candidate).is_dir() and detect(Path(candidate))["kind"] != "none":
                path, isolated = candidate, cand_isolated
                break
    elif record:
        path, isolated = _pick(record)
    if not path:
        # No workspace yet (the task never ran): the project folder itself.
        mission = json.loads((mission_dir / "mission.json").read_text("utf-8"))
        path = mission.get("repo") or ""
    if not path or not Path(path).is_dir():
        raise KeyError(mission_id)
    return Path(path), isolated


def tree_for(state_dir: Path, mission_id: str) -> Path:
    return _tree(state_dir, mission_id)[0]


def status(state_dir: Path, mission_id: str) -> dict[str, Any]:
    root, isolated = _tree(state_dir, mission_id)
    with _LOCK:
        return _public(mission_id, _PREVIEWS.get(mission_id), detect(root), isolated)


def start(state_dir: Path, mission_id: str) -> dict[str, Any]:
    root, isolated = _tree(state_dir, mission_id)
    info = detect(root)
    if info["kind"] == "none":
        raise ValueError("this task's files have nothing to run as a preview")
    with _LOCK:
        entry = _PREVIEWS.get(mission_id)
        if entry and entry["status"] in ("starting", "ready") and not entry.get("stop"):
            proc = entry.get("proc")
            if proc is None or proc.poll() is None:
                return _public(mission_id, entry, info, isolated)
        entry = {"status": "starting", "phase": "preparing", "port": _free_port(),
                 "log": [], "started": time.time()}
        _PREVIEWS[mission_id] = entry
    threading.Thread(target=_run, args=(mission_id, entry, root, info),
                     name=f"preview-{mission_id}", daemon=True).start()
    return _public(mission_id, entry, info, isolated)


def stop(state_dir: Path, mission_id: str) -> dict[str, Any]:
    with _LOCK:
        entry = _PREVIEWS.pop(mission_id, None)
    if entry:  # stop what runs even if its folder has since gone away
        entry["stop"] = True
        _kill(entry)
    try:
        root, isolated = _tree(state_dir, mission_id)
    except ValueError:  # its folder is gone; it is stopped all the same
        return {"mission": mission_id, "kind": "none", "how": "", "isolated": True,
                "status": "stopped"}
    return _public(mission_id, None, detect(root), isolated)


def stop_all() -> None:
    with _LOCK:
        entries = list(_PREVIEWS.values())
        _PREVIEWS.clear()
    for entry in entries:
        entry["stop"] = True
        _kill(entry)
