// End to end against the real relay (workerd, via `wrangler dev`): clients, the
// MCP server, the hooks, setup. Run: npm test (after `npm install` in relay/).

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { after, before, test } from "node:test";
import { createInterface } from "node:readline";

import { LineClient } from "../lib/client.mjs";
import { Invite, LineError } from "../lib/protocol.mjs";

const ROOT = resolve(import.meta.dirname, "..");
const BIN = join(ROOT, "bin", "chamber.mjs");
const WRANGLER = join(ROOT, "relay", "node_modules", ".bin", "wrangler");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const tmp = (name) => mkdtempSync(join(tmpdir(), `chamber-${name}-`));

let relay;
let relayProcess;
const children = new Set(); // killed at the end, so a failure cannot hang the run
const clients = new Set();

async function freePort() {
  return new Promise((done) => {
    const server = createServer().listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => done(port));
    });
  });
}

before(async () => {
  assert.ok(existsSync(WRANGLER), "npm install in relay/ first");
  const port = await freePort();
  relayProcess = spawn(WRANGLER, ["dev", "--local", "--ip", "127.0.0.1", "--port", String(port), "--persist-to", tmp("relay"), "--var", "IDLE_WIPE_SECONDS:2"], {
    cwd: join(ROOT, "relay"),
    stdio: "ignore",
    detached: true,
  });
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
  for (const child of children) {
    try { child.kill("SIGKILL"); } catch {}
  }
  for (const client of clients) {
    try { client.close(); } catch {}
  }
  try { process.kill(-relayProcess.pid, "SIGKILL"); } catch {}
  setTimeout(() => process.exit(0), 100).unref();
});

function run(args, { home, input = "", cwd = ROOT, env = {} } = {}) {
  return new Promise((done) => {
    const child = spawn(process.execPath, [BIN, ...args], { cwd, env: { ...process.env, CHAMBER_HOME: home, CHAMBER_HARNESSES: "claude", ...env } });
    children.add(child);
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => (stdout += d));
    child.stderr.on("data", (d) => (stderr += d));
    child.on("close", (code) => {
      children.delete(child);
      done({ code, stdout, stderr });
    });
    child.stdin.end(input);
  });
}

async function join_(invite, agent, options = {}) {
  const client = new LineClient(invite, agent, options);
  clients.add(client);
  client.run();
  await client.ready();
  return client;
}

