// The contract registry: producers publish versions, consumers are notified and
// acknowledge — and "told" stays visibly separate from "adapted".

import assert from "node:assert/strict";
import { test } from "node:test";

import { ContractRegistry, REGISTRY_PATH } from "../lib/contracts.mjs";
import { DecisionLedger } from "../lib/decisions.mjs";
import { LineTools } from "../lib/mcp.mjs";

// A TeamMemory-shaped stub: whole files in a map, with memory.mjs's read/write/edit/commit
// semantics (a commit based on an old version is refused, as the relay does).
function stubMemory() {
  const files = new Map(); // path -> { version, text }
  return {
    async commit(changes) {
      for (const c of changes) if ((files.get(c.path)?.version ?? 0) !== c.base) return { ok: false, current: [] };
      for (const c of changes) files.set(c.path, { version: c.base + 1, text: c.text });
      return { ok: true };
    },
    async read(path) {
      const f = files.get(path);
      return f ? { path, ...f } : null;
    },
    async write(path, text) {
      const f = files.get(path) || { version: 0 };
      files.set(path, { version: f.version + 1, text });
      return { ok: true };
    },
    async edit(path, oldText, newText) {
      const f = files.get(path);
      if (!f?.text) return { ok: false, error: `no such file: ${path} (write it instead)` };
      const count = f.text.split(oldText).length - 1;
      if (count !== 1) return { ok: false, error: `the snippet matches ${count} times in ${path}` };
      files.set(path, { version: f.version + 1, text: f.text.replace(oldText, newText) });
      return { ok: true };
    },
  };
}

// A LineClient-shaped fake: records what would have been sent.
function fakeClient(agent) {
  const sent = [];
  return {
    sent,
    invite: null,
    agent,
    roster: [],
    channels: [],
    connected: true,
    async send(to, text, { title } = {}) {
      sent.push({ to, text, title });
      return { id: `m${sent.length}`, to, thread: `m${sent.length}`, held: false };
    },
  };
}

function lineTools(agent) {
  const client = fakeClient(agent);
  const tools = new LineTools(client, null, process.cwd());
  const memory = stubMemory();
  tools.memory = memory;
  tools.contracts = new ContractRegistry(memory, agent);
  tools.decisions = new DecisionLedger(memory, agent);
  return { tools, client, memory };
}

test("a producer registers a contract; names and duplicates are checked", async () => {
  const registry = new ContractRegistry(stubMemory(), "nitin/claude-code.api");
  await assert.rejects(() => registry.register({ name: "Auth Token", title: "x" }), /lowercase letters/);
  await assert.rejects(() => registry.register({ name: "auth-token", title: "" }), /title is required/);

  const { version } = await registry.register({ name: "auth-token", title: "Auth token response", summary: "expires_in: ISO string" });
  assert.equal(version, 1);
  await assert.rejects(() => registry.register({ name: "auth-token", title: "again" }), /already registered/);

  const entry = await registry.get("auth-token");
  assert.equal(entry.version, 1);
  assert.equal(entry.producer, "nitin/claude-code.api");
  assert.deepEqual(entry.consumers, []);
});

test("publish bumps the version, names the consumers to notify, and records the announcement", async () => {
  const registry = new ContractRegistry(stubMemory(), "nitin/claude-code.api");
  await registry.register({ name: "auth-token", title: "Auth token response" });

  const rohan = new ContractRegistry(registry.memory, "rohan/codex.web");
  await rohan.subscribe("auth-token");
  await rohan.subscribe("auth-token"); // idempotent
  assert.equal((await registry.get("auth-token")).consumers.length, 1);

  await assert.rejects(() => registry.publish({ name: "auth-token", summary: "" }), /say what changed/);
  await assert.rejects(() => rohan.publish({ name: "auth-token", summary: "sneaky" }), /only the producer publishes/);
  await assert.rejects(() => registry.publish({ name: "nope", summary: "x" }), /no contract/);

  const { entry, version, notify } = await registry.publish({ name: "auth-token", summary: "expires_at_epoch_seconds added alongside the ISO field", decision: "d-20261029-02" });
  assert.equal(version, 2);
  assert.deepEqual(notify, ["rohan/codex.web"]);
  assert.equal(entry.decision, "d-20261029-02");
  assert.deepEqual(entry.notices.at(-1).to, ["rohan/codex.web"]);
  assert.equal(entry.notices.at(-1).version, 2);
});

