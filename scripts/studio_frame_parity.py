#!/usr/bin/env python3
"""Mixed-fps frame-parity gate for Skye Studio's renderer. No model calls.

Builds a three-segment mixed-frame-rate fixture (24, 30000/1001, 25 fps; distinct
colors and tone frequencies, concatenated with stream copy), renders 2-second
ranges around the joins through the real browser renderer, and verifies every
output frame against display-time truth: output frame N of a range starting at S
must show the picture the source displays at S + N/30 (the source frame with the
largest pts <= T), and the audio tone boundary must sit within one 40 ms window
of the source's.

Why not diff against `ffmpeg -ss S -i src -r 30`: the fps filter anchors its
output grid to the first frame after the seek and maps frames to the nearest
output slot, so that reference can place a boundary one output frame early
relative to its own timestamps (seen at both joins with this fixture). The
renderer is held to the stricter standard: what a viewer would see at that time.

Usage: python3 scripts/studio_frame_parity.py [workdir] [segment_seconds]
Exit 0 on PASS, 1 on FAIL.
"""
import math
import os
import subprocess
import sys

W, H, FPS = 160, 90, 30
SEGMENTS = [('a', '24', '0x516d8d', 440), ('b', '30000/1001', '0x746885', 660), ('c', '25', '0x4f746c', 880)]
COLOR_TOLERANCE = 12  # per-channel mean distance after calibrating prototypes on the same file
AUDIO_TOLERANCE_S = 0.04

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, check=True, **kw)


def make_fixture(work, seg_s):
    assets = os.path.join(work, 'proj', 'assets')
    os.makedirs(assets, exist_ok=True)
    long = os.path.join(assets, 'long.webm')
    if os.path.exists(long):
        return assets
    for name, rate, color, freq in SEGMENTS:
        run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
             f'color=c={color}:s={W}x{H}:r={rate}:d={seg_s}', '-f', 'lavfi', '-i',
             f'sine=frequency={freq}:sample_rate=48000:duration={seg_s}',
             '-c:v', 'libvpx-vp9', '-deadline', 'realtime', '-cpu-used', '8', '-b:v', '20k',
             '-c:a', 'libopus', '-shortest', os.path.join(assets, f'{name}.webm')])
    with open(os.path.join(assets, 'concat.txt'), 'w') as f:
        f.write("file 'a.webm'\nfile 'b.webm'\nfile 'c.webm'\n")
    run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i',
         os.path.join(assets, 'concat.txt'), '-c', 'copy', long])
    return assets


def video_pts(path):
    out = run(['ffprobe', '-v', 'error', '-select_streams', 'v', '-show_entries',
               'frame=pkt_pts_time', '-of', 'csv=p=0', path], text=True).stdout.split()
    return [float(p) for p in out if p]


def frames_rgb(path, extra=()):
    raw = run(['ffmpeg', '-v', 'error', *extra, '-i', path, '-an', '-vsync', '0',
               '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']).stdout
    size = W * H * 3
    return [raw[i:i + size] for i in range(0, len(raw) - size + 1, size)]


def mean_color(frame):
    rs = gs = bs = n = 0
    for j in range(0, len(frame), 97 * 3):
        rs += frame[j]; gs += frame[j + 1]; bs += frame[j + 2]; n += 1
    return (rs / n, gs / n, bs / n)


def video_check(all_pts, out_path, start, join_pts):
    """Every output frame must show the source frame displayed at its own output time.
    The renderer snaps the range start to the output frame grid (f0 = round(start*fps));
    output frame N shows film time (f0 + N)/fps. The displayed source frame at T is the
    one with the largest pts <= T; its segment label follows from the join timestamps.
    Output frames are labelled by color against prototypes sampled from the same file
    away from the joins, so a constant per-pipeline color conversion difference can
    neither mask a frame error nor create a false pass."""
    out = frames_rgb(out_path)
    start = round(start * FPS) / FPS

    def label_near(t):
        i = max(k for k, p in enumerate(all_pts) if p <= t + 5e-4)
        p = all_pts[i]
        return 'a' if p < join_pts[0] else ('b' if p < join_pts[1] else 'c')

    protos = {}
    for n, f in enumerate(out):
        t = start + n / FPS
        if all(abs(t - jp) > 0.4 for jp in join_pts):
            protos.setdefault(label_near(t), mean_color(f))
    bad = []
    for n, f in enumerate(out):
        t = start + n / FPS
        c = mean_color(f)
        got = min(protos, key=lambda s: sum((c[k] - protos[s][k]) ** 2 for k in range(3)))
        want = label_near(t)
        if got != want or math.dist(c, protos[got]) > 40:
            bad.append((n, round(t, 4), want, got))
    return len(out), bad


