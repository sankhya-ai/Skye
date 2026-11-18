# Code-backed shots

Open **Video** in Skye's browser UI. There is no desktop app. Skye's local backend handles import and export; this is not a browser-only WASM editor.

Import a video, review the contact sheet, adjust cuts, select a shot, and edit its code, captions or audio. Save, then render. The original remains unchanged. Footage remains pixels, not recovered objects, layers or camera geometry.

## Install the optional runtime

Skye's base Python install still uses the standard library only. For video:

```sh
python -m pip install -e '.[media]'
# Install an FFmpeg / ffprobe build with libvpx-vp9 and libopus on PATH.
# See LICENSES.md before distributing a binary or installer.
cd skye/media
npm ci
npx playwright install chromium
cd ../..
skye video doctor
skye run
```

Use Node 20 or later. The renderer uses a separate, empty Chromium profile with its sandbox enabled. It does not use your interactive browser, cookies or logins. If Chromium's sandbox cannot start, rendering fails; Skye does not disable it. `SKYE_CHROMIUM` may point to an installed compatible Chromium executable. Installing the optional runtime needs downloads; editing and rendering make no network or model calls.

The backend must run on the machine holding the media and coding-agent logins. The browser can be its UI. Do not expose Skye directly to the internet without an authenticated front door. Existing API-token and same-origin protections apply to all media routes, uploads and previews.

## Workflow

1. Import MP4, MOV, WebM, AVI or MPEG footage, up to 10 minutes and 512 MB. HDR imports fail explicitly rather than silently rendering the wrong colors.
2. Skye keeps the original and makes an SDR, square-pixel, 30 fps VP9/Opus mezzanine, up to 1920 × 1080. Variable-frame-rate footage is normalized once. Project frames refer to this mezzanine, not to original-file frame numbers.
3. **Detect cuts** uses local PySceneDetect AdaptiveDetector. It finds visual changes, not semantic scenes or speech boundaries. A talking head or a screen recording may remain one shot. Disable detection for a single-shot import.
4. Before editing, move the playhead inside a shot and choose **Split at playhead**, or **Merge next**. Cuts land on exact project frames, not codec keyframes. Boundary changes are explicit project edits. After any code/caption/audio edit, recutting is refused rather than discarding or retiming those edits. Reimport to make a different cut.
5. Select a shot. **Code** is HTML/CSS/JavaScript with `window.frame(t)`, where `t` is local seconds. **Captions** and **Audio** are JSON. Save uses an expected revision; stale saves fail.
6. **Render** builds a 640px preview or a source-sized final, then joins cached pictures and performs one continuous audio encode. Exports are WebM (VP9/Opus), not MP4. A caption/code change invalidates that shot's picture. Audio-only changes do not invalidate picture caches. Changing render quality has its own picture cache.
7. Download the render. Previous exports remain available on disk if a later render fails. The UI marks an older render when changes have not been rendered.

Only one heavy video job runs at a time. Output chunks are encoded serially, with two FFmpeg threads and streamed frame buffers rather than every frame in memory. Disk usage includes the original, mezzanine, picture/audio caches and exports. Old cache/export cleanup is manual in this draft. A restarted server marks interrupted jobs failed; restarting a render reuses completed chunks, but an interrupted import must be uploaded again.

## Edit with Claude Code or Codex

Select the agent, enter a request and choose **Prepare edit**. This writes a disposable proposal and a normal Skye `artist` job. Preparing never starts a model.

**Approve and run** approves exactly that job through Skye's existing approval store and dispatcher, runs your own installed/logged-in coding agent, validates the result and renders a local preview. The built-in `artist` role can read and write files, but has no Bash. MCP remains blocked for Claude Code. Owner role/tool overrides and the existing concurrency policy still apply. Your provider allowance is used when you explicitly run an agent.

The expandable manual route is:

```sh
skye video --state-dir /path/to/.skye prepare PROJECT_ID shot-0002 --adapter claude-code --goal 'Make the title shorter'
# Output includes a normal job path and proposal ID.
skye approve /path/to/PROPOSAL_ID.job.json
skye run /path/to/PROPOSAL_ID.job.json
skye video --state-dir /path/to/.skye apply PROJECT_ID PROPOSAL_ID
skye video --state-dir /path/to/.skye render PROJECT_ID
```