test("acks keep receipt (seen) separate from adaptation (adapted), and objections need a reason", async () => {
  const memory = stubMemory();
  const producer = new ContractRegistry(memory, "nitin/claude-code.api");
  await producer.register({ name: "auth-token", title: "Auth token response" });
  const consumer = new ContractRegistry(memory, "rohan/codex.web");
  await consumer.subscribe("auth-token");

  await assert.rejects(() => consumer.ack("auth-token", { stance: "maybe" }), /seen, adapted or object/);
  await assert.rejects(() => consumer.ack("auth-token", { stance: "object" }), /needs a reason/);
  await assert.rejects(() => consumer.ack("auth-token", { stance: "seen", version: 9 }), /cannot ack v9/);

  const outsider = new ContractRegistry(memory, "dev/codex.api");
  await assert.rejects(() => outsider.ack("auth-token", { stance: "seen" }), /does not consume/);

  await consumer.ack("auth-token", { stance: "seen" });
  await assert.rejects(() => consumer.ack("auth-token", { stance: "seen" }), /already recorded seen/);
  await consumer.ack("auth-token", { stance: "adapted", note: "contract tests pass both rollout orders" });
  await assert.rejects(() => consumer.ack("auth-token", { stance: "seen" }), /would overwrite evidence/);

  const { consumers } = await producer.status("auth-token");
  assert.equal(consumers.length, 1);
  assert.equal(consumers[0].state, "adapted");
  assert.equal(consumers[0].ack.note, "contract tests pass both rollout orders");
});

test("a new version makes every consumer outstanding again; session start lists only those", async () => {
  const memory = stubMemory();
  const producer = new ContractRegistry(memory, "nitin/claude-code.api");
  await producer.register({ name: "auth-token", title: "Auth token response" });
  const consumer = new ContractRegistry(memory, "rohan/codex.web");
  await consumer.subscribe("auth-token");
  await consumer.ack("auth-token", { stance: "adapted", note: "v1 consumer migrated; unit tests green" });

  assert.deepEqual(await producer.pendingFor("rohan/codex.web"), []);
  assert.equal(await producer.bootstrapFor("rohan/codex.web"), "");

  await producer.publish({ name: "auth-token", summary: "epoch seconds added" });

  let { consumers } = await producer.status("auth-token");
  assert.equal(consumers[0].state, "outstanding");
  assert.equal(consumers[0].lastVersion, 1);

  const bootstrap = await producer.bootstrapFor("rohan/codex.web");
  assert.match(bootstrap, /auth-token v2: epoch seconds added/);
  assert.match(bootstrap, /nitin\/claude-code\.api/);
  assert.deepEqual(await producer.pendingFor("dev/codex.api"), []); // not a consumer

  await consumer.ack("auth-token", { stance: "adapted", note: "web/auth.ts prefers seconds, ISO fallback; tests pass" });
  ({ consumers } = await producer.status("auth-token"));
  assert.equal(consumers[0].state, "adapted");
  assert.equal(await producer.bootstrapFor("rohan/codex.web"), "");
});

test("an objection is a standing state, not a pending one", async () => {
  const memory = stubMemory();
  const producer = new ContractRegistry(memory, "nitin/claude-code.api");
  await producer.register({ name: "auth-token", title: "Auth token response" });
  const consumer = new ContractRegistry(memory, "rohan/codex.web");
  await consumer.subscribe("auth-token");

  await consumer.ack("auth-token", { stance: "object", note: "dropping the ISO field breaks every v1 web client" });
  const { consumers } = await producer.status("auth-token");
  assert.equal(consumers[0].state, "object");
  assert.deepEqual(await producer.pendingFor("rohan/codex.web"), []); // resolved to an exception, not nagged
});

test("unsubscribe stops notices; past acks stay on the record", async () => {
  const memory = stubMemory();
  const producer = new ContractRegistry(memory, "nitin/claude-code.api");
  await producer.register({ name: "auth-token", title: "Auth token response" });
  const consumer = new ContractRegistry(memory, "rohan/codex.web");
  await consumer.subscribe("auth-token");
  await consumer.ack("auth-token", { stance: "seen" });
  const { removed } = await consumer.unsubscribe("auth-token");
  assert.equal(removed, true);

  const { notify } = await producer.publish({ name: "auth-token", summary: "v2" });
  assert.deepEqual(notify, []);
  const entry = await producer.get("auth-token");
  assert.equal(entry.acks.length, 1); // history kept
});

