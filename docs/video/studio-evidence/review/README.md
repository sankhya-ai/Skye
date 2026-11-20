# Local review-loop evidence

2026-11-20, Linux/Chrome, real running Skye UI. Original HTML title fixture, no outside footage or audio. No model/paid calls.

- `working.png`: UI range note at 1.000–2.500, API claim and progress.
- `done.png`: native clip shortened from 8 to 7 seconds, actual Python review CLI reply, original and resulting version IDs shown separately. The initial title fixture lacked the native `window.film.frame` contract; it was corrected and the complete flow rerun before these captures.
- `seek.png`: current-version frame pin at 1.500 seeks the native playhead to 00:01:15 at 30 fps. Old-version anchor stays disabled.

The proof actor is labeled Local CLI. Claim/progress were API calls; completion was `python -m skye.studio.review`. The `skye.cli` dispatch was separately exercised with list. This is deterministic local orchestration, not autonomous Claude/Codex editing.

Final suite: 686 passed, 14 skipped. Sixteen review-specific tests pass. Main UI TypeScript and production build pass. Native editor production build passes. Build warnings about large existing chunks remain. Timed-word source mapping and invalidation are unit tested; no new speech/voice generation or fresh ASR demonstration was done. Windows locking, large project hashing speed, archived-version playback and autonomous editing remain unproven.
