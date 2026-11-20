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

## Built-in composition recipes

For a new motion reel without hand-written scene code, read docs/video/COMPOSE.md.
Use Video > Compose for the no-code form or `skye studio compose PROJECT --recipe recipe.json --apply` with the project closed. Recipes generate separate native footage and editable text-page clips, not a whole-film canvas. Words edits phrases through native overrides. Use source assets already imported into the project, respect source ranges, and inspect the result. BPM is manual, not speech/music alignment. Arc is a phrase arrangement, not a native per-letter path. A generated block overlay is not a subject matte. Read docs/video/FRAMEY-STUDY.md for the reference analysis and unproven items.


## Design and timing

Read `docs/video/DESIGN.md` before composing a motion reel. Compose is upright-first by default. Treat its per-scene roles and lint as a draft, not a beauty grade. Inspect the export and follow the sentence, not only the grid. `skye studio compose PROJECT --lint` reports the current film. `skye studio timing PROJECT --source assets/narration.wav --mode speech` uses an optional local Vosk model; `--mode beat` estimates pulse locally. Never label unreviewed machine words as reviewed. Correct word/timestamp errors before setting `reviewed: true`. Low-confidence or absent matches need correction, not invented timing. Keep the project closed during offline apply.


## Optional local voice

`docs/video/VOICE.md` owns Kitten Mini setup and limits. The optional engine is `kitten-mini-int4`, not the flagship or int8 fallback. Video > Compose > Voice uses the scene groups as script, previews the generated WAV and chooses Generated voice timing. Build adds a separate native voice track and text entrances at generated segment bounds. Those are not word alignment or phonetic onsets. If the script changes, regenerate rather than claiming the old audio matches. For word timing use the existing local Speech analysis with a matching-language recognizer and review the results. No cloning, paid calls or cloud fallback.
