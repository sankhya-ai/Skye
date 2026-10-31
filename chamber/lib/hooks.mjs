// Claude Code hooks: how messages reach an agent that is already working, or idle.
//
//   hook   PostToolUse / UserPromptSubmit / SessionStart (sync, fast): new messages
//          are added to Claude's context next to the tool result or the prompt.
//   wait   Stop / SessionStart, run with `asyncRewake`: waits in the background for
//          the next message and exits 2 with it, which wakes an idle Claude.
//
// Both read the inbox the MCP server writes; claiming shows each message once.
// Only one `wait` runs per agent: a new one takes over and the previous exits.

import { existsSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { describe, Inbox, loadConfig } from "./inbox.mjs";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function stdinJson() {
  if (process.stdin.isTTY) return {};
  let text = "";
  for await (const chunk of process.stdin) text += chunk;
  try {
    return JSON.parse(text);
  } catch {
    return {};
  }
}

function inboxFor(name) {
  const config = loadConfig();
  return config ? new Inbox(`${config.owner}/${name}`) : null;
}

/** The team's MEMORY.md, for a new session (a short wait at most: never hold the session up). */
async function teamMemory(name) {
  const config = loadConfig();
  if (!config) return "";
  try {
    const { TeamMemory, ENTRY } = await import("./memory.mjs");
    const { DecisionLedger } = await import("./decisions.mjs");
    const { ContractRegistry } = await import("./contracts.mjs");
    const agent = `${config.owner}/${name}`;
    const memory = new TeamMemory(config.invite, agent);
    // Side by side, each given the same 3 seconds: one slow read never starves the others.
    // The timer is unref'd so a session whose reads came back at once isn't held for the rest.
    const deadline = new Promise((r) => setTimeout(() => r(null), 3000).unref());
    const within = (promise) => Promise.race([promise.catch(() => null), deadline]);
    const [file, bootstrap, pending] = await Promise.all([
      within(memory.read(ENTRY)),
      within(new DecisionLedger(memory, agent).bootstrap()),
      within(new ContractRegistry(memory, agent).bootstrapFor(agent)),
    ]);
    const parts = [];
    if (file?.text) parts.push(`The team's shared memory (MEMORY.md; read more with the chamber memory tools):\n\n${file.text}`);
    if (bootstrap) parts.push(`The team's decisions on record, approved by a teammate — follow them unless a teammate says otherwise (full record, and proposing new ones, via the chamber decision tool):\n\n${bootstrap}`);
    if (pending) parts.push(`Contract items still open for you (changes you consume that you have not adapted to or objected to; a seen ack is receipt and keeps the item open; and open objections on contracts you produce). Adapt, then contract action=ack with evidence:\n\n${pending}`);
    return parts.join("\n\n");
  } catch {
    return "";
  }
}

/**
 * New messages (and, at session start, the team's MEMORY.md) for the agent, in its harness's format.
 *   claude, codex (and DeepSeek Harness, through its Claude hooks bridge):
 *     hookSpecificOutput.additionalContext; at Stop with a message waiting: decision "block" (keep going)
 *   antigravity: injectSteps (SessionStart, PreInvocation); at Stop: decision "continue"
 */
export async function hook(name, { format = "claude", event: forced = "" } = {}) {
  const input = await stdinJson();
  const event = forced || input.hook_event_name || "PostToolUse";
  const inbox = inboxFor(name);
  if (event === "Stop") {
    // Only when something is waiting, so a stop is never held up for nothing (and never loops).
    const records = inbox ? inbox.claim() : [];
    if (records.length) process.stdout.write(JSON.stringify({ decision: format === "antigravity" ? "continue" : "block", reason: describe(records) }));
    return 0;
  }
  const records = inbox ? inbox.claim() : [];
  const parts = [];
  if (event === "SessionStart") {
    const memory = await teamMemory(name);
    if (memory) parts.push(memory);
  }
  if (records.length) parts.push(describe(records));
  if (!parts.length) return 0;
  const text = parts.join("\n\n---\n\n");
  if (format === "antigravity") process.stdout.write(JSON.stringify({ injectSteps: [{ systemMessage: { systemMessage: text, metadata: { source: "chamber" } } }] }));
  else process.stdout.write(JSON.stringify({ hookSpecificOutput: { hookEventName: event, additionalContext: text } }));
  return 0;
}

export async function wait(name, { pollMs = 1000, maxMs = 23 * 3600 * 1000 } = {}) {
  const inbox = inboxFor(name);
  if (!inbox) return 0;
  const pidPath = join(inbox.dir, "waiter.pid");
  writeFileSync(pidPath, String(process.pid)); // the previous waiter sees this and steps down
  const deadline = Date.now() + maxMs;
  let size = -1;
  try {
    while (Date.now() < deadline) {
      if (Number(readFileSync(pidPath, "utf8")) !== process.pid) return 0; // retired by a newer waiter
      const now = inbox.size();
      if (now !== size) {
        size = now;
        const records = inbox.claim();
        if (records.length) {
          process.stderr.write(describe(records));
          return 2; // asyncRewake: wake Claude with this
        }
      }
      await sleep(pollMs);
    }
    return 0;
  } finally {
    try {
      if (existsSync(pidPath) && Number(readFileSync(pidPath, "utf8")) === process.pid) unlinkSync(pidPath);
    } catch {}
  }
}
