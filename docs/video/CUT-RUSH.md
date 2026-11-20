# Cut Rush

Cut Rush is an original, offline motion-design template in Video > Code shot. It follows the more active visual grammar studied in the supplied reference, not the reference's assets or exact edit.

## What it does

- Ten changing compositions: a diagonal type field, floating cards, moving tiles, offset word groups, rotating footage, product display, split screen, overlapping footage cards, curved type and a closing brand.
- Staggered word entry with travel, scale, rotation and blur. Outgoing compositions push, roll and zoom before the cut.
- Uneven beats across a 20-second composition. Camera moves and tile/card movement continue between word entries.
- Deterministic block interruptions and short, real negative flashes using difference compositing. An independent 180-degree footage turn keeps the main words readable.
- Local footage seeks are awaited by the frame clock. No network, model calls, timers or animation loop is needed by the template.

## Editing

Insert Cut Rush at 20 seconds for the demonstration timing. Open Edit code to set the `CONFIG` media paths and brand. Import the media first and use project-relative paths such as `../assets/phone-wide.mp4`. Empty paths are supported as a graphics-only starter. Text, positions and timing are editable in the code. The demo is one canvas-based code shot; individual letters and footage cards are not native timeline layers or selectable text objects. Native shot duration, audio bed, code versioning, Undo and export still apply.

The canvas is 1280×720 and designed for a 16:9 project. A portrait crop would need its own layout, not a silent center crop. The stock footage is intentionally used as footage panels, not falsely presented as subject extraction. A prepared matte could be added separately, but no automatic cutout or behind-person type is claimed here.

## Reference study

Reference: https://x.com/liu8in/status/2123562606535565629/video/1?s=46

The local study used the 41.37-second video, overview samples at 4 fps and denser strips at 12 and 30 fps near transitions. Observed techniques include accumulated mixed-size type, curved letter placement, background/foreground hierarchy, moving media cards, picture reframing, the yellow-to-mint block transition near 6 seconds, and a spatial 180-degree picture turn near 15 seconds. A photographic color-negative flash was not conclusively established. Cut Rush includes negative flashes as an original added technique, not a claim about that reference.

## Rights

The research reference is not bundled. Original copy, layout, animation code and synthesized percussion were made for this proof. Footage is from Pexels, whose license permits editing and promotional use. Source videos:

- KATRIN BOLOVTSOVA: https://www.pexels.com/video/man-using-smartphone-7131834/
- Ron Lach: https://www.pexels.com/video/person-using-a-smartphone-to-text-9898435/
- License: https://www.pexels.com/license/

The people in the stock footage do not endorse Skye. The product display uses our own Skye editor capture. The project ZIP carries a source ledger. No paid model calls were used.

## Limits

This is developer-assisted motion design, not one-prompt automatic direction, new generated footage, subject-aware typography, speech synchronization, or a claim of Framey parity. The source material is two stock clips rather than a purpose-shot actor sequence. The preview export is 854×480 at 30 fps with an original audio bed, not a final sound mix.

## Proof run

The project was created, its four assets imported, its code saved, its audio bed added, and its export submitted from the actual Video UI. The final native export completed 600 frames in about 73 seconds: H.264/AAC, 854×480, 30 fps, 20.000 seconds, 3,851,872 bytes. The ten main compositions and dense negative/rotation transition strips were visually checked from the exported MP4, not only the source canvas. Audio peak was -1.7 dBFS; the audio was measured, not listening-reviewed.

The project ZIP was extracted into a new root, then all ten composition midpoints were reopened through the film host with no page problems. Repeated identical clocks produced identical screenshot hashes. The negative and next normal frame produced different hashes and were also inspected visually. This is a frame/reopen check, not a second full-length ZIP export. TypeScript, production build and the Python suite passed: 619 passed, 14 skipped. The build retains its existing bundle-size warning.
