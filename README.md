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

- Job contract validation, a conservative team policy (nothing runs unattended until the owner
  writes a rule), a dynamic per-role tool gateway, three worker adapters (Root, Claude Code, Codex),
  and ordered quality gates (tests from the policy's command; review by a SECOND worker, never
  self-review). Tested: `tests/` (12 tests).
- Root's mission system: durable jobs, delegated children with narrowing scope, bounded auto-start
  for routine proposals (owner policy, disabled by default), interrupt recovery markers.
- Chamber's identity v2/v3 with its test matrix (111 tests passing in the vendored tree).

Not built yet: the Chamber bridge that publishes job lifecycle events to a line, the designer/PM
roles operating a real app end to end, multi-machine teams, the hosted edition (see
`docs/editions.md`). This is a working local core, not a finished product.

## Quick start (local)

```sh
pip install -e .            # the skye dispatcher package
skye check job.json         # validate a contract
skye run job.json           # policy check -> worker -> gates
```

Write `skye.policy.json` before expecting unattended runs; without it every contract comes back
`needs-owner`. A job contract:

```json
{"goal": "add the health endpoint", "repo": "/path/to/repo", "role": "developer",
 "adapter": "claude-code", "gates": ["tests", "review"],
 "brief": "Small self-contained brief for the worker."}
```
