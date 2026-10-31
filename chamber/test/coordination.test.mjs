import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { CoordinationManager, CoordinationTask } from "../lib/coordination.mjs";
import { LineTools } from "../lib/mcp.mjs";

const tmp = (name) => mkdtempSync(join(tmpdir(), `chamber-coord-${name}-`));

test("CoordinationManager: full lifecycle, transitions and validation", () => {
  const dir = tmp("lifecycle");
  const mgr = new CoordinationManager(dir);

  // 1. Request
  const task = mgr.request({
    op: "ask",
    from: "frontend/codex.api",
    to: "backend/claude-code.api",
    title: "Verify Auth Token Schema",
    description: "Does POST /api/v2/auth require bearer or session cookie?",
    contract: "auth.schema.v2",
    branch: "feat/auth-token",
    files: ["src/api.ts"],
  });

  assert.equal(task.op, "ask");
  assert.equal(task.status, "requested");
  assert.equal(task.from, "frontend/codex.api");
  assert.equal(task.contract, "auth.schema.v2");

  // 2. Claim
  const claimed = mgr.claim(task.id, "backend/claude-code.api");
  assert.equal(claimed.status, "claimed");
  assert.equal(claimed.claimedBy, "backend/claude-code.api");

  // 3. In progress
  const started = mgr.startWork(task.id);
  assert.equal(started.status, "in_progress");

  // 4. Resolve with evidence
  const { task: resolved, alreadyResolved, memoryEntry } = mgr.resolve(task.id, {
    by: "backend/claude-code.api",
    resolution: "Bearer token in Authorization header (RFC 6750). Session cookies are deprecated.",
    evidence: {
      commit: "a1b2c3d4e5f6",
      schema: "auth.schema.v2",
    },
  });

  assert.equal(resolved.status, "resolved");
  assert.equal(alreadyResolved, false);
  assert.ok(memoryEntry.includes("[ASK] Verify Auth Token Schema"));
  assert.ok(memoryEntry.includes("RFC 6750"));
  assert.ok(memoryEntry.includes("Evidence: branch: `feat/auth-token`"));
  assert.ok(memoryEntry.includes("commit: `a1b2c3d4`"));

  // 5. Duplicate resolve idempotency
  const dup = mgr.resolve(task.id, {
    by: "backend/claude-code.api",
    resolution: "Alternative resolution",
  });
  assert.equal(dup.alreadyResolved, true);
  assert.equal(dup.task.resolution, "Bearer token in Authorization header (RFC 6750). Session cookies are deprecated.");
});

test("CoordinationManager: rejection and terminal state enforcement", () => {
  const dir = tmp("reject");
  const mgr = new CoordinationManager(dir);

  const task = mgr.request({
    op: "delegate",
    from: "pm/claude-code.api",
    to: "dev/codex.api",
    title: "Rewrite database in Haskell",
    description: "Immediate migration",
  });

  const rejected = mgr.reject(task.id, { by: "dev/codex.api", reason: "Scope exceeds team guidelines" });
  assert.equal(rejected.status, "rejected");
  assert.equal(rejected.rejectionReason, "Scope exceeds team guidelines");

  // Cannot start work or resolve rejected task
  assert.throws(() => mgr.startWork(task.id), /terminal task/);
});

test("CoordinationManager: handoff across agents", () => {
  const dir = tmp("handoff");
  const mgr = new CoordinationManager(dir);

  const task = mgr.request({
    op: "delegate",
    from: "infra/claude-code.api",
    to: "agent-1/codex.api",
    title: "Implement Redis connection pooling",
  });

  mgr.claim(task.id, "agent-1/codex.api");
  mgr.startWork(task.id);

  const handedOff = mgr.handoff(task.id, {
    fromAgent: "agent-1/codex.api",
    toAgent: "agent-2/claude-code.api",
    summary: "Built connection pool wrapper in pool.rs; tests pass.",
    nextStep: "Wire pool into HTTP server routing.",
    files: ["pool.rs"],
  });

  assert.equal(handedOff.op, "handoff");
  assert.equal(handedOff.status, "requested");
  assert.equal(handedOff.to, "agent-2/claude-code.api");
  assert.ok(handedOff.description.includes("Built connection pool wrapper"));
  assert.ok(handedOff.description.includes("Next step: Wire pool"));
  assert.deepEqual(handedOff.files, ["pool.rs"]);
});

