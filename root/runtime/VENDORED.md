# Vendored runtime

This is `subsystems/sankhya/third_party/kimi-agent-rs` from `sankhya-ai/tura`
at commit `9bca303bb0d02f3189582ef72e17f488dacc984d`, copied without its
`target/`, `.github/`, `_/` planning notes, and the `kimi-cli` submodule
reference.

Keep local changes small and listed here so upstream updates stay mergeable.

## Local changes

1. **Web search backends** (`kimi-agent/src/tools/web/search/`,
   `kimi-agent/src/config.rs`). `search.rs` became a module with one file per
   backend. `services.web_search` adds the keyless **Parallel** (anonymous MCP
   endpoint, one stateless `tools/call`) and **Firecrawl Keyless** backends,
   tried in order with fallback; keys are optional. `services.openrouter_search`
   (OpenRouter's paid `web` plugin) is the last resort when configured; an
   empty `base_url`/`api_key` falls back to the main provider's. Moonshot's
   service, when configured, is used alone as before.
2. **`CLAUDE.md` as project instructions** (`kimi-agent/src/soul/agent.rs`).
   Read after `AGENTS.md` and `agents.md`, so a repository set up for Claude
   Code works unchanged.
3. **Model-facing wording** (`kimi-agent/src/tools/file/*.rs`,
   `kimi-agent/src/tools/desc/file/*.md`). "Chotu coding runtimes" became
   "workspace-scoped runtimes" in tool descriptions and one error message.
4. **Removed the `kimi-cli-help` builtin skill**, which documents the Kimi CLI
   rather than this product.
5. **Extra skill roots** (`kimi-agent/src/skill/mod.rs`).
   `KIMI_EXTRA_SKILLS_DIRS` (path-list) adds roots after the built-in skills
   and before the user's and project's, so the host's learned skills load and
   an owner's own skill wins on a name clash.
6. **Tool-argument repair for weaker models** (`kimi-agent/src/soul/args_repair.rs`,
   moved out of `toolset.rs`). Lossless repairs (code fences, raw control
   characters in strings, trailing commas, double encoding) for every tool;
   completing cut-off arguments only for read-only tools, and `Shell` only
   with a closed command string.
7. **Whitespace-tolerant `StrReplaceFile`** (`kimi-agent/src/tools/file/tolerant.rs`).
   When `old` is not found exactly, a unique line-by-line match ignoring
   trailing (then surrounding) whitespace is applied, re-indenting the
   replacement; the result says so.
8. **Repeat nudge** (`kimi-agent/src/soul/repeat_guard.rs`, wired in
   `kimisoul.rs`). The third identical call with an identical result this
   turn gets a note in the model's context; wire events are unchanged.
9. **Recursive `Glob`** (`kimi-agent/src/tools/file/walk.rs`, `glob.rs`,
   `desc/file/glob.md`). Patterns starting with `**` walk the tree, pruning
   dependency, VCS, build and cache directories and bounding the walk, instead
   of being refused (local workspaces only).
10. **Verify gate** (`kimi-agent/src/soul/verify_gate.rs`, wired in
    `kimisoul.rs`). With `ROOT_VERIFY_GATE` set (the host sets it), a step that
    would end the turn while a successful `WriteFile`/`StrReplaceFile` has no
    later non-read-only `Shell` command gets a note and the turn continues — at
    most twice per turn. Off by default, so the runtime is upstream's elsewhere.
11. **Director turn facts and rules** (`soul/director.rs`, `kimisoul.rs`). The
    director's `context` slot carries `turn_facts` (files read and edited this
    turn, whether an edit is still unexercised); the before-step and turn-end
    questions gained rules on not re-reading and on exercising edits; and while
    an edit is unexercised the harness does not force another
    `ReadFile`/`Glob`/`Grep`.
12. **Path repair** (`tools/file/mod.rs`, `repair_path`). A path that does not
    exist is put right when the workspace path was written twice or a
    `?…`/`#…` suffix was appended, and only when the repair exists (or, for a
    new file, its directory does).
13. **Argument reshaping** (`soul/args_repair.rs`, `reshape`). Lossless: a
    `StrReplaceFile` call written flat (`{path, old, new}`) or with `edits` is
    moved into `edit`.
14. **Directed arguments must fit** (`soul/director.rs` `fits`, `kimisoul.rs`).
    When the director chose a tool and the model's arguments lack that tool's
    required fields (it wrote an edit for a directed `Shell`), the model takes
    the step itself instead of the harness dispatching a call that must fail.
15. **"Did you mean"** (`tools/file/mod.rs` `not_found`, `walk.rs`
    `find_by_name`). `ReadFile`, `ReadMediaFile` and `StrReplaceFile` name up
    to five files of the same name in the workspace when a path does not exist
    (bounded, pruned walk).
16. **`FetchURL` on local paths** (`tools/web/fetch.rs`). A `file://` URL or a
    filesystem path is refused with a pointer to `ReadFile`/`Glob`.
17. **Tool calls written as text** (`soul/leaked_calls.rs`, wired in
    `kimisoul.rs`). A step with no tool call whose text carries tool-call markup
    (DeepSeek DSML, `<tool_call>`, …) keeps the turn open with a note to make the
    call properly, at most twice per turn.
18. **Test fix** (`tests/create_llm.rs`). The non-streaming transport test now
    holds `ENV_LOCK`; it read env vars other tests set in parallel.
