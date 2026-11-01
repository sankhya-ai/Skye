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

The supported working slice, all covered by tests in `tests/` (64 tests):

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
- **Persisted dynamic workflows (missions).** `skye mission run` executes a workflow of
  contracts with `needs` handoffs (each step's brief carries the recorded outcomes of the steps it
  depends on), `when` conditions (a step runs only if a referenced step settled a given way;
  unmet means skipped, and dependents of a skipped step skip in cascade), and run-time growth: a
  settled step may end with a `{"propose_steps": [...]}` block proposing follow-up steps, which are
  validated like authored contracts (known role/adapter, fresh ids, needs pointing at real steps),
  capped at 32 steps, journaled as accepted/rejected, and dispatched through the same owner policy
  as anything else. Every transition is journaled (`missions/<id>/journal.jsonl`, fsync'd before
  reliance) so a crash is recoverable: settled steps are never re-run, dynamically accepted steps
  are rebuilt from the journal, and an interrupted Root step REATTACHES to its journaled Root job
  (polled, never duplicated). CLI adapters (claude-code/codex) cannot reattach - resume
  re-dispatches the step and the journal says so; partial effects in the repo from the interrupted
  attempt are yours to inspect. A failed step fails the mission (no silent retries);
  Independent steps (needs all settled) fan out CONCURRENTLY in waves: wave width is
  `min(4, policy.max_concurrent)` - the default policy cap of 1 keeps missions strictly
  sequential, and the dispatcher's cross-process registry remains the enforcement of last
  resort. Settlement is journaled in worklist order on the main thread, so dependency order,
  skip cascades, the policy gate and dynamic proposals keep their exact semantics under
  parallelism; a failed step fails the mission after its wave completes (already-dispatched
  siblings finish - killing them mid-flight would strand unknown state).
  `skye mission reset` archives the journal to start clean. `tests/test_mission.py` and
  `tests/test_integration_mission.py` cover the chain, the proposals, the skips, and a real
  kill-Skye recovery against the vendored Root with zero duplicate jobs.
- **The workflow UI.** `skye ui` serves local screens (127.0.0.1 only, per-run session token,
  no cookies, cross-origin POSTs rejected) for everything the journal knows: a missions overview,
  a per-step run trace with live status, the journal itself, and functional one-time approvals -
  approve a needs-owner step in the browser, resume the mission, redispatch an interrupted step.
  The screens adapt Apache-2.0 components from block/buzz and block/berd (attribution in
  `skye/ui/NOTICE`). `scripts/ui_scenario.py` drives the whole loop in a real browser against
  the vendored Root with a scripted model - no provider, no cost.
- **One-time owner approvals.** `skye approve <job.json>` approves exactly one contract, once:
  the approval binds to the contract's fingerprint (sha256 of its normalized form), is consumed
  when the worker starts, and is invalidated by any edit. It never overrides the concurrency cap.
  `skye approvals` lists what is outstanding.
- **A review gate with a verdict.** The reviewer (a second worker, never the author grading
  itself) must end with `{"verdict": "pass"}` or `{"verdict": "block", "reasons": [...]}` - the
  same shape Root's contract check enforces natively. Only an explicit pass opens the gate; a
  finished-but-silent reviewer fails it. The role comes from the policy's `review_role`.
- **Wired controls, honest limits.** `max_concurrent` is backed by a cross-process running
  registry (pid liveness, stale-slot cleanup; a cooperative limit - a same-instant race can still
  exceed it, documented). `budget.timeout_seconds` is wired into every adapter; fields an adapter
  cannot enforce are reported as `budget_notes` on the outcome (claude-code/codex: max_steps and
  max_cost_usd; Root: max_cost_usd is forwarded to Root's provider-price-dependent cost accounting,
  not claimed as a hard bound). Lifecycle events record the worker's job id and the contract
  fingerprint.
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
slice above is scripted by design), real Claude Code/Codex runs (the adapters' command lines are
doc-verified but no real model has completed a run here yet), parallel fan-out (execution is
sequential; the workflow is dynamic in shape, not in concurrency), and the workflow UI (screens
are being adapted from block/berd's automations feature - Apache-2.0 - in a later change; nothing
visual exists yet). Long-term scope, deliberately out of this repo's first cut:
multi-machine teams and the hosted edition (see `docs/editions.md`). This is a working local core,
not a finished product.

## Journey verification: what is enforced and what is not

Delivery is gated on replaying the owner-configured journey command (`journey_command` in the
team policy - a plain check such as a smoke test; exit 0 passes). Every replay records, beside
the bounded and secret-redacted output tail:

- the **mission** whose work it covers (only finished work awaiting verification can be replayed);
- the **repo revision** it ran against (git HEAD, or a tree digest for non-git repos);
- the **journey check's own digest** (every command token that names a file is hashed).

Integrity limits, stated plainly: the team WRITES to the project repo, so a worker can also
rewrite a journey script that lives inside it. Skye detects, not prevents, that: a changed
check digest between replays is surfaced on the record and in the UI (`journey_changed`). For a
hard guarantee, keep journey checks OUTSIDE the team's writable repo - the command may point at
any path - or protect the check in the repo's own review machinery (CODEOWNERS etc.). The
journey command itself always comes from the owner-managed policy file; workers, requests and
logs cannot supply or override it.

## Quick start (local)

```sh
pip install -e .            # the skye dispatcher package (installs the `skye` command)
skye check job.json         # validate a contract
skye run job.json           # policy check -> worker -> gates (or needs-owner)
skye approve job.json       # one-time approval of exactly that contract (single-use)
skye mission run flow.json  # run or resume a persisted dynamic workflow
skye mission status flow.json
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
