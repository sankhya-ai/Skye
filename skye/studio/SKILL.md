---
name: skye-shot-effects
description: Edit selected video shots with Skye's local effect commands and repeatable style recipes.
---

# Shot effects

Read docs/video/EFFECTS-CLI.md before editing. It is the parameter and
capability reference. Do not infer that a lower-level helper is a supported
command, or that a supported command has been visually proven on this clip.

1. List shots: `skye studio effect PROJECT --list`. With multiple video
   sections pass `--track N`. Resolve the intended shot from source range and
   timeline time before editing. Work on a copy when trying a new style.
2. Translate the requested style into a supported command or JSON recipe.
   A reference clip must first be watched/described by the caller. The Video tab accepts a small local command grammar: caption "words", text "words", punch, shake, flash, grain, glitch, mirror, volume 50%, mute. Other edits use the owner's installed Claude Code/Codex. Reference analysis and planar tracking are available through `python -m skye.studio.reference` and `python -m skye.studio.tracking`, or `reference "assets/ref.mp4" text "New words"` and `track "Words"` in Video. Read docs/video/LOCAL-ANALYSIS.md for measured scope and failure limits.
3. Apply the effect, for example:
   `skye studio effect PROJECT --shot 4 --effect caption-pop --text 'one|two|*three*'`.
   For moving foreground occlusion:
   `skye studio effect PROJECT --shot 4 --effect text-behind --text 'WALKING IN|TOKYO' --dur 2`.
4. Export through the normal Skye render path. Inspect actual frames and
   playback around entrances/exits, masks, cuts and text. Check sound when
   clips are copied or split. Report defects rather than calling a mismatch
   a match.

Text is overlay-first. Keep footage running under labels and captions. Use
full-frame cards only for an intentional explanation/specification beat.

For editable text plus a prepared native matte, use text-behind-native.
The older text-behind command is a local baked 30 fps composite. Both have
soft edges and simple temporal smoothing. Planar tracking is a separate generated text layer, not automatic foreground occlusion.
Small crowded figures and fast motion need a quality check. One motion effect
per shot is supported; multiple overlays can be combined. Duration-changing
ramps and unsupported clip fields are refused. Never bypass a refusal by
silently dropping existing edits. Re-list shots after splitting or track
insertion. No paid model/service calls are needed for these commands.
