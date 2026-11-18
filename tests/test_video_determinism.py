"""Shot code gets a fixed clock and fixed random values in the real renderer."""
import os
import pytest
from skye.media.importer import import_video
from skye.media.model import files_for, revision, update
from skye.media.process import ffmpeg
from skye.media.renderer import render

pytestmark = pytest.mark.skipif(os.environ.get("SKYE_TEST_MEDIA") != "1", reason="optional FFmpeg / Chromium stack")

CODE = """<div id="b" style="position:absolute;inset:0"></div>
<script>window.frame=(t)=>{
 const ok = Date.now()===Math.round(t*1000) && new Date().getTime()===Math.round(t*1000)
   && performance.now()===Math.round(t*1000) && Math.random()===0.5;
 document.getElementById('b').style.background = ok ? 'rgb(0,255,0)' : 'rgb(255,0,0)';
};</script>"""


def make(tmp_path):
    src = tmp_path / "in.webm"
    ffmpeg("-f", "lavfi", "-i", "color=c=0x193047:s=160x90:r=30:d=1", "-c:v", "libvpx-vp9", "-deadline", "realtime", str(src))
    p = import_video(tmp_path, src, scene_detection=False)
    return tmp_path / "media" / p["id"]


def test_clock_and_random_are_fixed(tmp_path):
    root = make(tmp_path)
    f = files_for(root, "shot-0001")
    f["visual.html"] = CODE
    update(root, "shot-0001", revision(root), f)
    out = root / "exports" / render(root)["file"]
    frame = tmp_path / "f.rgb"
    ffmpeg("-ss", "0.5", "-i", str(out), "-frames:v", "1", "-vf", "scale=4:4", "-pix_fmt", "rgb24", "-f", "rawvideo", str(frame))
    r, g, b = frame.read_bytes()[:3]
    assert g > 200 and r < 60 and b < 60, (r, g, b)
