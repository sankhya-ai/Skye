# Video editor

`video-editor` is a shipped role, selectable in the normal Agents and Teams views.
The Video tab opens the same film as the local editor. Its default Local commands
mode needs no LLM. Select one footage clip, then send `caption "Keep walking"`,
`text "Your words"`, `punch`, `shake`, `flash`, `grain`, `glitch`, `mirror`,
`volume 50%` or `mute`. Unknown commands fail, never approximate intent.

Claude Code and Codex remain optional, using your own installed CLI and login.
They receive a film snapshot and selected clip IDs, not editor keys or media. They
can propose basic timeline operations or a supported effect/text object. Skye's
trusted local generator makes effect pages and native operations. The frame checks
the revision and applies the result through its ordinary Undo path. Human edits
are part of the next snapshot; an edit made while the agent works rejects the
stale result. Generated but unapplied pages may remain as unused assets after a
conflict or a closed tab. No film edit is silently committed in that case.

Boundaries: one visual effect per request on one footage clip; linked sound stays
in scope. Locked tracks are rejected. Masks/cutouts and tracking are not exposed
through the text grammar yet. Reference analysis, tracking and M1 verification
remain separate milestones. On the Linux test host neither Claude Code nor Codex
is installed, so only deterministic local edits and stubbed dispatcher tests have
been exercised, not a real owner-LLM run.
