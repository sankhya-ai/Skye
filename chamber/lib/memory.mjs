// Team memory: the team's long-term memory as linked markdown files (an "LLM wiki"), after
// Cognition's agent memory repo and supermemory's memoryrepo, kept in the chamber's relay.
//
//   MEMORY.md         the entry point: short, loaded into every agent's session, links out
//   people/<name>.md  projects/<name>.md  decisions/<topic>.md  contracts/<api>.md  owners.md …
//   one fact per line: `- Billing moved to Stripe [source: talk/ab12#m9; by: nitin; added: 2026-10-12]`
//
// Files are sealed with the chamber's key (the relay stores ciphertext). Every write is a
// commit naming the version it was based on; if someone changed the file first (a teammate's
// agent, or a dream), the write is rejected with the current version to merge, as in Devin.

import { LineError } from "./protocol.mjs";

export const ENTRY = "MEMORY.md";
const enc = new TextEncoder();

export const CONVENTIONS = (today) => `## Team memory conventions
- MEMORY.md is the entry point, loaded into every agent's session. Keep it short: what everyone needs (the team, what's in flight, key owners and decisions), plus [[links]] to everything else.
- Everything else lives in files by topic: people/<name>.md, projects/<name>.md, decisions/<topic>.md, contracts/<api-or-interface>.md, conventions/<topic>.md, owners.md (who owns what). Evolve the layout as needed.
- One entry per line, as a bullet, with metadata at the end: \`- Billing moved to Stripe [source: talk/ab12cd34#m9; by: nitin; added: ${today}]\`. Always include source, by and added.
- Link files with [[path]] from the root, without .md (e.g. [[contracts/auth-token]]). Keep each fact in one place; link to it elsewhere.
- Memory states the current truth: update or remove entries when things change; if history matters, say so ("moved to Stripe in Oct, was Razorpay").
- Team decisions (contracts, architecture choices, conventions the team must follow) belong in the decision ledger: agents propose them with the decision tools, and a teammate's approval puts them on the record. Don't state unapproved proposals as team policy here.\n- Never store secrets, keys, tokens or credentials.`;

/**
 * Files kept by their own tools, path → tool. Their structure is what makes them work (a
 * ruling is a status line, an ack is one line per consumer), so agents and the dreamer change
 * them only through those tools, never by rewriting the file.
 */
export const RECORDS = { "decisions/ledger.md": "decision", "contracts/registry.md": "contract" };

export class TeamMemory {
  /** `invite`: the chamber (with this member's pass); `author`: e.g. "ashish/claude-code.api". */
  constructor(invite, author) {
    this.invite = invite;
    this.author = author;
    this.cache = new Map(); // path -> { version, text }
  }

  async #request(path, init = {}) {
    await this.invite.ready();
    const headers = { ...this.invite.auth, ...(this.invite.pass ? { "x-chamber-pass": this.invite.pass } : {}), ...(init.body ? { "content-type": "application/json" } : {}) };
    const response = await fetch(`${this.invite.relay}/v1/lines/${this.invite.lineId}/${path}`, { ...init, headers });
    const body = await response.json().catch(() => ({}));
    if (response.status === 409) return { conflict: body.current };
    if (!response.ok) throw new LineError(body.error || `memory: the relay answered ${response.status}`);
    return body;
  }

  async #seal(path, text) {
    const nonce = crypto.getRandomValues(new Uint8Array(12));
    const sealed = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce, additionalData: enc.encode(`${this.invite.lineId}|memory|${path}`) }, this.invite.key, enc.encode(text)));
    return Buffer.concat([nonce, sealed]).toString("base64url");
  }

  async #open(path, body) {
    const raw = new Uint8Array(Buffer.from(body, "base64url"));
    try {
      return new TextDecoder().decode(await crypto.subtle.decrypt({ name: "AES-GCM", iv: raw.slice(0, 12), additionalData: enc.encode(`${this.invite.lineId}|memory|${path}`) }, this.invite.key, raw.slice(12)));
    } catch {
      throw new LineError(`memory: ${path} does not open with this chamber's key`);
    }
  }

  /** [{ path, version, author, ts }] */
  async list() {
    return (await this.#request("memory")).files;
  }

  /** { path, version, text } or null. `version`: an older one, from history. */
  async read(path, version = 0) {
    const cached = this.cache.get(path);
    try {
      const row = await this.#request(`memory/file?path=${encodeURIComponent(path)}${version ? `&version=${version}` : ""}`);
      if (row.body === null) return { path, version: row.version, text: null };
      if (!version && cached?.version === row.version) return { path, ...cached };
      const text = await this.#open(path, row.body);
      if (!version) this.cache.set(path, { version: row.version, text });
      return { path, version: row.version, text };
    } catch (error) {
      if (/no such file/.test(error.message)) return null;
      throw error;
    }
  }

  /** Every file's text, for search and for dreaming. { path: { version, text } } */
  async all() {
    const out = {};
    for (const f of await this.list()) {
      const file = await this.read(f.path);
      if (file?.text != null) out[f.path] = { version: file.version, text: file.text };
    }
    return out;
  }

  /** Lines matching all words of `query`, with their file: [{ path, line, text }]. */
  async search(query, { limit = 40 } = {}) {
    const words = String(query).toLowerCase().split(/\s+/).filter(Boolean);
    const hits = [];
    for (const [path, { text }] of Object.entries(await this.all())) {
      text.split("\n").forEach((line, i) => {
        const low = `${path} ${line}`.toLowerCase();
        if (words.length && words.every((w) => low.includes(w))) hits.push({ path, line: i + 1, text: line.trim() });
      });
    }
    return hits.slice(0, limit);
  }

  /**
   * One commit: changes [{ path, text | null, base }] (base: the version read; 0 = new).
   * Returns { ok, seq } or { ok: false, current: [{ path, version, text }] } when someone moved first.
   */
  async commit(changes, message) {
    const sealed = [];
    for (const c of changes) {
      if (!/^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}\.md$/.test(c.path) || c.path.includes("..")) throw new LineError(`memory: bad path ${c.path} (use e.g. people/nitin.md)`);
      sealed.push({ path: c.path, base: c.base ?? 0, body: c.text === null ? null : await this.#seal(c.path, c.text.endsWith("\n") ? c.text : `${c.text}\n`) });
    }
    const result = await this.#request("memory/commit", { method: "POST", body: JSON.stringify({ author: this.author, message, changes: sealed }) });
    if (result.conflict) {
      const current = [];
      for (const row of result.conflict) current.push({ path: row.path, version: row.version, text: row.body ? await this.#open(row.path, row.body) : null });
      return { ok: false, current };
    }
    for (const c of changes) this.cache.delete(c.path);
    return { ok: true, seq: result.seq, changes: result.changes };
  }

  /** Write a whole file (based on the version last read, or new). */
  async write(path, text, message) {
    const current = await this.read(path);
    return this.commit([{ path, text, base: current?.version ?? 0 }], message);
  }

  /** Replace one exact snippet (must match once). */
  async edit(path, oldText, newText, message) {
    const current = await this.read(path);
    if (!current?.text) return { ok: false, error: `no such file: ${path} (write it instead)` };
    const count = current.text.split(oldText).length - 1;
    if (count !== 1) return { ok: false, error: `the snippet matches ${count} times in ${path}; include more context so it matches exactly once` };
    return this.commit([{ path, text: current.text.replace(oldText, newText), base: current.version }], message);
  }

  async history(limit = 50) {
    return (await this.#request(`memory/history?limit=${limit}`)).commits;
  }
}
