// The board: every conversation your agents are in, as cards — and your seat in them.
//
//   Waiting   the last word is someone else's, to you or one of your agents
//   Talking   anything else still open (you or your agent spoke last)
//   Done      you closed it (~/.chamber/board.json)
//
// A conversation is a thread: a message starts one (its own id) and every reply
// carries it on; messages from older clients follow `reply_to` back to one.
// Cards come from every agent's log in ~/.chamber/agents (inbox.jsonl, sent.jsonl).
// You take part as `<you>/board`: what you type in a card is sent from there.

import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, join } from "node:path";

import { LineClient } from "./client.mjs";
import { HOME, Inbox, isAdmin } from "./inbox.mjs";
import { LineError } from "./protocol.mjs";

export const SEAT = "board";

function threadOf(record, known) {
  return String(record.thread || known.get(String(record.reply_to || "")) || record.reply_to || record.id);
}

/** Group messages into conversations and say where each stands, newest activity first. */
export function threads(records, owner, seen = new Map(), doneMap = new Map()) {
  const mine = (agent) => String(agent || "").startsWith(`${owner}/`) || String(agent || "") === `@${owner}`;
  const known = new Map();
  const cards = new Map();
  for (const record of records) {
    const id = threadOf(record, known);
    known.set(record.id, id);
    const card = cards.get(id) ?? { id, title: "", messages: [], people: [] };
    cards.set(id, card);
    card.messages.push(record);
    card.title ||= record.title || String(record.text || "").trim().split("\n")[0].slice(0, 80) || "(untitled)";
    for (const agent of [record.from, record.to]) if (agent && agent !== "*" && !card.people.includes(agent)) card.people.push(agent);
  }
  return [...cards.values()]
    .map((card) => {
      const last = card.messages.at(-1);
      const toMe = mine(last.to) || (String(last.to || "").startsWith("#") && (last.mentions || []).includes(owner));
      const doneAt = doneMap.get(card.id) || 0;
      const isDone = doneAt > 0 && (last.ts || 0) <= doneAt;
      const status = isDone ? "done" : (!mine(last.from) && toMe ? "waiting" : "talking");
      // Unread, like Gmail: someone else said something since you last opened it.
      const lastFromOthers = [...card.messages].reverse().find((m) => !mine(m.from));
      const unread = !isDone && Boolean(lastFromOthers) && (lastFromOthers.ts || 0) > (seen.get(card.id) || 0);
      const channel = card.people.find((p) => p.startsWith("#")) || null;
      const branch = [...card.messages].reverse().find((m) => m.branch)?.branch || null;
      return {
        id: card.id,
        title: card.title,
        status,
        unread,
        people: card.people,
        with: card.people.filter((p) => !mine(p) && !p.startsWith("#")),
        channel,
        branch,
        count: card.messages.length,
        last: { from: last.from, text: String(last.text || "").slice(0, 160), ts: last.ts },
        updated: last.ts || 0,
        messages: card.messages,
      };
    })
    .sort((a, b) => b.updated - a.updated);
}

/** Every message any of `owner`'s agents sent or received, oldest first, one copy each. */
export function readAll(owner) {
  const root = join(HOME, "agents");
  const seen = new Map();
  for (const dir of existsSync(root) ? readdirSync(root).filter((d) => d.startsWith(`${owner}__`)).sort() : []) {
    for (const name of ["sent.jsonl", "inbox.jsonl"]) {
      let lines = [];
      try {
        lines = readFileSync(join(root, dir, name), "utf8").split("\n");
      } catch {
        continue;
      }
      for (const line of lines) {
        try {
          const record = JSON.parse(line);
          if (record?.id && !seen.has(record.id)) seen.set(record.id, record); // between two of your agents: in both logs
        } catch {}
      }
    }
  }
  return [...seen.values()].sort((a, b) => (a.ts || 0) - (b.ts || 0));
}

/** Workspaces grouped by folder: one project, its agents (Claude Code, Codex, …); agents with no folder stand alone. */
export function projectsOf(workspaces) {
  const byPath = new Map();
  for (const w of workspaces) {
    const key = w.path || `agent:${w.agent}`;
    if (!byPath.has(key)) byPath.set(key, { path: w.path, name: w.path ? basename(w.path) : w.agent.split("/")[1], agents: [] });
    byPath.get(key).agents.push({ agent: w.agent, kind: w.kind.split(".")[0], online: w.online });
  }
  return [...byPath.values()].sort((a, b) => Number(!a.path) - Number(!b.path) || a.name.localeCompare(b.name));
}

