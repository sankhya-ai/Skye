# Skye Studio adoption, provisional

This branch embeds the actual MIT-licensed OpenFilm Studio, not a new imitation of its timeline. OpenFilm is pinned to `715c3a6e46b11a0b3ee038e8f7b3e3b4e19c5c36`. Its timeline, Canvas, Color workbench, manual page overrides, operation core and renderer are retained. Skye provides the outer workspace, local process lifecycle and scoped dispatcher proposal bridge.

Source: https://github.com/openfilm/openfilm

## Run

Use Node 22+, npm, FFmpeg and Python 3.10+. From a checkout:

```sh
python -m skye.studio.setup
skye ui
```

Setup installs the locked JavaScript dependencies, builds the editor and installs Chromium. It downloads software, not model weights, and requires internet. Manual editing and local rendering need no model account. Ask uses an installed Claude Code or Codex CLI and can use that account's allowance or billing. Merely opening Studio does not run it. No real model was run for this experiment.

For release wheels use `scripts/build_wheel.sh`. The wheel carries source and built UI, never node_modules, FFmpeg or Chromium. `python -m skye.studio.setup` still installs the local runtime dependencies after installation. The inspected wheel was about 50 MB compressed, with 3,049 members before the final documentation cleanup; all editor source, runtime and built entry points were present, no node_modules.

## One film, shared editing

Each new project has one `film.html`. Manual operations and validated worker operations use OpenFilm's `applyOps` and the editor's `useFilm` path, including inverse operations for Undo. Workers get a disposable reference JSON and an operations JSON, not the live project or Studio key. They cannot introduce source paths or executable page/CSS fields through this bridge. Linked A/V is expanded into the approved scope. Changes outside that scope, locked tracks, stale revisions and unsupported properties are rejected.

Ask currently supports selected-clip timeline operations only. It cannot generate media, write page code, edit a Canvas board, add sources, add tracks or do a full color grade. A ripple moving later clips requires selecting those clips too. The fake equivalence test deliberately selects the full affected batch. The UI send is a model-run action, not a prepare-only action.

This is application validation, not an OS jail. The coding CLI runs with its normal local process privileges. No claim is made that removing its shell tool is a filesystem or network sandbox.

Studio settings/projects/exports live under the selected Skye state directory, separately from an existing OpenFilm installation. Provider connections, generation, transcription/translation, automatic updates, remote discovery and skill installation routes are denied. The local runtime rejects non-loopback `fetch`. This is not a blanket operating-system network firewall, nor a guarantee about arbitrary imported HTML in an interactive browser. Only import trusted project/page code.

Existing Skye shot projects remain under **Legacy projects**, untouched. No automatic migration is shipped. The legacy renderer is retained for those projects until a safe parity-tested migration exists; it is not combined with the new model or used to export new Studio projects. The duplicate legacy implementation has therefore not yet been removed. Undo is session-local; saved film and on-disk version history survive restart, not the live Undo stack.

## Measurements, Linux only

Reproducible scripts are in `scripts/test_studio_browser.mjs`, `studio_fake_proposal.py`, `studio_render_probe.mjs` and `studio_long_probe.mjs`. Evidence is in `docs/video/studio-evidence`.

- The browser test performs a real mouse trim/ripple on linked picture/audio, exact Undo/Redo checks, applies the captured batch through the real dispatcher with a deterministic fake, then checks equal resulting film and Undo/Redo. No model.
- A board contains 1,000 source cards. With a non-overlapping grid, 418 cards were mounted at the minimum 10% zoom and 6 at 100%. Zero video elements were mounted for unselected cards. Shared page/iframe JS heap was about 44.5 MB at 100%, not whole-browser memory. Earlier densely packed layout showed all 1,000 and about 81 MB. Posters replace inactive video decoders; one selected card may audition a video.
- Edit, Canvas and Color screenshots are real. Color scopes and wheels open; grading fidelity was not independently measured.
- A 12-second 640x360 export combines 24, 29.97 and 25 fps inputs, linked audio crossfade, animated HTML title and VTT captions. It reports 360 frames and 12.000 seconds for video and audio. Inspected cut frames show the dissolve and caption. Frequency checks identify the expected tones and blend through the transition. Browser-playable copy generation failed for the small WebM fixtures; the renderer explicitly fell back to originals. Proxy generation is unproven.
- A synthetic low-bitrate 160x90 mixed-fps concatenation is 1,200.069 seconds and 14.5 MB. It uses actual Range requests through Studio. Seven raw-video-element seeks took 2.7-79.2 ms. This is not editor timeline scrub latency. Restart restores identical film and revision. Approximate sum of all matching headless browser RSS was 566 MiB, not isolated renderer RSS or an enforced budget. JS heap for this simple playback page was 11 MB.
- Three 2-second range exports around minutes 6:40, 13:20 and 19:58 each produce 60 frames. A 20 ms tone-window estimate puts the first join's A/V boundary difference at 23 ms, the second at 47 ms. Direct FFmpeg reference already shows about 47 ms at the second join. At the first join the browser-rendered color boundary is one output frame later than the FFmpeg reference. This is a real mismatch, not a parity pass. The full 20-minute output was not rendered. These fixtures do not establish real-footage lip sync, 4K performance or long-session memory stability.

The existing suite plus new proposal tests passed: 471 passed, 14 optional tests skipped. The 14 optional media integration/determinism/structure tests then passed separately with `SKYE_TEST_MEDIA=1`. One earlier concurrent run hit a legacy Chromium screenshot-capture error; the serial rerun passed. Both UI TypeScript checks and production builds pass. M1 8 GB and real Claude Code/Codex runs remain the owner's checks.

## Decision

Keep this adoption on the draft branch for evaluation. The actual editor and shared-operation integration are useful. Do not call the full adoption gate passed: mixed-fps frame parity, real high-resolution footage, proxy reliability, whole-process memory and safe legacy migration still need work. Do not merge on these measurements alone.

mcut's Apache-licensed core was inspected at `8e68a465af39d2ead4b053f34f70dd45c5fd1a35`. Its `packages/timeline/src/engine.ts` uses a separate TanStack store, Project schema, command engine and snapshot history. Adding it here would introduce a second edit model and a translation boundary while OpenFilm already owns preview/export. No mcut source was copied. This was a bounded source-fit comparison, not an independent performance benchmark of mcut.

## Vendoring

`skye/studio/UPSTREAM.json` identifies the snapshot and changed paths. `SOURCE_MANIFEST.json` records each retained source file's upstream and copied SHA-256. `THIRD_PARTY_NOTICES.md` and the original notices preserve MIT, CC0 and font OFL attribution. Apache-2.0 remains the Skye license. No GPL/AGPL code was copied in this pass. Large upstream files are preserved rather than arbitrarily split; newly authored bridge files stay small. Future updates must review local diffs, rebuild, rerun the probes and regenerate the manifest. Do not edit upstream attribution away.
