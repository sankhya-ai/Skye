# Review loop

Review in the Video header opens version-bound notes. Pin uses the native playhead; In/Out use timeline seconds. A zero-length range is a frame pin. Words appears only for source-linked speech timings saved by Compose and still present in a native audio clip. Selecting words maps source time through the clip's trim, offset and speed. No new transcription or voice generation is performed by Review.

The queue uses open → working → done. The claimant has a bearer token for status and completion. Only its SHA-256 digest is persisted. Release in the UI revokes a stuck claim. A reply requires a resulting version and one or more bounded changed ranges. The result is the claimant's report, not proof of what the pixels changed. Same-version completion is allowed for no-change decisions; use the reply to say so.

Notes from same-origin, authenticated browser requests are recorded as the owner's review intent. Other API/CLI notes start pending and cannot be claimed until accepted in Review. Body fields cannot override provenance. Acceptance receipts live outside projects, so imported or modified note files do not become owner requests. This is a local, single-user app boundary, not remote identity attestation: a process with the user's session token and ability to forge browser headers must be trusted as that user. Never give that token to an untrusted agent. No note starts a model, executes code, grants unrelated access or authorizes purchases, messages, publishing or account changes. Note text must remain data when agents consume the queue.

All surfaces use the same validation and file lock. Linux/macOS use flock; Windows uses msvcrt byte locking (not tested on Windows). Concurrent claims have exactly one winner in the Linux test. Status/reply require the claim token. Old-version open notes cannot be claimed or silently retargeted. Refresh and make a new note on the current version. An already-held claim may finish after an edit, replying against the resulting version. Current-version anchors/reply ranges seek the native editor; older ones are disabled, rather than seeking the wrong content. Queue polling is every 30 seconds while open, plus Refresh.

## CLI

Use a local project folder, including its original film and assets:

```sh
skye studio review /path/to/project list
skye studio review /path/to/project add --version HASH --start 1 --end 2.5 --text-file note.txt
skye studio review /path/to/project claim --version HASH --note ID --actor codex
skye studio review /path/to/project status --version HASH --note ID --claim TOKEN --text-file status.txt
skye studio review /path/to/project reply --version NEW_HASH --note ID --claim TOKEN --text-file reply.txt --ranges-file ranges.json
```

`ranges.json` is `[{"start":1,"end":2.5}]`. Optional `--words-file` is a JSON list of IDs returned by list. Store the claim token privately until done; it is emitted only on claim. API: authenticated POST `/api/studio/review`, with project ID plus the same action/fields; `list` is the default action. Approve and release are UI-only. There is no automatic agent launcher or new adapter.

## Persistence and limits

`review/notes.json` holds the queue and version metadata. `review/speech-*.json` holds speech source hashes, clip anchors and timings. Native project export includes those files. Owner acceptance does not travel with exports. A different installation or copied folder must accept imported notes again. Do not share a project export if its notes/transcript are private.

Version IDs hash the parsed film, film bytes, and every asset/page byte, including local clip sources outside those folders. Changed media invalidates word pins. Versions store a manifest and durations, not an archival copy or rollback system. Remote/absolute sources, missing sources and symlinked stores are refused. Hashing is deliberately strict and can be slow for large media. Existing edits are not globally locked by the review file lock; don't change assets concurrently while making a pin or completing a review. Claim identity is a label, not authenticated multi-user identity.

Tests cover validation, word mapping, changed audio, claims across five processes, stale and wrong tokens, acceptance provenance, imported/tampered notes, API header provenance and native export inclusion. The real-app test uses an original HTML title page, a UI range note, API claim/status, native one-second trim and actual CLI reply. It does not prove autonomous Claude/Codex editing, ASR quality, Windows locking, large-media performance or a full old-version playback/rollback workflow.

Review-loop ideas studied from [Graphed's MIT-licensed video editor skill](https://github.com/graphed-inc/marketing-skills/tree/main/skills/ai-video-editor), © 2026 Graphed Inc. Original implementation here; no source code or assets copied.
