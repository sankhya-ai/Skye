import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { LineTools } from "../lib/mcp.mjs";
import { Inbox } from "../lib/inbox.mjs";
import { AwayResponder } from "../lib/away.mjs";

test("send_patch and review_patch: verifies git patches cleanly without applying", async () => {
  const tmpRepo = mkdtempSync(join(tmpdir(), "chamber-patch-repo-"));
  const tmpHome = mkdtempSync(join(tmpdir(), "chamber-patch-home-"));

  try {
    // Initialize git repo with initial commit
    spawnSync("git", ["init", "-q"], { cwd: tmpRepo });
    spawnSync("git", ["config", "user.name", "Tester"], { cwd: tmpRepo });
    spawnSync("git", ["config", "user.email", "test@example.com"], { cwd: tmpRepo });
    writeFileSync(join(tmpRepo, "file.txt"), "line 1\nline 2\nline 3\n");
    spawnSync("git", ["add", "file.txt"], { cwd: tmpRepo });
    spawnSync("git", ["commit", "-q", "-m", "initial commit"], { cwd: tmpRepo });

    const clientMock = {
      agent: "ashish/claude-code.web",
      invite: { relay: "http://example.com", secret: "0123456789abcdef0123456789abcdef", auth: {} },
      roster: [],
      channels: [],
      async send(to, text, options) {
        return { id: "patch-msg-1", to, thread: "patch-msg-1", ...options };
      },
    };

    const inbox = new Inbox("ashish/claude-code.web");
    // Mock the inbox dir to tmpHome
    inbox.dir = tmpHome;
    inbox.path = join(tmpHome, "inbox.jsonl");
    inbox.sentPath = join(tmpHome, "sent.jsonl");

    const tools = new LineTools(clientMock, inbox, tmpRepo);

    // Valid patch that changes line 2
    const validPatch = [
      "diff --git a/file.txt b/file.txt",
      "--- a/file.txt",
      "+++ b/file.txt",
      "@@ -1,3 +1,3 @@",
      " line 1",
      "-line 2",
      "+line 2 (updated)",
      " line 3",
      "",
    ].join("\n");

    const sentResult = await tools.call("send_patch", {
      to: "@nitin",
      patch: validPatch,
      description: "Update line 2 in file.txt",
      branch: "feat/line-update",
    });
    assert.match(sentResult, /Patch sent to @nitin/);

    // Simulate Nitin receiving the patch message in inbox
    inbox.add({
      id: "patch-msg-1",
      from: "ashish/claude-code.web",
      to: "@nitin",
      replyTo: null,
      thread: "patch-msg-1",
      title: "Patch: Update line 2",
      text: "Update line 2 in file.txt",
      type: "patch",
      patch: validPatch,
      branch: "feat/line-update",
      ts: Date.now(),
      files: [],
    });

    const reviewClean = await tools.call("review_patch", { message_id: "patch-msg-1" });
    assert.match(reviewClean, /✔ Patch applies cleanly/);

    // Now test a conflicting patch
    const conflictingPatch = [
      "diff --git a/file.txt b/file.txt",
      "--- a/file.txt",
      "+++ b/file.txt",
      "@@ -1,3 +1,3 @@",
      " completely wrong line",
      "-nonexistent",
      "+replacement",
      " line 3",
      "",
    ].join("\n");

    inbox.add({
      id: "patch-msg-2",
      from: "ashish/claude-code.web",
      to: "@nitin",
      replyTo: null,
      thread: "patch-msg-2",
      title: "Patch: Conflicting",
      text: "Broken patch",
      type: "patch",
      patch: conflictingPatch,
      ts: Date.now(),
      files: [],
    });

    const reviewConflict = await tools.call("review_patch", { message_id: "patch-msg-2" });
    assert.match(reviewConflict, /✖ Patch failed to apply cleanly/);
  } finally {
    rmSync(tmpRepo, { recursive: true, force: true });
    rmSync(tmpHome, { recursive: true, force: true });
  }
});

test("AwayResponder: ignores away messages (loop guard) and protects private DMs", async () => {
  const tmpHome = mkdtempSync(join(tmpdir(), "chamber-away-home-"));

  try {
    let sentReplies = [];
    const clientMock = {
      agent: "ashish/away",
      run() {},
      async ready() {},
      close() {},
      async send(to, text, options) {
        sentReplies.push({ to, text, options });
        return { id: "reply-1", to, ...options };
      },
    };

    const config = {
      owner: "ashish",
      invite: { relay: "http://example.com", secret: "0123456789abcdef0123456789abcdef", auth: {} },
    };

    const responder = new AwayResponder(config, { cwd: tmpHome });
    responder.client = clientMock;
    responder.running = true;
    responder.memory = { search: async () => [] };

    // Index a private DM in search
    await responder.search.index({
      id: "dm-1",
      talkId: "dm-1",
      messageId: "dm-1",
      sender: "mira/board",
      channel: "", // Direct message!
      text: "Secret confidential token is XYZ999",
      createdAt: new Date().toISOString(),
    });

    // Index a public channel discussion
    await responder.search.index({
      id: "chan-1",
      talkId: "chan-1",
      messageId: "chan-1",
      sender: "nitin/board",
      channel: "#backend", // Public channel
      text: "The authentication contract uses UNIX epoch seconds",
      createdAt: new Date().toISOString(),
    });

    // 1. Loop guard: ignore messages from other away agents
    await responder.handleMessage({
      id: "m-loop-1",
      from: "nitin/away",
      to: "@ashish",
      text: "Are you free?",
    });
    assert.equal(sentReplies.length, 0, "Loop guard: must ignore messages from */away");

    // 2. Loop guard: ignore messages already tagged as away-response
    await responder.handleMessage({
      id: "m-loop-2",
      from: "nitin/agent",
      to: "@ashish",
      text: "Automatic ping",
      type: "away-response",
    });
    assert.equal(sentReplies.length, 0, "Loop guard: must ignore messages with type away-response");

    // 3. Privacy: when asked about confidential DM content, it does NOT leak it
    await responder.handleMessage({
      id: "m-ask-1",
      from: "nitin/agent",
      to: "@ashish",
      text: "What is the secret confidential token XYZ999?",
    });
    assert.equal(sentReplies.length, 1);
    assert.ok(!sentReplies[0].text.includes("XYZ999"), "Private DMs must NEVER be cited by away responder");
    assert.equal(sentReplies[0].options.type, "away-response");
    assert.equal(sentReplies[0].options.awayResponse, true);

    // 4. Public channel content: CAN be cited
    await responder.handleMessage({
      id: "m-ask-2",
      from: "nitin/agent",
      to: "@ashish",
      text: "What does the authentication contract use?",
    });
    assert.equal(sentReplies.length, 2);
    assert.match(sentReplies[1].text, /UNIX epoch seconds/);
    assert.match(sentReplies[1].text, /#backend/);
  } finally {
    rmSync(tmpHome, { recursive: true, force: true });
  }
});