test("through the MCP tools: publish notifies every subscriber, and outcomes reach the producer", async () => {
  const { tools: be, client: beClient } = lineTools("nitin/claude-code.api");
  assert.match(await be.call("contract", { action: "register", name: "auth-token", title: "Auth token response", summary: "expires_in ISO" }), /Registered auth-token \(v1\)/);

  const { tools: fe, client: feClient } = lineTools("rohan/codex.web");
  fe.memory = be.memory; // the same shared memory
  fe.contracts = new ContractRegistry(be.memory, "rohan/codex.web");
  assert.match(await fe.call("contract", { action: "subscribe", name: "auth-token" }), /Subscribed to auth-token/);

  // BE publishes: the consumer gets a short, self-sufficient notice through the normal message path.
  const published = await be.call("contract", { action: "publish", name: "auth-token", summary: "expires_at_epoch_seconds added alongside the ISO field", decision: "d-20261029-02" });
  assert.match(published, /Published auth-token v2/);
  assert.match(published, /Notified 1 consumer/);
  assert.equal(beClient.sent.length, 1);
  assert.equal(beClient.sent[0].to, "rohan/codex.web");
  assert.equal(beClient.sent[0].title, "Changed: expires_at_epoch_seconds added alongside the ISO field (Auth token response v2)");
  assert.match(beClient.sent[0].text, /action=ack name=auth-token version=2 stance=adapted/);
  assert.match(beClient.sent[0].text, /decision d-20261029-02/);

  // seen is receipt only: recorded, but the producer is not pinged for it.
  assert.match(await fe.call("contract", { action: "ack", name: "auth-token", stance: "seen" }), /seen/);
  assert.equal(feClient.sent.length, 0, "a seen ack stays quiet");

  // adapted carries evidence and tells the producer.
  assert.match(await fe.call("contract", { action: "ack", name: "auth-token", stance: "adapted", note: "web/auth.ts prefers seconds with ISO fallback; both rollout orders pass" }), /producer has been told/);
  assert.equal(feClient.sent.length, 1);
  assert.equal(feClient.sent[0].to, "nitin/claude-code.api");
  assert.equal(feClient.sent[0].title, "rohan/codex.web updated for Auth token response (v2)");
  assert.match(feClient.sent[0].text, /adapted to auth-token v2/);
  assert.match(feClient.sent[0].text, /both rollout orders pass/);

  const status = await be.call("contract", { action: "status", name: "auth-token" });
  assert.match(status, /auth-token v2/);
  assert.match(status, /rohan\/codex\.web: ADAPTED — web\/auth\.ts prefers seconds/);
});

test("through the MCP tools: an objection requires a reason and reaches the producer", async () => {
  const { tools: be, client: beClient } = lineTools("nitin/claude-code.api");
  await be.call("contract", { action: "register", name: "auth-token", title: "Auth token response" });
  const { tools: fe, client: feClient } = lineTools("rohan/codex.web");
  fe.memory = be.memory;
  fe.contracts = new ContractRegistry(be.memory, "rohan/codex.web");
  await fe.call("contract", { action: "subscribe", name: "auth-token" });

  const refused = await be.call("contract", { action: "publish", name: "auth-token", summary: " " }).catch((e) => e);
  assert.match(String(refused.message), /say what changed/);

  await be.call("contract", { action: "publish", name: "auth-token", summary: "ISO field removed, epoch seconds only" });
  assert.equal(beClient.sent.length, 1);

  const objection = await fe.call("contract", { action: "ack", name: "auth-token", stance: "object", note: "breaks every v1 web client; propose a transition instead" });
  assert.match(objection, /objection is on the record/);
  assert.equal(feClient.sent.length, 1);
  assert.equal(feClient.sent[0].to, "nitin/claude-code.api");
  assert.equal(feClient.sent[0].title, "rohan/codex.web blocked: breaks every v1 web client; propose a transition instead (Auth token response v2)");
  assert.match(feClient.sent[0].text, /objects to auth-token v2/);
  assert.match(feClient.sent[0].text, /propose a transition/);
});

test("a proposal affecting a registered contract points at the publish loop", async () => {
  const { tools: be } = lineTools("nitin/claude-code.api");
  await be.call("contract", { action: "register", name: "auth-token", title: "Auth token response" });
  const { tools: fe, client: feClient } = lineTools("rohan/codex.web");
  fe.memory = be.memory;
  fe.contracts = new ContractRegistry(be.memory, "rohan/codex.web");
  await fe.call("contract", { action: "subscribe", name: "auth-token" });

  const proposed = await be.call("decision", { action: "propose", title: "Add epoch seconds to the auth token response", affects: "api/auth/schema.py, contracts/auth-token" });
  assert.match(proposed, /auth-token has 1 subscribed consumer \(rohan\/codex\.web\)/);
  assert.match(proposed, /contract action=publish name=auth-token/);
});

