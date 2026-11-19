# Native audio mixer

Storyboard > Audio mixer is an arranger and level panel over the same film clips the editor already plays and exports. It does not create a second mix engine. Import WAV/MP3/M4A/etc. or a video with sound using Import media, then choose the whole-film bed or a shot. Source in is explicit. A bed spans the current picture duration; shot sound starts with the shot and has its duration. Adding sound does not mute the footage's own sound, so lower/mute the original if replacing it.

The source must contain an audio stream and be long enough. No automatic looping, stretching, ducking or extension after later picture edits. Existing storyboard spanning policy still applies: a bed keeps its absolute time/length with anchor policy, and shot-contained audio follows its shot. Recheck the bed end after changing film length.

Channels show footage levels and separate sound clips, their timeline span, source, track and track mute/lock state. Level is linear 0–200%; 0% mutes. Boosts can clip. Separate sound clips expose fade in/out; video sound does not expose those controls here because the native fade also changes its picture. Native editor handles detailed timing, track mute/solo and meters. Separate sound can be removed without removing the picture. Apply/Mute/Remove are normal native operations with revision checks and Undo. Undo acknowledgement now comes after native history completion rather than a fixed refresh delay.

The existing native delivery loudness setting defaults to -14 LUFS and can change the whole mix level in preview/export. Channel percentages are relative gains, not a final peak/loudness promise. Source audition in the picker is the original source, not the mixed film. Hear the applied mix in the editor.

## Proof

Real UI workflow: two picture shots, imported local 220 Hz and 660 Hz WAV fixtures through the top-bar file input, added a four-second bed, set it to25% with0.2s in/out fades, attached a two-second sound to shot2 at50%, muted it and used native Undo. An initial check read the UI too early; file state was restored but the panel refresh could race the fixed500ms timer. Replaced that timer with native Undo completion acknowledgement, reran mute/Undo and waited for the exact restored50% panel value. Fresh reload also shows25%/50%.

Native UI export completed four seconds,480x854,30fps, audio stream present. Exported tone measurement:220Hz amplitude .12437 in the first shot and .12438 in the second,660Hz absent in the first and .24876 in the second. This verifies continuous bed across the cut and the2:1 relative level of the shot sound after delivery normalization. It is a controlled tone fixture, not a claim of a polished music mix or a listening test on real speech/music. Export frame, mixer panel and native timeline inspected. Native operations regression checks cover bed versus shot ripple, source-length refusal, fade limits, locks and stale revisions.

Remaining: master controls/meters in this panel, music search, auto-loop/ducking, envelope automation and richer per-shot sound UI. The native timeline still owns those editing details that it already supports.
