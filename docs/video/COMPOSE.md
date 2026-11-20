Compose now applies upright-first design by default and supports local beat/speech timing. Read [DESIGN.md](DESIGN.md) for the rules, timing review and current limits. Earlier proof notes below describe the first reusable milestone.

# Compose: videos from built-in parts

Compose creates separate footage, text, background and transition clips from imported media and phrase groups. It does not write one canvas containing the entire video. No cloud calls or external services are needed. Optional Speech timing uses a local model; the original workflow below uses manual BPM.

## Human path

1. Video > New project. Choose an aspect, then import licensed footage and optional audio/images.
2. Compose > Cut Rush. Pick Source and optionally Second. Enter one scene per line. Separate phrase groups with `|`. Set BPM, then Build.
3. Open any scene in the editor. The footage and phrase clips have their own timeline positions, duration, box and motion. Words opens a short form for changing the phrases without code. Select a clip in the native editor for timing and transform edits. Each phrase is a native HTML/text-page clip with a DOM text override, not a newly invented text primitive.
4. Add a music bed in Audio mixer. Compose does not silently create music or loop a short source.
5. Export normally. Check the result, particularly long text, tilted text, rapid flashes and the picture flip. Undo restores an applied Compose or Words operation inside the editor.

The simple Cut Rush form generates seven layout choices in sequence, four beats each. This is a starting arrangement, not automatic editorial judgment. Add/remove phrase lines to change its length. The current template uses the first source for the flip and no footage for its seventh closing title. For detailed control use Recipe. Existing project content is preserved: Compose appends after its end. It refuses an explicit start that would overlap existing content.

## Agent/offline path

With the project closed in the editor:

```
skye studio compose /path/to/project --recipe recipe.json
skye studio compose /path/to/project --recipe recipe.json --apply
```

Or `python -m skye.studio.motion ...` with the same arguments. The first command generates versioned support pages and returns a native operation plan without changing `film.html`. `--apply` saves the native operations, preserving unrelated film content, and prints the backup path. This offline path uses a film backup, not the open editor's Undo stack. Do not use it concurrently with the editor. The UI path uses revision-checked native operations and acknowledged Undo.

```
{
  "bpm": 120,
  "scenes": [
    {"words": ["TOO MANY", "TABS."], "source": "assets/man.mp4", "layout": "stack", "palette": "mint", "beats": 4},
    {"words": ["SAVE IT", "FOR LATER."], "source": "assets/phone.mp4", "layout": "arc", "palette": "mint", "duration": 2.4},
    {"words": ["FLIP THE", "SCRIPT."], "source": "assets/man.mp4", "layout": "flip", "palette": "paper", "beats": 4, "negative": true},
    {"words": ["skye.", "MAKE YOUR NEXT CUT."], "layout": "title", "palette": "paper", "duration": 3}
  ]
}
```

Supported fields:

- Recipe: `scenes`, optional `bpm` (40–240), optional `start` (at or after the existing film end).
- Scene: `words` (1–5 strings, 70 characters each, or a pipe-separated string), `source`, optional `second`, `sourceIn`, `layout`, `palette`, `beats` or explicit `duration`, and `negative`.
- Layouts: `stack`, `arc`, `scatter`, `split`, `cards`, `wall`, `flip`, `title`.
- Palettes: `mint`, `paper`, `gold`.
- Duration: 0.8–12 seconds if explicit. `beats` is 1–24 at the recipe BPM. The source must cover the full requested range. No silent loop/stretch or fallback to another source.
- `source` and `second` must be existing imported project assets. Empty source means graphics only. The source range is shared by cards/tiles in a scene. Footage audio is muted; use a deliberate audio bed/mix.

The local authenticated UI API also exposes `POST /api/studio/compose` with `{project,base,recipe}`. It returns `{project,base,ops,answer,duration}`. Apply these through the normal editor operation bridge; preparation alone does not change the timeline. `POST /api/studio/motion-text` reads phrase rows and a revision. `POST /api/studio/motion-text-change` takes `{project,base,edits:{clipId:"New phrase"}}` and returns native operations. Keep revision checks, locked-track refusals and Undo; do not bypass them by overwriting the film.

## What is actually built in

Separate editable phrase clips, auto-fit DOM text, mixed-scale/angled layouts, staggered native clip entry and sweep-out, native footage push/rotate, split screen, overlapping media cards, moving media tiles, brief negative footage filters, a deterministic short block cover/reveal, palette presets, and a manual beat grid. Background scene clips anchor the seven storyboard cards while the timeline keeps all individual layers.

Arc is a phrase arrangement, not a per-letter curve editor. The block overlay is not a matching pixel reconstruction between sources. BPM is user-provided, not detected from music. No automatic voice timing, semantic emphasis, subject/object matting, clean text-behind-person compositing, sound design or footage/art-direction selection is claimed. Prepared mattes and more detailed code templates remain separate tools. This proof's convenience does not establish exact reference matching or Framey automation parity.

## Verified proof

A new 16:9 project was created in Video, two licensed stock files and an original synthesized audio bed were imported, and the seven-line default was built with Compose. No scene code was edited for that project. It has 7 storyboard scenes, 15 independent phrase clips and native media/transition layers, on 12 reused tracks including audio rather than one track per object. Words changed the first phrase, and native UI Undo restored it, verified by reopening Words. The final export is 14.000 seconds, 854×480, 30 fps H.264/AAC, 1,863,111 bytes. The final export job took 69.3 seconds on this host.

All seven main compositions, the negative frames and the rotating picture transition were inspected from the exported MP4. Steady-state DOM text bounds fit each text page. Repeated negative-frame clocks produced the same screenshot hash. Extracting the native project ZIP into a new root and repeating the seven-scene bounds/clock checks succeeded without page problems. That is not a second full-length rerender. Audio peak is -1.7 dBFS; the bed was measured, not listening-reviewed. The full Python suite passed, 629 passed and 14 skipped; TypeScript and production build passed, with the existing bundle-size warning. The offline CLI apply path and append plan were also exercised on a separate small project, with a film backup created.