// ---- Hardening: the five gaps found by running the registry with three independent roles ----

test("adapted needs a substantive evidence note; it is recorded as a claim, and seen/object are unaffected", async () => {
  const memory = stubMemory();
  const producer = new ContractRegistry(memory, "nitin/claude-code.api");
  await producer.register({ name: "auth-token", title: "Auth token response" });
  const consumer = new ContractRegistry(memory, "rohan/codex.web");
  await consumer.subscribe("auth-token");
  for (const note of [undefined, "", "   ", "done", "ok tests pass"]) {
    await assert.rejects(() => consumer.ack("auth-token", { stance: "adapted", note }), /evidence note of at least 20/);
  }
  assert.equal((await producer.status("auth-token")).consumers[0].state, "outstanding", "a rejected ack records nothing");
  await consumer.ack("auth-token", { stance: "seen" }); // receipt needs no evidence
  await consumer.ack("auth-token", { stance: "adapted", note: "auth.mjs prefers epoch seconds; node --test 10/10" });
  assert.equal((await producer.status("auth-token")).consumers[0].state, "adapted");
});

test("seen keeps the session-start reminder until the consumer adapts or objects", async () => {
  const memory = stubMemory();
  const producer = new ContractRegistry(memory, "nitin/claude-code.api");
  await producer.register({ name: "auth-token", title: "Auth token response" });
  const consumer = new ContractRegistry(memory, "rohan/codex.web");
  await consumer.subscribe("auth-token");
  await producer.publish({ name: "auth-token", summary: "epoch seconds added" });

  await consumer.ack("auth-token", { stance: "seen" });
  assert.equal((await producer.status("auth-token")).consumers[0].state, "seen", "status still says seen");
  const nudge = await producer.bootstrapFor("rohan/codex.web");
  assert.match(nudge, /auth-token v2/);
  assert.match(nudge, /acked seen: not adapted yet/);

  await consumer.ack("auth-token", { stance: "adapted", note: "auth.mjs prefers epoch seconds; node --test 10/10" });
  assert.equal(await producer.bootstrapFor("rohan/codex.web"), "");

  // an objection also resolves the reminder for the consumer, and surfaces to the producer
  const jobs = new ContractRegistry(memory, "dev/codex.jobs");
  await jobs.subscribe("auth-token");
  await jobs.ack("auth-token", { stance: "seen" });
  await jobs.ack("auth-token", { stance: "object", note: "quarterly deploy cannot take this" });
  assert.equal(await jobs.bootstrapFor("dev/codex.jobs"), "");
  assert.match(await producer.bootstrapFor("nitin/claude-code.api"), /OPEN OBJECTION on auth-token v2 from dev\/codex\.jobs: quarterly deploy/);
});

test("publish with nobody subscribed warns that undeclared dependents are invisible", async () => {
  const { tools: be, client } = lineTools("nitin/claude-code.api");
  await be.call("contract", { action: "register", name: "auth-token", title: "Auth token response" });
  const out = await be.call("contract", { action: "publish", name: "auth-token", summary: "epoch seconds added" });
  assert.match(out, /Published auth-token v2/);
  assert.match(out, /WARNING: no consumers are subscribed/);
  assert.match(out, /cannot see who else depends/);
  assert.match(out, /contract action=subscribe name=auth-token/);
  assert.equal(client.sent.length, 0);
  assert.match(await be.call("contract", { action: "status", name: "auth-token" }), /cannot see undeclared dependents/);
});

