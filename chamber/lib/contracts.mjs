// The contract registry: which API/schema contracts the team has, who consumes each one,
// and who has caught up to which version.
//
// The gap this closes: a producer changes a contract, the message goes out, and nobody can
// tell "the notice was delivered" from "the consumer adapted". So the registry keeps the two
// apart: a consumer's ack of `seen` records receipt; `adapted` records that they updated,
// with evidence (tests, a commit); `object` records a blocker for the producer and the team.
// `contract status` shows, per consumer, where they stand against the current version.
//
// Publishing pushes a short notice to every subscribed consumer through the normal chamber
// message path, so an agent that is working sees it mid-task (the hooks) and an idle agent
// is woken. The notice is a pointer — the full record is one contract tool call away — so
// routine announcements stay cheap.
//
// The registry is one shared-memory file, contracts/registry.md, so it inherits memory's
// end-to-end encryption and optimistic concurrency. One entry per contract:
//
//   ## auth-token · Auth token response
//   - version: 3
//   - summary: expires_at_epoch_seconds added alongside the ISO field
//   - producer: nitin/claude-code.api
//   - updated: 2026-10-29
//   - decision: d-20261029-02                                   (optional, set by publish)
//   - consumer: rohan/codex.web · subscribed 2026-10-28          (repeatable)
//   - notice: v3 · 2026-10-29 · by nitin/claude-code.api · to rohan/codex.web, dev/codex.api
//   - ack: rohan/codex.web · v3 · seen · 2026-10-29              (repeatable)
//   - ack: rohan/codex.web · v3 · adapted · 2026-10-29 · contract tests pass both rollout orders
//
// What the registry does NOT do: it does not infer who consumes a contract (only agents that
// subscribe are tracked), does not run or verify the evidence in an `adapted` note (it only
// requires that a substantive note is present), does not verify who an agent is, and does not
// block a publish (open objections and zero subscribers are warned about, not gated).
//
// Trust note: same as the decision ledger — this is a record for a team acting in good
// faith, not proof. The producer/consumer/ack fields are plain text an agent bypassing the
// tools could forge, and nothing here verifies that claimed test evidence really ran. For
// agents using the tools as intended, it keeps "told" visibly separate from "done".

import { LineError } from "./protocol.mjs";

export const REGISTRY_PATH = "contracts/registry.md";
const HEADER = "# Contract registry\n<!-- chamber-contracts:v1 -->\n\nProducers publish versions; subscribed consumers are notified and acknowledge. A `seen` ack means the notice arrived, `adapted` means the consumer updated (with evidence). Delivered is not adapted.\n";

const NAME = /^[a-z0-9][a-z0-9-]{0,63}$/;
const today = () => new Date().toISOString().slice(0, 10);
export const MIN_EVIDENCE_CHARS = 20;
const esc = (s) => String(s ?? "").replace(/\s+/g, " ").trim();

