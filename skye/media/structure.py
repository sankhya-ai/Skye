"""Explicit boundary changes before editing. Never silently ripple a shot edit."""
from __future__ import annotations
import shutil
import tempfile
from pathlib import Path
from .importer import TEMPLATE
from .model import MediaError, files_for, load, lock, revision, write_json
from .process import ffmpeg


def recut(root: Path, expected: str, cuts: list[int]):
    with lock(root):
        if revision(root) != expected:
            raise MediaError("Project changed. Reload before changing cuts")
        p = load(root)
        if (not isinstance(cuts, list) or any(type(n) is not int for n in cuts)
                or len(cuts) < 2 or cuts[0] != 0 or cuts[-1] != p["frames"]
                or sorted(set(cuts)) != cuts or len(cuts) > 501):
            raise MediaError("Cuts must cover the video in ordered integer frames")
        for u in p["units"]:
            f = files_for(root, u["id"])
            if (f["visual.html"] != TEMPLATE or f["captions.json"].strip() != "[]"
                    or __import__('json').loads(f["audio.json"]) != {"gain": 1, "mute": False, "fade_frames": 0}):
                raise MediaError("Adjust cuts before editing. Reimport to create a different cut without losing edits")
        with tempfile.TemporaryDirectory(prefix="cuts-", dir=root) as tmp:
            folder = Path(tmp) / "units"
            folder.mkdir()
            units = []
            for i, (start, end) in enumerate(zip(cuts, cuts[1:])):
                u = {"id": f"shot-{i+1:04d}", "start": start, "end": end}
                units.append(u)
                shot = folder / u["id"]
                shot.mkdir()
                write_json(shot / "unit.json", u)
                (shot / "visual.html").write_text(TEMPLATE, "utf-8")
                write_json(shot / "captions.json", [])
                write_json(shot / "audio.json", {"gain": 1, "mute": False, "fade_frames": 0})
                write_json(shot / "edit.json", {name: (shot / name).read_text("utf-8") for name in ("visual.html", "captions.json", "audio.json")})
                ffmpeg("-ss", str(start/30), "-i", str(root / "source.webm"), "-frames:v", "1",
                       "-vf", "scale=320:-2", str(shot / "thumb.jpg"))
            previous = Path(tmp) / "previous"
            (root / "units").rename(previous)
            try:
                folder.rename(root / "units")
                p["units"] = units
                p["segmentation"] = "manual"
                write_json(root / "project.json", p)
            except BaseException:
                shutil.rmtree(root / "units", ignore_errors=True)
                previous.rename(root / "units")
                raise
        return revision(root)
