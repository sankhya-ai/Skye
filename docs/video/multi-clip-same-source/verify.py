#!/usr/bin/env python3
"""SSIM-check that out.mp4 draws each clip's own #t range. Usage: python3 verify.py [out.mp4]"""
import re, subprocess, sys

OUT = sys.argv[1] if len(sys.argv) > 1 else "out.mp4"
PAIRS = [(1, 2), (4, 6), (7, 10), (11, 14), (13, 18), (15, 22), (17, 26)]

def grab(src, t, out, crop):
    vf = ["-vf", "scale=-2:1920,crop=1080:1920"] if crop else []
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(t), "-i", src,
                    "-frames:v", "1", *vf, out, "-y"], check=True)

def ssim(a, b):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", a, "-i", b, "-lavfi", "ssim", "-f", "null", "-"],
                       capture_output=True, text=True)
    return float(re.search(r"All:([0-9.]+)", r.stderr).group(1))

ok = True
for t, s in PAIRS:
    grab(OUT, t, "of.png", False)
    grab("src.mp4", s, "sf.png", True)
    best, others = ssim("of.png", "sf.png"), []
    for wrong in (s - 2, s + 2):
        if 0 <= wrong <= 35:
            grab("src.mp4", wrong, "sw.png", True)
            others.append(ssim("of.png", "sw.png"))
    good = best > max(others)
    ok &= good
    print(f"timeline={t}s expect_src={s}s ssim={best:.4f} vs_neighbors={max(others):.4f} {'OK' if good else 'FAIL'}")
sys.exit(0 if ok else 1)
