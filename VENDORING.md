# Vendored sources

Skye vendors two sankhya-ai projects verbatim except where noted:

- `root/` - sankhya-ai/root @ 99fd79b (branch pr2), copied via `git archive`.
  Excluded: `root/bench/` (upstream benchmark harness; not used by Skye's
  runtime or tests; its run artifacts carry contributor-local paths).
- `chamber/` - sankhya-ai/chamber @ a5e9842 (branch identity-v2), copied via `git archive`.

Upstream licenses and notices are preserved in place: `chamber/LICENSE`,
`chamber/NOTICE`, `root/runtime/LICENSE`, `root/runtime/NOTICE`.
Skye's own code is licensed under Apache-2.0 (see `LICENSE`); the vendored
projects above are Apache-2.0 under their own notices.

## UI screen sources (adapted, Apache-2.0)

The `skye ui` screens adapt components from two Block projects at the owner's
request; per-file attribution headers and `skye/ui/NOTICE` carry the details:

- `block/buzz` @ `acf56fff6fbfece9f143de3f096cb1d258fb9a9f`
  (https://github.com/block/buzz, Apache-2.0): workflow run trace and
  approval card patterns.
- `block/berd` @ `2d2754640abbb5a7f26fe190551a7173f5c87788`
  (https://github.com/block/berd, Apache-2.0): automations overview row
  pattern, app shell geometry (floating glass sidebar over the dot-grid
  canvas, Projects/Chats sections), home clock/greeting/composer layout,
  and the light visual tokens.

## Chamber needs-assessment (what Skye actually uses)

Skye imports NO Chamber code. The only touchpoint is the optional event
relay: when SKYE_LINE_INVITE names a Chamber line, `skye/events.py`'s
LineBridge publishes dispatch lifecycle events to that line through Root's
vendored Python line client (`root/server/tura_code/line`), and the line
runs on `chamber/relay` (Cloudflare worker, `wrangler dev`; exercised by
tests/test_line_bridge.py, env-skipped without the relay's npm deps). The
default sink is the local events.jsonl (FileBridge) - Chamber is never
required for the engine, the journal, approvals, or the UI.

Dependency footprint, measured: the relay path needs ONLY `chamber/relay`
(its src, serve.mjs, package.json, wrangler.toml). At the owner's explicit
direction ("we dont need everything from chamber, just functioning") the
unused remainder of the upstream checkout was removed: board/, ui/, docs/,
desktop/, lib/, test/, bin/, scripts/, .github/, Dockerfile, create.sh,
install.sh, vm.sh, and the root package/docker/git meta files. Kept:
`chamber/relay/` plus the upstream LICENSE, NOTICE, README.md and
SECURITY.md for provenance. The full upstream tree remains recoverable from
sankhya-ai/chamber @ a5e9842 (branch identity-v2) recorded above; this trim
drops nothing Skye or its tests reference.
