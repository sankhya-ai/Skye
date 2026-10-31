import assert from "node:assert/strict";
import { test } from "node:test";
import { Invite } from "../lib/protocol.mjs";
import { LineClient } from "../lib/client.mjs";

test("Invite.syncUrl: formats correct sync endpoint URL", () => {
  const invite = new Invite("https://relay.example.com", "0123456789abcdef0123456789abcdef");
  invite.lineId = "testline1234567890abcdef12345678";
  assert.equal(
    invite.syncUrl(50, 200),
    "https://relay.example.com/v1/lines/testline1234567890abcdef12345678/sync?since=50&limit=200"
  );
});

test("LineClient.syncHistory: fetches, decrypts, and passes opened messages", async () => {
  const invite = await Invite.create("https://relay.example.com").ready();
  const meta = { sender: "ashish/codex", to: "@nitin", id: "msg-sync-1", replyTo: null };
  const sealedBody = await invite.seal(
    {
      text: "Auth token spec finalized",
      title: "Spec: auth token",
      type: "decision",
      thread: "msg-sync-1",
      files: [],
    },
    meta
  );

  // Mock fetch to simulate relay /sync response
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url).includes("/sync")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          head: 1,
          hasMore: false,
          messages: [
            {
              seq: 1,
              id: "msg-sync-1",
              from: "ashish/codex",
              to_owner: "nitin",
              reply_to: null,
              body: sealedBody,
              ts: 1700000000000,
              mentions: ["nitin"],
            },
          ],
        }),
      };
    }
    return originalFetch(url);
  };

  try {
    const client = new LineClient(invite, "nitin/laptop");
    const received = [];
    const res = await client.syncHistory({
      since: 0,
      onMessage: (m) => received.push(m),
    });

    assert.equal(res.messages.length, 1);
    assert.equal(received.length, 1);
    assert.equal(received[0].id, "msg-sync-1");
    assert.equal(received[0].text, "Auth token spec finalized");
    assert.equal(received[0].type, "decision");
    assert.equal(received[0].from, "ashish/codex");
    assert.equal(received[0].to, "@nitin");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("LineClient.syncHistory: includes member pass and owner headers for access control", async () => {
  const invite = await Invite.create("https://relay.example.com").ready();
  invite.pass = "member-pass-123";
  let capturedHeaders = null;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts = {}) => {
    capturedHeaders = opts.headers;
    return { ok: true, status: 200, json: async () => ({ head: 0, hasMore: false, messages: [] }) };
  };
  try {
    const client = new LineClient(invite, "ashish/laptop");
    await client.syncHistory();
    assert.equal(capturedHeaders["x-chamber-pass"], "member-pass-123");
    assert.equal(capturedHeaders["x-chamber-owner"], "ashish");
    assert.ok(capturedHeaders["Authorization"]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
