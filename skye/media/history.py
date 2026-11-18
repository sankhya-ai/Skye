"""Undo and redo for every project change: saves, agent edits, splits and merges.

An entry holds the cut list plus the files of only the shots it touched, before and after.
Shot folders are never deleted. A merged-away or undone shot stays on disk, so redo is exact.
"""
from __future__ import annotations

from pathlib import Path

from .model import MediaError, UNIT_FILES, atomic, files_for, load, lock, read_json, revision, safe_file, validate_edits, write_json
from .process import thumbnail

MAX_ENTRIES = 100


def capture(root: Path, ids: list[str]) -> dict:
    return {"units": [dict(u) for u in load(root)["units"]], "files": {i: files_for(root, i) for i in ids}}


def _write_shot(root: Path, unit: dict, files: dict | None, retime: bool):
    folder = root / "units" / unit["id"]
    folder.mkdir(parents=True, exist_ok=True)
    if files is not None:
        validate_edits(files, unit["end"] - unit["start"])
        write_json(safe_file(root, f"units/{unit['id']}/edit.json"), files)
        for name in UNIT_FILES:
            atomic(safe_file(root, f"units/{unit['id']}/{name}"), files[name].encode())
    if retime:  # new or re-timed shot: its thumbnail and mirror must match the cut
        thumbnail(safe_file(root, "source.webm"), unit["start"], unit["end"], folder / "thumb.jpg")
        write_json(folder / "unit.json", unit)


def apply_state(root: Path, state: dict):
    """Shot files first, project.json last: the cut list is the commit point."""
    by_id = {u["id"]: u for u in state["units"]}
    p = load(root)
    current = {u["id"]: u for u in p["units"]}
    moved = {i: current.get(i) != u for i, u in by_id.items()}
    for shot_id, files in state["files"].items():
        _write_shot(root, by_id[shot_id], files, moved[shot_id])
    for u in state["units"]:
        if u["id"] not in state["files"]:
            _write_shot(root, u, None, moved[u["id"]])
    cuts = lambda units: [(u["start"], u["end"]) for u in units]
    if cuts(p["units"]) != cuts(state["units"]):
        p["segmentation"] = "manual"
    p["units"] = [dict(u) for u in state["units"]]
    write_json(root / "project.json", p)
    load(root)


def _index(root: Path) -> dict:
    path = root / "history" / "index.json"
    return read_json(path) if path.exists() else {"entries": [], "cursor": 0}


def record(root: Path, label: str, before: dict, after: dict):
    """Applies `after` and files it. A new change discards the redo branch."""
    (root / "history").mkdir(exist_ok=True)
    apply_state(root, after)
    index = _index(root)
    entries = index["entries"][:index["cursor"]] + [{"label": label, "before": before, "after": after}]
    entries = entries[-MAX_ENTRIES:]
    write_json(root / "history" / "index.json", {"entries": entries, "cursor": len(entries)})


def step(root: Path, expected: str, direction: int) -> dict:
    with lock(root):
        if revision(root) != expected:
            raise MediaError("Project changed. Reload first")
        index = _index(root)
        at = index["cursor"] - (1 if direction < 0 else 0)
        if not 0 <= at < len(index["entries"]):
            raise MediaError("Nothing to undo" if direction < 0 else "Nothing to redo")
        entry = index["entries"][at]
        apply_state(root, entry["before"] if direction < 0 else entry["after"])
        index["cursor"] += direction
        write_json(root / "history" / "index.json", index)
        return {"revision": revision(root), "label": entry["label"]}


def summary(root: Path) -> dict:
    index = _index(root)
    labels = [e["label"] for e in index["entries"]]
    return {"undo": labels[index["cursor"] - 1] if index["cursor"] else None,
            "redo": labels[index["cursor"]] if index["cursor"] < len(labels) else None}
