// A team's day in one chamber, against the real relay: three people, each with a board and
// two coding agents (nine connections), several channels, @mentions, DMs to people and to
// agents, threads, a burst of messages, people going offline and coming back, and three
// agents writing the same memory file at once. Nothing may be lost, duplicated or misrouted.

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { after, before, test } from "node:test";

import { threads } from "../lib/board.mjs";
import { LineClient } from "../lib/client.mjs";
import { TeamMemory } from "../lib/memory.mjs";
import { Invite } from "../lib/protocol.mjs";

const ROOT = resolve(import.meta.dirname, "..");
const WRANGLER = join(ROOT, "relay", "node_modules", ".bin", "wrangler");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const tmp = (name) => mkdtempSync(join(tmpdir(), `chamber-team-${name}-`));
let relay;
let relayProcess;
const clients = new Set();

before(async () => {
  const port = await new Promise((done) => {
    const s = createServer().listen(0, "127.0.0.1", () => {
      const { port: p } = s.address();
      s.close(() => done(p));
    });
  });
  relayProcess = spawn(WRANGLER, ["dev", "--local", "--ip", "127.0.0.1", "--port", String(port), "--persist-to", tmp("relay")], { cwd: join(ROOT, "relay"), stdio: "ignore", detached: true });
  relayProcess.unref();
  relay = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 120; i++) {
    try {
      if ((await fetch(relay)).ok) return;
    } catch {}
    await sleep(500);
  }
  throw new Error("relay did not start");
});

after(() => {
  for (const c of clients) {
    try { c.close(); } catch {}
  }
  try { process.kill(-relayProcess.pid, "SIGKILL"); } catch {}
});

/** A connected agent that records everything it receives. */
async function agent(invite, name, options) {
  const got = [];
  const client = new LineClient(invite, name, { ...options, onMessage: (m) => got.push(m) });
  client.got = got;
  clients.add(client);
  client.run();
  await client.ready();
  return client;
}

const texts = (client) => client.got.map((m) => m.text);
const settle = () => sleep(600);

