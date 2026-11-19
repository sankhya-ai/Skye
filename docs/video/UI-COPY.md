# Video UI copy

Keep controls short. No explanatory paragraphs or caveats in the working UI. Use labels, state, units, and actionable errors. Detailed behavior and limits belong in these docs:

- [Audio](AUDIO-MIXER.md): relative gain, native normalization, shot versus bed placement, no looping/stretch/ducking.
- [Tracking and comparison](LOCAL-ANALYSIS.md): numeric region/corners, planar limits, diagnostics, safe failures.
- [Masks](TRACK-MATTE.md): prepared alpha only, versioning, additional disk use, grow/shrink/feather, no brush/roto.
- [Code shots](CODE-SHOTS.md): deterministic scene API, native Undo, saved-film edge samples.

Spanning layers: Keep timing anchors existing time/length. Stop on overlap rejects structural edits involving spanning layers. Shot-contained layers follow the shot. Recheck bed length after duration edits.

Export check shows source sizes, cache, free disk and stage/duration, not a reliable time or peak-disk estimate. Cold export may prepare a whole-source proxy. The current preflight warning is documented rather than shown as UI prose.
