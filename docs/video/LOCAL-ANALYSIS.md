# Local video analysis and lettering

These tools run on your machine. They make no model calls, upload nothing, and
install nothing on their own. Optional analysis dependencies:

```sh
python3 -m pip install -e '.[video-analysis]'
```

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
shot passed for 0.9 seconds, but the same setup lost the plane at 1.07 seconds on
a two-second attempt and was correctly rejected. That real result is not a
claim of long, drift-free crowd tracking.