test("a team's day: channels, mentions, DMs, threads, a burst, offline people, shared memory", async () => {
  const invite = await Invite.create(relay).ready();
  const cursors = tmp("cursors");
  const team = {};
  // Connect in a fixed order: each person's Claude Code first (it becomes their chosen agent).
  for (const owner of ["ashish", "nitin", "rohan"]) {
    team[owner] = {
      code: await agent(invite, `${owner}/claude-code.api`, { kind: "claude-code", answers: true, priority: 5, cursorPath: join(cursors, `${owner}-code.json`) }),
      codex: await agent(invite, `${owner}/codex.web`, { kind: "codex", answers: true, priority: 5, cursorPath: join(cursors, `${owner}-codex.json`) }),
      board: await agent(invite, `${owner}/board`, { kind: "human", listens: true, cursorPath: join(cursors, `${owner}-board.json`) }),
    };
  }
  await settle();
  const { ashish, nitin, rohan } = team;
  assert.equal(ashish.board.roster.length, 9, "everyone sees all nine connections");

  // Channels: #backend (all three), #release (ashish + rohan only).
  await nitin.board.channel("join", "#backend");
  await nitin.board.channel("add", "#backend", "ashish");
  await nitin.board.channel("add", "#backend", "rohan");
  await rohan.board.channel("join", "#release");
  await rohan.board.channel("add", "#release", "ashish");
  await settle();

  // 1. Plain channel chatter reaches every member's board, and no coding agent.
  await nitin.board.send("#backend", "Schema freeze is Friday.");
  await settle();
  assert.ok(texts(ashish.board).includes("Schema freeze is Friday."));
  assert.ok(texts(rohan.board).includes("Schema freeze is Friday."));
  for (const p of [ashish, nitin, rohan]) for (const a of [p.code, p.codex]) assert.ok(!texts(a).includes("Schema freeze is Friday."), `${a.agent} must not be woken by chatter`);

  // 2. An @mention in a channel reaches the mentioned person's chosen agent (their first connected), and only that one.
  await nitin.code.send("#backend", "@ashish what's the auth token's expiry type?");
  await settle();
  assert.ok(texts(ashish.code).includes("@ashish what's the auth token's expiry type?"), "ashish's chosen agent gets the mention");
  assert.ok(!texts(ashish.codex).includes("@ashish what's the auth token's expiry type?"), "not both of his agents");
  assert.ok(!texts(rohan.code).some((t) => t.includes("expiry type")), "and not people who weren't mentioned");
  assert.ok(texts(rohan.board).some((t) => t.includes("expiry type")), "boards in the channel still see it");

  // 3. A private channel stays private.
  await rohan.code.send("#release", "@ashish release notes draft is ready");
  await settle();
  assert.ok(texts(ashish.code).includes("@ashish release notes draft is ready"));
  assert.ok(!texts(nitin.board).some((t) => t.includes("release notes")), "nitin is not in #release");

  // 4. A DM to a person, answered by their agent in the same thread, back to the exact asking agent.
  const asked = await ashish.codex.send("@rohan", "Can you take the iOS crash?", { title: "iOS crash" });
  await settle();
  const atRohan = rohan.code.got.find((m) => m.id === asked.id);
  assert.ok(atRohan, "rohan's chosen agent gets the DM");
  assert.ok(texts(rohan.board).includes("Can you take the iOS crash?"), "and his board shows a copy");
  assert.ok(!texts(rohan.codex).includes("Can you take the iOS crash?"));
  await rohan.code.send(atRohan.from, "On it, PR by tonight.", { replyTo: atRohan.id, thread: atRohan.thread, title: atRohan.title });
  await settle();
  const reply = ashish.codex.got.find((m) => m.replyTo === asked.id);
  assert.equal(reply?.text, "On it, PR by tonight.");
  assert.equal(reply.thread, asked.thread, "the reply stays in the conversation");
  assert.ok(!texts(ashish.code).includes("On it, PR by tonight."), "the reply goes to the asking agent, not his other one");

  // 4b. A message to someone's board (a reply in a talk their board started) is for that
  // person: their chosen agent answers it, and the board keeps its copy.
  const toBoard = await nitin.codex.send("ashish/board", "Review the onboarding brief?", { title: "Onboarding" });
  await settle();
  assert.ok(ashish.code.got.some((m) => m.id === toBoard.id), "ashish's chosen agent gets a message sent to his board");
  assert.ok(texts(ashish.board).includes("Review the onboarding brief?"), "and his board still shows it");

  // 5. Addressing one specific agent.
  await rohan.board.send("nitin/codex.web", "Your Codex: please rebase the web branch.");
  await settle();
  assert.ok(texts(nitin.codex).includes("Your Codex: please rebase the web branch."));
  assert.ok(!texts(nitin.code).includes("Your Codex: please rebase the web branch."));

  // 6. A burst: 3 senders x 20 messages at once into #backend. Every board gets all 60, once, in one shared order.
  const before = { ashish: ashish.board.got.length, rohan: rohan.board.got.length };
  await Promise.all([
    ...Array.from({ length: 20 }, (_, i) => nitin.board.send("#backend", `burst nitin ${i}`)),
    ...Array.from({ length: 20 }, (_, i) => ashish.code.send("#backend", `burst ashish ${i}`)),
    ...Array.from({ length: 20 }, (_, i) => rohan.codex.send("#backend", `burst rohan ${i}`)),
  ]);
  await sleep(1500);
  const burstAt = (p, from) => p.board.got.slice(from).filter((m) => m.text.startsWith("burst "));
  const seenByRohan = burstAt(rohan, before.rohan);
  const seenByAshish = burstAt(ashish, before.ashish);
  assert.equal(seenByRohan.length, 60, "rohan's board got all 60");
  assert.equal(new Set(seenByRohan.map((m) => m.id)).size, 60, "no duplicates");
  assert.equal(seenByAshish.length, 60, "ashish's board got all 60 too (it hears its own agents' channel messages)");
  assert.deepEqual(seenByAshish.map((m) => m.id), seenByRohan.map((m) => m.id), "everyone sees the same order");
  const order = (list) => list.map((m) => m.seq);
  assert.deepEqual(order(seenByRohan), [...order(seenByRohan)].sort((a, b) => a - b), "in the relay's order");

  // 7. Rohan goes offline. A DM to him waits for his agent; channel messages wait for his board; nothing is lost.
  for (const c of [rohan.code, rohan.codex, rohan.board]) c.close();
  await settle();
  const dm = await nitin.code.send("@rohan", "While you were out: the build is red.");
  assert.equal(dm.held, true, "held: none of rohan's agents is here");
  await nitin.board.send("#backend", "Freeze moved to Monday.");
  await nitin.board.send("#backend", "@rohan can you confirm Monday works?");
  await settle();
  // His board comes back first: it sees the DM (a copy) and the channel messages, but must not take the DM from his agents.
  const board2 = await agent(invite, "rohan/board", { kind: "human", listens: true, cursorPath: join(cursors, "rohan-board.json") });
  await settle();
  assert.ok(texts(board2).includes("While you were out: the build is red."), "his board shows the DM");
  assert.ok(texts(board2).includes("Freeze moved to Monday."));
  assert.ok(texts(board2).includes("@rohan can you confirm Monday works?"));
  assert.ok(!texts(board2).some((t) => t.startsWith("burst ")), "and not what it already had (its cursor)");
  const code2 = await agent(invite, "rohan/claude-code.api", { kind: "claude-code", answers: true, priority: 5, cursorPath: join(cursors, "rohan-code.json") });
  await settle();
  assert.ok(texts(code2).includes("While you were out: the build is red."), "his agent still gets the DM to answer");
  assert.ok(texts(code2).includes("@rohan can you confirm Monday works?"), "and the mention");
  assert.ok(!texts(code2).includes("Freeze moved to Monday."), "but not plain chatter");
  assert.ok(!texts(code2).includes("Can you take the iOS crash?"), "nor what it had before going offline");

  // 8. The board's view of ashish's day: conversations and where they stand.
  const records = [...ashish.board.got, ...ashish.code.got, ...ashish.codex.got].map((m) => ({ id: m.id, from: m.from, to: m.to, reply_to: m.replyTo, thread: m.thread, title: m.title, text: m.text, ts: m.ts, mentions: m.mentions }));
  records.push({ id: asked.id, from: "ashish/codex.web", to: "@rohan", reply_to: null, thread: asked.thread, title: "iOS crash", text: "Can you take the iOS crash?", ts: Date.now() - 60_000 });
  const unique = [...new Map(records.map((r) => [r.id, r])).values()].sort((a, b) => a.ts - b.ts);
  const cards = threads(unique, "ashish");
  const ios = cards.find((c) => c.title === "iOS crash");
  assert.equal(ios.status, "waiting", "rohan's answer is waiting on ashish");
  assert.equal(ios.count, 2);
  const expiry = cards.find((c) => c.title.includes("expiry type"));
  assert.equal(expiry.status, "waiting", "a channel @mention of ashish is waiting on him");
  assert.equal(expiry.channel, "#backend");
  const freeze = cards.find((c) => c.title === "Schema freeze is Friday.");
  assert.equal(freeze.status, "talking", "plain chatter is not waiting on him");

  // 9. Three agents remember into the same file at once; with retries, nothing is lost.
  const remember = async (who, line) => {
    const memory = new TeamMemory(invite, who);
    for (let attempt = 0; attempt < 6; attempt++) {
      const current = await memory.read("decisions/release.md");
      const text = `${current?.text ?? "# Release\n"}- ${line}\n`;
      const result = await memory.commit([{ path: "decisions/release.md", text, base: current?.version ?? 0 }], line);
      if (result.ok) return attempt;
      await sleep(Math.random() * 100);
    }
    throw new Error(`${who} could not save`);
  };
  const retries = await Promise.all([
    remember("ashish/claude-code.api", "Freeze moves to Monday [source: talk/x#1; by: nitin; added: 2026-10-13]"),
    remember("nitin/codex.web", "Rohan owns the iOS crash [source: talk/y#2; by: rohan; added: 2026-10-13]"),
    remember("rohan/claude-code.api", "Release notes by ashish [source: talk/z#3; by: rohan; added: 2026-10-13]"),
  ]);
  const final = (await new TeamMemory(invite, "check").read("decisions/release.md")).text;
  for (const fact of ["Freeze moves to Monday", "Rohan owns the iOS crash", "Release notes by ashish"]) assert.ok(final.includes(fact), `kept: ${fact}`);
  assert.ok(retries.some((n) => n > 0), "at least one writer was told to merge (the concurrency rule was exercised)");
  assert.equal((await new TeamMemory(invite, "check").history()).length, 3, "three commits, one per writer");
});
