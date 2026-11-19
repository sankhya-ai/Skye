# Mac / M1 check

The test host is Linux x86_64, not an M1 Mac. Nothing here certifies M1,
VideoToolbox, hardware decode or HEVC alpha. This kit makes the remaining check
repeatable on your Mac without sending data anywhere.

From your Skye checkout, after the usual editor setup:

```sh
python3 -m skye.studio.doctor --out "$HOME/Desktop/skye-check"
```

It never installs packages, calls a model, logs into an account or uploads
results. It records OS/CPU architecture, Python, tool locations and optional
module presence, then generates a one-second test clip, applies a caption,
renders it through Skye and saves a frame. A missing prerequisite is reported
with its log; do not confuse a missing setup with an M1 incompatibility.

Open Desktop/skye-check/report.json, frame.png and smoke.mp4. The frame should
show readable white SKYE CHECK text over a blue background, no black/error
frames. The video should show the caption animation. Keep the logs if it fails.
A zero exit status means those command steps succeeded, not that every visual
or platform feature passed.

Then check the parts a one-second synthetic render cannot establish:

- Start `skye ui`, open Video, import a short 1080p H.264 clip and a 4K clip.
  Scrub, play and move a clip; confirm thumbnails, correct source frames and no
  playback/proxy error. Note native Apple Silicon vs Rosetta if relevant.
- Select a shot, send `caption "Mac check"`, then click Undo. Confirm the text
  appears and disappears while the footage is unchanged. Try your installed
  Claude Code/Codex with one volume edit if you want to test that login too.
- Export a short cut with linked sound and a speed change. Listen for sync
  at the cut and inspect the source ranges. Compare export with the preview.
- If alpha/HEVC matters, import a known transparent clip above a colored
  background and export. Check visible transparency, not only codec metadata.
  Try a prepared PNG matte and inspect its edges in both preview and export.
- For tracking, try `track "Mac check"` on a textured stationary plane. Watch
  for drift. A successful command is not proof that the plane is correct.

Send the report and relevant logs only if you want help reading a failure. The
report contains local paths and platform details, so review before sharing it.