function parse(text) {
  const entries = [];
  if (!text) return entries;
  let current = null;
  for (const line of text.split("\n")) {
    const head = line.match(/^## (\S+) · (.*)$/);
    if (head) {
      current = { name: head[1], title: head[2].trim(), consumers: [], acks: [], notices: [], raw: [line] };
      entries.push(current);
      continue;
    }
    if (!current) continue;
    current.raw.push(line);
    const field = line.match(/^- ([a-z-]+): (.*)$/);
    if (!field) continue;
    const [, key, value] = field;
    if (key === "consumer") {
      const m = value.match(/^(\S+) · subscribed (\S+)$/);
      if (m) current.consumers.push({ agent: m[1], since: m[2] });
    } else if (key === "ack") {
      const m = value.match(/^(\S+) · v(\d+) · (seen|adapted|object) · (\S+)(?: · (.*))?$/);
      if (m) current.acks.push({ agent: m[1], version: Number(m[2]), stance: m[3], date: m[4], note: m[5] || "" });
    } else if (key === "notice") {
      const m = value.match(/^v(\d+) · (\S+) · by (\S+) · to (.*)$/);
      if (m) current.notices.push({ version: Number(m[1]), date: m[2], by: m[3], to: m[4].split(", ").filter(Boolean) });
    } else {
      current[key] = key === "version" ? Number(value) : value.trim();
    }
  }
  return entries;
}

function render(entry) {
  const lines = [`## ${entry.name} · ${entry.title}`];
  lines.push(`- version: ${entry.version}`);
  if (entry.summary) lines.push(`- summary: ${entry.summary}`);
  lines.push(`- producer: ${entry.producer}`);
  lines.push(`- updated: ${entry.updated}`);
  if (entry.decision) lines.push(`- decision: ${entry.decision}`);
  for (const c of entry.consumers ?? []) lines.push(`- consumer: ${c.agent} · subscribed ${c.since}`);
  for (const n of entry.notices ?? []) lines.push(`- notice: v${n.version} · ${n.date} · by ${n.by} · to ${n.to.join(", ")}`);
  for (const a of entry.acks ?? []) lines.push(`- ack: ${a.agent} · v${a.version} · ${a.stance} · ${a.date}${a.note ? ` · ${a.note}` : ""}`);
  return lines.join("\n");
}

/** Where one consumer stands against the contract's current version: their latest ack for it, or "outstanding". */
function consumerState(entry, agent) {
  const acks = (entry.acks ?? []).filter((a) => a.agent === agent && a.version === Number(entry.version));
  const latest = acks[acks.length - 1];
  if (latest) return { state: latest.stance, ack: latest };
  const prior = (entry.acks ?? []).filter((a) => a.agent === agent);
  return { state: "outstanding", ack: null, lastVersion: prior.length ? Math.max(...prior.map((a) => a.version)) : 0 };
}

/** Consumers whose latest ack (any version, most recent last) is an objection nobody has since superseded. */
export function openObjections(entry) {
  const out = [];
  for (const c of entry.consumers ?? []) {
    const mine = (entry.acks ?? []).filter((a) => a.agent === c.agent);
    if (!mine.length) continue;
    const top = Math.max(...mine.map((a) => a.version));
    const atTop = mine.filter((a) => a.version === top);
    const last = atTop[atTop.length - 1];
    if (last.stance === "object") out.push({ agent: c.agent, version: last.version, note: last.note, date: last.date });
  }
  return out;
}

export class ContractRegistry {
  /** `memory`: a TeamMemory (or a stub with read/write/edit); `author`: e.g. "nitin/claude-code.api". */
  constructor(memory, author) {
    this.memory = memory;
    this.author = author;
  }

  /**
   * Read the registry, apply `change(entries)`, and commit against the version read. If
   * someone committed in between, the relay refuses and we start over from theirs, so a
   * concurrent ack, subscription or publish is never written over.
   */
  async #mutate(message, change) {
    for (let attempt = 0; attempt < 3; attempt++) {
      const file = await this.memory.read(REGISTRY_PATH);
      const entries = parse(file?.text);
      const result = change(entries); // throws LineError, or returns { out } after changing entries
      const text = `${HEADER}\n${entries.map(render).join("\n\n")}\n`;
      const written = await this.memory.commit([{ path: REGISTRY_PATH, text, base: file?.version ?? 0 }], message);
      if (written.ok) return result.out;
    }
    throw new LineError("contract: the registry kept changing while writing; try again");
  }

  #find(entries, name) {
    const entry = entries.find((e) => e.name === name);
    if (!entry) throw new LineError(`contract: no contract "${name}" registered`);
    return entry;
  }

  async list() {
    const file = await this.memory.read(REGISTRY_PATH);
    return parse(file?.text);
  }

  async get(name) {
    return this.#find(await this.list(), name);
  }

  /** Declare a contract and its producer. One per name; the producer is the one who publishes new versions. */
  async register({ name, title, summary = "" }) {
    name = esc(name);
    title = esc(title);
    if (!NAME.test(name)) throw new LineError("contract register: a name is lowercase letters, digits and '-', e.g. auth-token");
    if (!title) throw new LineError("contract register: a title is required");
    return this.#mutate(`contract: register ${name}`, (entries) => {
      if (entries.some((e) => e.name === name)) throw new LineError(`contract register: "${name}" is already registered (publish a new version instead)`);
      entries.push({ name, title, version: 1, summary: esc(summary), producer: this.author, updated: today(), consumers: [], acks: [], notices: [] });
      return { out: { name, version: 1 } };
    });
  }

  /** The calling agent consumes this contract: it gets a notice on every new version. Idempotent. */
  async subscribe(name) {
    return this.#mutate(`contract: subscribe ${name}`, (entries) => {
      const entry = this.#find(entries, name);
      if (entry.consumers.some((c) => c.agent === this.author)) return { out: { entry, added: false } };
      entry.consumers.push({ agent: this.author, since: today() });
      return { out: { entry, added: true } };
    });
  }

  /** Stop following a contract. Past acks stay on the record. */
  async unsubscribe(name) {
    return this.#mutate(`contract: unsubscribe ${name}`, (entries) => {
      const entry = this.#find(entries, name);
      const before = entry.consumers.length;
      entry.consumers = entry.consumers.filter((c) => c.agent !== this.author);
      return { out: { entry, removed: entry.consumers.length !== before } };
    });
  }

  /**
   * The producer announces a new version. Bumps the version, records the announcement (who
   * was told, which version, when), and returns the consumers to notify — the caller (the
   * MCP layer) sends each a short notice through the normal message path, which reaches
   * working agents mid-task and wakes idle ones.
   */
  async publish({ name, summary, decision = "" }) {
    name = esc(name);
    summary = esc(summary);
    if (!summary) throw new LineError("contract publish: say what changed (summary)");
    return this.#mutate(`contract: publish ${name}`, (entries) => {
      const entry = this.#find(entries, name);
      if (entry.producer !== this.author) throw new LineError(`contract publish: ${entry.producer} produces ${name}; only the producer publishes new versions (good-faith record, see the registry's trust note)`);
      entry.version = Number(entry.version) + 1;
      entry.summary = summary;
      entry.updated = today();
      if (esc(decision)) entry.decision = esc(decision);
      const notify = entry.consumers.map((c) => c.agent).filter((a) => a !== this.author);
      entry.notices.push({ version: entry.version, date: today(), by: this.author, to: notify });
      return { out: { entry, version: entry.version, notify, objections: openObjections(entry) } };
    });
  }

  /**
   * A consumer records where they stand on a version: `seen` (the notice arrived),
   * `adapted` (they updated — say how: tests, commit), or `object` (it breaks them; the
   * reason is required). `seen` never follows `adapted` for the same version: receipt
   * must not overwrite evidence of adaptation.
   */
  async ack(name, { version, stance, note = "" } = {}) {
    stance = esc(stance);
    note = esc(note);
    if (!["seen", "adapted", "object"].includes(stance)) throw new LineError("contract ack: stance must be seen, adapted or object");
    if (stance === "object" && !note) throw new LineError("contract ack: an objection needs a reason (what breaks)");
    if (stance === "adapted" && note.length < MIN_EVIDENCE_CHARS) throw new LineError(`contract ack: adapted needs an evidence note of at least ${MIN_EVIDENCE_CHARS} characters (what you changed and what you ran: tests, commit). It is recorded as your claim; the registry does not verify it. If you only received the notice, ack seen.`);
    return this.#mutate(`contract: ack ${name} (${stance})`, (entries) => {
      const entry = this.#find(entries, name);
      if (!entry.consumers.some((c) => c.agent === this.author)) throw new LineError(`contract ack: ${this.author} does not consume ${name} (subscribe first)`);
      version = Number(version ?? entry.version);
      if (!Number.isInteger(version) || version < 1 || version > Number(entry.version)) throw new LineError(`contract ack: ${name} is at v${entry.version}; cannot ack v${version}`);
      const mine = entry.acks.filter((a) => a.agent === this.author && a.version === version);
      if (stance === "seen" && mine.some((a) => a.stance === "adapted")) throw new LineError(`contract ack: ${this.author} already adapted ${name} v${version}; a seen ack would overwrite evidence with receipt`);
      if (mine.some((a) => a.stance === stance)) throw new LineError(`contract ack: ${this.author} already recorded ${stance} for ${name} v${version}`);
      const ack = { agent: this.author, version, stance, date: today(), note };
      entry.acks.push(ack);
      return { out: { entry, ack } };
    });
  }

  /** One contract, with each consumer's standing against the current version computed. */
  async status(name) {
    const entry = await this.get(name);
    return {
      entry,
      consumers: entry.consumers.map((c) => ({ agent: c.agent, since: c.since, ...consumerState(entry, c.agent) })),
    };
  }

  /**
   * Contracts this agent consumes where the current version is not yet resolved by them: no
   * ack at all, or only `seen` (receipt is not adaptation). `adapted` and `object` resolve it.
   */
  async pendingFor(agent) {
    return (await this.list()).filter((e) => {
      if (!e.consumers.some((c) => c.agent === agent)) return false;
      const { state } = consumerState(e, agent);
      return state === "outstanding" || state === "seen";
    });
  }

  /** Open objections on contracts this agent produces. */
  async objectionsFor(agent) {
    return (await this.list()).filter((e) => e.producer === agent).flatMap((e) => openObjections(e).map((o) => ({ name: e.name, ...o })));
  }

  /** The short session-start list: unresolved changes for this agent, plus open objections on what it produces. Capped. */
  async bootstrapFor(agent, { limit = 5 } = {}) {
    const pending = await this.pendingFor(agent);
    const lines = pending.slice(0, limit).map((e) => {
      const summary = e.summary && e.summary.length > 90 ? `${e.summary.slice(0, 87)}…` : e.summary;
      const seen = consumerState(e, agent).state === "seen" ? " [you acked seen: not adapted yet]" : "";
      return `- ${e.name} v${e.version}: ${summary} (${e.producer}, ${e.updated}${e.decision ? `, decision ${e.decision}` : ""})${seen}`;
    });
    if (pending.length > limit) lines.push(`- …and ${pending.length - limit} more (contract tool, action=list)`);
    const objections = (await this.objectionsFor(agent)).slice(0, limit);
    for (const o of objections) lines.push(`- OPEN OBJECTION on ${o.name} v${o.version} from ${o.agent}: ${o.note.length > 90 ? `${o.note.slice(0, 87)}…` : o.note}`);
    return lines.join("\n");
  }
}
