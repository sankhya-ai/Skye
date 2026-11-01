# Skye

**An autonomous engineering team in a box.** Hand Skye a goal. A team of coding agents plans it,
builds it, reviews it and checks it, and stops to ask you before anything that needs your say-so.

Skye runs on your machine and drives the coding agents you already use. It does not ship a model,
resell tokens or download an agent for you.

[Quick start](#quick-start) | [How it works](#how-it-works) | [Workers](#workers) | [Honest status](#honest-status) | [License](#license)

---

## What Skye gives you

- **A team, not a single agent.** Standard roles (developer, senior dev, backend, frontend,
  reviewer, designer, artist, PM, tester, explorer, devops), each with its own instructions and an
  explicit tool catalogue. You can add or refine roles, and workers can propose new specialists,
  which stay inactive until you promote them.
- **Workflows that grow as they run.** A mission is a persisted workflow of steps with handoffs and
  conditions. A finished step can propose follow-up steps. Independent steps run in parallel, and
  every transition is journaled so a crash resumes instead of restarting.
- **You stay in charge.** An owner policy decides what runs unattended. Everything else waits for a
  one-time approval tied to the exact contract. Ideas for new work land in a backlog and need your
  approval before they start.
- **Review before delivery.** A second worker reviews the first and must return an explicit pass.
  A review can send work back with feedback for a bounded number of rounds.
- **Live control of running work.** Watch output and progress, send follow-ups to a running agent
  with honest delivery status, continue a recorded session or start a step fresh, and stop a run
  with confirmed-stop and uncertain states kept separate.
- **Tickets that remember.** Backlog items are tickets with labels and a permanent thread. A bug
  found on delivered work reopens the same ticket with its evidence.
- **A local UI.** `skye ui` serves missions, run traces, the journal and approvals on 127.0.0.1.
- **No LLM in the coordination path.** Dispatch, policy, approvals and gates are plain code. Tokens
  are spent only inside the workers, on your own subscriptions or keys.

## How it works

```
goal -> contract -> policy check -> worker -> gates -> delivery

policy check: outside the policy? needs-owner, approve once
worker:       Root, Claude Code or Codex
gates:        output contract, tests, reviewer verdict, journey check
```

1. You write a **contract**: goal, repo, role, adapter, gates and a small brief.
2. Skye checks it against your **policy**. Anything outside the policy comes back `needs-owner`.
3. A **worker** does the job in your repo through its adapter.
4. **Gates** decide whether the result counts: a contract check on the output, your test command,
   and a reviewer who must say pass. Delivery can also require replaying a journey check you own.
5. Lifecycle events go to a local `events.jsonl`, or to a Chamber line if you configure one.

## Workers

| Worker | What it is |
| --- | --- |
| **Root** (vendored in `root/`) | An open coding agent with dynamic tools, scoped jobs and missions that park and resume. Best integrated: Skye uses its real owner HTTP API and reattaches to interrupted jobs. Optional. |
| **Claude Code** | Your local install, driven through its CLI. Your login, your subscription. |
| **Codex** | Your local install, driven through `codex exec`. Your login, your subscription. |

**Chamber** (vendored in `chamber/`) is the built-in coordination layer. Skye uses its relay to
carry sealed team messages when you configure a line, and it stays optional for the engine, the
journal, approvals and the UI.

## Quick start

Requires Python 3.10+. The `skye` package has no third-party Python dependencies.

```sh
pip install -e .               # installs the skye command
skye check job.json            # validate a contract
skye run job.json              # policy check -> worker -> gates (or needs-owner)
skye approve job.json          # one-time approval of exactly that contract
skye mission run flow.json     # run or resume a persisted workflow
skye mission status flow.json  # show mission progress
skye ui                        # local screens on 127.0.0.1
```

A contract:

```json
{"goal": "add the health endpoint", "repo": "/path/to/repo", "role": "developer",
 "adapter": "claude-code", "gates": ["tests", "review"],
 "brief": "Small self-contained brief for the worker."}
```

Write a `skye.policy.json` before expecting unattended runs. Without one, every contract comes back
`needs-owner`. Start from `skye.policy.example.json`, and see `examples/` for roles.

For the `root` adapter, start the vendored Root server locally. Skye finds it with `SKYE_ROOT_URL`
(default `http://127.0.0.1:8788`) and authenticates with `SKYE_ROOT_TOKEN` or Root's local token
file. To publish events to a Chamber line, set `SKYE_LINE_INVITE` and `SKYE_LINE_TO`.

## Honest status

Skye is a working local core, not a finished product.

- **Verified:** the Skye test suite (363 passed, 1 skipped at the last full run), real end-to-end
  jobs against the vendored Root using its scripted model, and browser checks of the UI.
- **Not yet verified:** real signed-in Claude Code and Codex runs. Those adapters were checked
  against simulated provider processes and the vendors' CLI documentation, not a live model run.
  Design and planning quality with real models against real apps is also unproven.
- **Not a sandbox:** tool catalogues and write claims are cooperative guards, not OS security
  boundaries. A role whose catalogue includes Bash can do anything Bash can.
- **Not built:** the hosted edition and multi-machine teams. See `docs/editions.md`.

The detailed engineering notes, including every enforced control and its known limits, are in
`docs/technical-status.md`.

## License

Skye is released under the **Apache License 2.0**. See [LICENSE](LICENSE).

Vendored and adapted code keeps its own notices: `chamber/LICENSE` and `chamber/NOTICE`,
`root/runtime/LICENSE` and `root/runtime/NOTICE`, and `skye/ui/NOTICE` for the UI components
adapted from Apache-2.0 projects. See `VENDORING.md` for sources and versions.
