"""Versioned media files, validation and optimistic single-shot transactions."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from pathlib import Path


class MediaError(ValueError):
    pass


LOCKS: dict[str, threading.RLock] = {}
_LOCK = threading.Lock()
UNIT_FILES = ("visual.html", "captions.json", "audio.json")


def lock(root: Path):
    with _LOCK:
        return LOCKS.setdefault(str(root.resolve()), threading.RLock())


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic(path: Path, data: bytes):
    temp = path.with_name(path.name + ".tmp")
    with temp.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def write_json(path: Path, data):
    atomic(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode())


def read_json(path: Path):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise MediaError(f"Cannot read {path.name}: {exc}") from exc


def project_dir(state: Path, project_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{16}", project_id):
        raise MediaError("Invalid video project")
    root = state / "media" / project_id
    if root.is_symlink() or not root.is_dir():
        raise MediaError("Video project not found")
    return root


def safe_file(root: Path, relative: str) -> Path:
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise MediaError("Media path escapes project")
    # Refuse symlinked parents too, including ones resolving within the project.
    if any(p.is_symlink() for p in (path, *path.parents) if p != root.parent):
        raise MediaError("Symlinks are not media files")
    return path


def load(root: Path) -> dict:
    p = read_json(safe_file(root, "project.json"))
    if p.get("version") != 1 or p.get("fps") != 30:
        raise MediaError("Unsupported video project")
    if not isinstance(p.get("width"), int) or not 2 <= p["width"] <= 1920:
        raise MediaError("Invalid canvas width")
    if not isinstance(p.get("height"), int) or not 2 <= p["height"] <= 1920:
        raise MediaError("Invalid canvas height")
    end = 0
    seen = set()
    for u in p.get("units", []):
        if not re.fullmatch(r"shot-[0-9]{4}", u.get("id", "")) or u["id"] in seen:
            raise MediaError("Invalid shot id")
        seen.add(u["id"])
        if type(u.get("start")) is not int or type(u.get("end")) is not int:
            raise MediaError("Shot boundaries must be integer frames")
        if u["start"] != end or u["end"] <= end:
            raise MediaError("Shot boundaries must be contiguous and fixed")
        end = u["end"]
    if not seen or end != p.get("frames"):
        raise MediaError("Shots do not cover the source")
    return p


def unit(root: Path, shot_id: str) -> dict:
    return next((u for u in load(root)["units"] if u["id"] == shot_id), None) or _missing()


def _missing():
    raise MediaError("Shot not found")


def validate_edits(files: dict, frames: int):
    if set(files) != set(UNIT_FILES) or any(not isinstance(v, str) for v in files.values()):
        raise MediaError("A shot contains visual.html, captions.json and audio.json only")
    if len(files["visual.html"].encode()) > 128 * 1024:
        raise MediaError("Shot code exceeds 128 KB")
    if "frame" not in files["visual.html"]:
        raise MediaError("Shot code must define window.frame(t)")
    try:
        captions = json.loads(files["captions.json"])
        audio = json.loads(files["audio.json"])
    except ValueError as exc:
        raise MediaError("Invalid shot JSON") from exc
    if not isinstance(captions, list) or len(captions) > 1000:
        raise MediaError("Captions must be a list, at most 1000 entries")
    for c in captions:
        if not isinstance(c, dict) or set(c) != {"start", "end", "text"}:
            raise MediaError("Caption needs start, end and text")
        if type(c["start"]) is not int or type(c["end"]) is not int:
            raise MediaError("Caption times must be integer local frames")
        if not 0 <= c["start"] < c["end"] <= frames:
            raise MediaError("Caption crosses the shot boundary")
        if not isinstance(c["text"], str) or len(c["text"]) > 500:
            raise MediaError("Caption text exceeds 500 characters")
    if not isinstance(audio, dict) or set(audio) != {"gain", "mute", "fade_frames"}:
        raise MediaError("Audio needs gain, mute and fade_frames")
    if type(audio["gain"]) not in (float, int) or not math.isfinite(audio["gain"]) or not 0 <= audio["gain"] <= 4:
        raise MediaError("Audio gain must be 0 to 4")
    if type(audio["mute"]) is not bool or type(audio["fade_frames"]) is not int:
        raise MediaError("Invalid audio settings")
    if not 0 <= audio["fade_frames"] <= frames // 2:
        raise MediaError("Audio fade exceeds shot boundary")


def files_for(root: Path, shot_id: str) -> dict:
    u = unit(root, shot_id)
    committed = safe_file(root, f'units/{u["id"]}/edit.json')
    files = read_json(committed) if committed.exists() else {
        name: safe_file(root, f'units/{u["id"]}/{name}').read_text("utf-8") for name in UNIT_FILES}
    validate_edits(files, u["end"] - u["start"])
    return files


def revision(root: Path) -> str:
    h = hashlib.sha256(safe_file(root, "project.json").read_bytes())
    for u in load(root)["units"]:
        for name, body in files_for(root, u["id"]).items():
            h.update(name.encode() + b"\0" + body.encode())
    return h.hexdigest()


def position(root: Path, shot_id: str) -> int:
    return next(i for i, u in enumerate(load(root)["units"], 1) if u["id"] == shot_id)


def update(root: Path, shot_id: str, expected: str, files: dict, label: str | None = None) -> str:
    """Only supplied files enter the trusted tree. Workers never write there directly."""
    from . import history
    with lock(root):
        if revision(root) != expected:
            raise MediaError("Project changed. Reload before saving")
        u = unit(root, shot_id)
        validate_edits(files, u["end"] - u["start"])
        before = history.capture(root, [shot_id])
        after = {"units": before["units"], "files": {shot_id: files}}
        history.record(root, label or f"Edit shot {position(root, shot_id)}", before, after)
        return revision(root)