function mcp(home, agent, cwd) {
  const child = spawn(process.execPath, [BIN, "mcp", "--agent", agent], { cwd, env: { ...process.env, CHAMBER_HOME: home } });
  children.add(child);
  const lines = createInterface({ input: child.stdout });
  const pending = new Map();
  lines.on("line", (line) => {
    const response = JSON.parse(line);
    pending.get(response.id)?.(response);
  });
  let id = 0;
  const call = (method, params = {}) =>
    new Promise((done) => {
      pending.set(++id, (response) => done(response.result));
      child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`);
    });
  const tool = async (name, args = {}) => {
    const result = await call("tools/call", { name, arguments: args });
    return { text: result.content[0].text, isError: result.isError };
  };
  return { child, call, tool, close: () => { try { lines.close(); } catch {} try { child.stdin.end(); } catch {} } };
}

test("sealing: the relay can route but not read, alter or re-attribute", async () => {
  const invite = await Invite.create("https://relay.example").ready();
  const meta = { sender: "a/x", to: "@b", id: "m1", replyTo: null };
  const body = await invite.seal({ text: "hi" }, meta);
  assert.deepEqual(await invite.open(body, meta), { text: "hi" });
  for (const change of [{ sender: "c/x" }, { to: "@c" }, { id: "m2" }, { replyTo: "m0" }]) {
    await assert.rejects(invite.open(body, { ...meta, ...change }), LineError);
  }
  await assert.rejects((await Invite.create("https://relay.example").ready()).open(body, meta), LineError);
  assert.throws(() => Invite.parse("https://relay.example/"), LineError);
  assert.equal(String(Invite.parse(String(invite))), String(invite));
});

test("a project's Claude Code: asked, shown mid-task by the hook, woken when idle, replies with files", async () => {
  const invite = Invite.create(relay);
  const home = tmp("ashish");
  const project = tmp("project");
  writeFileSync(join(project, "schema.py"), "class Token:\n    expires_in: int\n");
  assert.equal((await run(["join", String(invite), "--as", "ashish"], { home })).code, 0);
  assert.equal(readFileSync(join(home, "config.json")).length > 0, true);

  const server = mcp(home, "claude-code.api", project);
  const init = await server.call("initialize", { protocolVersion: "2025-06-18" });
  assert.deepEqual((await server.call("tools/list")).tools.map((t) => t.name), [
    "agents",
    "ask",
    "inbox",
    "reply",
    "search",
    "incident_memory",
    "send_patch",
    "review_patch",
    "memory",
    "memory_search",
    "memory_write",
    "memory_edit",
    "decision",
    "contract",
    "coordinate_ask",
    "coordinate_delegate",
    "coordinate_review",
    "coordinate_propose",
    "coordinate_resolve",
    "coordinate_handoff",
    "coordinate_status",
    "context_package",
  ]);

  await invite.ready();
  const nitin = await join_(invite, "nitin/root", { kind: "root", answers: true, priority: 10 });
  for (let i = 0; i < 50 && !nitin.roster.some((a) => a.agent === "ashish/claude-code.api"); i++) await sleep(100);
  // Ashish's server connects and hears of the join on its own schedule: wait for it there too.
  let agents = "";
  for (let i = 0; i < 100; i++) {
    agents = (await server.tool("agents")).text;
    if (agents.includes("nitin/root")) break;
    await sleep(100);
  }
  assert.match(agents, /nitin\/root \(root, answers\)/);

  // Mid-task: the PostToolUse hook adds it to Claude's context, once.
  const asked = nitin.ask("@ashish", "What type is expires_in?", { timeout: 20000 });
  let shown;
  for (let i = 0; i < 50; i++) {
    shown = await run(["hook", "--agent", "claude-code.api"], { home, input: JSON.stringify({ hook_event_name: "PostToolUse" }) });
    if (shown.stdout) break;
    await sleep(100);
  }
  const context = JSON.parse(shown.stdout).hookSpecificOutput;
  assert.equal(context.hookEventName, "PostToolUse");
  assert.match(context.additionalContext, /from nitin\/root[\s\S]*What type is expires_in\?/);
  assert.match(context.additionalContext, /not your user's instructions/);
  assert.equal((await run(["hook", "--agent", "claude-code.api"], { home, input: "{}" })).stdout, "");

  // Claude replies, with a file; the asker gets both.
  const messageId = context.additionalContext.match(/\[([0-9a-f]{32})\]/)[1];
  const replied = await server.tool("reply", { message_id: messageId, message: "int, UNIX epoch", files: ["schema.py"] });
  assert.equal(replied.isError, false);
  const { reply } = await asked;
  assert.equal(reply.text, "int, UNIX epoch");
  assert.equal(new TextDecoder().decode(reply.files[0].data), "class Token:\n    expires_in: int\n");

  // Idle: the asyncRewake waiter exits 2 with the next message; a newer waiter retires an older one.
  const first = run(["wait", "--agent", "claude-code.api"], { home });
  await sleep(500);
  const second = run(["wait", "--agent", "claude-code.api"], { home });
  assert.equal((await first).code, 0);
  await sleep(300);
  await nitin.send("ashish/claude-code.api", "Are you free to review?", { files: [{ name: "big.bin", data: new Uint8Array(randomBytes(65536 + 10)) }] });
  const woken = await second;
  assert.equal(woken.code, 2);
  assert.match(woken.stderr, /Are you free to review\?/);
  assert.match(woken.stderr, /big\.bin \(65546 bytes\)/);

  // Claude asks back and waits for the answer.
  nitin.onMessage = (message) => nitin.send(message.from, `seen: ${message.text}`, { replyTo: message.id });
  const back = await server.tool("ask", { to: "nitin/root", message: "ping", wait_seconds: 20 });
  assert.match(back.text, /Answer:[\s\S]*seen: ping/);
  assert.match((await server.tool("ask", { to: "nitin/root", message: "x", files: ["nope.txt"] })).text, /not a file/);

  nitin.close();
  server.close();
});

test("contract changes: publish reaches a working consumer mid-task and a new session, acks reach the producer", async () => {
  const invite = Invite.create(relay);
  const homeBE = tmp("nitin");
  const homeFE = tmp("rohan");
  const projectBE = tmp("api");
  const projectFE = tmp("web");
  assert.equal((await run(["join", String(invite), "--as", "nitin"], { home: homeBE })).code, 0);
  assert.equal((await run(["join", String(invite), "--as", "rohan"], { home: homeFE })).code, 0);
  await invite.ready();

  const be = mcp(homeBE, "claude-code.api", projectBE);
  const fe = mcp(homeFE, "codex.web", projectFE);
  await be.call("initialize", { protocolVersion: "2025-06-18" });
  await fe.call("initialize", { protocolVersion: "2025-06-18" });

  // BE registers the contract it produces; FE subscribes as a consumer.
  assert.match((await be.tool("contract", { action: "register", name: "auth-token", title: "Auth token response", summary: "expires_in: ISO string" })).text, /Registered auth-token/);
  assert.match((await fe.tool("contract", { action: "subscribe", name: "auth-token" })).text, /Subscribed to auth-token/);

  // BE publishes a change: the consumer is told automatically, no human relaying.
  const published = await be.tool("contract", { action: "publish", name: "auth-token", summary: "expires_at_epoch_seconds added alongside the ISO field", decision: "d-20261029-02" });
  assert.match(published.text, /Published auth-token v2/);
  assert.match(published.text, /Notified 1 consumer/);

  // The working FE agent sees the notice mid-task through its hook, once.
  let shown;
  for (let i = 0; i < 50; i++) {
    shown = await run(["hook", "--agent", "codex.web"], { home: homeFE, input: JSON.stringify({ hook_event_name: "PostToolUse" }) });
    if (shown.stdout) break;
    await sleep(100);
  }
  const context = JSON.parse(shown.stdout).hookSpecificOutput;
  assert.match(context.additionalContext, /published auth-token v2: expires_at_epoch_seconds added/);
  assert.match(context.additionalContext, /stance=adapted/);
  assert.equal((await run(["hook", "--agent", "codex.web"], { home: homeFE, input: "{}" })).stdout, "", "the notice is shown once");

  // A fresh FE session starts with the unacknowledged change in its bootstrap.
  let started;
  for (let i = 0; i < 50; i++) {
    started = await run(["hook", "--agent", "codex.web"], { home: homeFE, input: JSON.stringify({ hook_event_name: "SessionStart" }) });
    if (started.stdout.includes("auth-token v2")) break;
    await sleep(100);
  }
  assert.match(started.stdout, /Contract items still open for you/);
  assert.match(started.stdout, /auth-token v2: expires_at_epoch_seconds added/);

  // Seen is receipt only: it does not clear the session-start reminder, and adapted needs real evidence.
  assert.match((await fe.tool("contract", { action: "ack", name: "auth-token", stance: "seen" })).text, /Recorded/);
  started = await run(["hook", "--agent", "codex.web"], { home: homeFE, input: JSON.stringify({ hook_event_name: "SessionStart" }) });
  assert.match(started.stdout, /auth-token v2: expires_at_epoch_seconds added/);
  assert.match(started.stdout, /acked seen: not adapted yet/);
  assert.match((await fe.tool("contract", { action: "ack", name: "auth-token", stance: "adapted", note: "done" })).text, /evidence note of at least 20/);

  // FE adapts and records evidence; the producer is told, and the registry shows who caught up.
  assert.match((await fe.tool("contract", { action: "ack", name: "auth-token", stance: "adapted", note: "web/auth.ts prefers seconds with ISO fallback; both rollout orders pass" })).text, /producer has been told/);
  let told;
  for (let i = 0; i < 50; i++) {
    told = await run(["hook", "--agent", "claude-code.api"], { home: homeBE, input: JSON.stringify({ hook_event_name: "PostToolUse" }) });
    if (told.stdout) break;
    await sleep(100);
  }
  assert.match(told.stdout, /adapted to auth-token v2/);
  const status = await be.tool("contract", { action: "status", name: "auth-token" });
  assert.match(status.text, /rohan\/codex\.web: ADAPTED — web\/auth\.ts prefers seconds/);

  // The next fresh FE session is clean again.
  started = await run(["hook", "--agent", "codex.web"], { home: homeFE, input: JSON.stringify({ hook_event_name: "SessionStart" }) });
  assert.equal(started.stdout.includes("Contract items still open for you"), false);

  be.close();
  fe.close();
});


test("contract hardening over a real relay: zero-subscriber warning, objection surfacing, registry in context_package", async () => {
  const invite = Invite.create(relay);
  const homeBE = tmp("nitin");
  const homeJobs = tmp("dev");
  assert.equal((await run(["join", String(invite), "--as", "nitin"], { home: homeBE })).code, 0);
  assert.equal((await run(["join", String(invite), "--as", "dev"], { home: homeJobs })).code, 0);
  await invite.ready();
  const be = mcp(homeBE, "claude-code.api", tmp("api"));
  const jobs = mcp(homeJobs, "codex.jobs", tmp("jobs"));
  await be.call("initialize", { protocolVersion: "2025-06-18" });
  await jobs.call("initialize", { protocolVersion: "2025-06-18" });

  await be.tool("contract", { action: "register", name: "auth-token", title: "Auth token response", summary: "expires_in: ISO string" });
  const nobody = await be.tool("contract", { action: "publish", name: "auth-token", summary: "epoch seconds added" });
  assert.match(nobody.text, /WARNING: no consumers are subscribed/);

  // The undeclared consumer finds the contract through context_package, not only a contract list.
  const ctx = await jobs.tool("context_package", { contracts: ["auth-token"] });
  assert.match(ctx.text, /contract auth-token\*\* v2: epoch seconds added/);
  assert.match(ctx.text, /0 subscribed consumers/);

  await jobs.tool("contract", { action: "subscribe", name: "auth-token" });
  await jobs.tool("contract", { action: "ack", name: "auth-token", stance: "object", note: "quarterly deploy cannot take this" });

  // The producer's next session start shows the open objection; the next publish warns and tells the consumers.
  const started = await run(["hook", "--agent", "claude-code.api"], { home: homeBE, input: JSON.stringify({ hook_event_name: "SessionStart" }) });
  assert.match(started.stdout, /OPEN OBJECTION on auth-token v2 from dev\/codex\.jobs: quarterly deploy/);
  const again = await be.tool("contract", { action: "publish", name: "auth-token", summary: "v3 removes the ISO field" });
  assert.match(again.text, /Published auth-token v3/);
  assert.match(again.text, /WARNING: 1 open objection/);
  let shown;
  for (let i = 0; i < 50; i++) {
    shown = await run(["hook", "--agent", "codex.jobs"], { home: homeJobs, input: JSON.stringify({ hook_event_name: "PostToolUse" }) });
    if (shown.stdout.includes("v3")) break;
    await sleep(100);
  }
  assert.match(shown.stdout, /Open objection on this contract: dev\/codex\.jobs/);
  const ctx2 = await jobs.tool("context_package", { query: "token" });
  assert.match(ctx2.text, /your standing: outstanding/);

  be.close();
  jobs.close();
});

test("setup adds the hooks next to the project's own, idempotently, and remove takes them out", async () => {
  const home = tmp("setup");
  const project = tmp("proj");
  mkdirSync(join(project, ".claude"));
  mkdirSync(join(project, ".git", "info"), { recursive: true });
  const own = { permissions: { allow: ["Bash(ls)"] }, hooks: { PostToolUse: [{ matcher: "Edit", hooks: [{ type: "command", command: "lint.sh" }] }] } };
  writeFileSync(join(project, ".claude", "settings.local.json"), JSON.stringify(own));
  await run(["join", String(Invite.create(relay)), "--as", "nitin"], { home });
  for (let i = 0; i < 2; i++) {
    const result = await run(["setup", project, "--no-register"], { home });
    assert.equal(result.code, 0, result.stderr);
  }
  const settings = JSON.parse(readFileSync(join(project, ".claude", "settings.local.json"), "utf8"));
  assert.deepEqual(settings.permissions, own.permissions);
  const commands = Object.values(settings.hooks).flat().flatMap((g) => g.hooks.map((h) => h.command));
  assert.equal(commands.filter((c) => c === "lint.sh").length, 1);
  assert.equal(commands.filter((c) => c.includes("CHAMBER=1") && c.includes(" hook ")).length, 3);
  const stop = settings.hooks.Stop[0].hooks[0];
  assert.equal(stop.asyncRewake, true);
  assert.match(stop.command, /wait --agent claude-code\.chamber-proj-/);
  assert.match(readFileSync(join(project, ".git", "info", "exclude"), "utf8"), /\.claude\/settings\.local\.json/);

  await run(["remove", project, "--no-register"], { home });
  assert.deepEqual(JSON.parse(readFileSync(join(project, ".claude", "settings.local.json"), "utf8")), own);
});

test("one command: the relay serves the installer, which installs, joins and sets up the project", async () => {
  const invite = Invite.create(relay);
  const home = tmp("home");
  const lineHome = join(home, ".chamber");
  const bin = join(home, "bin");
  const project = tmp("nitins-api");
  await new Promise((done) => spawn("git", ["init", "-q"], { cwd: project }).on("close", done));

  const script = await (await fetch(`${relay}/install`)).text();
  const { readdirSync } = await import("node:fs");
  for (const f of readdirSync(join(ROOT, "lib"))) assert.match(script, new RegExp(`lib/${f}`), `the installer must hand out lib/${f}`);
  assert.match(script, new RegExp(`RELAY="${relay}"`));
  assert.equal(await (await fetch(`${relay}/client/lib/protocol.mjs`)).text(), readFileSync(join(ROOT, "lib", "protocol.mjs"), "utf8"));
  assert.equal((await fetch(`${relay}/client/../../etc/passwd`)).status, 404);

  const result = await new Promise((done) => {
    const child = spawn("sh", ["-c", `curl -fsSL ${relay}/install | sh -s -- ${invite.secret} nitin`], {
      cwd: project,
      env: { ...process.env, HOME: home, CHAMBER_HOME: lineHome, CHAMBER_BIN: bin, CHAMBER_NO_REGISTER: "1", CHAMBER_HARNESSES: "claude" },
    });
    children.add(child);
    let out = "";
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (out += d));
    child.on("close", (code) => done({ code, out }));
  });
  assert.equal(result.code, 0, result.out);
  assert.match(result.out, /in the chamber as nitin/);
  assert.match(result.out, /Claude Code  as nitin\/claude-code\.chamber-nitins-api/);
  assert.equal(JSON.parse(readFileSync(join(lineHome, "config.json"), "utf8")).invite, String(invite));
  assert.ok(existsSync(join(lineHome, "app", "board", "assets", "board.mjs")), "the board comes with the install");
  const hooks = JSON.parse(readFileSync(join(project, ".claude", "settings.local.json"), "utf8")).hooks;
  assert.match(hooks.Stop[0].hooks[0].command, new RegExp(`${lineHome}/app/bin/chamber.mjs`));

  // The installed command works on its own; a teammate does not invite (the admin does).
  const status = await new Promise((done) => {
    const child = spawn(join(bin, "chamber"), ["invite"], { env: { ...process.env, HOME: home, CHAMBER_HOME: lineHome } });
    let out = "";
    child.stderr.on("data", (d) => (out += d));
    child.on("close", (code) => done({ code, out }));
  });
  assert.equal(status.code, 1);
  assert.match(status.out, /only your chamber's admin invites people/);
  assert.match(result.out, /chamber start/, "the installer says how to come back");

  // The next day: the relay has a newer client, and Nitin works in another project.
  // `chamber start` (not the install command) updates, connects that project, keeps his key and name.
  writeFileSync(join(lineHome, "app", "lib", "protocol.mjs"), "// yesterday's client\n" + readFileSync(join(ROOT, "lib", "protocol.mjs"), "utf8"));
  const other = tmp("nitins-web");
  await new Promise((done) => spawn("git", ["init", "-q"], { cwd: other }).on("close", done));
  const start = (args) =>
    new Promise((done) => {
      const child = spawn(join(bin, "chamber"), ["start", "--no-board", ...args], { cwd: other, env: { ...process.env, HOME: home, CHAMBER_HOME: lineHome, CHAMBER_NO_REGISTER: "1", CHAMBER_HARNESSES: "claude" } });
      children.add(child);
      let out = "";
      child.stdout.on("data", (d) => (out += d));
      child.stderr.on("data", (d) => (out += d));
      child.on("close", (code) => done({ code, out }));
    });
  const again = await start([]);
  assert.equal(again.code, 0, again.out);
  assert.match(again.out, /Updated chamber \(1 file\)/);
  assert.match(again.out, /You are nitin on /);
  assert.match(again.out, /claude-code\.chamber-nitins-web\S* connected/);
  assert.equal(readFileSync(join(lineHome, "app", "lib", "protocol.mjs"), "utf8"), readFileSync(join(ROOT, "lib", "protocol.mjs"), "utf8"));
  assert.ok(existsSync(join(other, ".claude", "settings.local.json")));
  assert.equal(JSON.parse(readFileSync(join(lineHome, "config.json"), "utf8")).invite, String(invite));
  assert.doesNotMatch((await start([])).out, /Updated/, "nothing to update the second time");
});

test("board: messages become conversations that say where they stand", async () => {
  const { threads } = await import("../lib/board.mjs");
  const rec = (id, from, to, ts, extra = {}) => ({ id, from, to, ts, text: id, ...extra });
  const records = [
    rec("a1", "nitin/claude-code.api", "@ashish", 1, { title: "Heartbeat payload?", thread: "a1" }),
    rec("a2", "ashish/claude-code.root", "nitin/claude-code.api", 2, { reply_to: "a1", thread: "a1" }),
    rec("b1", "nitin/root", "ashish/board", 3, { text: "Review the lease fix?\nmore" }),
    rec("b2", "ashish/board", "nitin/root", 4, { reply_to: "b1" }), // an older client: no thread
    rec("b3", "nitin/root", "ashish/board", 5, { reply_to: "b2" }),
    rec("c1", "ashish/claude-code.root", "@nitin", 6, { title: "Port?", thread: "c1" }),
  ];
  const cards = Object.fromEntries(threads(records, "ashish").map((c) => [c.id, c]));
  assert.deepEqual(Object.keys(cards).sort(), ["a1", "b1", "c1"]);
  assert.equal(cards.a1.status, "talking");
  assert.equal(cards.b1.title, "Review the lease fix?");
  assert.equal(cards.b1.status, "waiting");
  assert.equal(cards.b1.count, 3);
  assert.deepEqual(cards.b1.with, ["nitin/root"]);
  assert.equal(cards.c1.status, "talking");
});

test("chamber board: see a talk, answer it, close it, start one; only from this machine", async () => {
  const invite = Invite.create(relay);
  const home = tmp("board");
  await run(["join", String(invite), "--as", "ashish"], { home });
  writeFileSync(join(home, "relay.json"), JSON.stringify({ relay: invite.relay, secret: invite.secret })); // the admin's machine
  const port = await freePort();
  const board = spawn(process.execPath, [BIN, "board", "--no-open", "--port", String(port)], { env: { ...process.env, CHAMBER_HOME: home } });
  children.add(board);
  const url = `http://127.0.0.1:${port}`;
  const get = async (path) => (await fetch(url + path)).json();
  let sessionToken = null;
  const post = async (path, body, headers = {}) => {
    if (!sessionToken) {
      sessionToken = (await get("/api/board/session")).token;
    }
    const reqHeaders = { "content-type": "application/json", ...headers };
    if (!reqHeaders["x-chamber-token"] && !headers.origin && sessionToken) {
      reqHeaders["x-chamber-token"] = sessionToken;
    }
    return fetch(url + path, { method: "POST", headers: reqHeaders, body: JSON.stringify(body) });
  };
  let data;
  for (let i = 0; i < 100; i++) {
    try {
      data = await get("/api/board");
      if (data.connected) break;
    } catch {}
    await sleep(100);
  }
  assert.equal(data.connected, true);
  assert.match(await (await fetch(url)).text(), /<div id="root">/);

  await invite.ready();
  const nitin = await join_(invite, "nitin/claude-code.web", { kind: "claude-code", answers: true });
  const receipt = await nitin.send("@ashish", "Is expires_in an int?", { title: "Heartbeat payload" });
  assert.deepEqual(receipt.delivered, ["ashish/board"]);
  let card;
  for (let i = 0; i < 50 && !card; i++) {
    card = (await get("/api/board")).cards.find((c) => c.status === "waiting");
    if (!card) await sleep(100);
  }
  assert.equal(card.title, "Heartbeat payload");

  const got = new Promise((done) => (nitin.onMessage = done));
  assert.equal((await post(`/api/board/talks/${card.id}`, { to: "nitin/claude-code.web", text: "Yes, epoch seconds." })).status, 200);
  const answer = await got;
  assert.equal(answer.text, "Yes, epoch seconds.");
  assert.equal(answer.thread, card.id);
  assert.deepEqual((await get(`/api/board/talks/${card.id}`)).messages.map((m) => m.from), ["nitin/claude-code.web", "ashish/board"]);
  // Read, like Gmail: unread until opened.
  const second = new Promise((done) => setTimeout(done, 50));
  await second;
  await nitin.send("@ashish", "One more: is it seconds or ms?", { thread: card.id, title: "Heartbeat payload", replyTo: answer.id });
  let unread;
  for (let i = 0; i < 50 && !unread; i++) {
    unread = (await get("/api/board")).cards.find((c) => c.id === card.id && c.unread);
    if (!unread) await sleep(100);
  }
  assert.ok(unread, "a new message from them makes it unread");
  await get(`/api/board/talks/${card.id}?seen=1`);
  assert.equal((await get("/api/board")).cards.find((c) => c.id === card.id).unread, false, "opening it marks it read");

  const fresh = new Promise((done) => (nitin.onMessage = done));
  await post("/api/board/talks", { to: "@nitin", text: "Let's play?", title: "Welcome" });
  assert.equal((await fresh).title, "Welcome");
  assert.match((await get("/api/board/invite")).command, new RegExp(`/install \\| sh -s -- ${invite.secret}$`));

  // Only this machine, only the board's own page.
  const raw = (path, headers) => new Promise((done) => {
    import("node:http").then(({ request }) => {
      const req = request({ host: "127.0.0.1", port, path, method: "GET", headers }, (res) => done(res.statusCode));
      req.end();
    });
  });
  assert.equal(await raw("/api/board", { host: `evil.example:${port}` }), 403);
  assert.equal((await post("/api/board/talks", { to: "@nitin", text: "x" }, { origin: "https://evil.example" })).status, 403);
  assert.equal((await fetch(`${url}/api/board/talks`, { method: "POST", headers: { "content-type": "text/plain" }, body: "{}" })).status, 403);
  assert.equal((await fetch(`${url}/api/board/files?path=${encodeURIComponent("/etc/passwd")}`)).status, 404);
  nitin.close();
  board.kill();
});

test("names: optional to join, changeable anytime, records move with you, never an account name", async () => {
  const invite = Invite.create(relay);
  const home = tmp("names");
  const joined = await run(["join", String(invite)], { home });
  assert.match(joined.stdout, /You are in the chamber as [a-z0-9._-]+ \(change it anytime: chamber name <new>\)/);
  const guessed = JSON.parse(readFileSync(join(home, "config.json"), "utf8")).owner;
  assert.ok(!["root", "admin"].includes(guessed));

  mkdirSync(join(home, "agents", `${guessed}__claude-code.api`), { recursive: true });
  writeFileSync(join(home, "agents", `${guessed}__claude-code.api`, "sent.jsonl"), "{}\n");
  assert.equal((await run(["name", "Nitin"], { home })).code, 0);
  assert.equal((await run(["name"], { home })).stdout.trim(), "nitin");
  assert.ok(existsSync(join(home, "agents", "nitin__claude-code.api", "sent.jsonl")), "your records move with your name");
  const refused = await run(["name", "root"], { home });
  assert.equal(refused.code, 1);
  assert.match(refused.stderr, /account name/);

  // The board renames too, and keeps working under the new name.
  const port = await freePort();
  const board = spawn(process.execPath, [BIN, "board", "--no-open", "--port", String(port)], { env: { ...process.env, CHAMBER_HOME: home } });
  children.add(board);
  const url = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 100; i++) {
    try {
      if ((await (await fetch(`${url}/api/board`)).json()).connected) break;
    } catch {}
    await sleep(100);
  }
  const sess = await (await fetch(`${url}/api/board/session`)).json();
  const renamed = await fetch(`${url}/api/board/name`, { method: "POST", headers: { "content-type": "application/json", "x-chamber-token": sess.token }, body: JSON.stringify({ name: "nitin-k" }) });
  assert.equal((await renamed.json()).name, "nitin-k");
  let data;
  for (let i = 0; i < 100; i++) {
    data = await (await fetch(`${url}/api/board`)).json();
    if (data.connected) break;
    await sleep(100);
  }
  assert.equal(data.me, "nitin-k");
  assert.ok(data.agents.some((a) => a.agent === "nitin-k/board"));
  board.kill();
});