19. **Read boundary** (`tools/file/mod.rs` `approve_outside_read`; `read.rs`,
    `read_media.rs`, `grep.rs`). With `ROOT_READ_BOUNDARY` set, reading a path
    outside the working directory requests approval (action `read file outside
    of working directory`), as editing there already did. `Glob` already refused.
20. **Reasoning that runs out the reply** (`soul/kimisoul.rs`). A step that
    comes back empty (all reasoning, no answer or call) is retried with
    reasoning off, not with the same settings: measured on OpenRouter,
    effort and `reasoning.max_tokens` did not bound reasoning for
    `deepseek-v4.1-flash`, so an identical retry reasoned to the limit again.
21. **Director arguments without reasoning** (`soul/kimisoul.rs`
    `directed_step`). The call that writes a decided tool's arguments runs
    with reasoning off.
22. **OpenRouter provider preference** (`kosong/src/chat_provider/kimi.rs`).
    `ROOT_OPENROUTER_SORT` = `throughput`|`price`|`latency` sends
    `provider: {sort}`.
23. **Cancelled turns leave nothing running** (`soul/kimisoul.rs` `ApprovalTask`,
    `soul/mod.rs` `run_soul`). The step's approval piping task is aborted when
    the step is dropped, not only when it finishes: a cancelled turn used to
    leave it running on the shared approval queue, where it took the next
    turn's first approval into the dead turn's wire and that tool call hung
    for good. A UI loop that misses its shutdown window is aborted too.
24. **Requests don't stall the wire** (`wire/server.rs` `request_approval`,
    `request_tool_call`). The UI loop sends a request and moves on; the caller
    waits on its own request. It used to wait for the answer, so a long host
    tool call (a ten-minute `process` wait) held back every later event and a
    parallel call's approval until it returned.
25. **Retries that ride out an outage** (`soul/kimisoul.rs` `retry_class`,
    `retry_delay`; `wire/types.rs` `StepRetry`). A provider failure
    (connection, timeout, 408/429/5xx/529) backs off 2s doubling to 30s, up to
    `max_retries_per_step` attempts (root sets 10); an empty or malformed reply
    keeps three quick attempts. Each retry sends a `StatusUpdate` with `retry`.
26. **One PowerShell per agent on Windows, and no console windows**
    (`tools/persistent_shell.rs`, `tools/shell.rs`, `kaos::no_window`). As in
    DeepSeek Harness's persistent `pwsh` tool, `Shell` keeps one hidden
    PowerShell open and sends each command into it (an id and the command in
    base64, read back up to an end marker with the exit code), so variables,
    functions and modules persist; every call is anchored to the work dir
    again. A timeout, a cancelled call or `exit` kills its process tree and
    the next call starts fresh. bash keeps one process per call. Every child
    the runtime starts on Windows (shells, ripgrep, MCP servers) gets
    `CREATE_NO_WINDOW`: the host runs it without a console, so each used to
    open a window. The tool description says the session persists.
27. **A `mac-apps` built-in skill** (`kimi-agent/src/skills/mac-apps/`): how
    to use the host's `mac_*` tools (root-driver) — look, act on an element
    token, check — and what never to do unasked. Only a skill file; the tools
    themselves are the host's (`server/tura_code/driver`).
28. **The cost budget stops the main loop** (`soul/kimisoul.rs`, the step's
    budget checks): `check_cost_budget` now runs before each model step next to
    the token and wall-clock checks. It previously gated only subagent tasks and
    compaction, so `CHOTU_MAX_TURN_COST_USD` recorded cost but never ended a
    turn. Covered by the host's `test_jobs.py` (cost cap, real runtime).

Tests: `search/tests.rs` (backends and fallback against a mock server) and
`tests/load_agents_md.rs` for 1–2; the tool schema and description tests were
updated for 3 and 9; `tests/extra_skills_dirs.rs` for 5; unit tests in
`args_repair.rs`, `tolerant.rs`, `repeat_guard.rs` and `walk.rs`, and
`tests/tool_glob.rs` (rewritten for the pruned walk) for 6–9; unit tests in
`verify_gate.rs`, `director.rs`, `tools/file/mod.rs`, `args_repair.rs`, `leaked_calls.rs` and `web/fetch.rs` for 10–17; a unit test in `kimi.rs` for 22; a unit test in `kimisoul.rs` for 23; the host's `test_processes.py` covers 24; a unit test in `kimisoul.rs` for 25; unit tests in `persistent_shell.rs` (the bash twin everywhere, PowerShell on Windows) and the host's `test_windows.py` for 26.

29. **A symlink out of the workspace is outside it** (`utils/path.rs` `is_within_directory`): the name check let
    ReadFile/Grep/Glob and the edit tools reach a file through a symlink inside the workspace (a cloned repo can ship one)
    without the host's outside-path approval ever firing. It now also requires the resolved real path to be inside the real
    directory (a not-yet-existing tail resolves through its nearest existing parent). Unix test in the same file.

## Updating from Tura

```bash
# from a tura checkout
diff -ru subsystems/sankhya/third_party/kimi-agent-rs /path/to/root/runtime \
  -x target -x .github -x _ -x .gitmodules -x .pre-commit-config.yaml -x VENDORED.md
```

Re-apply the changes above, then `cargo test --workspace`.