export class Board {
  constructor(config) {
    this.config = config;
    this.owner = config.owner;
    this.agent = `${config.owner}/${SEAT}`;
    this.inbox = new Inbox(this.agent);
    this.statePath = join(HOME, "board.json");
    this.seat = new LineClient(config.invite, this.agent, {
      kind: "human",
      label: "you, on the board",
      listens: true,
      cursorPath: this.inbox.cursorPath,
      onMessage: (message) => this.inbox.add(message),
      onKnock: (knock) => this.knocked(knock),
    });
    this.wakes = []; // recent knocks' outcomes, newest last, for the board to show
  }

  /** A message waits for one of your agents that isn't connected: wake it, if you allow the sender to. */
  async knocked({ id, from, to }) {
    if (!to.startsWith(`${this.owner}/`) || to === this.agent) return;
    const { wake } = await import("./wake.mjs");
    const outcome = wake(to, from, { message: id });
    this.wakes.push({ at: Date.now(), agent: to, from, message: id, ...outcome });
    this.wakes = this.wakes.slice(-50);
  }

  start() {
    this.seat.run();
    this.removed = new Set();
    const refresh = () => this.members().then((m) => (this.removed = new Set((m.removed || []).map((r) => r.owner))), () => {});
    void refresh();
    this.removedTimer = setInterval(refresh, 30_000);
    this.removedTimer.unref?.();
    this.cachedQuota = null;
    const refreshQuota = () => this.quota().then((q) => { if (q) this.cachedQuota = q; }, () => {});
    void refreshQuota();
    this.quotaTimer = setInterval(refreshQuota, 60_000);
    this.quotaTimer.unref?.();
  }

  /** Names the admin removed: off the board (their past talks stay in Done/All). */
  async refreshRemoved() {
    try {
      this.removed = new Set(((await this.members()).removed || []).map((r) => r.owner));
    } catch {}
  }

  async quota() {
    try {
      await this.config.invite.ready();
      const response = await fetch(`${this.config.invite.relay}/v1/lines/${this.config.invite.lineId}/quota`, { headers: this.config.invite.auth });
      if (!response.ok) return null;
      return await response.json();
    } catch {
      return null;
    }
  }

  close() {
    clearInterval(this.removedTimer);
    clearInterval(this.quotaTimer);
    this.seat.close();
  }

  readState() {
    try {
      return JSON.parse(readFileSync(this.statePath, "utf8"));
    } catch {
      return {};
    }
  }

  writeState(state) {
    writeFileSync(this.statePath, JSON.stringify(state, null, 2));
  }

  /** When you last opened each talk: { talk: ts }. */
  seen() {
    return new Map(Object.entries(this.readState().seen || {}));
  }

  markSeen(id) {
    const state = this.readState();
    const seen = new Map(Object.entries(state.seen || {}));
    seen.set(id, Date.now());
    state.seen = Object.fromEntries(seen);
    this.writeState(state);
  }

  /** When talks were marked done: { talk: ts }. */
  done() {
    return new Map(Object.entries(this.readState().done || {}));
  }

  markDone(id, isDone = true) {
    const state = this.readState();
    const done = new Map(Object.entries(state.done || {}));
    if (isDone) {
      done.set(id, Date.now());
    } else {
      done.delete(id);
    }
    state.done = Object.fromEntries(done);
    this.writeState(state);
  }

  workspaces() {
    const online = new Set(this.seat.roster.map((a) => a.agent));
    const root = join(HOME, "agents");
    const out = [];
    const add = (agent, path) => {
      if (agent === this.agent || out.some((w) => w.agent === agent)) return;
      const name = agent.split("/")[1];
      const kind = name.includes(".") ? name.split(".")[0] : name;
      out.push({ agent, name: path ? basename(path) : name.replace(/^claude-code\./, ""), kind, path, online: online.has(agent) });
    };
    for (const dir of existsSync(root) ? readdirSync(root).filter((d) => d.startsWith(`${this.owner}__`)).sort() : []) {
      const agent = `${this.owner}/${dir.slice(this.owner.length + 2)}`;
      let path = "";
      try {
        path = String(JSON.parse(readFileSync(join(root, dir, "project.json"), "utf8")).path || "");
      } catch {}
      if (path || online.has(agent)) add(agent, path); // a project set up with `chamber setup`, or an agent of yours that is here now
    }
    for (const agent of [...online].sort()) if (agent.startsWith(`${this.owner}/`)) add(agent, "");
    return out;
  }

