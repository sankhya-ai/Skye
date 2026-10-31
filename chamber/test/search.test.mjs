import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { ChamberSearch } from "../lib/search.mjs";

test("ChamberSearch: indexes, ranks with BM25, and retrieves incidents", async () => {
  const tmp = mkdtempSync(join(tmpdir(), "chamber-search-"));
  const search = new ChamberSearch("ashish", { baseDir: tmp });

  try {
    await search.index({
      id: "doc1",
      talkId: "talk-auth",
      messageId: "m100",
      sender: "ashish/codex.api",
      channel: "#backend",
      type: "decision",
      title: "Auth Token Spec",
      text: "Auth token expiry is an integer representing UNIX epoch seconds. Implemented in auth/schema.py.",
    });

    await search.index({
      id: "doc2",
      talkId: "talk-billing",
      messageId: "m201",
      sender: "nitin/claude-code.web",
      channel: "#billing",
      type: "incident",
      title: "Stripe Webhook Bug",
      text: "Incident postmortem: TypeError cannot read property id of undefined during Stripe signature verification. Fixed in billing/webhook.ts.",
    });

    await search.index({
      id: "doc3",
      talkId: "talk-deploy",
      messageId: "m305",
      sender: "system/runner",
      channel: "#ops",
      type: "patch",
      title: "Cloudflare Worker Deploy",
      text: "Applied patch: update wrangler.toml compatibility_date to 2026-09-01.",
    });

    // 1. Keyword search
    const results = await search.search({ query: "auth token expiry" });
    assert.ok(results.length >= 1, "should find auth token doc");
    assert.equal(results[0].id, "doc1");
    assert.equal(results[0].citation, "talk/talk-auth#m100");

    // 2. Incident recall
    const incidents = await search.incidentMemory("TypeError cannot read property id");
    assert.ok(incidents.length >= 1, "should find incident postmortem");
    assert.equal(incidents[0].id, "doc2");
    assert.match(incidents[0].text, /Stripe signature verification/);

    // 3. Channel filter
    const opsResults = await search.search({ query: "patch", channel: "#ops" });
    assert.equal(opsResults.length, 1);
    assert.equal(opsResults[0].id, "doc3");

    const billingResults = await search.search({ query: "patch", channel: "#billing" });
    assert.equal(billingResults.length, 0);
  } finally {
    search.close();
    rmSync(tmp, { recursive: true, force: true });
  }
});

test("ChamberSearch: in-memory fallback works reliably", async () => {
  const search = new ChamberSearch("test", { inMemory: true });
  await search.index({
    id: "mem1",
    title: "Database Lock Issue",
    text: "Resolved database busy timeout by enabling WAL mode in sqlite.",
    type: "incident",
  });

  const res = await search.search({ query: "timeout WAL" });
  assert.equal(res.length, 1);
  assert.equal(res[0].id, "mem1");
  search.close();
});