test("an open objection is warned about on the next publish, passed to consumers, and does not block it", async () => {
  const { tools: be, client: beClient } = lineTools("nitin/claude-code.api");
  await be.call("contract", { action: "register", name: "auth-token", title: "Auth token response" });
  const mk = (agent) => {
    const t = lineTools(agent);
    t.tools.memory = be.memory;
    t.tools.contracts = new ContractRegistry(be.memory, agent);
    return t;
  };
  const jobs = mk("dev/codex.jobs");
  const web = mk("rohan/codex.web");
  await jobs.tools.call("contract", { action: "subscribe", name: "auth-token" });
  await web.tools.call("contract", { action: "subscribe", name: "auth-token" });
  await be.call("contract", { action: "publish", name: "auth-token", summary: "v2 additive" });
  await jobs.tools.call("contract", { action: "ack", name: "auth-token", stance: "object", note: "quarterly deploy cannot take this" });

  assert.match(await be.call("contract", { action: "status", name: "auth-token" }), /OPEN OBJECTION: dev\/codex\.jobs \(v2\): quarterly deploy/);
  beClient.sent.length = 0;
  const out = await be.call("contract", { action: "publish", name: "auth-token", summary: "v3 removes ISO field" });
  assert.match(out, /Published auth-token v3/, "not a gate");
  assert.match(out, /WARNING: 1 open objection not resolved by this publish: dev\/codex\.jobs \(v2\): quarterly deploy/);
  assert.equal(beClient.sent.length, 2);
  for (const m of beClient.sent) assert.match(m.text, /Open objection on this contract: dev\/codex\.jobs/);

  // once the objector supersedes it with an adapted ack at the newer version, the warning clears
  await jobs.tools.call("contract", { action: "ack", name: "auth-token", stance: "adapted", note: "jobs client reads new field; node --test 6/6" });
  assert.doesNotMatch(await be.call("contract", { action: "publish", name: "auth-token", summary: "v4 cleanup" }), /open objection/i);
});

test("context_package includes registry contracts by name or by matching words; unrelated ones stay out", async () => {
  const { tools: be } = lineTools("nitin/claude-code.api");
  await be.call("contract", { action: "register", name: "auth-token", title: "Auth token response", summary: "expires_at ISO" });
  await be.call("contract", { action: "register", name: "billing-webhook", title: "Billing webhook payload" });
  const web = lineTools("rohan/codex.web");
  web.tools.memory = be.memory;
  web.tools.contracts = new ContractRegistry(be.memory, "rohan/codex.web");
  await web.tools.call("contract", { action: "subscribe", name: "auth-token" });
  await be.call("contract", { action: "publish", name: "auth-token", summary: "epoch seconds added" });
  await web.tools.call("contract", { action: "ack", name: "auth-token", stance: "object", note: "cannot ship this quarter" });

  const named = await web.tools.call("context_package", { contracts: ["auth-token"] });
  assert.match(named, /contract auth-token\*\* v2: epoch seconds added/);
  assert.match(named, /your standing: object/);
  assert.match(named, /Open objection from rohan\/codex\.web: cannot ship this quarter/);
  assert.match(named, /1 subscribed consumer/);
  assert.doesNotMatch(named, /billing-webhook/);

  const byQuery = await web.tools.call("context_package", { query: "billing" });
  assert.match(byQuery, /contract billing-webhook/);
  assert.doesNotMatch(byQuery, /contract auth-token/);
  assert.match(byQuery, /Compiled Context Package \(1 source-backed facts\)/);
});

