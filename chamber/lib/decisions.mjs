// The decision ledger: the team's decisions on record, as structured entries in shared memory.
//
// Agents propose; other agents review; teammates rule. A proposal is an agent's suggestion
// until a person approves it with `chamber decide <id> approve`. Every decision has a way to
// stop being served (the ADR lifecycle): approval of a proposal that supersedes an older
// decision retires that linked record to `superseded` in the same commit, so that pair never
// both stands. Contradictions nobody linked are for reviewers to catch. Reviews ride on the agents'
// existing sessions — proposals surface at session start and an agent records its judgment
// with decision_review. No extra model runs, embeddings or API keys: the only added context
// is a capped list at session start. Approved decisions are on the record — every new agent
// session starts with them (see hooks.mjs), and decision_check warns an agent before it
// contradicts one.
//
// The ledger is one shared-memory file, decisions/ledger.md, so it inherits memory's
// end-to-end encryption and optimistic concurrency. One entry per decision:
//
//   ## d-20261029-01 · Auth token expiry is int epoch seconds
//   - status: proposed | approved | rejected | superseded
//   - rationale: matches the JWT exp claim; no Date parsing across services
//   - affects: api/auth/schema.py, contracts/auth-token
//   - source: talk/ab12cd34#m9
//   - proposed-by: nitin/claude-code.api
//   - added: 2026-10-29
//   - conflicts: d-20261012-02          (set when a proposal overlaps an approved decision)
//   - review: dev/codex.api · object — breaks v1 clients · 2026-10-29   (repeatable)
//   - supersedes: d-20261012-02         (proposal replaces an approved decision)
//   - superseded-by: d-20261029-01      (set when the replacement is approved)
//   - decided-by: nitin (human)         (approve/reject only)
//   - decided-on: 2026-10-29
//   - reason: …                          (reject only, optional)
//
// Trust note: this is a record for a team acting in good faith, not proof. Nothing stops an
// agent from editing the ledger with the raw memory tools or running `chamber decide` itself
// if it has a shell, and the proposed-by / decided-by fields are plain editable text — an
// agent bypassing the tools could forge them, and the file would not show it. For agents
// using the tools as intended, the ledger keeps suggestions visibly separate from rulings.
// Authenticated rulings (signed or relay-checked) are a possible follow-up.

import { LineError } from "./protocol.mjs";

export const LEDGER_PATH = "decisions/ledger.md";
const HEADER = "# Decision ledger\n<!-- chamber-ledger:v1 -->\n\nProposed by agents, approved by humans. Only `approved` entries bind the team.\n";

const today = () => new Date().toISOString().slice(0, 10);
const esc = (s) => String(s ?? "").replace(/\s+/g, " ").trim();

// Words too common to signal that two decisions are about the same thing.
const STOP = new Set("about after also always before between does from have into just like make more must need never only over same should some such than that their them then there these they this those through under until upon when where which while will with within without would your".split(" "));

/** Words worth comparing: lowercase alphanumeric runs of 4+ characters, minus common words. */
const tokens = (text) => new Set(String(text).toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length >= 4 && !STOP.has(w)));

