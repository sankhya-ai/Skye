"""Split and merge shots without losing edits. Every change can be undone."""
from __future__ import annotations

import json
from pathlib import Path

from . import history
from .importer import TEMPLATE
from .model import MediaError, files_for, load, lock, revision

FRESH_AUDIO = {"gain": 1, "mute": False, "fade_frames": 0}


def _next_id(root: Path) -> str:
    used = [int(p.name[5:]) for p in (root / "units").glob("shot-[0-9][0-9][0-9][0-9]")]
    return f"shot-{max(used, default=0) + 1:04d}"


def _locate(root: Path, expected: str, shot_id: str):
    if revision(root) != expected:
        raise MediaError("Project changed. Reload first")
    units = load(root)["units"]
    index = next((i for i, u in enumerate(units) if u["id"] == shot_id), None)
    if index is None:
        raise MediaError("Shot not found")
    return units, index


def split(root: Path, expected: str, shot_id: str, frame: int) -> dict:
    """Both halves keep the code and audio settings. Captions follow the cut."""
    with lock(root):
        units, i = _locate(root, expected, shot_id)
        u = units[i]
        if type(frame) is not int or not u["start"] < frame < u["end"]:
            raise MediaError("Move the playhead inside the shot first")
        files = files_for(root, shot_id)
        k = frame - u["start"]
        captions = json.loads(files["captions.json"])
        audio = json.loads(files["audio.json"])
        first = [{**c, "end": min(c["end"], k)} for c in captions if c["start"] < k]
        second = [{**c, "start": max(c["start"] - k, 0), "end": c["end"] - k} for c in captions if c["end"] > k]
        def half(captions, length):
            return {"visual.html": files["visual.html"], "captions.json": json.dumps(captions, ensure_ascii=False, indent=2),
                    "audio.json": json.dumps({**audio, "fade_frames": min(audio["fade_frames"], length // 2)}, indent=2)}
        new_id = _next_id(root)
        a = {"id": shot_id, "start": u["start"], "end": frame}
        b = {"id": new_id, "start": frame, "end": u["end"]}
        before = history.capture(root, [shot_id])
        after = {"units": [*units[:i], a, b, *units[i + 1:]],
                 "files": {shot_id: half(first, k), new_id: half(second, u["end"] - frame)}}
        history.record(root, f"Split shot {i + 1}", before, after)
        return {"revision": revision(root), "shot": new_id, "notes": []}


def merge(root: Path, expected: str, shot_id: str) -> dict:
    """Joins a shot with the next. Code and audio of one side must win, and the note says which."""
    with lock(root):
        units, i = _locate(root, expected, shot_id)
        if i + 1 >= len(units):
            raise MediaError("There is no next shot to merge")
        left, right = units[i], units[i + 1]
        lf, rf = files_for(root, left["id"]), files_for(root, right["id"])
        notes = []
        code = lf["visual.html"]
        if code == TEMPLATE and rf["visual.html"] != TEMPLATE:
            code = rf["visual.html"]
        elif rf["visual.html"] not in (TEMPLATE, code):
            notes.append(f"Shot {i + 2} code was dropped. Undo to get it back.")
        audio = lf["audio.json"]
        if json.loads(rf["audio.json"]) != FRESH_AUDIO and json.loads(rf["audio.json"]) != json.loads(audio):
            notes.append(f"Shot {i + 2} audio settings were dropped.")
        shift = left["end"] - left["start"]
        captions = json.loads(lf["captions.json"]) + [{**c, "start": c["start"] + shift, "end": c["end"] + shift}
                                                      for c in json.loads(rf["captions.json"])]
        merged = {"id": left["id"], "start": left["start"], "end": right["end"]}
        before = history.capture(root, [left["id"], right["id"]])
        after = {"units": [*units[:i], merged, *units[i + 2:]],
                 "files": {left["id"]: {"visual.html": code, "audio.json": audio,
                                         "captions.json": json.dumps(captions, ensure_ascii=False, indent=2)}}}
        history.record(root, f"Merge shots {i + 1} and {i + 2}", before, after)
        return {"revision": revision(root), "shot": left["id"], "notes": notes}