The proposal contains only that shot's code, caption/audio data, frame-count context and a reference thumbnail. The import gate refuses extra files, symlinks, changed metadata, stale revisions, captions crossing a cut and audio settings outside the shot. Only the three editable files are imported. No worker-authored shell or FFmpeg command is executed by the media engine.

This is an application-level import boundary, not an OS filesystem jail for Claude/Codex. Worker tool configuration is not an OS security boundary. Run untrusted workers in an appropriate system sandbox if you need that guarantee. The isolated media renderer's Chromium sandbox is a separate boundary.

## Shot code

```html
<style>
#label { position:absolute; left:6%; top:8%; color:white; font:600 42px system-ui; }
</style>
<div id="label"></div>
<script>
window.frame = (t) => {
  const label = document.getElementById('label');
  label.textContent = 'One small change.';
  label.style.opacity = String(Math.min(1, t * 3));
  document.getElementById('source').style.filter = 'saturate(0.85)';
};
</script>
```

`source` is the original footage frame on a canvas. You can crop, transform, mask, cover or filter it with CSS, or draw over it. Code must set a complete state from `t`, not accumulate mutations across frames. The logical canvas stays source-sized in both preview and final, so positioning scales with the output. Browser CSS animations/transitions and timers are disabled. The renderer fixes random values and supplies a local clock. This helps repeatable rendering, but is not a proof of determinism for arbitrary JavaScript or different system fonts/browser versions.

Shot pages have no audio, external URLs, local-file URLs, npm imports or network access. Use inline SVG, CSS, canvas and data-URI assets. This initial implementation does not load external JS libraries, even ones you installed elsewhere. HTML runs only in the isolated rendering browser, never inline in the Skye UI or in Node. Runtime code errors fail the render instead of exporting blank frames as success.

Captions use local, end-exclusive integer frames:

```json
[{"start": 0, "end": 30, "text": "One second, only in this shot."}]
```

Audio settings affect only the source audio slice owned by this shot:

```json
{"gain": 1, "mute": false, "fade_frames": 0}
```

Gain is 0 to 4. A fade cannot exceed half the shot. At 30 fps and 48 kHz each frame owns 1600 samples. Unchanged original audio continues across hard cuts; fades are explicit, not added automatically. A silent source gets an explicit silent output track.

## Files and recovery

Each project lives under the UI's state directory in `media/PROJECT_ID`. It contains an immutable original, source mezzanine, `project.json`, `units/shot-NNNN`, proposal folders, caches, history and exports. Large media lives in state, not a project Git worktree. The directory is not sent to a coding agent wholesale.

Each shot has `unit.json`, readable `visual.html`, `captions.json` and `audio.json` mirrors, a thumbnail, and an authoritative atomic `edit.json` commit. Use the UI or proposal-import path to edit. Direct edits to the mirror files are not committed edits. The single canonical blob prevents a crash between separate code/caption/audio writes from exposing a partially accepted shot. Before images or sound change, the source hash is rechecked. Cache identities include source, shot bounds, relevant content, output profile, renderer code/package lock and installed FFmpeg version.

History holds previous shot payloads. This draft does not yet have a history/undo UI or an automatic cleanup policy. A crash during the explicit pre-edit recut operation is not yet journal-recoverable; keep the original for reimport.

## Verified and not yet verified

Verified on Linux with real FFmpeg, headless Chromium and PySceneDetect: two-shot import/detection, portrait and silent sources, Unicode filenames, fixed frame counts, caption validation, audio-only cache reuse, single-shot picture invalidation, stale saves, failed-render preservation, and the existing dispatcher with a fake worker that writes a real proposal and then runs the real renderer. No hosted generation or model calls are used by tests.

Not yet verified: real Claude Code/Codex generation quality, M1 memory/performance, long-file endurance, color-managed HDR, complex phone rotation/VFR cases, or hostile-code resource exhaustion. Browser rendering has a job timeout and sandbox, but no per-process OS memory quota. Do not describe the M1 experience as benchmarked.

Not in this draft: automatic transcription, audio-source replacement, music tracks, cross-shot transitions, timing ripple/reorder, MP4, live collaborative editing, recovered burned-in text, drag-to-edit page elements or direct compatibility with another editor's project format. No code from the excluded storyboard application is used.

Run the tests:

```sh
pytest
SKYE_TEST_MEDIA=1 pytest tests/test_video_model.py tests/test_video_api.py tests/test_video_integration.py
```