  overview() {
    return {
      configured: true,
      me: this.owner,
      admin: isAdmin(this.config),
      removed: [...(this.removed ?? [])],
      connected: this.seat.connected,
      problem: this.seat.problem,
      agents: this.seat.roster,
      channels: this.seat.channels.filter((c) => c.members.includes(this.owner)).map((c) => ({ ...c, joined: true }))
        .concat(this.seat.channels.filter((c) => !c.members.includes(this.owner)).map((c) => ({ ...c, joined: false }))),
      workspaces: this.workspaces(),
      projects: projectsOf(this.workspaces()),
      quota: this.cachedQuota,
      cards: threads(readAll(this.owner), this.owner, this.seen(), this.done()).map(({ messages, ...card }) => card),
    };
  }

  reactionsPath() {
    return join(HOME, "reactions.json");
  }

  getReactions() {
    try {
      if (!existsSync(this.reactionsPath())) return {};
      return JSON.parse(readFileSync(this.reactionsPath(), "utf8"));
    } catch {
      return {};
    }
  }

  toggleReaction(messageId, emoji, user = this.owner) {
    const all = this.getReactions();
    all[messageId] ||= {};
    all[messageId][emoji] ||= [];
    const idx = all[messageId][emoji].indexOf(user);
    if (idx >= 0) {
      all[messageId][emoji].splice(idx, 1);
      if (all[messageId][emoji].length === 0) delete all[messageId][emoji];
      if (Object.keys(all[messageId]).length === 0) delete all[messageId];
    } else {
      all[messageId][emoji].push(user);
    }
    try {
      mkdirSync(HOME, { recursive: true });
      writeFileSync(this.reactionsPath(), JSON.stringify(all, null, 2));
    } catch {}
    return all[messageId] || {};
  }

  conversation(id) {
    const card = threads(readAll(this.owner), this.owner, this.seen(), this.done()).find((c) => c.id === id);
    if (!card) return null;
    const reactions = this.getReactions();
    const messages = card.messages.map(({ id, from, to, text, ts, files, dir, mentions, patch, branch, type }) => ({
      id,
      from,
      to,
      text,
      ts,
      patch,
      branch,
      type,
      files: files || [],
      dir,
      mentions: mentions || [],
      reactions: reactions[id] || {},
    }));
    return { ...card, messages };
  }

  /** Send from your seat: a new conversation, or into one (`thread`). */
  async say(to, text, { thread = null, title = "", files = [] } = {}) {
    let replyTo = null;
    if (thread) {
      const convo = this.conversation(thread);
      if (!convo) throw new LineError("no such conversation");
      title = convo.title;
      replyTo = convo.messages.at(-1).id;
    }
    title ||= text.split("\n")[0].slice(0, 80);
    const parsedFiles = (Array.isArray(files) ? files : []).map((f) => ({
      name: String(f.name || "file"),
      data: typeof f.data === "string" ? Buffer.from(f.data, "base64") : (f.data instanceof Uint8Array ? f.data : Buffer.from(f.data || [])),
    }));
    const receipt = await this.seat.send(to, text, { replyTo, thread, title, files: parsedFiles });
    // Also save sent files locally so they can be viewed/downloaded from the board
    for (const f of parsedFiles) {
      try {
        const dir = join(this.inbox.dir, "files", receipt.id);
        mkdirSync(dir, { recursive: true });
        writeFileSync(join(dir, f.name), f.data);
      } catch {}
    }
    this.inbox.sent(receipt, { to, text, replyTo, title, files: parsedFiles });
    this.markSeen(receipt.thread);
    // Your own agent with no session open would never see this: start it.
    const mine = String(to).startsWith(`${this.owner}/`) && to !== this.agent;
    if (mine && receipt.held) {
      const { wake } = await import("./wake.mjs");
      return { ...receipt, wake: wake(to, this.agent, { message: receipt.id }) };
    }
    return receipt;
  }

  channel(op, name, owner = null) {
    return this.seat.channel(op, name, owner);
  }

  async invite() {
    const { inviteCommand } = await import("./admin.mjs");
    return inviteCommand(this.config);
  }

  async inviteCode() {
    const { inviteCode } = await import("./admin.mjs");
    return inviteCode(this.config);
  }

  async members() {
    const { listMembers } = await import("./admin.mjs");
    return listMembers(this.config);
  }

  async revoke(owner, op = "revoke") {
    const { revoke } = await import("./admin.mjs");
    const result = await revoke(this.config, owner, op);
    await this.refreshRemoved();
    return result;
  }
}