test("a private relay serves only its own chambers", async () => {
  const mine = await Invite.create("http://x").ready();
  const port = await freePort();
  const priv = spawn(WRANGLER, ["dev", "--local", "--ip", "127.0.0.1", "--port", String(port), "--persist-to", tmp("private"), "--var", `ALLOWED_LINES:${mine.lineId}`], {
    cwd: join(ROOT, "relay"), stdio: "ignore", detached: true,
  });
  const url = `http://127.0.0.1:${port}`;
  try {
    for (let i = 0; i < 120; i++) {
      try {
        if ((await fetch(url)).ok) break;
      } catch {}
      await sleep(500);
    }
    const ours = await Invite.parse(`${url}/#${mine.secret}`).ready();
    const other = await Invite.create(url).ready();
    assert.equal((await fetch(`${url}/v1/lines/${ours.lineId}/roster`, { headers: ours.auth })).status, 200);
    const refused = await fetch(`${url}/v1/lines/${other.lineId}/roster`, { headers: other.auth });
    assert.equal(refused.status, 403);
    assert.match((await refused.json()).error, /only its own chambers/);
    assert.equal((await fetch(`${url}/install`)).status, 200); // joining stays open; the key is the gate
  } finally {
    process.kill(-priv.pid, "SIGTERM");
  }
});