test("installer packaging: every relative import of a shipped client file is itself shipped", async () => {
  const { readFileSync, readdirSync } = await import("node:fs");
  const install = readFileSync(new URL("../relay/src/install.js", import.meta.url), "utf8");
  const shipped = new Set([...install.matchAll(/^\s+"((?:lib|bin)\/[^"]+)":/gm)].map((m) => m[1]));
  assert.ok(shipped.has("lib/contracts.mjs") && shipped.has("lib/decisions.mjs"));
  for (const file of shipped) {
    const src = readFileSync(new URL(`../${file}`, import.meta.url), "utf8");
    for (const m of src.matchAll(/(?:from\s+|import\()\s*"(\.{1,2}\/[^"]+\.mjs)"/g)) {
      const target = new URL(m[1], new URL(`../${file}`, import.meta.url)).pathname.replace(/^.*\/chamber\/|^.*?\/(lib|bin)\//, (x, d) => (d ? `${d}/` : ""));
      const rel = target.startsWith("lib/") || target.startsWith("bin/") ? target : null;
      assert.ok(rel ? shipped.has(rel) : true, `${file} imports ${m[1]} which the installer does not ship`);
    }
  }
  // and every lib file is either shipped or deliberately server/desktop-only
  const libs = readdirSync(new URL("../lib", import.meta.url)).filter((f) => f.endsWith(".mjs")).map((f) => `lib/${f}`);
  assert.ok(libs.filter((f) => !shipped.has(f)).length <= 2, "unshipped lib files grew");
});

test("a consumer subscribing while the producer publishes is kept, and told", async () => {
  const memory = stubMemory();
  const producer = new ContractRegistry(memory, "nitin/claude-code.api");
  await producer.register({ name: "auth-token", title: "Auth token response" });
  await new ContractRegistry(memory, "rohan/codex.web").subscribe("auth-token");
  await producer.publish({ name: "auth-token", summary: "expires_at added" });
  // Between the next publish's read and its commit, a second consumer subscribes.
  const read = memory.read.bind(memory);
  let raced = false;
  memory.read = async (path) => {
    const file = await read(path);
    if (!raced) {
      raced = true;
      await new ContractRegistry({ ...memory, read }, "dev/codex.jobs").subscribe("auth-token");
    }
    return file;
  };
  const { version, notify } = await producer.publish({ name: "auth-token", summary: "expires_at_epoch_seconds added" });
  memory.read = read;
  assert.equal(version, 3);
  assert.deepEqual(notify, ["rohan/codex.web", "dev/codex.jobs"]);
  assert.deepEqual((await producer.get("auth-token")).consumers.map((c) => c.agent), ["rohan/codex.web", "dev/codex.jobs"]);
});


test("contract subjects normalize whitespace, use readable labels and truncate at whole words", async () => {
  const { tools: be, client } = lineTools("api/claude");
  await be.call("contract", { action: "register", name: "auth-token", title: "Auth token response" });
  const consumer = new ContractRegistry(be.memory, "web/codex");
  await consumer.subscribe("auth-token");
  await be.call("contract", { action: "publish", name: "auth-token", summary: "  Token expiry\nformat changed\tfor older clients  " });
  assert.equal(client.sent.at(-1).title, "Changed: Token expiry format changed for older clients (Auth token response v2)");
  const summary = "Older builds need the old field " + "during the transition ".repeat(20);
  await be.call("contract", { action: "publish", name: "auth-token", summary });
  const title = client.sent.at(-1).title;
  assert.ok(Array.from(title).length <= 180);
  assert.match(title, /^Changed: Older builds need the old field /);
  assert.match(title, /… \(Auth token response v3\)$/);
  const excerpt = title.slice("Changed: ".length, title.indexOf("…"));
  assert.ok(summary.startsWith(excerpt + " "), "truncate between words, not inside one");
  assert.ok(client.sent.at(-1).text.includes(summary.trim()), "full summary remains in body");
});

test("long Unicode contract subjects keep version, stance and intact code points", async () => {
  const { tools: be, client } = lineTools("api/claude");
  await be.call("contract", { action: "register", name: "auth-token", title: "🔑".repeat(80) });
  const { tools: fe, client: feClient } = lineTools("web/" + "🚀".repeat(80));
  fe.contracts = new ContractRegistry(be.memory, fe.client.agent);
  await fe.call("contract", { action: "subscribe", name: "auth-token" });
  await be.call("contract", { action: "publish", name: "auth-token", summary: "🧩".repeat(300) });
  await fe.call("contract", { action: "ack", name: "auth-token", stance: "object", note: "🛑".repeat(300) });
  for (const title of [client.sent.at(-1).title, feClient.sent.at(-1).title]) {
    assert.ok(Array.from(title).length <= 180);
    assert.match(title, /v2\)$/);
    assert.ok(title.isWellFormed());
    assert.doesNotMatch(title, /\[contract\]/);
  }
  assert.match(feClient.sent.at(-1).title, / blocked: /);
  await fe.call("contract", { action: "ack", name: "auth-token", stance: "adapted", note: "Consumer updated; all fixture tests passed" });
  assert.match(feClient.sent.at(-1).title, / updated for .*\(v2\)$/);
  assert.ok(feClient.sent.at(-1).title.isWellFormed());
});

test("a short word before a long identifier doesn't shrink the subject to that word", async () => {
  const { tools: be, client } = lineTools("api/claude");
  await be.call("contract", { action: "register", name: "auth-token", title: "Auth token response" });
  await new ContractRegistry(be.memory, "web/codex").subscribe("auth-token");
  await be.call("contract", { action: "publish", name: "auth-token", summary: `renamed ${"x".repeat(400)}` });
  const title = client.sent.at(-1).title;
  assert.ok(Array.from(title).length <= 180);
  assert.ok(Array.from(title).length > 150, `kept most of the room: ${title}`);
  assert.match(title, /^Changed: renamed x+… \(Auth token response v2\)$/);
});
