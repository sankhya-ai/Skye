# Per-shot effects

Run from an installed Skye checkout (or the repository root):

```sh
skye studio effect PROJECT --list
skye studio effect PROJECT --shot 4 --effect caption-pop --text 'sunday|crowd.|*no plan.*'
```

Equivalent entry point: `python -m skye.studio.edit`. Python API:
`from skye.studio.edit import apply_effect, apply_recipe, read_shots`.
No model or paid service is called by these commands.

## Shot selection and safety

A shot is a video clip, in document order, on the footage section. If more
than one non-effect section contains video, select `--track N`, the section
number counting from 1. Use `--list` again after adding tracks or splitting
clips, since numbers can change. The native parser normalizes clip IDs,
implicit at=0 and quoted attributes before applying an edit, while preserving
the film head, comments and section flags. Footage clips currently need a
finite #t=start,end range. Locked tracks are refused.
Unsupported/ambiguous cases fail rather than guessing.

The command validates the resulting film with Skye's native film parser
before replacing film.html. A successful edit saves film.html.bak. It does
not post or publish the video. Export it through the normal Skye renderer.
Do not run concurrent edits against one project.

## Effects and parameters

`--at` is an absolute film second. Otherwise the effect begins at shot start
plus `--offset` (default 0.2s). Overlays are clipped at the shot boundary.
Explicit out-of-range timing is refused. `--dur` sets duration where supported.
Text lines/words use `|`; asterisks mark accent lines where supported.

| Effect | Parameters | Result |
|---|---|---|
| caption-pop | --text 'one|two|*three*', --fg, --accent | Animated words with highlight |
| ktype | --text 'BIG|*LINES*' | Sliding typography, transparent by default |
| caption | --text 'KICKER|Main line' | Kicker, accent bar, main line |
| meme | --text 'TOP|BOTTOM' | Stroked meme text |
| hook | --text 'Setup|The turn' | Animated two-line hook |
| freeze-pop | --image ../assets/cutout.png, --label | Cutout still overlay, supplied image required |
| flash | --dur 0.12, --fg '#ffffff' | Full-frame flash effect |
| film-burn | --dur 0.4 | Light-leak overlay |
| grain | --strength 0.45 | Grain and vignette overlay |
| sticker | --shape star, burst or heart | Inline SVG sticker, no emoji-font dependency |
| shake | --amp 7, --dur 0.35, --offset 1.2 | Shot-relative jitter |
| punch | --zoom 1.15 | Push-in across the shot |
| whip-in / whip-out | --dur 0.4 | Whip at shot beginning/end |
| glitch | --dur 0.7 | Two silent blended video copies |
| mirror | | Mirrored halves, duplicate audio muted |
| ramp | --speeds 0.75,1.5 | Equal source portions with balanced speeds |
| text-behind | --text 'WALKING IN|TOKYO', --dur 2 | Local baked text occluded by foreground people |
| text-behind-native | --text 'WALKING IN|TOKYO', --dur 2 | Prepared PNG matte, editable text layer, original footage retained |
| cutout | | Existing bundled lightweight person cutout in preview/export |

Text overlays leave footage running. Full-frame scene cards remain available
through scenes.py with overlay=False for an intentional explanation moment.
Do not insert cards for routine labels/captions.

## Known limits

- One clip-motion preset per shot. Existing animation is protected rather
  than silently replaced. Multiple text/effect overlays can be combined.
- A ramp must preserve the shot's total duration. Timeline ripple for a
  duration-changing ramp is not implemented here.
- Text-behind supports a plain un-sped, centered object-fit:cover local shot
  with an explicit width/height viewport. Other crop, motion and clip fields
  are refused. It bakes at 30 fps; configurable frame rate is pending.
- The local text-behind pipeline requires rembg, NumPy, Pillow, ffmpeg and
  ffprobe. u2netp downloads on first use if not cached. It is a foreground
  segmentation model, not a guaranteed person-only semantic mask. Inspect
  each shot. Crowds, small figures and thin edges can flicker or be missed.
- Exponential temporal smoothing and soft edges reduce shimmer but can
  leave trails on fast motion. There is no optical-flow stabilization,
  camera tracking, perspective reconstruction or automatic ground anchoring.
- The baked text-behind command creates a new composite asset. The separate
  text-behind-native command instead prepares alpha PNG masks, leaves title
  text editable, and inserts a silent foreground copy with the native matte
  property. Both retain the original source. See TRACK-MATTE.md.
- Text-behind measured around 45 seconds for 60 frames in the earlier
  private crowd test; this varies with source size, masks and encoding.
- Swipe, outline-glow, color-pop, picture-in-picture, match-cut zooms and
  beat detection are not all exposed by this command. Lower-level helpers
  are not proof of a tested per-shot command.

## Reference recipes

A person or coding agent watches a reference, describes the style and writes
JSON. This command does not analyze reference video or understand natural
language itself. A chat-capable agent can translate a request into commands;
Skye's human chat UI is not wired to this interface yet.

recipe.json:

```json
[
  {"effect":"punch","zoom":1.12},
  {"effect":"caption-pop","text":"code.|games.|*rockets.*","offset":0.3}
]
```

```sh
skye studio effect PROJECT --shot 2 --recipe recipe.json
```

Recipes accept 1-30 supported effect objects. A split/bake effect must be last.
A failed step restores the original film and removes newly generated output
files. Recipes are a repeatable style description, not an automatic promise
of visual similarity to a reference. Render and inspect before accepting.

## Verification so far

- One-line caption-pop command: applied to a walk shot and rendered through
  Skye, exported frame inspected.
- One-line text-behind command: generated a two-second crowd composite,
  inserted it in film.html, exported through Skye, overlap inspected.
- One-line text-behind-native command: prepared 60 crowd mask frames,
  added editable text and a silent masked foreground copy, then exported
  through Skye. Inspected title occlusion in the actual export.
- Existing --of-cutout mode 0: short export completed with the bundled
  lightweight person model. Edges were visibly soft/ragged.
- Other command paths have unit/native-parser tests, not all end-to-end
  visual proofs. Effects demonstrated in earlier hand-authored reels are
  distinct from command-level verification.
