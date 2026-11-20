"""Serial, bounded-memory picture cache, sample-exact audio chunks, one final mux."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from .model import (MediaError, digest, file_hash, files_for, load, lock, read_json,
                    revision, safe_file, write_json)
from .process import binary, ffmpeg

VERSION = "skye-shot-1"


def cache_key(project: dict, unit: dict, files: dict, profile: str, kind: str) -> str:
    relevant = {"html": files["visual.html"], "captions": files["captions.json"]} if kind == "picture" else files["audio.json"]
    return digest(json.dumps([VERSION, project["source_hash"], unit, profile, kind, relevant,
                              project["width"], project["height"], project.get("renderer_hash", VERSION)], sort_keys=True).encode())


def uses_source(html: str) -> bool:
    """Only code that touches the source canvas needs footage pixels inside the page."""
    code = re.sub(r"<!--.*?-->|/\*.*?\*/|(?<![:\"'])//[^\n]*", "", html, flags=re.S)
    return "source" in code


def render_picture(root: Path, p: dict, u: dict, files: dict, profile: str, output: Path):
    scale = min(1, (640 if profile == "preview" else 1920) / max(p["width"], p["height"]))
    width, height = [max(2, int(n * scale) // 2 * 2) for n in (p["width"], p["height"])]
    with tempfile.TemporaryDirectory(prefix="render-", dir=root) as tmp:
        spec = Path(tmp) / "render.json"
        write_json(spec, {"width": width, "height": height, "frames": u["end"] - u["start"],
                          "html": files["visual.html"], "designWidth": p["width"], "designHeight": p["height"],
                          "mode": "composite" if uses_source(files["visual.html"]) else "overlay",
                          "captions": json.loads(files["captions.json"])})
        base = [binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "2", "-filter_threads", "1"]
        frames = str(u["end"] - u["start"])
        crf = "30" if profile == "preview" else "22"
        vp9 = ["-an", "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "6", "-crf", crf, "-b:v", "0", "-pix_fmt", "yuv420p"]
        code_args = [binary("node"), str(Path(__file__).with_name("render.mjs")), str(spec)]
        seek = ["-ss", str(u["start"] / 30), "-i", str(safe_file(root, "source.webm"))]
        if uses_source(files["visual.html"]):
            decode_args = [*base, *seek, "-an", "-vf", f"scale={width}:{height}", "-frames:v", frames,
                           "-pix_fmt", "rgba", "-f", "rawvideo", "pipe:1"]
            encode_args = [*base, "-y", "-f", "image2pipe", "-framerate", "30", "-i", "pipe:0", *vp9, str(output)]
        else:
            decode_args = None
            encode_args = [*base, "-y", *seek, "-f", "image2pipe", "-framerate", "30", "-i", "pipe:0",
                           "-filter_complex", f"[0:v]scale={width}:{height}[b];[b][1:v]overlay=format=auto,format=yuv420p",
                           "-frames:v", frames, *vp9, str(output)]
        processes = []
        # Stderr goes to files, never pipe buffers that can deadlock the pipeline.
        with tempfile.TemporaryFile() as decerr, tempfile.TemporaryFile() as jserr, tempfile.TemporaryFile() as encerr:
            try:
                decode = subprocess.Popen(decode_args, stdout=subprocess.PIPE, stderr=decerr) if decode_args else None
                code = subprocess.Popen(code_args, stdin=decode.stdout if decode else subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=jserr, start_new_session=True)
                processes[:] = [decode, code] if decode else [code, code]
                if decode:
                    decode.stdout.close()
                encode = subprocess.Popen(encode_args, stdin=code.stdout, stdout=subprocess.DEVNULL, stderr=encerr)
                processes.append(encode)
                code.stdout.close()
                for proc in reversed(processes):
                    proc.wait(timeout=900)
                if any(proc.returncode for proc in processes):
                    details = []
                    for stream in (jserr, decerr, encerr):
                        stream.seek(0)
                        details.append(stream.read().decode(errors="replace")[-3000:])
                    raise MediaError("\n".join(details))
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise MediaError(f"Picture render failed: {exc}") from exc
            finally:
                for proc in processes:
                    if proc.poll() is None:
                        if proc is processes[1]:
                            import signal
                            os.killpg(proc.pid, signal.SIGKILL)
                        else:
                            proc.kill()
                    proc.wait()


def render_audio(root: Path, p: dict, u: dict, files: dict, output: Path):
    cfg = json.loads(files["audio.json"])
    # 30fps -> 1600 samples per frame. No independent AAC priming at each cut.
    start, end = u["start"] * 1600, u["end"] * 1600
    duration = (end - start) / 48000
    gain = 0 if cfg["mute"] else cfg["gain"]
    if p["has_audio"]:
        inputs = ["-i", str(safe_file(root, "source.webm"))]
        af = f"aresample=48000,atrim=start_sample={start}:end_sample={end},asetpts=PTS-STARTPTS,volume={gain}"
    else:
        inputs = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        af = f"atrim=end_sample={end-start},asetpts=PTS-STARTPTS"
    fade = cfg["fade_frames"] / 30
    if fade:
        af += f",afade=t=in:d={fade},afade=t=out:st={duration-fade}:d={fade}"
    af += f",apad=whole_len={end-start},atrim=end_sample={end-start}"
    ffmpeg(*inputs, "-vn", "-af", af, "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(output))


def render(root: Path, profile="preview", progress=None, only: str | None = None) -> dict:
    if profile not in ("preview", "final"):
        raise MediaError("Unknown render profile")
    with lock(root):
        p = load(root)
        from .process import run
        p["renderer_hash"] = digest(
            Path(__file__).with_name("render.mjs").read_bytes() + Path(__file__).with_name("harness.js").read_bytes()
            + Path(__file__).with_name("package-lock.json").read_bytes()
            + run([binary("ffmpeg"), "-version"], 10))
        rev = revision(root)
        if file_hash(safe_file(root, "source.webm")) != p["source_hash"]:
            raise MediaError("Source changed. Reimport the video")
        cache = safe_file(root, "cache")
        cache.mkdir(exist_ok=True)
        reused, changed, audio_changed, pictures, sounds = [], [], [], [], []
        units = [u for u in p["units"] if only in (None, u["id"])]
        if not units:
            raise MediaError("Shot not found")
        for i, u in enumerate(units):
            files = files_for(root, u["id"])
            if progress:
                progress({"shot": u["id"], "current": i + 1, "total": len(units)})
            for kind, suffix, dest in (("picture", ".webm", pictures), ("audio", ".wav", sounds)):
                key = cache_key(p, u, files, profile if kind == "picture" else "audio", kind)
                output = cache / (key + suffix)
                if output.exists():
                    if kind == "picture":
                        reused.append(u["id"])
                else:
                    temp = cache / (key + ".partial" + suffix)
                    try:
                        if kind == "picture":
                            render_picture(root, p, u, files, profile, temp)
                            changed.append(u["id"])
                        else:
                            render_audio(root, p, u, files, temp)
                            audio_changed.append(u["id"])
                        temp.replace(output)
                    finally:
                        temp.unlink(missing_ok=True)
                dest.append(output)
        exports = safe_file(root, "exports")
        exports.mkdir(exist_ok=True)
        output = exports / (f"{rev}-{profile}.webm" if only is None else f"{rev}-{only}-{profile}.webm")
        with tempfile.TemporaryDirectory(prefix="mux-", dir=root) as tmp:
            paths = []
            for kind, items in (("picture", pictures), ("audio", sounds)):
                listing = Path(tmp) / (kind + ".txt")
                # These names contain only our own SHA256 keys, never user input.
                listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in items), "utf-8")
                paths.append(listing)
            temp = output.with_suffix(".partial.webm")
            try:
                ffmpeg("-f", "concat", "-safe", "0", "-i", str(paths[0]), "-f", "concat", "-safe", "0", "-i", str(paths[1]),
                       "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "libopus", "-ar", "48000", str(temp))
                if revision(root) != rev:
                    raise MediaError("Project changed during render")
                temp.replace(output)
            finally:
                temp.unlink(missing_ok=True)
        result = {"revision": rev, "profile": profile, "file": output.name, "rendered": changed,
                  "reused": reused, "audio_rendered": audio_changed, "shot": only}
        if only is None:  # a one-shot preview never replaces the full render
            write_json(root / "latest.json", result)
        return result
