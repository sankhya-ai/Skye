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