test("CoordinationManager: Context Compiler (Advantage 3)", () => {
  const dir = tmp("context");
  const mgr = new CoordinationManager(dir);

  const memoryMarkdown = [
    "# Team Memory",
    "",
    "- **[CONTRACT] Payment API**: Charges use idempotency key `idemp_` and Stripe v2024. *(Evidence: branch: `feat/payments`, commit: `b7c8d9e0`)*",
    "- **[DECISION] Auth**: We do not store passwords in plain text; Argon2id only.",
    "- **[CONVENTION] Logging**: Use structured JSON logging via pino.",
  ].join("\n");

  const pkg = mgr.compileContextPackage({
    memoryMarkdown,
    symbols: ["Payment", "charge"],
    contracts: ["payment.api"],
  });

  assert.equal(pkg.factsCount, 1);
  assert.ok(pkg.facts[0].includes("Payment API"));
  assert.ok(pkg.facts[0].includes("idempotency key"));

  // Query matching Auth
  const authPkg = mgr.compileContextPackage({
    memoryMarkdown,
    query: "password argon2",
  });
  assert.equal(authPkg.factsCount, 1);
  assert.ok(authPkg.facts[0].includes("Argon2id only"));
});

test("LineTools MCP: coordination tools integration", async () => {
  const home = tmp("tools");
  const project = tmp("project");
  mkdirSync(project, { recursive: true });

  const mockClient = {
    agent: "test/claude-code.api",
    connected: true,
    roster: [],
    channels: [],
    invite: { relay: "http://127.0.0.1", secret: "0".repeat(64) },
    ask: async (to, text, opts) => {
      opts.onSent?.({ id: "receipt-123", to, held: false });
      return { receipt: { id: "receipt-123", to }, reply: { id: "reply-1", from: to, text: "Bearer token verified", ts: Date.now(), files: [] } };
    },
    send: async (to, text) => ({ id: "sent-456", to, held: false }),
  };

  const mockInbox = {
    dir: join(home, "agents", "test__claude-code.api"),
    add: (m) => m,
    claim: () => [],
    sent: () => {},
    find: () => null,
  };
  mkdirSync(mockInbox.dir, { recursive: true });

  const tools = new LineTools(mockClient, mockInbox, project);

  // 1. coordinate_ask
  const askRes = await tools.call("coordinate_ask", {
    to: "backend/codex.api",
    title: "Verify Auth API",
    contract: "auth.v1",
    wait_seconds: 5,
  });
  assert.match(askRes, /Coordination request #/);
  assert.match(askRes, /Answer received:/);

  // 2. coordinate_status
  const statusRes = await tools.call("coordinate_status", {});
  assert.match(statusRes, /Verify Auth API/);
  assert.match(statusRes, /ASK/);

  // 3. coordinate_resolve
  const tasks = tools.coordination.list();
  assert.equal(tasks.length, 1);
  const taskId = tasks[0].id;

  const resolveRes = await tools.call("coordinate_resolve", {
    coord_id: taskId,
    resolution: "Contract confirmed against schema spec v1.2",
    commit_to_memory: false, // Don't write to memory in this mock
  });
  assert.match(resolveRes, /resolved/);

  // 4. context_package
  const ctx = await tools.call("context_package", { query: "Auth" });
  assert.match(ctx, /Compiled Context Package/);
  assert.match(ctx, /Verify Auth API/);

  // 5. coordinate_propose
  const propRes = await tools.call("coordinate_propose", {
    to: "backend/codex.api",
    title: "Switch to Ed25519 tokens",
    proposal: "Use raw Ed25519 signatures for internal microservice RPCs",
    contract: "auth.ed25519.v1",
  });
  assert.match(propRes, /Proposal #/);
  assert.match(propRes, /Switch to Ed25519 tokens/);
  const propTasks = tools.coordination.list({ op: "propose" });
  assert.equal(propTasks.length, 1);
  assert.equal(propTasks[0].status, "requested");
  assert.equal(propTasks[0].contract, "auth.ed25519.v1");
});

test("CoordinationManager: automatic deadline expiration to timed_out", () => {
  const dir = tmp("timeout");
  const mgr = new CoordinationManager(dir);

  // Create task with deadline in the past
  const task = mgr.request({
    op: "delegate",
    from: "agentA/root",
    to: "agentB/root",
    title: "Urgent Hotfix",
    deadlineMs: Date.now() - 1000, // expired 1s ago
  });

  assert.equal(task.status, "requested");

  // Querying task should trigger automated expiration to timed_out
  const fetched = mgr.get(task.id);
  assert.equal(fetched.status, "timed_out");

  // Listing tasks should reflect timed_out
  const list = mgr.list({ status: "timed_out" });
  assert.equal(list.length, 1);
  assert.equal(list[0].id, task.id);

  // Terminal state guards: cannot claim or resolve a timed_out task
  assert.throws(() => mgr.claim(task.id, "agentB/root"), /timed out/);
  assert.throws(() => mgr.resolve(task.id, { by: "agentB/root", resolution: "Done" }), /timed_out/);
});

test("CoordinationManager: importTaskFromMessage for cross-machine ingestion", () => {
  const dir = tmp("import");
  const mgr = new CoordinationManager(dir);

  // Machine B receives message sent by Machine A containing ACP tag
  const incomingMsg = {
    id: "msg-999",
    from: "nitin/claude-code.web",
    to: "ashish/codex.api",
    title: "[DELEGATE] Database Index Migration",
    text: "[ACP:DELEGATE:aabbcc0011223344] Database Index Migration\n\nAdd composite index on (team_id, created_at)",
    branch: "feat/db-indexing",
    ts: Date.now(),
  };

  const imported = mgr.importTaskFromMessage(incomingMsg);
  assert.ok(imported);
  assert.equal(imported.id, "aabbcc0011223344");
  assert.equal(imported.op, "delegate");
  assert.equal(imported.status, "requested");
  assert.equal(imported.from, "nitin/claude-code.web");
  assert.equal(imported.to, "ashish/codex.api");
  assert.equal(imported.branch, "feat/db-indexing");

  // Machine B can now claim and resolve the task directly
  const claimed = mgr.claim("aabbcc0011223344", "ashish/codex.api");
  assert.equal(claimed.status, "claimed");
  assert.equal(claimed.claimedBy, "ashish/codex.api");
});

test("CoordinationManager: concurrency-safe atomic persistence", () => {
  const dir = tmp("concurrency");
  const mgr1 = new CoordinationManager(dir);
  const mgr2 = new CoordinationManager(dir);

  // Process 1 creates task 1
  const t1 = mgr1.request({
    id: "task-001",
    op: "ask",
    from: "user1/agent",
    to: "user2/agent",
    title: "Task 1",
  });

  // Process 2 creates task 2
  const t2 = mgr2.request({
    id: "task-002",
    op: "ask",
    from: "user2/agent",
    to: "user1/agent",
    title: "Task 2",
  });

  // Both tasks must exist without clobbering each other
  assert.ok(mgr1.get("task-001"));
  assert.ok(mgr1.get("task-002"));
  assert.ok(mgr2.get("task-001"));
  assert.ok(mgr2.get("task-002"));
  assert.equal(mgr1.list().length, 2);
  assert.equal(mgr2.list().length, 2);
});

