import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { Inbox } from "../lib/inbox.mjs";
import { LineClient } from "../lib/client.mjs";
import { LineTools } from "../lib/mcp.mjs";
import { ApprovalGateManager, ChamberRunner, signWorkflow, verifyWorkflow } from "../lib/runner.mjs";
import { newAdminKey } from "../lib/admin.mjs";
import { Invite, LineError } from "../lib/protocol.mjs";

test("Security 1: Attachment path traversal via message ID or filename is blocked", () => {
  const tmp = mkdtempSync(join(tmpdir(), "chamber-sec-inbox-"));
  try {
    const inbox = new Inbox("testuser/agent");
    inbox.dir = tmp;
    inbox.path = join(tmp, "inbox.jsonl");

    // Traversal via message ID
    const traversalMsg = {
      id: "../../../outside_dir",
      from: "attacker/agent",
      to: "testuser/agent",
      title: "Attack",
      text: "Payload",
      ts: Date.now(),
      files: [{ name: "payload.sh", data: Buffer.from("echo hacked") }],
    };

    // Should either reject or sanitize into a safe local directory inside inbox.dir/files
    inbox.add(traversalMsg);

    // Assert that NO file was created outside inbox.dir
    assert.equal(existsSync(join(tmp, "..", "outside_dir")), false);
    assert.equal(existsSync(join(tmp, "..", "payload.sh")), false);

    // Traversal via filename
    const filenameTraversalMsg = {
      id: "0123456789abcdef0123456789abcdef",
      from: "attacker/agent",
      to: "testuser/agent",
      title: "Attack 2",
      text: "Payload 2",
      ts: Date.now(),
      files: [{ name: "../../escape.txt", data: Buffer.from("escape") }],
    };

    inbox.add(filenameTraversalMsg);

    assert.equal(existsSync(join(tmp, "escape.txt")), false);
    assert.equal(existsSync(join(tmp, "..", "escape.txt")), false);

    // Verify file stayed within designated directory
    const savedFile = join(tmp, "files", "0123456789abcdef0123456789abcdef", "escape.txt");
    assert.equal(existsSync(savedFile), true);
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});

test("Security 2: MCP file attachments are strictly confined to project root", async () => {
  const projectDir = mkdtempSync(join(tmpdir(), "chamber-sec-project-"));
  const outsideDir = mkdtempSync(join(tmpdir(), "chamber-sec-outside-"));

  try {
    writeFileSync(join(projectDir, "inside.txt"), "hello inside");
    writeFileSync(join(outsideDir, "secret.key"), "SUPER_SECRET_KEY");

    let sentFiles = [];
    const clientMock = {
      agent: "ashish/claude-code",
      invite: { relay: "http://example.com", secret: "0123456789abcdef", auth: {} },
      roster: [],
      channels: [],
      async ask(to, msg, opts = {}) {
        sentFiles = opts.files || [];
        opts.onSent?.({ to, id: "msg-1" });
        return { receipt: { to }, reply: null };
      },
    };

    const inbox = new Inbox("ashish/claude-code");
    inbox.dir = projectDir;
    inbox.path = join(projectDir, "inbox.jsonl");
    inbox.sentPath = join(projectDir, "sent.jsonl");

    const tools = new LineTools(clientMock, inbox, projectDir);

    // 1. Legitimate project file works
    await tools.call("ask", { to: "@nitin", message: "hello", files: ["inside.txt"] });
    assert.equal(sentFiles.length, 1);
    assert.equal(sentFiles[0].name, "inside.txt");

    // 2. Relative traversal outside project must be rejected
    await assert.rejects(
      tools.call("ask", { to: "@nitin", message: "hello", files: ["../outside/secret.key"] }),
      (err) => err instanceof LineError && /inside the project folder/.test(err.message)
    );

    // 3. Absolute path outside project must be rejected
    await assert.rejects(
      tools.call("ask", { to: "@nitin", message: "hello", files: [join(outsideDir, "secret.key")] }),
      (err) => err instanceof LineError && /inside the project folder/.test(err.message)
    );

    // 4. Symlink escaping project root must be rejected
    try {
      symlinkSync(join(outsideDir, "secret.key"), join(projectDir, "symlink_escape.txt"));
      await assert.rejects(
        tools.call("ask", { to: "@nitin", message: "hello", files: ["symlink_escape.txt"] }),
        (err) => err instanceof LineError && /outside project folder/.test(err.message)
      );
    } catch (e) {
      if (e.code !== "EPERM") throw e; // Symlink permission on some platforms
    }
  } finally {
    rmSync(projectDir, { recursive: true, force: true });
    rmSync(outsideDir, { recursive: true, force: true });
  }
});

test("Security 3: Remote approval gates leak NO plaintext to relay", async () => {
  const tmp = mkdtempSync(join(tmpdir(), "chamber-sec-gate-"));
  const mgr = new ApprovalGateManager(tmp);

  try {
    let capturedBody = null;
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts = {}) => {
      if (opts.body) {
        capturedBody = JSON.parse(opts.body);
      }
      return { ok: true, status: 200, json: async () => ({ ok: true, status: "pending" }) };
    };

    await mgr.createGate({
      description: "DROP TABLE users; -- Super sensitive SQL",
      action: "database:drop_all",
      requestedBy: "agent-007",
      relayUrl: "https://relay.chamber.dev",
      lineId: "line1234567890abcdef1234567890ab",
      authToken: "auth-token-xyz",
    });

    assert.ok(capturedBody, "Registration body was sent to relay");
    // Verify ciphertext is present
    assert.ok(capturedBody.sealed, "Ciphertext sealed payload must be present");

    // CRITICAL: Plaintext description, action, and requester must NOT be sent to relay!
    assert.equal(capturedBody.description, undefined, "Plaintext description must NOT be sent to relay");
    assert.equal(capturedBody.action, undefined, "Plaintext action must NOT be sent to relay");
    assert.equal(capturedBody.requestedBy, undefined, "Plaintext requestedBy must NOT be sent to relay");

    globalThis.fetch = originalFetch;
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});

test("Security 4: Workflow signing binds strictly to lineId and admin private key", async () => {
  const adminA = newAdminKey();
  const adminB = newAdminKey();
  const lineA = "lineaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
  const lineB = "linebbbbbbbbbbbbbbbbbbbbbbbbbbbb";

  const workflow = "name: deploy\non: patch\nrun: echo deploying\n";

  // Sign with Admin A for Line A
  const signed = signWorkflow(workflow, adminA.private, { lineId: lineA });
  assert.ok(signed.startsWith("# chamber-signature:"));

  // 1. Verify with correct admin public key and matching lineId
  const validCheck = verifyWorkflow(signed, adminA.public, { lineId: lineA });
  assert.equal(validCheck.valid, true);

  // 2. Reject if verified against different Chamber lineId
  const lineMismatchCheck = verifyWorkflow(signed, adminA.public, { lineId: lineB });
  assert.equal(lineMismatchCheck.valid, false);
  assert.match(lineMismatchCheck.error, /line mismatch/i);

  // 3. Reject if verified against a different Admin public key
  const adminMismatchCheck = verifyWorkflow(signed, adminB.public, { lineId: lineA });
  assert.equal(adminMismatchCheck.valid, false);

  // 4. Reject if workflow content was tampered after signing
  const tampered = signed.replace("echo deploying", "echo hacked");
  const tamperCheck = verifyWorkflow(tampered, adminA.public, { lineId: lineA });
  assert.equal(tamperCheck.valid, false);
});

test("Security 5: Workflow runner fails closed on unsupported commands and errors", async () => {
  const tmp = mkdtempSync(join(tmpdir(), "chamber-sec-runner-"));
  try {
    const runner = new ChamberRunner({
      owner: "ashish",
      invite: Invite.create("http://example.com"),
    });
    runner.cwd = tmp;

    // 1. Workflow with echo succeeds
    const echoRes = await runner.testExecuteSteps("name: t\non: p\nrun: echo hello world\n");
    assert.equal(echoRes.ok, true);
    assert.equal(echoRes.output, "hello world");

    // 2. Workflow with invalid command fails closed (does NOT report fake success!)
    const failRes = await runner.testExecuteSteps("name: t\non: p\nrun: this_command_does_not_exist_at_all_12345\n");
    assert.equal(failRes.ok, false);
    assert.match(failRes.error, /Step failed|not found/i);

    // 3. Workflow with empty steps fails closed
    const emptyRes = await runner.testExecuteSteps("name: t\non: p\n# no run commands\n");
    assert.equal(emptyRes.ok, false);
    assert.match(emptyRes.error, /no executable steps/i);
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});

test("Security 6: Cursor persistence commits ONLY after durable processing succeeds", async () => {
  const tmp = mkdtempSync(join(tmpdir(), "chamber-sec-cursor-"));
  const cursorFile = join(tmp, "cursor.json");

  try {
    const invite = Invite.create("http://example.com");
    await invite.ready();

    let shouldFail = true;
    const client = new LineClient(invite, "testuser/agent", {
      cursorPath: cursorFile,
      onMessage: async (msg) => {
        if (shouldFail) {
          throw new Error("Simulated disk write failure or database lock");
        }
      },
    });

    assert.equal(client.cursor, 0);

    // Seal a test message
    const msgId = "0123456789abcdef0123456789abcdef";
    const body = await invite.seal({ text: "test" }, { sender: "sender/agent", to: "testuser/agent", id: msgId });
    const frame = { t: "msg", seq: 105, id: msgId, from: "sender/agent", to: "testuser/agent", body, ts: Date.now() };

    // 1. Process message when disk write fails: cursor must NOT advance!
    await client.testFrame(frame);
    assert.equal(client.cursor, 0, "Cursor must not advance if onMessage fails");
    assert.equal(existsSync(cursorFile), false, "Cursor file must not be written if processing failed");

    // 2. Process message when disk write succeeds: cursor commits and persists!
    shouldFail = false;
    await client.testFrame(frame);
    assert.equal(client.cursor, 105, "Cursor advances after durable success");
    assert.equal(existsSync(cursorFile), true, "Cursor file is persisted");
    const saved = JSON.parse(readFileSync(cursorFile, "utf8"));
    assert.equal(saved.cursor, 105);
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});

test("Security 7: Local board mutation routes strictly reject missing or mismatched tokens", async () => {
  const { createServer } = await import("node:http");
  const { handler } = await import("../lib/server.mjs");
  const sessionToken = "chamber-secret-auth-token-12345";
  const live = { sessionToken };
  let sHandler;
  const server = createServer((req, res) => sHandler(req, res));
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  sHandler = handler(live, port);

  try {
    const url = `http://127.0.0.1:${port}/api/board/onboarding/join`;

    // 1. Missing token -> 403 Forbidden
    const resNoToken = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ invite: "invalid" }),
    });
    assert.equal(resNoToken.status, 403);
    const dataNoToken = await resNoToken.json();
    assert.match(dataNoToken.detail, /Invalid or missing session token/i);

    // 2. Mismatched/incorrect token -> 403 Forbidden
    const resBadToken = await fetch(url, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-chamber-token": "wrong-token-value",
      },
      body: JSON.stringify({ invite: "invalid" }),
    });
    assert.equal(resBadToken.status, 403);
    const dataBadToken = await resBadToken.json();
    assert.match(dataBadToken.detail, /Invalid or missing session token/i);

    // 3. Valid token passes security check (proceeds to handler logic, which returns 400 for bad input)
    const resValidToken = await fetch(url, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-chamber-token": sessionToken,
      },
      body: JSON.stringify({ invite: "" }),
    });
    assert.equal(resValidToken.status, 400); // 400 from handler, NOT 403
  } finally {
    server.close();
  }
});

