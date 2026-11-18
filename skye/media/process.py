"""Bounded local subprocesses. No shell strings, downloaded binaries or codec bundles."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from .model import MediaError


def run(args: list[str], timeout: int = 900) -> bytes:
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaError(f"Media command failed: {exc}") from exc
    if result.returncode:
        raise MediaError(result.stderr.decode(errors="replace")[-2500:] or "Media command failed")
    return result.stdout


def binary(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise MediaError(f"Install {name} to use local video editing")
    return found


def ffmpeg(*args: str, timeout=900) -> bytes:
    return run([binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-threads", "2", "-filter_threads", "1", *args], timeout)


def probe(path: Path) -> dict:
    data = json.loads(run([binary("ffprobe"), "-v", "error", "-show_entries", "format=duration,format_name:stream=codec_type,width,height,color_transfer,nb_frames",
                           "-of", "json", "-protocol_whitelist", "file,pipe", str(path)], 30))
    if not set(data["format"]["format_name"].split(",")) & {"mov", "mp4", "matroska", "webm", "avi", "mpegts", "mpeg", "ogg"}:
        raise MediaError("Use MP4, WebM, MOV, AVI or MPEG footage, not a playlist")
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if not v:
        raise MediaError("This file has no video")
    try:
        duration = float(data["format"]["duration"])
    except (KeyError, ValueError) as exc:
        raise MediaError("Video duration is unknown") from exc
    if not 0 < duration <= 600:
        raise MediaError("Use a video up to 10 minutes long")
    return {"width": v["width"], "height": v["height"], "duration": duration,
            "audio": any(s["codec_type"] == "audio" for s in data["streams"]),
            "color_transfer": v.get("color_transfer", "unknown"),
            "frames": int(v.get("nb_frames", 0) or 0)}


def diagnostics() -> dict:
    report = {}
    for name in ("ffmpeg", "ffprobe", "node"):
        report[name] = bool(shutil.which(name))
    try:
        import scenedetect  # optional dependency, never loaded by the main Skye install
        report["scene_detection"] = scenedetect.__version__
    except ImportError:
        report["scene_detection"] = False
    if report["ffmpeg"]:
        # -buildconf prints to stderr on some builds; -L is more reliable.
        license_text = run([binary("ffmpeg"), "-L"], 10).decode(errors="replace")
        report["ffmpeg_license"] = "GPL" if "GNU General Public" in license_text else "check installed build"
    return report