/** A relay of its own (claiming locks a relay to one chamber, so claim tests can't share). */
async function freshRelay(name, vars = []) {
  const port = await freePort();
  const proc = spawn(WRANGLER, ["dev", "--local", "--ip", "127.0.0.1", "--port", String(port), "--persist-to", tmp(name), ...vars.flatMap((v) => ["--var", v])], { cwd: join(ROOT, "relay"), stdio: "ignore", detached: true });
  const url = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 120; i++) {
    try {
      if ((await fetch(url)).ok) break;
    } catch {}
    await sleep(500);
  }
  return { url, stop: () => process.kill(-proc.pid, "SIGTERM") };
}

test("the Deploy-button path: a fresh relay is claimed by the first chamber; the admin invites; teammates only join", async () => {
  const fresh = await freshRelay("claim");
  try {
    const page = await (await fetch(fresh.url, { headers: { accept: "text/html" } })).text();
    assert.match(page, new RegExp(`curl -fsSL ${fresh.url}/new \\| sh`), "the landing page shows the one command");

    // The admin: one command, inside their project.
    const home = tmp("admin-home");
    const lineHome = join(home, ".chamber");
    const project = tmp("admin-proj");
    const made = await new Promise((done) => {
      const child = spawn("sh", ["-c", `curl -fsSL ${fresh.url}/new | sh -s -- ashish`], { cwd: project, env: { ...process.env, HOME: home, CHAMBER_HOME: lineHome, CHAMBER_BIN: join(home, "bin"), CHAMBER_NO_REGISTER: "1", CHAMBER_HARNESSES: "claude" } });
      children.add(child);
      let out = "";
      child.stdout.on("data", (d) => (out += d));
      child.stderr.on("data", (d) => (out += d));
      child.on("close", (code) => done({ code, out }));
    });
    assert.equal(made.code, 0, made.out);
    assert.match(made.out, /you are its admin, as ashish\. The relay is now private to it\./);
    assert.match((await (await fetch(fresh.url, { headers: { accept: "text/html" } })).text()), /belongs to a chamber/);
    assert.equal((await fetch(`${fresh.url}/v1/claim`, { method: "POST", headers: { "content-type": "application/json" }, body: "{}" })).status, 409, "a relay is claimed once");
    const stranger = await Invite.create(fresh.url).ready();
    assert.equal((await fetch(`${fresh.url}/v1/lines/${stranger.lineId}/roster`, { headers: stranger.auth })).status, 403, "and serves only its chamber");

    // Personal invites are on: the admin's invite brings a teammate in; the teammate can't invite.
    const admin = { CHAMBER_HOME: lineHome };
    const command = (await run(["invite"], { home: lineHome, env: admin })).stdout;
    const key = /sh -s -- (\S+)/.exec(command)[1];
    assert.match(key, /~/, "a personal pass in the command");
    const mate = tmp("mate");
    await run(["join", `${fresh.url}/#${key}`, "--as", "nitin"], { home: mate });
    assert.match((await run(["invite"], { home: mate })).stderr, /only your chamber's admin/);
    const config = JSON.parse(readFileSync(join(mate, "config.json"), "utf8"));
    const nitin = await join_(await Invite.parse(config.invite).ready(), "nitin/board", { kind: "human", listens: true });
    assert.equal(nitin.connected, true, "the teammate is in");
    nitin.close();
  } finally {
    fresh.stop();
  }
});

test("knocks: a message to an agent that isn't connected knocks on its owner's board, now and when the board comes back", async () => {
  const invite = await Invite.create(relay).ready();
  const knocks = [];
  const ashBoard = await join_(invite, "ashish/board", { kind: "human", listens: true, onKnock: (k) => knocks.push(k) });
  const mayaBoard = await join_(invite, "maya/board", { kind: "human", listens: true, onKnock: (k) => knocks.push({ ...k, wrong: true }) });
  const mayaCode = await join_(invite, "maya/claude-code.web", { kind: "claude-code", answers: true });
  const ashCode = await join_(invite, "ashish/claude-code.api", { kind: "claude-code", answers: true });
  await sleep(200);

  const sent = await mayaCode.send("ashish/claude-code.api", "Is expires_at in seconds?");
  await sleep(300);
  assert.equal(knocks.length, 0, "a connected agent gets the message itself: no knock");

  ashCode.close();
  await sleep(300);
  const asked = await mayaCode.send("ashish/claude-code.api", "And the refresh token?");
  await sleep(300);
  assert.deepEqual(knocks.map(({ id, from, to }) => ({ id, from, to })), [{ id: asked.id, from: "maya/claude-code.web", to: "ashish/claude-code.api" }], "only the owner's board, and no content");
  assert.equal(knocks[0].text, undefined);

  // A board that was away hears of what still waits.
  ashBoard.close();
  await sleep(200);
  const again = [];
  await join_(invite, "ashish/board", { kind: "human", listens: true, onKnock: (k) => again.push(k) });
  await sleep(300);
  assert.deepEqual(again.map((k) => k.id), [asked.id]);
  assert.notEqual(sent.id, asked.id);
  mayaBoard.close();
});

test("channels: boards hear everything, Claude Code only @mentions; offline gets it later", async () => {
  const invite = await Invite.create(relay).ready();
  const cursor = join(tmp("chan"), "cursor.json");
  const inbox = (client) => {
    const got = [];
    client.onMessage = (m) => got.push(m);
    return got;
  };
  const ashBoard = await join_(invite, "ashish/board", { kind: "human", listens: true });
  const ashCode = await join_(invite, "ashish/claude-code.api", { kind: "claude-code", answers: true, cursorPath: cursor });
  const nitBoard = await join_(invite, "nitin/board", { kind: "human", listens: true });
  const boardGot = inbox(ashBoard);
  const codeGot = inbox(ashCode);
  await sleep(200);
  assert.ok(nitBoard.channels.some((c) => c.name === "#general" && c.members.includes("ashish")), "everyone starts in #general");

  await nitBoard.send("#general", "Deploying at 5.");
  await sleep(400);
  assert.deepEqual(boardGot.map((m) => m.text), ["Deploying at 5."]);
  assert.equal(codeGot.length, 0, "plain channel chatter does not wake Claude Code");

  const asked = await nitBoard.send("#general", "@ashish can your agent review the auth PR?");
  assert.deepEqual(asked.mentions, ["ashish"]);
  await sleep(400);
  assert.equal(codeGot.at(-1)?.text, "@ashish can your agent review the auth PR?");
  assert.equal(codeGot.at(-1).to, "#general");
  assert.deepEqual(codeGot.at(-1).mentions, ["ashish"]);

  // Offline: a mention waits for the agent.
  ashCode.close();
  await sleep(300);
  await nitBoard.send("#general", "@ashish ping while you were away");
  const back = await join_(invite, "ashish/claude-code.api", { kind: "claude-code", answers: true, cursorPath: cursor });
  const later = inbox(back);
  await sleep(500);
  assert.deepEqual(later.map((m) => m.text), ["@ashish ping while you were away"]);

  // A new channel: only members hear it; a non-member must join first.
  await nitBoard.channel("join", "#backend");
  await sleep(200);
  const before = boardGot.length;
  await nitBoard.send("#backend", "schema changed");
  await sleep(300);
  assert.equal(boardGot.length, before, "not a member, not delivered");
  await assert.rejects(ashBoard.send("#backend", "hi"), /join #backend first/);
  await ashBoard.channel("join", "#backend");
  await sleep(200);
  await ashBoard.send("#backend", "joined, reading now");

  // Only people in a channel can add or remove others there.
  const outsider = await join_(invite, "rohan/board", { kind: "human", listens: true });
  let refusal = "";
  outsider.ws.addEventListener("message", (e) => { const f = JSON.parse(String(e.data)); if (f.t === "error") refusal = f.error; });
  await outsider.channel("leave", "#backend", "nitin");
  await sleep(300);
  assert.match(refusal, /only people in #backend/);
  outsider.close();

  // Anyone in a channel can add a teammate; the added one hears it from then on.
  await nitBoard.channel("join", "#ops");
  await nitBoard.channel("add", "#ops", "ashish");
  await sleep(300);
  assert.ok(ashBoard.channels.find((c) => c.name === "#ops")?.members.includes("ashish"));
  const opsBefore = boardGot.length;
  await nitBoard.send("#ops", "deploy window moved to 6");
  await sleep(300);
  assert.equal(boardGot.length, opsBefore + 1);
  assert.equal(boardGot.at(-1).to, "#ops");

  // Private channel isolation: non-members cannot see or self-join #p- prefixed channels
  await nitBoard.channel("join", "#p-security");
  await sleep(300);
  assert.ok(nitBoard.channels.some((c) => c.name === "#p-security"), "creator is in private channel");
  assert.ok(!ashBoard.channels.some((c) => c.name === "#p-security"), "outsiders cannot see private channel");

  let privateRefusal = "";
  const refusalListener = (e) => {
    try {
      const f = JSON.parse(String(e.data));
      if (f.t === "error") privateRefusal = f.error;
    } catch {}
  };
  ashBoard.ws.addEventListener("message", refusalListener);
  await ashBoard.channel("join", "#p-security");
  await sleep(300);
  assert.match(privateRefusal, /private: an existing member must add you/i);

  // When added by member, now they are admitted
  await nitBoard.channel("add", "#p-security", "ashish");
  await sleep(300);
  assert.ok(ashBoard.channels.some((c) => c.name === "#p-security"), "added member now in private channel");

  for (const c of [ashBoard, back, nitBoard]) c.close();
});

test("personal invites: a pass per member, names are theirs, the admin revokes", async () => {
  const { newAdminKey, issuePass, revocation } = await import("../lib/admin.mjs");
  const admin = newAdminKey();
  const port = await freePort();
  const relayProc = spawn(WRANGLER, ["dev", "--local", "--ip", "127.0.0.1", "--port", String(port), "--persist-to", tmp("personal"), "--var", `ADMIN_KEY:${admin.public}`], {
    cwd: join(ROOT, "relay"), stdio: "ignore", detached: true,
  });
  const url = `http://127.0.0.1:${port}`;
  try {
    for (let i = 0; i < 120; i++) {
      try {
        if ((await fetch(url)).ok) break;
      } catch {}
      await sleep(500);
    }
    const chamber = await Invite.create(url).ready();
    const as = (pass) => new Invite(url, chamber.secret, pass);
    const tryJoin = async (invite, agent) => {
      const client = new LineClient(await invite.ready(), agent, { kind: "human", listens: true });
      clients.add(client);
      client.run();
      for (let i = 0; i < 40 && !client.connected && !client.problem; i++) await sleep(100);
      await sleep(200);
      return client;
    };

    const nobody = await tryJoin(chamber, "eve/board");
    assert.equal(nobody.connected, false);
    assert.match(nobody.problem, /personal invite|refused/);
    nobody.close();

    const passA = issuePass(admin.private, chamber.lineId);
    const passB = issuePass(admin.private, chamber.lineId);
    const ashish = await tryJoin(as(passA), "ashish/board");
    assert.equal(ashish.connected, true);
    const imposter = await tryJoin(as(passB), "ashish/claude-code.x");
    assert.equal(imposter.connected, false, "a name belongs to the member who has it");
    imposter.close();
    const nitin = await tryJoin(as(passB), "nitin/board");
    assert.equal(nitin.connected, true);

    const members = await (await fetch(`${url}/v1/lines/${chamber.lineId}/members`, { headers: chamber.auth })).json();
    assert.equal(members.personal, true);
    assert.deepEqual(members.members.map((m) => m.owner).sort(), ["ashish", "nitin"]);

    const forged = await fetch(`${url}/v1/lines/${chamber.lineId}/admin`, { method: "POST", headers: { ...chamber.auth, "content-type": "application/json" }, body: JSON.stringify({ order: revocation(newAdminKey().private, chamber.lineId, "nitin") }) });
    assert.equal(forged.status, 403, "only the admin's key can revoke");
    const order = await fetch(`${url}/v1/lines/${chamber.lineId}/admin`, { method: "POST", headers: { ...chamber.auth, "content-type": "application/json" }, body: JSON.stringify({ order: revocation(admin.private, chamber.lineId, "nitin") }) });
    assert.equal(order.status, 200);
    await sleep(500);
    assert.equal(nitin.connected, false, "revoked: disconnected now");
    nitin.close();
    const again = await tryJoin(as(passB), "nitin-2/board");
    assert.equal(again.connected, false, "and their pass no longer works, under any name");
    assert.match(again.problem, /removed/);
    again.close();

    // Someone who never had a pass (joined before invites were personal) is removed by name.
    const orderFor = (owner, op) => fetch(`${url}/v1/lines/${chamber.lineId}/admin`, { method: "POST", headers: { ...chamber.auth, "content-type": "application/json" }, body: JSON.stringify({ order: revocation(admin.private, chamber.lineId, owner, op) }) });
    assert.equal((await orderFor("mira", "revoke")).status, 200);
    const listed = await (await fetch(`${url}/v1/lines/${chamber.lineId}/members`, { headers: chamber.auth })).json();
    assert.deepEqual(listed.removed.map((r) => r.owner).sort(), ["mira", "nitin"]);
    const mira = await tryJoin(as(issuePass(admin.private, chamber.lineId)), "mira/board");
    assert.equal(mira.connected, false, "a removed name stays out, even with a new invite");
    assert.match(mira.problem, /mira was removed/);
    mira.close();
    assert.equal((await orderFor("mira", "restore")).status, 200);
    const back = await tryJoin(as(issuePass(admin.private, chamber.lineId)), "mira/board");
    assert.equal(back.connected, true, "restored: a new invite works again");
    back.close();
    ashish.close();
  } finally {
    process.kill(-relayProc.pid, "SIGTERM");
  }
});

test("team memory: sealed, versioned, stale writes rejected, Claude's tools, and a dream", async () => {
  const { TeamMemory } = await import("../lib/memory.mjs");
  const invite = await Invite.create(relay).ready();
  const a = new TeamMemory(invite, "ashish/claude-code.api");
  const b = new TeamMemory(invite, "nitin/claude-code.web");

  assert.equal((await a.write("MEMORY.md", "# Team\n- [[contracts/auth-token]]", "start")).ok, true);
  const raw = await (await fetch(`${relay}/v1/lines/${invite.lineId}/memory/file?path=MEMORY.md`, { headers: invite.auth })).json();
  assert.ok(!raw.body.includes("Team"), "the relay stores ciphertext");
  assert.equal((await b.read("MEMORY.md")).text, "# Team\n- [[contracts/auth-token]]\n");

  // Devin's rule: a write based on an old version is rejected with the current one.
  const seen = await a.read("MEMORY.md");
  await b.edit("MEMORY.md", "# Team", "# Team (nitin + ashish)", "rename");
  const stale = await a.commit([{ path: "MEMORY.md", text: "overwrite", base: seen.version }], "late");
  assert.equal(stale.ok, false);
  assert.match(stale.current[0].text, /nitin \+ ashish/);
  assert.equal((await a.search("nitin ashish"))[0].path, "MEMORY.md");
  assert.deepEqual((await a.history()).map((c) => c.message), ["rename", "start"]);
  assert.equal((await a.read("MEMORY.md", 1)).text.startsWith("# Team\n"), true, "older versions stay readable");

  // Claude Code's memory tools, through the MCP server.
  const home = tmp("mem");
  await run(["join", String(invite), "--as", "ashish"], { home });
  const server = mcp(home, "claude-code.api", tmp("proj"));
  await server.call("initialize", {});
  assert.match((await server.tool("memory")).text, /nitin \+ ashish/);
  assert.match((await server.tool("memory_write", { path: "contracts/auth-token.md", content: "- expires_in is an int (UNIX epoch) [source: talk/x#1; by: nitin; added: 2026-10-12]", message: "auth contract" })).text, /Saved/);
  assert.match((await server.tool("memory_search", { query: "expires_in epoch" })).text, /contracts\/auth-token.md:1/);
  server.close();

  // A dream, against a scripted model: reads, merges, commits once.
  const { createServer } = await import("node:http");
  const script = [
    [{ name: "list_files", args: {} }],
    [{ name: "read_file", args: { path: "MEMORY.md" } }],
    [{ name: "write_file", args: { path: "owners.md", content: "- nitin owns tura-website billing [source: talk/x#1; by: nitin; added: 2026-10-12]" } }],
    [{ name: "write_file", args: { path: "MEMORY.md", content: "# Team (nitin + ashish)\n- [[contracts/auth-token]] · [[owners]]" } }],
    [{ name: "commit", args: { message: "added owners; linked from MEMORY.md" } }],
  ];
  let step = 0;
  const llm = createServer((req, res) => {
    req.resume();
    req.on("end", () => {
      const calls = script[step++] ?? [{ name: "commit", args: { message: "done" } }];
      res.setHeader("content-type", "application/json");
      res.end(JSON.stringify({ choices: [{ message: { content: "", tool_calls: calls.map((c, i) => ({ id: `c${step}${i}`, type: "function", function: { name: c.name, arguments: JSON.stringify(c.args) } })) } }], usage: { cost: 0.001 } }));
    });
  }).listen(0);
  await new Promise((r) => llm.once("listening", r));
  const dreamt = await run(["dream"], { home, env: { OPENROUTER_API_KEY: "test", CHAMBER_LLM_URL: `http://127.0.0.1:${llm.address().port}` } });
  llm.close();
  assert.equal(dreamt.code, 0, dreamt.stdout + dreamt.stderr);
  assert.match(dreamt.stdout, /Dreamt: added owners; linked from MEMORY.md \(2 file\(s\) changed/);
  assert.match((await a.read("owners.md")).text, /nitin owns tura-website billing/);
  assert.equal((await a.history())[0].message, "dream: added owners; linked from MEMORY.md");
});

test("harnesses: Codex, Antigravity and DeepSeek Harness get the tools and hooks, and remove takes them out", async () => {
  const home = tmp("harness");
  const dshHome = tmp("dsh");
  const project = tmp("proj");
  mkdirSync(join(project, ".git", "info"), { recursive: true });
  mkdirSync(join(project, ".codex"));
  writeFileSync(join(project, ".codex", "config.toml"), 'model = "o4"\n');
  mkdirSync(join(project, ".agents"));
  writeFileSync(join(project, ".agents", "mcp_config.json"), JSON.stringify({ mcpServers: { github: { command: "gh-mcp" } } }));
  writeFileSync(join(dshHome, "cordis.patch.yml"), "- id: mine\n  name: x\n");
  await run(["join", String(Invite.create(relay)), "--as", "ashish"], { home });
  const env = { CHAMBER_HARNESSES: "codex,antigravity,dsh", DSH_HOME: dshHome };
  const set = await run(["setup", project, "--no-register"], { home, env });
  assert.equal(set.code, 0, set.stderr);
  assert.match(set.stdout, /Codex\s+as ashish\/codex\.root-line|Codex\s+as ashish\/codex\./);

  const codex = readFileSync(join(project, ".codex", "config.toml"), "utf8");
  assert.match(codex, /^model = "o4"/, "the project's own settings stay");
  assert.match(codex, /\[mcp_servers\.chamber\]/);
  for (const e of ["SessionStart", "UserPromptSubmit", "PostToolUse", "Stop"]) assert.match(codex, new RegExp(`\\[\\[hooks\\.${e}\\]\\]`));
  const ag = JSON.parse(readFileSync(join(project, ".agents", "mcp_config.json"), "utf8"));
  assert.ok(ag.mcpServers.github && ag.mcpServers.chamber, "Antigravity keeps its servers and gets chamber");
  assert.deepEqual(Object.keys(JSON.parse(readFileSync(join(project, ".agents", "hooks.json"), "utf8")).chamber), ["SessionStart", "PreInvocation", "Stop"]);
  const patch = readFileSync(join(dshHome, "cordis.patch.yml"), "utf8");
  assert.match(patch, /- id: mine/);
  assert.match(patch, /id: chamber-mcp[\s\S]*dsh-hooks-claude-code/);
  assert.match(readFileSync(join(project, ".git", "info", "exclude"), "utf8"), /\.codex\/config\.toml/);

  // Their hooks speak each harness's format.
  const agent = /--agent (codex\.[\w.-]+)/.exec(codex)[1];
  const inbox = join(home, "agents", `ashish__${agent}`);
  mkdirSync(inbox, { recursive: true });
  writeFileSync(join(inbox, "inbox.jsonl"), JSON.stringify({ id: "m1", from: "nitin/claude-code.web", to: "@ashish", text: "ping", ts: 1, files: [] }) + "\n");
  const stop = await run(["hook", "--agent", agent, "--format", "codex"], { home, input: JSON.stringify({ hook_event_name: "Stop" }) });
  assert.equal(JSON.parse(stop.stdout).decision, "block", "a waiting message keeps Codex going");
  assert.equal((await run(["hook", "--agent", agent, "--format", "codex"], { home, input: JSON.stringify({ hook_event_name: "Stop" }) })).stdout, "", "and only once");
  writeFileSync(join(inbox, "inbox.jsonl"), JSON.stringify({ id: "m2", from: "nitin/claude-code.web", to: "@ashish", text: "pong", ts: 2, files: [] }) + "\n", { flag: "a" });
  const pre = await run(["hook", "--agent", agent, "--format", "antigravity", "--event", "PreInvocation"], { home, input: "{}" });
  assert.match(JSON.parse(pre.stdout).injectSteps[0].systemMessage.systemMessage, /pong/);

  await run(["remove", project, "--no-register"], { home, env });
  assert.equal(readFileSync(join(project, ".codex", "config.toml"), "utf8"), 'model = "o4"\n');
  assert.equal(JSON.parse(readFileSync(join(project, ".agents", "mcp_config.json"), "utf8")).mcpServers.chamber, undefined);
});