def pcm(path, start, duration):
    raw = run(['ffmpeg', '-v', 'error', '-ss', str(start), '-i', path, '-t', str(duration),
               '-vn', '-ac', '1', '-ar', '48000', '-f', 's16le', '-']).stdout
    import struct
    n = len(raw) // 2
    return struct.unpack(f'<{n}h', raw[:n * 2])


def tone_boundary(samples, freq_from, freq_to, win=0.02):
    """First window where freq_to dominates freq_from, in seconds from the clip start."""
    w = int(48000 * win)
    def power(seg, freq):
        # Goertzel at an exact frequency, normalized
        coeff = 2 * math.cos(2 * math.pi * freq / 48000)
        s0 = s1 = s2 = 0.0
        for x in seg:
            s0 = x + coeff * s1 - s2
            s2, s1 = s1, s0
        return s1 * s1 + s2 * s2 - coeff * s1 * s2
    for i in range(0, len(samples) - w, w):
        seg = samples[i:i + w]
        if power(seg, freq_to) > 4 * power(seg, freq_from):
            return i / 48000
    return None


def audio_check(assets, out_path, start, end, freq_from, freq_to):
    """Tone boundary in the renderer's audio within one window of the source concat's."""
    long = os.path.join(assets, 'long.webm')
    ref = tone_boundary(pcm(long, start, end - start), freq_from, freq_to)
    got = tone_boundary(pcm(out_path, 0, end - start), freq_from, freq_to)
    if ref is None or got is None:
        return ref, got, False
    return ref, got, abs(got - ref) <= AUDIO_TOLERANCE_S


def main():
    work = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else '/tmp/studio-frame-parity'
    seg_s = float(sys.argv[2]) if len(sys.argv) > 2 else 400
    assets = make_fixture(work, seg_s)
    duration = float(run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                          '-of', 'csv=p=0', os.path.join(assets, 'long.webm')], text=True).stdout.strip())
    counts = [len(video_pts(os.path.join(assets, f'{s[0]}.webm'))) for s in SEGMENTS]
    all_pts = video_pts(os.path.join(assets, 'long.webm'))
    join_pts = [all_pts[counts[0]], all_pts[counts[0] + counts[1]]]
    print(f'fixture: {len(all_pts)} frames, joins at {join_pts[0]:.3f}s and {join_pts[1]:.3f}s')
    failures = 0
    for label, jp, seg_from, seg_to in [('join1', join_pts[0], SEGMENTS[0], SEGMENTS[1]),
                                        ('join2', join_pts[1], SEGMENTS[1], SEGMENTS[2])]:
        start, end = jp - 1, jp + 1
        out = os.path.join(work, f'out-{label}.mp4')
        run(['node', os.path.join(ROOT, 'scripts', 'studio_render_range.mjs'),
             os.path.join(work, 'proj'), str(start), str(end), out, str(FPS), str(duration)])
        grid_start = round(start * FPS) / FPS
        n, bad = video_check(all_pts, out, start, join_pts)
        ok = not bad
        failures += not ok
        print(f'{label} video: {n} frames vs display-time truth; mismatches {len(bad)}',
              *bad[:5] if bad else [])
        ref, got, aok = audio_check(assets, out, grid_start, end, seg_from[3], seg_to[3])
        failures += not aok
        print(f'{label} audio: tone boundary source {ref and round(ref, 3)}s, '
              f'renderer {got and round(got, 3)}s; within {AUDIO_TOLERANCE_S}s: {aok}')
    print('PASS' if not failures else f'FAIL ({failures} checks failed)')
    return 0 if not failures else 1


if __name__ == '__main__':
    sys.exit(main())
