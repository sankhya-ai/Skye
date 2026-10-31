// The decision ledger: agents propose, teammates rule, approved decisions go on the record.

import assert from "node:assert/strict";
import { test } from "node:test";

import { DecisionLedger, LEDGER_PATH } from "../lib/decisions.mjs";
import { LineTools } from "../lib/mcp.mjs";

// A TeamMemory-shaped stub: whole files in a map, with memory.mjs's read/write/edit/commit
// semantics (a commit based on an old version is refused, as the relay does).
function stubMemory() {
  const files = new Map(); // path -> { version, text }
  return {
    files,
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

test("a proposal is recorded as proposed, never binding, with its provenance", async () => {
  const ledger = new DecisionLedger(stubMemory(), "nitin/claude-code.api");
  const { entry, conflicts } = await ledger.propose({
    title: "Auth token expiry is int epoch seconds",
    rationale: "matches the JWT exp claim",
    affects: "api/auth/schema.py, contracts/auth-token",
    source: "talk/ab12cd34#m9",
  });
  assert.deepEqual(conflicts, []);
  assert.equal(entry.status, "proposed");
  assert.equal(entry["proposed-by"], "nitin/claude-code.api");
  assert.equal(entry.source, "talk/ab12cd34#m9");

  const listed = await ledger.list();
  assert.equal(listed.length, 1);
  assert.equal((await ledger.list({ status: "approved" })).length, 0);
  assert.equal(await ledger.bootstrap(), "- 1 proposed decision open for review (decision tool, action=review); a teammate rules on them (proposals only)");
});

test("a teammate's ruling puts a decision on the record, and new sessions get it", async () => {
  const ledger = new DecisionLedger(stubMemory(), "nitin/claude-code.api");
  const { entry } = await ledger.propose({ title: "Billing moved to Stripe", affects: "billing/" });

  await assert.rejects(() => ledger.decide(entry.id, "maybe", { by: "nitin" }), /approve or reject/);
  const decided = await ledger.decide(entry.id, "approve", { by: "nitin" });
  assert.equal(decided.status, "approved");

  const bootstrap = await ledger.bootstrap();
  assert.match(bootstrap, new RegExp(`\\[${entry.id}\\] Billing moved to Stripe`));
  assert.match(bootstrap, /affects: billing\//);

  // A decided entry stays decided.
  await assert.rejects(() => ledger.decide(entry.id, "reject", { by: "nitin" }), /already approved/);
});

test("a teammate can reject with a reason, and it never reaches new sessions", async () => {
  const ledger = new DecisionLedger(stubMemory(), "nitin/claude-code.api");
  const { entry } = await ledger.propose({ title: "Drop Postgres for SQLite" });
  await ledger.decide(entry.id, "reject", { by: "nitin", reason: "relay already runs on Postgres" });
  const [rejected] = await ledger.list({ status: "rejected" });
  assert.equal(rejected.reason, "relay already runs on Postgres");
  assert.equal(await ledger.bootstrap(), "");
});

test("a proposal overlapping an approved decision comes back flagged", async () => {
  const ledger = new DecisionLedger(stubMemory(), "nitin/claude-code.api");
  const first = await ledger.propose({ title: "Auth token expiry is int epoch seconds", affects: "api/auth/schema.py" });
  await ledger.decide(first.entry.id, "approve", { by: "nitin" });

  const second = await ledger.propose({ title: "Auth token expiry becomes ISO strings", affects: "api/auth/schema.py" });
  assert.deepEqual(second.conflicts.map((c) => c.id), [first.entry.id]);
  const [flagged] = await ledger.list({ status: "proposed" });
  assert.equal(flagged.conflicts, first.entry.id);
});

test("decision_check finds the approved rules constraining the work at hand", async () => {
  const ledger = new DecisionLedger(stubMemory(), "nitin/claude-code.api");
  const auth = await ledger.propose({ title: "Auth token expiry is int epoch seconds", affects: "api/auth/schema.py, contracts/auth-token" });
  await ledger.decide(auth.entry.id, "approve", { by: "nitin" });
  const billing = await ledger.propose({ title: "Billing moved to Stripe", affects: "billing/" });
  await ledger.decide(billing.entry.id, "approve", { by: "nitin" });

  assert.deepEqual((await ledger.check({ paths: ["api/auth/schema.py"] })).map((e) => e.id), [auth.entry.id]);
  assert.deepEqual((await ledger.check({ topic: "changing the auth-token contract" })).map((e) => e.id), [auth.entry.id]);
  assert.deepEqual(await ledger.check({ paths: ["ui/board.tsx"] }), []);
  // Nothing approved yet on a topic returns the rules that do apply, never proposals.
  assert.equal((await ledger.check({})).length, 2);
});

test("the ledger survives a second propose after the file exists", async () => {
  const memory = stubMemory();
  const ledger = new DecisionLedger(memory, "nitin/claude-code.api");
  await ledger.propose({ title: "One" });
  await ledger.propose({ title: "Two" });
  const entries = await ledger.list();
  assert.equal(entries.length, 2);
  assert.notEqual(entries[0].id, entries[1].id);
  assert.match((await memory.read(LEDGER_PATH)).text, /chamber-ledger:v1/);
});

test("other agents review a proposal; reviews advise, they do not rule", async () => {
  const memory = stubMemory();
  const proposer = new DecisionLedger(memory, "nitin/claude-code.api");
  const reviewer = new DecisionLedger(memory, "dev/codex.api");
  const { entry } = await proposer.propose({ title: "Auth token expiry is int epoch seconds" });

  await reviewer.review(entry.id, { stance: "object", reason: "breaks v1 mobile clients" });
  const second = new DecisionLedger(memory, "maya/antigravity.web");
  await second.review(entry.id, { stance: "agree" });

  const [listed] = await proposer.list();
  assert.equal(listed.reviews.length, 2);
  assert.match(listed.reviews[0], /dev\/codex.api · object — breaks v1 mobile clients/);
  assert.match(listed.reviews[1], /maya\/antigravity.web · agree/);
  assert.equal(listed.status, "proposed"); // reviews never rule

  // One review per agent; closed proposals take no reviews.
  await assert.rejects(() => reviewer.review(entry.id, { stance: "agree" }), /already reviewed/);
  await proposer.decide(entry.id, "approve", { by: "nitin" });
  await assert.rejects(() => second.review(entry.id, { stance: "agree" }), /already approved/);
  await assert.rejects(() => second.review("d-19990101-99", { stance: "agree" }), /no decision/);
});

test("bootstrap never silently truncates a ruling: long titles are marked, ids stay", async () => {
  const ledger = new DecisionLedger(stubMemory(), "nitin/claude-code.api");
  const longTitle = "All services must use the shared retry-with-jitter policy for every outbound HTTP call including webhooks";
  const { entry } = await ledger.propose({ title: longTitle, affects: "lib/http.js" });
  await ledger.decide(entry.id, "approve", { by: "nitin" });
  const bootstrap = await ledger.bootstrap();
  assert.match(bootstrap, /…/); // visibly cut
  assert.ok(bootstrap.includes(`[${entry.id}]`)); // route to the full record
  assert.ok(!bootstrap.includes(longTitle)); // but never a partial ruling passed off as whole
  for (const line of bootstrap.split("\n")) assert.ok(line.length <= 140, `line over cap: ${line}`);
});

test("a superseding approval retires the old rule in the same commit", async () => {
  const ledger = new DecisionLedger(stubMemory(), "nitin/claude-code.api");
  const old = await ledger.propose({ title: "Auth token expiry is int epoch seconds", affects: "api/auth/schema.py" });
  await ledger.decide(old.entry.id, "approve", { by: "nitin" });

  // Only an approved decision can be superseded.
  const draft = await ledger.propose({ title: "Retry policy draft" });
  await assert.rejects(() => ledger.propose({ title: "X", supersedes: draft.entry.id }), /only an approved decision can be superseded/);
  await assert.rejects(() => ledger.propose({ title: "X", supersedes: "d-19990101-99" }), /no decision/);

  const next = await ledger.propose({ title: "Auth token expiry becomes milliseconds epoch", affects: "api/auth/schema.py", supersedes: old.entry.id });
  assert.equal(next.entry.supersedes, old.entry.id);

  await ledger.decide(next.entry.id, "approve", { by: "nitin" });
  const entries = await ledger.list();
  const retired = entries.find((e) => e.id === old.entry.id);
  assert.equal(retired.status, "superseded");
  assert.equal(retired["superseded-by"], next.entry.id);

  // Sessions and checks serve only the standing rule.
  const bootstrap = await ledger.bootstrap();
  assert.match(bootstrap, /milliseconds epoch/);
  assert.ok(!bootstrap.includes("int epoch seconds"));
  assert.deepEqual((await ledger.check({ paths: ["api/auth/schema.py"] })).map((e) => e.id), [next.entry.id]);
  assert.deepEqual((await ledger.list({ status: "superseded" })).map((e) => e.id), [old.entry.id]);
});

test("check can include in-flight proposals, clearly separated from approved rules", async () => {
  const ledger = new DecisionLedger(stubMemory(), "nitin/claude-code.api");
  const rule = await ledger.propose({ title: "Billing via Stripe", affects: "billing/" });
  await ledger.decide(rule.entry.id, "approve", { by: "nitin" });
  await ledger.propose({ title: "Billing retries with jitter", affects: "billing/retry.js" });

  assert.deepEqual((await ledger.check({ topic: "billing" })).map((e) => e.status), ["approved"]);
  assert.deepEqual((await ledger.check({ topic: "billing", includeProposals: true })).map((e) => e.status), ["approved", "proposed"]);
});

test("a ruling made while someone else writes is retried on their version, not written over it", async () => {
  const memory = stubMemory();
  const ledger = new DecisionLedger(memory, "nitin/claude-code.api");
  const { entry } = await ledger.propose({ title: "Billing moves to Stripe" });
  // Between the ruling's read and its commit, another agent proposes something.
  const read = memory.read.bind(memory);
  let raced = false;
  memory.read = async (path) => {
    const file = await read(path);
    if (!raced) {
      raced = true;
      await new DecisionLedger({ ...memory, read }, "dev/codex.web").propose({ title: "Invoices keep their PDF for seven years" });
    }
    return file;
  };
  await ledger.decide(entry.id, "approve", { by: "nitin" });
  memory.read = read;
  const all = await ledger.list();
  assert.deepEqual(all.map((e) => [e.title, e.status]), [["Billing moves to Stripe", "approved"], ["Invoices keep their PDF for seven years", "proposed"]]);
});

test("titles are kept literally and common words do not make two decisions collide", async () => {
  const ledger = new DecisionLedger(stubMemory(), "nitin/claude-code.api");
  const { entry } = await ledger.propose({ title: "Prices show $& and $' literally" });
  await ledger.decide(entry.id, "approve", { by: "nitin" });
  assert.equal((await ledger.list())[0].title, "Prices show $& and $' literally");
  const { conflicts } = await ledger.propose({ title: "Sessions should expire with the browser" });
  assert.deepEqual(conflicts, []);
  const { conflicts: real } = await ledger.propose({ title: "Prices rounded to cents" });
  assert.deepEqual(real.map((c) => c.id), [entry.id]);
});

test("agents change the ledger through the decision tool, not by editing the file", async () => {
  const tools = new LineTools({ agent: "nitin/claude-code.api", invite: {} }, null, process.cwd());
  for (const name of ["memory_write", "memory_edit"]) {
    await assert.rejects(tools.call(name, { path: LEDGER_PATH, content: "- status: approved", old: "proposed", new: "approved" }), /kept by the decision tool/);
  }
});
