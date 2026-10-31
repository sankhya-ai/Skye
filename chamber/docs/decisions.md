# The decision ledger

Agents propose; agents review; teammates rule. Approved decisions go on the record — and every
decision has a way to stop being served. (The lifecycle follows architecture decision
records: proposed, approved, rejected, superseded.)

Shared memory already holds what the team knows, but anything an agent writes there looks the
same as anything else — an agent's suggestion and a decision the team agreed on are
indistinguishable. The decision ledger (`decisions/ledger.md` in shared memory) separates
them, on the record:

- **Agents propose.** When agents settle something durable — an API contract, an architecture
  choice, a convention — an agent records it with the `decision` tool (action=propose): title,
  rationale, what it affects, and where it was settled. A proposal is a suggestion, nothing
  more.
- **Other agents review.** Open proposals surface at every agent's next session start, and an
  agent records its judgment with `decision_review` (agree or object, with a reason). Reviews
  advise the ruling teammate; they change nothing on their own. This is where contradiction
  catching actually lives: a reviewing agent can spot a semantic clash that word matching
  never will.
- **Teammates rule.** In the app, Memory → Decisions shows what is waiting, with each
  proposal's reasons, reviews and any overlap, and Approve / Reject buttons. From a terminal,
  `chamber decisions` lists the ledger and `chamber decide <id> approve` (or
  `reject --reason …`) does the same. Either way the ruling goes on the record with your name and the date. The agent MCP tools
  deliberately have no ruling tool, so the path an agent uses every day cannot mark its own
  suggestion approved.
- **Every new session starts with the record.** Approved decisions are injected alongside
  MEMORY.md when an agent session starts, and `decision` (action=check) lets an agent list the approved
  decisions constraining the files it is about to touch — before it contradicts them.
- **Old rules retire.** A proposal can mark the approved decision it replaces (`supersedes`).
  When a teammate approves the replacement, the linked old decision becomes `superseded` in
  the same commit — that pair never both stands, and the retired rule is no longer served to
  new sessions. Contradictions nobody linked are for reviewers to catch; the flag only
  retires what the proposal names.
- **Conflicts surface instead of hiding.** A proposal that overlaps an approved decision (same
  files, contracts or topic words) comes back flagged with the decisions it collides with, so
  the teammate ruling on it sees the clash.

The ledger is plain structured Markdown in the chamber's end-to-end-encrypted shared memory,
committed against the version it was read at: if two agents (or an agent and you) write at
once, the second is refused by the relay and redone on top of the first, so nothing is
written over. Agents' memory tools and the dreamer leave the ledger file alone; it changes
only through the decision tool, the app and the CLI. No new dependencies, no
embeddings, no API keys, no background model runs.

## What it costs

Deliberately small. The intelligence comes from sessions the team is already paying for:
proposing and reviewing are ordinary tool calls inside an agent's existing Claude Code, Codex
or other session. The full recurring overhead, measured on this patch:

- Tool schemas: the `decision` tool adds 1,173 characters of MCP tool definition (~300
  tokens at ~4 chars/token), loaded with the rest of chamber's tools in clients that preload
  schemas.
- Server instructions: one added sentence, 149 characters (~40 tokens).
- Session start: the approved-decisions list, capped at 20 entries x 140 characters plus a
  header and an open-proposals line — worst case ~3,100 characters (~780 tokens), and zero
  while the ledger is empty. Long titles are cut with an ellipsis, never mid-ruling, and the
  id is always shown so the full record is one tool call away.

Worst case that is ~4,400 characters (~1,100 tokens) per session, roughly a third of it the
fixed tool schema and instructions; with an empty ledger the fixed ~1,300 characters (~330
tokens) is all there is. Proposals and reviews are short tool calls, only when an agent has
something to say. No polling, no scheduled summarization, no embedding passes, no per-message
tax. Conflict pre-flagging on propose is lexical (matching paths and words) — free, and a
first filter only; agent review is the semantic check.

## Trust note: a record for people acting in good faith, not proof

Two things this ledger is not. It is not enforcement: nothing stops a determined agent from
rewriting the file through the relay directly, or from running `chamber decide` itself if it has
a shell. And it is not proof: the `proposed-by` / `decided-by` fields are plain, editable
text. An agent that bypasses the tools can write `decided-by: nitin (human)` itself, and no
one could tell from the file. What the ledger does give a team whose agents use the tools as
intended is a clear, durable separation between suggestions and rulings — and a single place
to look. Authenticated rulings (signed or relay-checked) are a possible follow-up.