function parse(text) {
  const entries = [];
  if (!text) return entries;
  let current = null;
  for (const line of text.split("\n")) {
    const head = line.match(/^## (\S+) · (.*)$/);
    if (head) {
      current = { id: head[1], title: head[2].trim(), raw: [line] };
      entries.push(current);
      continue;
    }
    if (!current) continue;
    current.raw.push(line);
    const field = line.match(/^- ([a-z-]+): (.*)$/);
    if (field && field[1] === "review") (current.reviews ??= []).push(field[2].trim());
    else if (field) current[field[1]] = field[2].trim();
  }
  return entries;
}

function render(entry) {
  const lines = [`## ${entry.id} · ${entry.title}`];
  lines.push(`- status: ${entry.status}`);
  if (entry.rationale) lines.push(`- rationale: ${entry.rationale}`);
  if (entry.affects) lines.push(`- affects: ${entry.affects}`);
  if (entry.source) lines.push(`- source: ${entry.source}`);
  lines.push(`- proposed-by: ${entry["proposed-by"]}`);
  lines.push(`- added: ${entry.added}`);
  if (entry.conflicts && entry.conflicts !== "none") lines.push(`- conflicts: ${entry.conflicts}`);
  if (entry.supersedes) lines.push(`- supersedes: ${entry.supersedes}`);
  if (entry["superseded-by"]) lines.push(`- superseded-by: ${entry["superseded-by"]}`);
  for (const r of entry.reviews ?? []) lines.push(`- review: ${r}`);
  if (entry["decided-by"]) lines.push(`- decided-by: ${entry["decided-by"]}`);
  if (entry["decided-on"]) lines.push(`- decided-on: ${entry["decided-on"]}`);
  if (entry.reason) lines.push(`- reason: ${entry.reason}`);
  return lines.join("\n");
}

export class DecisionLedger {
  /** `memory`: a TeamMemory (or a stub with read/write/edit); `author`: e.g. "nitin/claude-code.api". */
  constructor(memory, author) {
    this.memory = memory;
    this.author = author;
  }

  /**
   * Read the ledger, let `change(text, entries)` return { text, out }, and commit against the
   * version read. If someone committed in between, the relay refuses and we start over from
   * theirs, so a concurrent proposal, review or ruling is never written over.
   */
  async #change(message, change) {
    for (let attempt = 0; attempt < 3; attempt++) {
      const file = await this.memory.read(LEDGER_PATH);
      const { text, out } = change(file?.text ?? "", parse(file?.text));
      const result = await this.memory.commit([{ path: LEDGER_PATH, text, base: file?.version ?? 0 }], typeof message === "function" ? message(out) : message);
      if (result.ok) return out;
    }
    throw new LineError("decision: the ledger kept changing while writing; try again");
  }

  async list({ status } = {}) {
    const file = await this.memory.read(LEDGER_PATH);
    const entries = parse(file?.text);
    return status ? entries.filter((e) => e.status === status) : entries;
  }

  /** Approved decisions overlapping `affects`/`title`: the ones a proposal may contradict. Lexical only — no model calls. */
  #conflicts(entries, { title, affects }) {
    const mine = tokens(`${title} ${affects}`);
    if (!mine.size) return [];
    return entries.filter((e) => {
      if (e.status !== "approved") return false;
      const theirs = tokens(`${e.title} ${e.affects ?? ""}`);
      return [...mine].some((w) => theirs.has(w));
    });
  }

  /**
   * Record an agent's proposal. Returns { entry, conflicts } — conflicts are approved
   * decisions this overlaps, so the agent (and later the human) can see the collision.
   */
  async propose({ title, rationale = "", affects = "", source = "", supersedes = "" }) {
    title = esc(title);
    if (!title) throw new LineError("decision_propose: a title is required");
    return this.#change(({ entry }) => `decision: propose ${entry.id}`, (text, entries) => {
      if (supersedes) {
        const old = entries.find((e) => e.id === supersedes);
        if (!old) throw new LineError(`decision propose: no decision ${supersedes} to supersede`);
        if (old.status !== "approved") throw new LineError(`decision propose: ${supersedes} is ${old.status}; only an approved decision can be superseded`);
      }
      const date = today().replaceAll("-", "");
      const seq = Math.max(0, ...entries.filter((e) => e.id.startsWith(`d-${date}-`)).map((e) => Number(e.id.slice(`d-${date}-`.length)) || 0)) + 1;
      const conflicts = this.#conflicts(entries, { title, affects });
      const entry = {
        id: `d-${date}-${String(seq).padStart(2, "0")}`,
        title,
        status: "proposed",
        rationale: esc(rationale),
        affects: esc(affects),
        source: esc(source),
        "proposed-by": this.author,
        added: today(),
        conflicts: conflicts.length ? conflicts.map((c) => c.id).join(", ") : "none",
        ...(supersedes ? { supersedes } : {}),
      };
      return { text: `${(text.trim() ? text : HEADER).trimEnd()}\n\n${render(entry)}\n`, out: { entry, conflicts } };
    });
  }

  /**
   * A teammate's ruling on a proposal: approve or reject. The human-facing surface (the
   * chamber CLI) calls this; the agent MCP tools deliberately have no ruling tool. That
   * separation keeps an agent's suggestion visibly distinct from a teammate's ruling for
   * everyone using the tools as intended — the decided-by field is editable text, not
   * proof of who ruled (see the trust note above).
   */
  async decide(id, outcome, { by, reason = "" } = {}) {
    if (!["approve", "reject"].includes(outcome)) throw new LineError("decide: outcome must be approve or reject");
    const status = outcome === "approve" ? "approved" : "rejected";
    return this.#change(`decision: ${outcome} ${id}`, (text, entries) => {
      const entry = entries.find((e) => e.id === id);
      if (!entry) throw new LineError(`decide: no decision ${id} in the ledger`);
      if (entry.status !== "proposed") throw new LineError(`decide: ${id} is already ${entry.status}`);
      const next = [`## ${entry.id} · ${entry.title}`, `- status: ${status}`, `- decided-by: ${esc(by)} (human)`, `- decided-on: ${today()}`];
      if (outcome === "reject" && esc(reason)) next.push(`- reason: ${esc(reason)}`);
      // Functions, not strings, as replacements: a title with "$&" or "$'" in it stays literal.
      let newText = text.replace(`## ${entry.id} · ${entry.title}\n- status: proposed`, () => next.join("\n"));
      if (outcome === "approve" && entry.supersedes) {
        // Retire the replaced decision in the same commit: no window where both rules stand.
        const old = entries.find((e) => e.id === entry.supersedes);
        if (!old || old.status !== "approved") throw new LineError(`decide: ${id} supersedes ${entry.supersedes}, which is not an approved decision (it changed since the proposal)`);
        newText = newText.replace(`## ${old.id} · ${old.title}\n- status: approved`, () => `## ${old.id} · ${old.title}\n- status: superseded\n- superseded-by: ${id}`);
      }
      return { text: newText, out: { ...entry, status } };
    });
  }

  /**
   * Another agent's review of a proposal: agree or object, with a reason. Reviews ride on
   * the agent's existing session — no extra model run. They advise the ruling teammate;
   * they are not votes and change nothing on their own. Same editable-text caveat as the
   * rest of the record.
   */
  async review(id, { stance, reason = "" } = {}) {
    if (!["agree", "object"].includes(stance)) throw new LineError("decision_review: stance must be agree or object");
    return this.#change(`decision: review ${id} (${stance})`, (text, entries) => {
      const entry = entries.find((e) => e.id === id);
      if (!entry) throw new LineError(`decision_review: no decision ${id} in the ledger`);
      if (entry.status !== "proposed") throw new LineError(`decision_review: ${id} is already ${entry.status}; reviews are for open proposals`);
      if ((entry.reviews ?? []).some((r) => r.startsWith(`${this.author} ·`))) throw new LineError(`decision_review: ${this.author} already reviewed ${id}`);
      const block = entry.raw.join("\n").trimEnd();
      const line = `- review: ${this.author} · ${stance}${esc(reason) ? ` — ${esc(reason)}` : ""} · ${today()}`;
      return { text: text.replace(block, () => `${block}\n${line}`), out: { id, stance, by: this.author } };
    });
  }

  /**
   * Approved decisions constraining these paths/topics — call before contradicting work.
   * `includeProposals`: also the open proposals overlapping, so work-in-flight decisions are
   * visible before they rule.
   */
  async check({ paths = [], topic = "", includeProposals = false } = {}) {
    const mine = tokens(`${topic} ${paths.join(" ")}`);
    const entries = await this.list();
    const approved = entries.filter((e) => e.status === "approved");
    const match = (e) => {
      if (!mine.size) return true;
      const theirs = tokens(`${e.title} ${e.affects ?? ""}`);
      return [...mine].some((w) => theirs.has(w));
    };
    const hits = approved.filter(match);
    if (!includeProposals) return hits;
    const proposals = entries.filter((e) => e.status === "proposed").filter((e) => !hits.some((h) => h.id === e.id) && match(e));
    return [...hits, ...proposals];
  }

  /** The short list new sessions start with: decisions on record, newest first, capped. */
  async bootstrap({ limit = 20, lineLength = 140 } = {}) {
    const entries = await this.list();
    const [approved, proposed] = [entries.filter((e) => e.status === "approved"), entries.filter((e) => e.status === "proposed")];
    if (!approved.length && !proposed.length) return "";
    const lines = approved.slice(-limit).reverse().map((e) => {
      // Never let truncation change a ruling's meaning: cut only the title, mark it, and keep
      // the id so the full record is one `decision` tool call (or `chamber decisions`) away.
      const title = e.title.length > 100 ? `${e.title.slice(0, 97)}…` : e.title;
      let line = `- [${e.id}] ${title}`;
      if (e.affects && line.length + ` (affects: ${e.affects})`.length <= lineLength) line += ` (affects: ${e.affects})`;
      return line;
    });
    if (proposed.length) lines.push(`- ${proposed.length} proposed decision${proposed.length === 1 ? "" : "s"} open for review (decision tool, action=review); a teammate rules on them (proposals only)`);
    return lines.join("\n");
  }
}