test("Security 8: Coordination task state machine strictly prohibits unauthorized transitions", async () => {
  const { CoordinationManager } = await import("../lib/coordination.mjs");
  const tmp = mkdtempSync(join(tmpdir(), "chamber-sec-coord-"));

  try {
    const mgr = new CoordinationManager(tmp);

    // 1. Create a requested task
    const task = mgr.request({
      op: "review",
      from: "alice/agent",
      to: "bob/agent",
      title: "Security PR Review",
      description: "Please audit crypto signatures",
    });
    assert.equal(task.status, "requested");

    // 2. Claim and start work
    mgr.claim(task.id, "bob/agent");
    assert.equal(mgr.get(task.id).status, "claimed");

    mgr.startWork(task.id);
    assert.equal(mgr.get(task.id).status, "in_progress");

    // 3. Resolve transitions to terminal state 'resolved'
    mgr.resolve(task.id, { by: "bob/agent", resolution: "Audit passed, no issues" });
    assert.equal(mgr.get(task.id).status, "resolved");

    // 4. Terminal state is immutable: cannot reject or startWork once resolved
    assert.throws(
      () => mgr.reject(task.id, { by: "bob/agent", reason: "Rejected later" }),
      /already resolved/i
    );
    assert.throws(
      () => mgr.startWork(task.id),
      /terminal task/i
    );
    assert.throws(
      () => mgr.claim(task.id, "eve/agent"),
      /already resolved/i
    );

    // 5. Rejected task cannot be resolved or started
    const rejectTask = mgr.request({
      op: "ask",
      from: "eve/agent",
      to: "bob/agent",
      title: "Unsafe query",
    });
    mgr.reject(rejectTask.id, { by: "bob/agent", reason: "Forbidden" });
    assert.equal(mgr.get(rejectTask.id).status, "rejected");

    assert.throws(
      () => mgr.resolve(rejectTask.id, { by: "bob/agent", resolution: "Should fail" }),
      /Cannot resolve task.*status rejected/i
    );
    assert.throws(
      () => mgr.startWork(rejectTask.id),
      /terminal task/i
    );
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});
