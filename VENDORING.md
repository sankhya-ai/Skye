# Vendored sources

Skye vendors two sankhya-ai projects verbatim except where noted:

- `root/` - sankhya-ai/root @ 99fd79b (branch pr2), copied via `git archive`.
  Excluded: `root/bench/` (upstream benchmark harness; not used by Skye's
  runtime or tests; its run artifacts carry contributor-local paths).
- `chamber/` - sankhya-ai/chamber @ a5e9842 (branch identity-v2), copied via `git archive`.

Upstream licenses and notices are preserved in place: `chamber/LICENSE`,
`chamber/NOTICE`, `root/runtime/LICENSE`, `root/runtime/NOTICE`.
Skye's own license is pending the owner's choice; this file grants no rights
beyond those upstream texts.

## UI screen sources (adapted, Apache-2.0)

The `skye ui` screens adapt components from two Block projects at the owner's
request; per-file attribution headers and `skye/ui/NOTICE` carry the details:

- `block/buzz` @ `acf56fff6fbfece9f143de3f096cb1d258fb9a9f`
  (https://github.com/block/buzz, Apache-2.0): workflow run trace and
  approval card patterns.
- `block/berd` @ `2d2754640abbb5a7f26fe190551a7173f5c87788`
  (https://github.com/block/berd, Apache-2.0): automations overview row
  pattern.
