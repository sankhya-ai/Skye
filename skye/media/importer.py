"""Immutable originals, fixed 30fps project frames and optional local shot detection."""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from .model import MediaError, write_json, file_hash
from .process import ffmpeg, probe

TEMPLATE = '''<!doctype html>
<style>
#title {position:absolute;left:6%;right:6%;top:8%;color:white;
font:600 42px system-ui;letter-spacing:-1px;text-shadow:0 2px 18px #0008}
</style>
<div id="title"></div>
<script>
// t is local seconds. The source canvas and captions are composed separately.
// No network, external files, wall-clock animations or audio in this page.
window.frame = (t) => {
  const title = document.getElementById('title');
  title.textContent = '';
  title.style.opacity = String(Math.min(1, t * 3));
};
</script>
'''


def detect(path: Path, frames: int) -> list[int]:
    try:
        from scenedetect import open_video, SceneManager
        from scenedetect.detectors import AdaptiveDetector
    except ImportError as exc:
        raise MediaError("Install skye[media] for scene detection, or choose one shot") from exc
    video = open_video(str(path))
    manager = SceneManager()
    manager.add_detector(AdaptiveDetector(min_scene_len=15))
    manager.auto_downscale = True
    manager.detect_scenes(video=video, show_progress=False)
    return sorted({0, frames, *(int(start.get_frames()) for start, _ in manager.get_scene_list())})


def import_video(state: Path, source: Path, *, scene_detection=True, cuts=None, name=None) -> dict:
    if source.is_symlink() or not source.is_file() or source.stat().st_size > 512 * 1024 * 1024:
        raise MediaError("Use a regular video file under 512 MB")
    info = probe(source)
    if info["color_transfer"] in ("smpte2084", "arib-std-b67"):
        raise MediaError("HDR needs a color-managed conversion to SDR before import")
    project_id = uuid.uuid4().hex[:16]
    media = state / "media"
    media.mkdir(mode=0o700, parents=True, exist_ok=True)
    stage = media / (".import-" + project_id)
    stage.mkdir(mode=0o700)
    try:
        shutil.copyfile(source, stage / "original.bin")
        # A single CFR mezzanine avoids guessing VFR source PTS during each shot render.
        # VP9 + Opus are non-GPL codecs. No binary is bundled with Skye.
        filters = "scale=w=min(1920\\,iw):h=min(1080\\,ih):force_original_aspect_ratio=decrease:force_divisible_by=2,fps=30,setsar=1"
        ffmpeg("-i", str(stage / "original.bin"), "-map", "0:v:0", "-map", "0:a:0?",
               "-vf", filters, "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "6",
               "-crf", "24", "-b:v", "0", "-pix_fmt", "yuv420p", "-c:a", "libopus",
               "-ar", "48000", str(stage / "source.webm"))
        normalized = probe(stage / "source.webm")
        # Count decoded frames, not format duration, which can include Opus padding.
        from .process import run, binary
        count = json.loads(run([binary("ffprobe"), "-v", "error", "-select_streams", "v:0",
                               "-count_frames", "-show_entries", "stream=nb_read_frames", "-of", "json",
                               str(stage / "source.webm")]))
        frames = int(count["streams"][0]["nb_read_frames"])
        boundaries = cuts if cuts is not None else detect(stage / "source.webm", frames) if scene_detection else [0, frames]
        if (not isinstance(boundaries, list) or not 2 <= len(boundaries) <= 501 or boundaries[0] != 0
                or boundaries[-1] != frames or any(type(n) is not int for n in boundaries)
                or sorted(set(boundaries)) != boundaries):
            raise MediaError("Cuts must be ordered project frame indices including 0 and the end")
        units = []
        for i, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
            shot_id = f"shot-{i + 1:04d}"
            folder = stage / "units" / shot_id
            folder.mkdir(parents=True)
            u = {"id": shot_id, "start": start, "end": end}
            units.append(u)
            write_json(folder / "unit.json", u)
            (folder / "visual.html").write_text(TEMPLATE, "utf-8")
            write_json(folder / "captions.json", [])
            write_json(folder / "audio.json", {"gain": 1, "mute": False, "fade_frames": 0})
            write_json(folder / "edit.json", {name: (folder / name).read_text("utf-8") for name in ("visual.html", "captions.json", "audio.json")})
            ffmpeg("-ss", str(start / 30), "-i", str(stage / "source.webm"), "-frames:v", "1",
                   "-vf", "scale=320:-2", str(folder / "thumb.jpg"))
        project = {"version": 1, "id": project_id, "name": (name or source.stem)[:120],
                   "fps": 30, "frames": frames, "width": normalized["width"], "height": normalized["height"],
                   "source_hash": file_hash(stage / "source.webm"),
                   "original_hash": file_hash(stage / "original.bin"), "has_audio": normalized["audio"],
                   "segmentation": "detected" if scene_detection and cuts is None else "manual", "units": units}
        write_json(stage / "project.json", project)
        (stage / "original.bin").chmod(0o400)
        (stage / "source.webm").chmod(0o400)
        stage.rename(media / project_id)
        return project
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
