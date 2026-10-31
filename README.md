# Skye

An autonomous engineering team in a box: coding agents you already use, coordinated like a team.

- **Chamber** (vendored at `chamber/`) is the coordination layer: agent-to-agent messaging, handoffs,
  shared encrypted project memory, approval gates, a board humans watch. Its identity v2/v3 mode adds
  per-member signing/encryption keys and per-epoch read keys with atomic revoke+rotate.
- **Root** (vendored at `root/`) is one worker: an open coding agent with dynamic tools, scoped jobs,
  missions that park and resume, and opt-in Linux isolation. It is the best-integrated worker, not the
  only one.
- **Your own Claude Code or Codex** work too: Skye drives your local installs through their CLIs.
  Your subscription, your login - Skye never proxies, pays for, or downloads a coding agent.

The coordination path is **plain code - no LLM in it, ever**. The dispatcher validates contracts,
checks the team policy, resolves tool catalogues and starts workers; the LLMs live inside the
workers, which you already pay for.

## What is real today (honest state)

The supported working slice, all covered by tests in `tests/` (28 tests):

- **Contract -> policy -> gateway -> worker -> gates, for real.** With adapter `root`, Skye drives
  the vendored Root through its real owner HTTP API: the adapter authenticates with Root's local
  owner token, starts the job, and polls it to a settled outcome - "job started" is not a result.
  `tests/test_integration_root.py` runs this end to end with Root's scripted model (no provider,
  no cost): a real Root job writes a real file through Root's tool path, Skye reads the settled
  contract-checked result, and the policy's test gate runs against the repo. A worker that breaks
  its output contract fails the dispatch before gates run.
- **The designer/PM slice.** A designer contract dispatched to Root inspects a real rendered
  page (Playwright over a fixture) through the dynamic-tool gateway and settles with its
  verdict/reasons contract checked; a PM contract settles the same way without the UI tool.
  `tests/test_integration_designer.py` proves the inspection ran (the fixture's rendered content
  is in the tool result) - it does not claim design or planning QUALITY.
- **The Chamber bridge.** The dispatcher emits lifecycle events (accepted, needs-owner, gate
  pass/fail, done, failed) and publishes them to a Chamber line through Root's vendored line
  client - sealed messages on the real relay, verified in `tests/test_line_bridge.py` with a
  second member receiving them. The audience is explicit (SKYE_LINE_TO is required; '*' is a
  choice, never a default) and the default payload is minimal (kind/role/status/reason; the goal
  is mission content and only appears with SKYE_LINE_VERBOSE=1). No line configured: events go to
  a local `events.jsonl`, never silently dropped; a dead line sink never loses an event or kills
  a dispatch. The Root owner token authenticates the adapter host-side only - it never appears in
  worker output, events, or errors (tested).
- **Claude Code and Codex adapters.** Command construction is verified against the current vendor
  CLI references (Claude Code: headless `-p`, `--output-format`, tool restriction via `--tools`
  with `--disallowedTools mcp__*`; Codex: `codex exec` with its read-only-by-default sandbox).
  These mechanisms are CLI-level configuration, NOT an OS security boundary: a role whose
  catalogue includes Bash can do anything Bash can. Real model runs with these adapters need your
  own installed, logged-in tools.
- Root's mission system: durable jobs, delegated children with narrowing scope, bounded auto-start
  for routine proposals (owner policy, disabled by default; the lifetime fuse survives edits and
  restarts until explicitly reset), interrupt recovery markers.
- Chamber's identity v2/v3 with its test matrix (111 tests passing in the vendored tree).

Missing from the working product path: designer/PM flows with real models against real apps (the
slice above is scripted by design), multi-contract missions with dependencies, and handoff of
results between contracts. Long-term scope, deliberately out of this repo's first cut:
multi-machine teams and the hosted edition (see `docs/editions.md`). This is a working local core,
not a finished product.

## Quick start (local)

```sh
pip install -e .            # the skye dispatcher package
skye check job.json         # validate a contract
skye run job.json           # policy check -> worker -> gates
```

For adapter `root`, start the vendored Root server locally; Skye finds it via `SKYE_ROOT_URL`
(default `http://127.0.0.1:8788`) and authenticates with `SKYE_ROOT_TOKEN` or the token file under
`SKYE_ROOT_HOME`/`TURA_CODE_HOME`. To publish lifecycle events to a Chamber line, set
`SKYE_LINE_INVITE` (a line invite URL) and optionally `SKYE_LINE_TO`.

Write `skye.policy.json` before expecting unattended runs; without it every contract comes back
`needs-owner`. A job contract:

```json
{"goal": "add the health endpoint", "repo": "/path/to/repo", "role": "developer",
 "adapter": "claude-code", "gates": ["tests", "review"],
 "brief": "Small self-contained brief for the worker."}
```
