// Dreaming: a background agent that maintains the team's memory (after Devin's "dreaming"
// and supermemory's memoryrepo dreamer). It reads what your agents talked about since the
// last dream, plus the memory, and:
//   1. adds what is durable and not yet remembered (decisions, contracts, owners, conventions,
//      people, patterns across talks)
//   2. cleans up: merges duplicates, removes outdated entries, fixes [[links]], keeps MEMORY.md
//      short, and resolves contradictions by reading the source talk
// Its edits land as one commit; if someone wrote the same file meanwhile, the dream is
// dropped and the next one retries. Runs on a member's machine (only members can read the
// memory) with any OpenRouter model.

import { appendFileSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { readAll, threads } from "./board.mjs";
import { HOME } from "./inbox.mjs";
import { CONVENTIONS, RECORDS, TeamMemory } from "./memory.mjs";
import { LineError } from "./protocol.mjs";

const MAX_STEPS = 40;
const SETTINGS = () => join(HOME, "settings.json");
const DREAMS = () => join(HOME, "dreams.jsonl");
export const DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash";

export function settings() {
  try {
    return JSON.parse(readFileSync(SETTINGS(), "utf8"));
  } catch {
    return {};
  }
}

export function saveSettings(changes) {
  const next = { ...settings(), ...changes };
  writeFileSync(SETTINGS(), JSON.stringify(next, null, 1), { mode: 0o600 });
  return next;
}

export const modelKey = () => process.env.OPENROUTER_API_KEY || settings().openrouterKey || "";

export function dreams(limit = 30) {
  try {
    return readFileSync(DREAMS(), "utf8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l)).slice(-limit).reverse();
  } catch {
    return [];
  }
}

function instructions(today) {
  return `You are the dreamer: a background agent that maintains a software team's shared memory. Today is ${today}.

The memory is a set of markdown files (MEMORY.md is the entry point). The team's coding agents talk to each other in "talks"; the ones since your last run are in the inbox (call read_inbox). Agents already save some things as they go, so much may be saved already.

Your two jobs:
1. Add new memory. Save anything durable from the inbox that isn't remembered yet: decisions and why (as discussed; only the decision ledger says what a teammate approved), API/interface contracts agreed between people, who owns what, conventions, people and their roles, what's in flight. Spot patterns across talks. Skip small talk, one-off questions and anything already obsolete.
2. Clean up memory. Merge duplicates, remove outdated entries, fix broken or missing [[links]], keep MEMORY.md short. When entries contradict each other, read the source talk with read_source and keep what was actually said (the most recent statement wins unless the source shows otherwise).

## Tools
- read_inbox: the new talks. Cite a message as talk/<thread>#<message>.
- list_files, read_file, search: the memory.
- write_file / delete_file: change the memory (your working copy). ${Object.keys(RECORDS).join(" and ")} are read-only for you: people and agents keep them with their own tools.
- read_source: the talk behind a [source: talk/…] ref.
- commit: finish. Call exactly once, at the end, saying what you added, merged, removed or corrected.

## Process
1. Orient: list_files, read MEMORY.md, read the inbox.
2. Search before writing. Edit the file a fact belongs in; never create near-duplicates.
3. Make focused edits, then commit. If nothing is worth changing, commit with a message saying so and change nothing.

${CONVENTIONS(today)}`;
}

const TOOLS = [
  ["read_inbox", "The talks since the last dream.", {}],
  ["list_files", "All memory files.", {}],
  ["read_file", "Read a memory file.", { path: { type: "string" } }],
  ["search", "Lines in memory containing all the given words.", { query: { type: "string" } }],
  ["write_file", "Create or replace a whole memory file.", { path: { type: "string" }, content: { type: "string" } }],
  ["delete_file", "Delete a memory file (after merging its content elsewhere).", { path: { type: "string" } }],
  ["read_source", "Read the talk behind a ref like talk/<thread>#<message>.", { ref: { type: "string" } }],
  ["commit", "Finish the dream: what you changed, in one line.", { message: { type: "string" } }],
].map(([name, description, properties]) => ({ type: "function", function: { name, description, parameters: { type: "object", properties, required: Object.keys(properties) } } }));

/** The talks with something new since `since` (ms), as markdown, one section per talk. */
export function inbox(owner, since) {
  const records = readAll(owner);
  const fresh = records.filter((r) => (r.ts || 0) > since);
  if (!fresh.length) return { text: "", count: 0 };
  const cards = threads(records, owner).filter((c) => c.messages.some((m) => (m.ts || 0) > since));
  const text = cards
    .map((c) => [`# talk/${c.id}: ${c.title}${c.channel ? ` (${c.channel})` : ""}`, ...c.messages.map((m) => `## ${m.from} → ${m.to} · ${m.id} · ${new Date(m.ts || 0).toISOString().slice(0, 16)}\n${m.text}`)].join("\n\n"))
    .join("\n\n---\n\n");
  return { text, count: fresh.length };
}

function source(owner, ref) {
  const [, thread, message] = /^talk\/([^#\s]+)(?:#(\S+))?/.exec(String(ref)) || [];
  const card = threads(readAll(owner), owner).find((c) => c.id === thread);
  if (!card) return `No talk ${ref} on this machine.`;
  return card.messages.map((m) => `${m.id === message ? ">> " : ""}${m.from} → ${m.to} (${m.id}): ${m.text}`).join("\n");
}

/**
 * Dream once. `config`: the chamber config; `log(kind, text)`: live progress.
 * Returns { ok, message, changes, steps } or { ok: false, reason }.
 */
export async function dream(config, { model = settings().dreamModel || DEFAULT_MODEL, log = () => {}, llmUrl = process.env.CHAMBER_LLM_URL || "https://openrouter.ai/api/v1/chat/completions" } = {}) {
  const key = modelKey();
  if (!key) throw new LineError("dreaming needs an OpenRouter key: set it on the board (Memory → Dream) or OPENROUTER_API_KEY");
  const owner = config.owner;
  const since = settings().lastDream || 0;
  const started = Date.now();
  const memory = new TeamMemory(config.invite, `${owner}/dreamer`);
  const files = await memory.all(); // { path: { version, text } }
  const work = Object.fromEntries(Object.entries(files).map(([p, f]) => [p, f.text]));
  const box = inbox(owner, since);
  let message = null;
  const record = (kind, text) => log(kind, text);

  const run = (name, args) => {
    if (name === "read_inbox") return box.text || "(nothing new since the last dream)";
    if (name === "list_files") return Object.keys(work).sort().join("\n") || "(memory is empty)";
    if (name === "read_file") return work[args.path] ?? `No such file: ${args.path}`;
    if (name === "search") {
      const words = String(args.query || "").toLowerCase().split(/\s+/).filter(Boolean);
      const hits = [];
      for (const [p, text] of Object.entries(work)) text.split("\n").forEach((l, i) => words.every((w) => `${p} ${l}`.toLowerCase().includes(w)) && hits.push(`${p}:${i + 1}: ${l.trim()}`));
      return hits.slice(0, 40).join("\n") || "No matches.";
    }
    if ((name === "write_file" || name === "delete_file") && RECORDS[String(args.path)]) return `Refused: ${args.path} is kept by the team's ${RECORDS[String(args.path)]} tool. Read it, but leave it as it is.`;
    if (name === "write_file") {
      if (!/^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}\.md$/.test(String(args.path)) || String(args.path).includes("..")) return `Refused: bad path ${args.path}`;
      work[args.path] = String(args.content).endsWith("\n") ? String(args.content) : `${args.content}\n`;
      record("write", args.path);
      return `Wrote ${args.path}.`;
    }
    if (name === "delete_file") {
      delete work[args.path];
      record("write", `deleted ${args.path}`);
      return `Deleted ${args.path}.`;
    }
    if (name === "read_source") return source(owner, args.ref);
    if (name === "commit") {
      message = String(args.message || "dream");
      return "Committed.";
    }
    return `Unknown tool ${name}.`;
  };

  const messages = [
    { role: "system", content: instructions(new Date().toISOString().slice(0, 10)) },
    { role: "user", content: `${box.count} new message(s) in the inbox. The memory has ${Object.keys(work).length} file(s). Begin.` },
  ];
  let steps = 0;
  let cost = 0;
  while (message === null && steps < MAX_STEPS) {
    steps += 1;
    const response = await fetch(llmUrl, {
      method: "POST",
      headers: { authorization: `Bearer ${key}`, "content-type": "application/json" },
      body: JSON.stringify({ model, messages, tools: TOOLS, usage: { include: true } }),
    });
    if (!response.ok) throw new LineError(`the model answered ${response.status}: ${(await response.text()).slice(0, 200)}`);
    const reply = await response.json();
    cost += Number(reply.usage?.cost || 0);
    const said = reply.choices?.[0]?.message;
    if (!said) throw new LineError("the model returned nothing");
    messages.push({ role: "assistant", content: said.content ?? "", ...(said.tool_calls ? { tool_calls: said.tool_calls } : {}) });
    if (!said.tool_calls?.length) {
      messages.push({ role: "user", content: "Use the tools, and finish with commit." });
      continue;
    }
    for (const call of said.tool_calls) {
      let args = {};
      try {
        args = JSON.parse(call.function.arguments || "{}");
      } catch {}
      const output = run(call.function.name, args);
      if (call.function.name !== "write_file") record("tool", `${call.function.name}${args.path ? ` ${args.path}` : args.query ? ` "${args.query}"` : ""}`);
      messages.push({ role: "tool", tool_call_id: call.id, content: String(output).slice(0, 60_000) });
    }
  }

  const changes = [];
  for (const [path, text] of Object.entries(work)) if (files[path]?.text !== text) changes.push({ path, text, base: files[path]?.version ?? 0 });
  for (const path of Object.keys(files)) if (!(path in work)) changes.push({ path, text: null, base: files[path].version });
  let outcome = { ok: true };
  if (message === null) outcome = { ok: false, reason: `stopped after ${MAX_STEPS} steps without a commit` };
  else if (changes.length) {
    const result = await memory.commit(changes, `dream: ${message}`);
    if (!result.ok) outcome = { ok: false, reason: "a teammate changed the same files meanwhile; the next dream retries" };
  }
  const entry = { ts: started, ms: Date.now() - started, ok: outcome.ok, message: message ?? "", reason: outcome.reason ?? "", inbox: box.count, steps, cost, model, changes: changes.map((c) => (c.text === null ? `- ${c.path}` : `${c.base ? "~" : "+"} ${c.path}`)) };
  appendFileSync(DREAMS(), `${JSON.stringify(entry)}\n`);
  if (outcome.ok) saveSettings({ lastDream: started });
  return entry;
}
