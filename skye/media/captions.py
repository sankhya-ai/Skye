"""Caption import from SRT or WebVTT, split across shots at the cuts."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import history
from .model import MediaError, files_for, load, lock, revision

CUE = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*-->\s*(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")


def _frame(h, m, s, ms, fps=30) -> int:
    return round(((int(h or 0) * 60 + int(m)) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000) * fps)


def parse(text: str) -> list[tuple[int, int, str]]:
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\ufeff", "")):
        lines = [ln for ln in block.strip().split("\n") if ln.strip()]
        for i, line in enumerate(lines):
            m = CUE.search(line)
            if m:
                g = m.groups()
                body = re.sub(r"<[^>]+>", "", "\n".join(lines[i + 1:])).strip()
                if body:
                    cues.append((_frame(*g[:4]), _frame(*g[4:]), body[:500]))
                break
    if not cues:
        raise MediaError("No captions found. Use an SRT or WebVTT file")
    return cues


def import_captions(root: Path, expected: str, text: str) -> dict:
    """Adds to existing captions. One undo step reverts the whole import."""
    cues = parse(text)
    with lock(root):
        if revision(root) != expected:
            raise MediaError("Project changed. Reload first")
        units = load(root)["units"]
        files, added = {}, 0
        for u in units:
            f = files_for(root, u["id"])
            items = json.loads(f["captions.json"])
            new = [{"start": max(s, u["start"]) - u["start"], "end": min(e, u["end"]) - u["start"], "text": t}
                   for s, e, t in cues if s < u["end"] and e > u["start"] and min(e, u["end"]) > max(s, u["start"])]
            if new:
                merged = sorted(items + new, key=lambda c: (c["start"], c["end"]))[:1000]
                files[u["id"]] = {**f, "captions.json": json.dumps(merged, ensure_ascii=False, indent=2)}
                added += len(new)
        if not added:
            raise MediaError("These captions fall outside the video")
        before = history.capture(root, list(files))
        history.record(root, "Import captions", before, {"units": before["units"], "files": files})
        return {"revision": revision(root), "added": added, "shots": len(files)}
