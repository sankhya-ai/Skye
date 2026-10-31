import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { ApprovalGateManager, installDefaultWorkflows, verifyWorkflow } from "../lib/runner.mjs";
import { newAdminKey } from "../lib/admin.mjs";
import { LineTools } from "../lib/mcp.mjs";
import { Inbox } from "../lib/inbox.mjs";
import { threads } from "../lib/board.mjs";

test("installDefaultWorkflows: creates pre-signed on-patch.yaml and on-gate.yaml", async () => {
  const tmp = mkdtempSync(join(tmpdir(), "chamber-workflows-"));
  const admin = newAdminKey();
  const lineId = "testline1234567890abcdef12345678";

  try {
    const { patchFile, gateFile } = await installDefaultWorkflows(tmp, admin.private, lineId);

    assert.ok(patchFile);
    assert.ok(gateFile);

    const patchContent = readFileSync(patchFile, "utf8");
    const gateContent = readFileSync(gateFile, "utf8");

    assert.match(patchContent, /name: on-patch/);
    assert.match(patchContent, /on: patch/);
    assert.match(patchContent, /git apply --check/);

    assert.match(gateContent, /name: on-migration/);
    assert.match(gateContent, /gate:/);
    assert.match(gateContent, /db-migrate/);

    // Verify cryptographic Ed25519 signatures
    const patchCheck = verifyWorkflow(patchContent, admin.public, { lineId });
    assert.equal(patchCheck.valid, true);

    const gateCheck = verifyWorkflow(gateContent, admin.public, { lineId });
    assert.equal(gateCheck.valid, true);
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});

test("ApprovalGateManager: remote mobile URL and remote sync", async () => {
  const tmp = mkdtempSync(join(tmpdir(), "chamber-gate-remote-"));
  const mgr = new ApprovalGateManager(tmp);

  try {
    // Mock relay server
    let remoteStatus = "pending";
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts = {}) => {
      const u = new URL(url);
      if (u.pathname.includes("/gates")) {
        if (opts.method === "POST") {
          return { ok: true, status: 200, json: async () => ({ ok: true, status: "pending" }) };
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({
            ok: true,
            gate: { status: remoteStatus, approver: "phone-user" },
          }),
        };
      }
      if (u.pathname.startsWith("/gate/")) {
        if (opts.method === "POST") {
          const body = JSON.parse(opts.body || "{}");
          remoteStatus = body.decision === "approve" ? "approved" : "rejected";
          return { ok: true, status: 200, json: async () => ({ ok: true, status: remoteStatus }) };
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({
            ok: true,
            gate: { status: remoteStatus, approver: "phone-user" },
          }),
        };
      }
      return originalFetch(url, opts);
    };

    const gate = await mgr.createGate({
      description: "Run database migration",
      action: "migrate:up",
      requestedBy: "ashish/claude-code",
      relayUrl: "https://relay.chamber.dev",
      lineId: "testline1234567890abcdef12345678",
      authToken: "test-auth-token",
    });

    assert.equal(gate.status, "pending");
    assert.ok(gate.mobileUrl);
    assert.match(gate.mobileUrl, /^https:\/\/relay\.chamber\.dev\/gate\/[a-z0-9_-]{16,64}\/[0-9a-f]{32}\?token=[0-9a-f]{48}#[A-Za-z0-9_-]+$/);

    // Initial remote sync: still pending
    const synced1 = await mgr.syncRemote(gate.id, "https://relay.chamber.dev", "testline1234567890abcdef12345678", "test-auth-token");
    assert.equal(synced1.status, "pending");

    // Simulate mobile user approving on phone
    remoteStatus = "approved";

    // Second remote sync: catches the phone approval and unblocks local gate
    const synced2 = await mgr.syncRemote(gate.id, "https://relay.chamber.dev", "testline1234567890abcdef12345678", "test-auth-token");
    assert.equal(synced2.status, "approved");
    assert.equal(synced2.resolvedBy, "phone-user");

    // Local gate state updated
    const local = mgr.getGate(gate.id);
    assert.equal(local.status, "approved");

    globalThis.fetch = originalFetch;
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});

test("Opt-in branch tags: pass through MCP ask and appear on cards", async () => {
  const tmpHome = mkdtempSync(join(tmpdir(), "chamber-branch-home-"));

  try {
    let sentPayload = null;
    const clientMock = {
      agent: "ashish/claude-code",
      invite: { relay: "http://example.com", secret: "0123456789abcdef", auth: {} },
      roster: [],
      channels: [],
      async ask(to, text, options) {
        sentPayload = { to, text, ...options };
        options.onSent?.({ id: "msg-123", to, thread: "thread-123", delivered: [to] });
        return {
          receipt: { id: "msg-123", to, thread: "thread-123", delivered: [to] },
          reply: {
            id: "msg-124",
            from: to,
            to: "ashish/claude-code",
            thread: "thread-123",
            text: "LGTM!",
            branch: options.branch,
            ts: Date.now(),
          },
        };
      },
    };

    const inbox = new Inbox("ashish/claude-code");
    inbox.dir = tmpHome;
    inbox.path = join(tmpHome, "inbox.jsonl");
    inbox.sentPath = join(tmpHome, "sent.jsonl");

    const tools = new LineTools(clientMock, inbox, tmpHome);
    const result = await tools.call("ask", {
      to: "@nitin",
      message: "Can you review this branch?",
      branch: "feat/opt-in-branches",
    });

    assert.ok(sentPayload);
    assert.equal(sentPayload.branch, "feat/opt-in-branches");
    assert.match(result, /LGTM!/);

    // Verify that cardsOf extracts branch onto Card
    const records = [
      {
        id: "msg-123",
        from: "ashish/claude-code",
        to: "@nitin",
        thread: "thread-123",
        title: "Can you review this branch?",
        text: "Can you review this branch?",
        branch: "feat/opt-in-branches",
        ts: Date.now() - 1000,
      },
      {
        id: "msg-124",
        from: "nitin/terminal",
        to: "ashish/claude-code",
        thread: "thread-123",
        text: "LGTM!",
        branch: "feat/opt-in-branches",
        ts: Date.now(),
      },
    ];

    const cards = threads(records, "ashish");
    assert.equal(cards.length, 1);
    assert.equal(cards[0].branch, "feat/opt-in-branches");
    assert.equal(cards[0].title, "Can you review this branch?");
  } finally {
    rmSync(tmpHome, { recursive: true, force: true });
  }
});
