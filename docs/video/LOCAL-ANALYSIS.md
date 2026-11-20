# Local video analysis and lettering

These tools run on your machine. They make no model calls, upload nothing, and
install nothing on their own. Optional analysis dependencies:

```sh
python3 -m pip install -e '.[video-analysis]'
```

CLI aliases: `skye studio reference`, `skye studio track`, and `skye studio doctor`.

Tesseract is optional for OCR. FFmpeg and Node are also needed for rendering.
The shipped `video-editor` team role can call these commands through your own
Claude Code/Codex. The embedded selected-clip proposer gets a narrower tool
catalogue and cannot run shell commands itself. It can request a supported plan;
Skye runs the local generator and returns revision-checked native operations.

## Read a reference

```sh
python3 -m skye.studio.reference reference.mp4 --out analysis.json
python3 -m skye.studio.reference reference.mp4 --out analysis.json \
  --apply-to /path/to/project --shot 1 --reference-shot 1 --text 'NEW|WORDS'
```

In Video, select one footage clip and send:

```text
reference "assets/ref.mp4" text "NEW|WORDS"
```

The reference must already be a local asset in that project. Absolute paths,
parent traversal and outside symlinks are refused. Analysis is limited to five
minutes. It measures hard-cut candidates at 6 samples/second, shot durations,
brief white-flash candidates, and OCR text boxes with rough size and position.
The JSON includes measurements, uncertainty and a recipe for each reference shot.
The runner scales supported effect times to the target shot's duration. It uses
your new caption words, not OCR wording. Unknown or unmeasurable effects are not
invented. A shot with no supported recipe is rejected, not called a match.

Current transfer is flash timing plus a generic caption-pop preset. It does not
copy exact fonts, typography placement, caption animation, complex transitions,
speech or semantics. OCR can mistake scene signage for captions; dissolves,
whip-pans and lighting changes can confuse cuts. It does not change your target
cut structure. Read the report before treating it as a reference match.

## Compare a rendered result

```sh
python3 -m skye.studio.reference reference.mp4 --compare rendered.mp4 \
  --out comparison.json --no-ocr --tolerance .2
```

This is read-only. It reports duration difference, median detected-shot length,
paired cut/flash candidates and unpaired events on each side. Events are paired
one-to-one in time order within the tolerance, not credited more than once.
The report includes the sampling resolution. It does not stretch either timeline
or hide missing events behind a single percentage. With OCR enabled it also gives
aggregate candidate text height/vertical center; null means unmeasured, not zero.
It deliberately produces no overall style-match score. Same cut times do not
prove matching footage, story, typography, motion, color, camera or sound.
`--compare` cannot be combined with `--apply-to`.

Verification: two 19-second versions of the real street reel paired six detected
cut candidates at the same sampled times, median 2.5s, duration delta -0.019s.
OCR was disabled, so caption geometry stayed null. Comparing the six-second hero
proof against the four-second sound fixture reported two unpaired reference cuts
and a -2s duration difference, not a match. Those are bounded detector examples,
not a general accuracy claim. Real footage is private testing only; creators
retain their rights.

## Plant text on a plane

```sh
python3 -m skye.studio.tracking /path/to/project --shot 1 --text 'KEEP WALKING' \
  --quad .25,.78,.75,.78,.85,.88,.15,.88 --roi .05,.65,.95,.98
```

Or select one shot in Video and send `track "PLANTED"` for the default ground
region and trapezoid. The CLI is how you choose a more suitable region now.
`--quad` is four normalized screen corners, clockwise from top-left. `--roi` is
normalized x0,y0,x1,y1 for a textured stationary plane. Preview and export use
the same generated page and precomputed transforms. The text remains plain text
in that page; tracking samples and quality are saved beside it in CLI projects
and embedded as JSON in the page for Video-tab edits.

Tracking uses forward/backward Lucas-Kanade flow and RANSAC homographies. It is
planar tracking, not a 3D camera solve. A perspective quad is chosen, not inferred
from scene meaning. Failure to retain enough consistent points aborts before
changing the film. Inliers and reprojection error are evidence, not a guarantee
that the chosen points belong to the intended ground. Review the render.

Limits: a local unsped, centered-cover footage clip, no existing visual styles
except object-fit:cover, at most 30 seconds, 15 tracking samples/second by default.
Large parallax, moving people, cuts, motion blur and feature loss can fail. There
is no automatic reacquisition. This does not add foreground occlusion. Combine
with a prepared matte separately when people must walk in front of the text.

Proofs: a three-second known pan tracks within 1.5 pixels in the automated test;
the native rendered grid proof moves the lettering with the grid. A real street
shot passed for 0.9 seconds, but the same setup lost the plane at 1.27 seconds after outlier pruning on
a two-second attempt and was correctly rejected. That real result is not a
claim of long, drift-free crowd tracking.

A separate local check on the 19-second edited street reel found all six known
cut boundaries within 0.14 seconds of the edit, with a 2.5-second median shot.
The initial conservative detector missed same-source cuts; a second criterion
now includes large spatial changes with moderate histogram change. A second
white-letter OCR pass recovered POV and the main caption, but noisy scene text
remains in candidate output. This is a measured test case, not a general accuracy
guarantee or a claim that style transfer matches that reel.

### Tracking correction in the storyboard

Select a footage shot and choose **Track text**. The dialog shows the composed first frame and accepts a normalized feature ROI and four text corners in perimeter order (TL, TR, BR, BL), text, and sampling fps. Correct coordinates and run again. By default this replaces only overlays explicitly tagged as tracking owned by that shot; older untagged overlays are not guessed or removed. Locked prior tracking cannot be replaced. All edits use native operations and Undo.

The current UI is numeric, not a corner-drag editor. Tracking is limited to unsped centered-cover shots, 30 seconds, local OpenCV. Inlier counts and median reprojection errors are diagnostics, not confidence percentages or proof of the intended plane. Inspect the full export for drift. No 3D camera solve, segmentation, automatic occlusion or model calls. Changing project/closing the view while preparing prevents the abandoned dialog from submitting an edit.
