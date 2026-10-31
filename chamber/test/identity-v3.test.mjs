// Identity v3 (brick three) end to end against the real relay (workerd, via `wrangler dev`):
// per-epoch READ keys on top of v2's signed attribution. The matrix from docs/identity-v2.md:
// authorized reads, unauthorized member/relay/cross-line reads, revocation + rotation,
// late-join history, join/revoke race, restart persistence, distribution authenticity,
// downgrade semantics. Stated everywhere: rotation is NOT retroactive - a revoked member keeps
// the epochs they already held; only messages sent AFTER the rotation are closed to them.

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync, mkdtempSync, statSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { after, before, test } from "node:test";

process.env.CHAMBER_HOME = mkdtempSync(join(tmpdir(), "chamber-v3-home-")); // member + epoch keys land here (0600)

const ROOT = resolve(import.meta.dirname, "..");
const WRANGLER = join(ROOT, "relay", "node_modules", ".bin", "wrangler");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const tmp = (name) => mkdtempSync(join(tmpdir(), `chamber-v3-${name}-`));
const hex32 = () => randomBytes(16).toString("hex");
const clients = new Set();

const { Invite } = await import("../lib/protocol.mjs");
const { LineClient } = await import("../lib/client.mjs");
const { newAdminKey, issueCertificate, revocation, rotation } = await import("../lib/admin.mjs");
const identity = await import("../lib/identity.mjs");
const epochLib = await import("../lib/epoch.mjs");

let relay;
let relayProc;
let relayPort;
let relayDir;
const admin = newAdminKey();

async function waitRelayUp() {
  for (let i = 0; i < 120; i++) {
    try {
      if ((await fetch(relay)).ok) return;
    } catch {}
    await sleep(500);
  }
  assert.fail("relay did not start");
}

async function spawnRelay() {
  relayProc = spawn(WRANGLER, ["dev", "--local", "--ip", "127.0.0.1", "--port", String(relayPort), "--persist-to", relayDir, "--var", `ADMIN_KEY:${admin.public}`, "CHAMBER_TEST_HOOKS:1"], {
    cwd: join(ROOT, "relay"), stdio: "ignore", detached: true,
  });
  relay = `http://127.0.0.1:${relayPort}`;
  await waitRelayUp();
}

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
  relayPort = await freePort();
  relayDir = tmp("relay");
  await spawnRelay();
});

after(() => {
  for (const c of clients) c.close();
  if (relayProc) process.kill(-relayProc.pid, "SIGTERM");
});

/** A v3 member: signing + encryption keys on this machine (0600), the admin's v3 certificate. */
async function v3member(chamber, owner) {
  identity.enrollOwner(chamber.lineId, owner);
  const key = identity.loadMemberKey(chamber.lineId, owner);
  assert.ok(key.encPublic && key.encPrivate, "a v3 member key carries a separate X25519 encryption keypair");
  const cert = issueCertificate(admin.private, chamber.lineId, owner, key.public, key.encPublic);
  return { key, cert, invite: await chamber.withCertificate(cert, admin.public).ready() };
}

async function tryJoin(invite, agent, opts = {}) {
  const client = new LineClient(invite, agent, { kind: "human", listens: true, ...opts });
  clients.add(client);
  client.run();
  for (let i = 0; i < 60 && !client.connected && !client.problem; i++) await sleep(100);
  await sleep(200);
  return client;
}

async function waitFor(fn, what, tries = 40) {
  for (let i = 0; i < tries; i++) {
    const value = fn();
    if (value) return value;
    await sleep(100);
  }
  assert.fail(`timed out waiting for ${what}`);
}

const wrapSet = (...members) => members.map((m) => ({ owner: m.owner, enc: m.key.encPublic }));

/** Advance the relay's trusted current epoch (the admin's signed rotation order). */
async function rotate(line, epoch, expectOk = true) {
  const response = await fetch(`${relay}/v1/lines/${line.lineId}/admin`, { method: "POST", headers: { ...line.auth, "content-type": "application/json" }, body: JSON.stringify({ order: rotation(admin.private, line.lineId, epoch) }) });
  const body = await response.json().catch(() => ({}));
  if (expectOk) assert.equal(response.status, 200, body.error || "rotation accepted");
  return { status: response.status, body };
}

// ----- the shared v3 chamber ---------------------------------------------------------------------

let lineX, alice, bob, carol;
const E1 = epochLib.generateEpochKey();
const E2 = epochLib.generateEpochKey();

test("setup: a v3 chamber, epoch 1 distributed to two members, authorized reads", async () => {
  lineX = await Invite.create(relay).ready();
  alice = { owner: "ashish", ...(await v3member(lineX, "ashish")) };
  bob = { owner: "nitin", ...(await v3member(lineX, "nitin")) };
  alice.client = await tryJoin(alice.invite, "ashish/board");
  assert.equal(alice.client.connected, true);
  assert.equal(alice.client.lineVersion, 3, "the first v3 admit fixes the line at v3");
  assert.match(alice.client.identityNote, /v3/);
  assert.match(alice.client.identityNote, /not retroactive/, "the note states the revocation limit");
  bob.client = await tryJoin(bob.invite, "nitin/board");
  assert.equal(bob.client.connected, true);

  await alice.client.sendDistribution(admin.private, 1, E1, wrapSet(alice, bob));
  await rotate(lineX, 1); // the relay now trusts epoch 1; stale/other epochs are refused
  await waitFor(() => alice.client.epochs.current === 1 && bob.client.epochs.current === 1, "epoch 1 to land on both members");
  assert.equal(alice.client.epochs.keys["1"], E1, "the unwrapped key is the admin's epoch key");

  let got = null;
  alice.client.onMessage = (m) => (got = m);
  await bob.client.send("*", "epoch-one hello");
  got = await waitFor(() => got, "ashish to read nitin's epoch-1 message");
  assert.equal(got.text, "epoch-one hello");
});

test("1. a member without a wrap cannot read and cannot send (fail closed, never the shared key)", async () => {
  carol = { owner: "mia", ...(await v3member(lineX, "mia")) };
  carol.client = await tryJoin(carol.invite, "mia/board");
  assert.equal(carol.client.connected, true, "the relay admits her: attribution (v2) works for her");
  assert.equal(carol.client.epochs.current, 0, "but no epoch key was wrapped to her");
  let got = null;
  carol.client.onMessage = (m) => (got = m);
  await bob.client.send("*", "not for mia");
  await sleep(1500);
  assert.equal(got, null, "a v3 body does not open without the epoch key - she does not even see it");
  await assert.rejects(() => carol.client.send("*", "can i write?"), /epoch key/, "sending fails closed rather than falling back to the shared key");
});

test("2. ciphertext-only views: the shared key and the relay's rows open nothing v3", async () => {
  // The relay's stored rows (exactly what a relay operator holds)...
  const sync = await (await fetch(lineX.syncUrl(0), { headers: { ...lineX.auth, "x-chamber-pass": alice.cert } })).json();
  const bodies = (sync.messages || sync.rows || []).filter((m) => m.epoch >= 1);
  assert.ok(bodies.length >= 2, "epoch-sealed bodies are stored");
  for (const row of bodies) {
    assert.ok(!row.body.includes("hello") && !row.body.includes("mia"), "no plaintext in stored rows");
    // ...do not open with the SHARED invite key (what a revoked member or invite leaker holds)...
    await assert.rejects(() => lineX.open(row.body, { sender: row.from, to: row.to, id: row.id, replyTo: row.reply_to }), /does not open/);
  }
  // ...nor with the wrong epoch of the RIGHT key set.
  const row = bodies[0];
  await assert.rejects(() => lineX.open(row.body, { sender: row.from, to: row.to, id: row.id, replyTo: row.reply_to }, { n: 99, key: E1 }), /does not open/);
  // Cross-line: the same key material on another line's AAD opens nothing.
  const lineY = await Invite.create(relay).ready();
  const meta = { sender: "nitin/board", to: "*", id: row.id, replyTo: "" };
  await assert.rejects(() => lineY.open(row.body, meta, { n: 1, key: E1 }), /does not open/);
});

test("3. distribution authenticity: forged, tampered and stale distributions are ignored", async () => {
  const before = alice.client.epochs;
  // A distribution whose payload is signed by a MEMBER's key, not the admin's: ignored, even
  // though the transport frame itself is properly signed by that member.
  const evilKey = epochLib.generateEpochKey();
  await bob.client.sendDistribution(bob.key.private, 7, evilKey, wrapSet(alice));
  await sleep(1200);
  assert.equal(alice.client.epochs.current, before.current, "a member-signed distribution installs nothing");
  assert.equal(alice.client.epochs.keys["7"], undefined);
  // A tampered wrap (the admin's signature no longer matches): ignored.
  const wrap = await epochLib.wrapEpochKey(alice.key.encPublic, { line: lineX.lineId, epoch: 7, owner: "ashish", key: evilKey });
  const tampered = { line: lineX.lineId, epoch: 7, wraps: { ashish: { eph: wrap.eph, sealed: wrap.sealed.slice(0, -2) + "AA" } } };
  const sig = epochLib.signDistribution(admin.private, { line: lineX.lineId, epoch: 7, wraps: { ashish: wrap } }); // signed BEFORE tampering
  const payload = { type: "chamber-epoch", text: "", thread: "", ...tampered, sig };
  const id = hex32();
  const ts = Date.now();
  const sealed = await lineX.seal(payload, { sender: "ashish/board", to: "*", id, replyTo: null });
  await alice.client.testFrame({ t: "msg", seq: 91001, id, from: "ashish/board", to: "*", to_owner: null, reply_to: null, body: sealed, ts, epoch: 0, sig: identity.signEnvelope(alice.key.private, { line: lineX.lineId, from: "ashish/board", to: "*", id, replyTo: "", ts, hash: identity.bodyHash(sealed) }) });
  assert.equal(alice.client.epochs.keys["7"], undefined, "a tampered distribution installs nothing");
  // A legitimately signed re-distribution of an OLD epoch never rolls the current epoch back.
  await alice.client.sendDistribution(admin.private, 1, E1, wrapSet(alice, bob));
  await sleep(1200);
  assert.equal(alice.client.epochs.current, 1, "still 1 (epoch 2 has not happened yet)");
});

test("4. revocation + rotation: the revoked member keeps the past, loses the future", async () => {
  // The admin removes nitin AND rotates in ONE signed order: no window where an old-epoch send
  // still lands for the revoked holder's retained key. Then epoch 2 is wrapped only to ashish.
  const order = await fetch(`${relay}/v1/lines/${lineX.lineId}/admin`, { method: "POST", headers: { ...lineX.auth, "content-type": "application/json" }, body: JSON.stringify({ order: revocation(admin.private, lineX.lineId, "nitin", "revoke", 2) }) });
  assert.equal(order.status, 200);
  assert.equal((await order.json()).epoch, 2, "the revocation and the rotation were one atomic order");
  await alice.client.sendDistribution(admin.private, 2, E2, wrapSet(alice));
  await waitFor(() => alice.client.epochs.current === 2, "epoch 2 to land on ashish");
  for (let i = 0; i < 40 && bob.client.connected; i++) await sleep(100);
  assert.equal(bob.client.connected, false, "the revoked member's socket is dropped");

  await alice.client.send("*", "post-rotation secret");

  // NOT retroactive, stated and tested: nitin's client still holds epoch 1 and opens old bodies.
  await sleep(500); // let the relay store the epoch-2 message
  const sync = await (await fetch(lineX.syncUrl(0), { headers: { ...lineX.auth, "x-chamber-pass": alice.cert } })).json();
  const rows = (sync.messages || sync.rows || []).filter((m) => m.epoch >= 1);
  const oldRow = rows.find((r) => r.epoch === 1);
  const oldOpened = await lineX.open(oldRow.body, { sender: oldRow.from, to: oldRow.to, id: oldRow.id, replyTo: oldRow.reply_to }, { n: 1, key: bob.client.epochs.keys["1"] });
  assert.ok(oldOpened.text, "old ciphertext still opens with a key the revoked member already held - documented, not a leak");
  // ...but the future is closed: epoch-2 bodies do not open with anything nitin has.
  const newRow = rows.find((r) => r.epoch === 2);
  assert.ok(newRow, "the epoch-2 message is stored");
  await assert.rejects(() => lineX.open(newRow.body, { sender: newRow.from, to: newRow.to, id: newRow.id, replyTo: newRow.reply_to }, { n: 2, key: bob.client.epochs.keys["1"] }), /does not open/, "epoch-1 keys open nothing from epoch 2");
  // His client, injected with the epoch-2 frame directly, drops it silently.
  let bobGot = null;
  bob.client.onMessage = (m) => (bobGot = m);
  await bob.client.testFrame({ t: "msg", seq: 91002, id: newRow.id, from: newRow.from, to: "*", to_owner: null, reply_to: null, body: newRow.body, ts: newRow.ts, epoch: 2, sig: newRow.sig });
  assert.equal(bobGot, null, "no key for epoch 2: the frame is dropped, not delivered");
  // And he cannot rejoin or send: his certificate is dead (the relay side of revocation).
  const again = await tryJoin(bob.invite, "nitin/board");
  assert.equal(again.connected, false);
  assert.match(again.problem, /removed/);
  again.close();

  // Fail-closed for ACTIVE senders: a stale client (offline through the rotation, still holding a
  // valid cert) sends under retained epoch 1 - the relay refuses, so a revoked holder's retained
  // epoch key never yields NEW traffic. And a future epoch is refused too.
  const frames = [];
  alice.client.ws.addEventListener("message", (event) => {
    try {
      const f = JSON.parse(String(event.data));
      if (f.t === "error") frames.push(f);
    } catch {}
  });
  for (const [n, key] of [[1, E1], [7, E1]]) {
    const id = hex32();
    const ts = Date.now();
    const body = await lineX.seal({ text: `epoch ${n} attempt`, files: [], thread: id, title: "", type: "message", patch: null, branch: null }, { sender: "ashish/board", to: "*", id, replyTo: null }, { n, key });
    alice.client.ws.send(JSON.stringify({ t: "send", id, to: "*", body, mentions: [], ts, epoch: n, sig: identity.signEnvelope(alice.key.private, { line: lineX.lineId, from: "ashish/board", to: "*", id, replyTo: "", ts, hash: identity.bodyHash(body), epoch: n }) }));
  }
  await waitFor(() => frames.length >= 2, "both epoch refusals");
  assert.match(frames.find((f) => /stale/.test(f.error || ""))?.error || "", /stale epoch 1.*epoch 2/, "old-epoch sends are refused, explicitly");
  assert.match(frames.find((f) => /ahead/.test(f.error || ""))?.error || "", /epoch 7 is ahead/, "future-epoch sends are refused, explicitly");

  // Rotation orders are monotonic: a replayed or older rotation is rejected, so a replayed
  // distribution can never roll the line back to an epoch a revoked member holds.
  const replay = await rotate(lineX, 2, false);
  assert.equal(replay.status, 409);
  assert.match(replay.body.error || "", /never roll back/);
  const rollback = await rotate(lineX, 1, false);
  assert.equal(rollback.status, 409);
});

test("5. late join + join/revoke race in one rotation: reads from the join epoch forward", async () => {
  // Carol (mia) was admitted but never wrapped; the admin's epoch-2 rotation (which removed
  // nitin) now ALSO adds mia - one distribution, the new membership set.
  await alice.client.sendDistribution(admin.private, 2, E2, wrapSet(alice, carol));
  await waitFor(() => carol.client.epochs.current === 2, "epoch 2 to reach mia");
  assert.equal(carol.client.epochs.keys["1"], undefined, "she was never wrapped epoch 1");
  // History before her join epoch stays closed even when replayed straight into her client.
  const sync = await (await fetch(lineX.syncUrl(0), { headers: { ...lineX.auth, "x-chamber-pass": alice.cert } })).json();
  const rows = (sync.messages || sync.rows || []).filter((m) => m.epoch >= 1);
  const oldRow = rows.find((r) => r.epoch === 1);
  let got = null;
  carol.client.onMessage = (m) => (got = m);
  await carol.client.testFrame({ t: "msg", seq: 91003, id: oldRow.id, from: oldRow.from, to: "*", to_owner: null, reply_to: null, body: oldRow.body, ts: oldRow.ts, epoch: 1, sig: oldRow.sig });
  assert.equal(got, null, "epoch-1 history does not open for a member wrapped from epoch 2");
  // ...while current traffic flows for her.
  await alice.client.send("*", "welcome mia");
  got = await waitFor(() => got, "mia reads epoch-2 traffic");
  assert.equal(got.text, "welcome mia");
});

test("5b. blobs follow the epoch, and the history rule is epoch-granular (not time-granular)", async () => {
  // Blob storage path (files over 64KB): sealed under the current epoch, opens for wrapped
  // members, opens with NOTHING else - the static invite key included.
  const data = randomBytes(80 * 1024);
  const blobId = hex32();
  const put = await fetch(lineX.blobUrl(blobId), { method: "PUT", headers: { ...lineX.auth, "x-chamber-pass": alice.cert }, body: await lineX.sealBlob(data, blobId, { n: 2, key: E2 }) });
  assert.equal(put.status, 200);
  const got = await fetch(lineX.blobUrl(blobId), { headers: { ...lineX.auth, "x-chamber-pass": alice.cert } });
  const sealedBytes = new Uint8Array(await got.arrayBuffer());
  assert.deepEqual([...(await lineX.openBlob(sealedBytes, blobId, { n: 2, key: E2 })).slice(0, 8)], [...data.slice(0, 8)], "epoch-sealed blob opens with the epoch key");
  await assert.rejects(() => lineX.openBlob(sealedBytes, blobId), /does not open/, "the static invite key opens no epoch blob");
  await assert.rejects(() => lineX.openBlob(sealedBytes, blobId, { n: 1, key: E1 }), /does not open/, "an older epoch opens no epoch-2 blob");

  // The exact history rule: a member wrapped the CURRENT epoch reads everything sealed under it,
  // WHENEVER it was sent - including traffic from before they joined. Only a rotation AT the join
  // makes pre-join traffic unreadable. dave joins now and is wrapped epoch 2 (no rotation).
  const dave = { owner: "dave", ...(await v3member(lineX, "dave")) };
  await alice.client.sendDistribution(admin.private, 2, E2, wrapSet(alice, carol, dave)); // dave offline: it waits for him
  dave.client = await tryJoin(dave.invite, "dave/board");
  assert.equal(dave.client.connected, true);
  await waitFor(() => dave.client.epochs.current === 2, "the waiting distribution to reach dave");
  const texts = [];
  dave.client.onMessage = (m) => texts.push(m.text);
  const sync = await (await fetch(lineX.syncUrl(0), { headers: { ...lineX.auth, "x-chamber-pass": dave.cert } })).json();
  for (const row of (sync.messages || []).filter((m) => m.epoch >= 1)) {
    await dave.client.testFrame({ ...row, to: row.to || "*" });
  }
  assert.ok(texts.includes("post-rotation secret"), "pre-join traffic under a HELD epoch opens - the rule is epoch-granular");
  assert.ok(texts.includes("welcome mia"), "all epoch-2 traffic opens, whenever sent");
  assert.ok(!texts.includes("epoch-one hello") && !texts.includes("not for mia"), "epoch-1 history stays closed: dave was never wrapped epoch 1");
  dave.client.close();
});

test("6. restart persistence: a fresh client on the same machine reads and sends from disk", async () => {
  const restarted = await tryJoin(alice.invite, "ashish/board");
  assert.equal(restarted.connected, true);
  assert.equal(restarted.epochs.current, 2, "epoch keys loaded from the 0600 file, no new distribution needed");
  assert.equal(restarted.epochs.keys["2"], E2);
  let got = null;
  carol.client.onMessage = (m) => (got = m); // the relay never echoes to the sender; mia holds epoch 2
  await restarted.send("*", "after restart");
  got = await waitFor(() => got, "the restarted client sends under the current epoch");
  assert.equal(got.text, "after restart");
  assert.equal(statSync(epochLib.epochPath(lineX.lineId, "ashish")).mode & 0o777, 0o600, "the epoch key file is owner-only");
  restarted.close();
});

test("7. downgrade semantics: no silent path onto or off a v3 line", async () => {
  // A v2 certificate on the v3 line: explicit refusal, never shared-key reads.
  const dave = identity.enrollOwner(lineX.lineId, "dave");
  const daveKey = identity.loadMemberKey(lineX.lineId, "dave");
  const v2cert = issueCertificate(admin.private, lineX.lineId, "dave", daveKey.public); // no enc: v2
  const davesTry = await tryJoin(await lineX.withCertificate(v2cert, admin.public).ready(), "dave/board");
  assert.equal(davesTry.connected, false);
  assert.match(davesTry.problem, /needs chamber >= v3/);
  davesTry.close();
  void dave;
  // A v3 certificate on a v2 line: explicit mismatch, never silent v2.
  const lineV2 = await Invite.create(relay).ready();
  const eli = identity.enrollOwner(lineV2.lineId, "eli");
  const eliKey = identity.loadMemberKey(lineV2.lineId, "eli");
  const eliV2 = await tryJoin(await lineV2.withCertificate(issueCertificate(admin.private, lineV2.lineId, "eli", eliKey.public), admin.public).ready(), "eli/board");
  assert.equal(eliV2.connected, true);
  assert.equal(eliV2.lineVersion, 2, "the v2 line is claimed at v2 by the v2 certificate");
  const eliV3 = await tryJoin(await lineV2.withCertificate(issueCertificate(admin.private, lineV2.lineId, "fay", eliKey.public, eliKey.encPublic), admin.public).ready(), "fay/board");
  assert.equal(eliV3.connected, false);
  assert.match(eliV3.problem, /v2 but your certificate is v3/);
  eliV3.close();
  // requireV3 on a v2 line refuses instead of downgrading.
  const strict = await tryJoin(eliV2.invite, "eli/strict", { requireV3: true });
  assert.equal(strict.connected, false);
  assert.match(strict.problem, /v3 read scope was required/);
  strict.close();
  // requireV2 is SATISFIED by a v3 line (v3 includes v2's attribution).
  const ok = await tryJoin(alice.invite, "ashish/strict2", { requireV2: true });
  assert.equal(ok.connected, true);
  assert.equal(ok.lineVersion, 3);
  ok.close();
  eliV2.close();
});

test("8. relay restart durability: trusted epoch, revocation and resumption survive a relay restart", async () => {
  // Idempotent-retry shape of the atomic order first (crash-after-apply recovery): a repeated
  // revoke+rotate answers 409 naming the CURRENT epoch - which confirms the state instead of
  // rolling anything back - and the revocation stands.
  const retry = await fetch(`${relay}/v1/lines/${lineX.lineId}/admin`, { method: "POST", headers: { ...lineX.auth, "content-type": "application/json" }, body: JSON.stringify({ order: revocation(admin.private, lineX.lineId, "nitin", "revoke", 2) }) });
  assert.equal(retry.status, 409, "a repeated atomic order is a state confirmation, not a rollback");
  assert.match((await retry.json()).error || "", /already at epoch 2/);

  // Close every client, then kill the relay hard and respawn it on the SAME persisted state.
  for (const c of [...clients]) {
    try {
      c.close();
    } catch {}
  }
  await sleep(300);
  process.kill(-relayProc.pid, "SIGKILL");
  await sleep(500);
  await spawnRelay();

  // The revoked member is still refused (revocation survived the restart)...
  const again = await tryJoin(bob.invite, "nitin/board");
  assert.equal(again.connected, false);
  assert.match(again.problem, /removed/);
  again.close();

  // ...a current member resumes from her on-disk epoch keys without any new distribution...
  const alice2 = await tryJoin(alice.invite, "ashish/board");
  assert.equal(alice2.connected, true);
  assert.equal(alice2.lineVersion, 3, "the line version survived the restart");
  assert.equal(alice2.epochs.current, 2, "her epoch keys came from the 0600 file");
  const carol2 = await tryJoin(carol.invite, "mia/board");
  assert.equal(carol2.connected, true);

  // ...and the trusted current epoch survived: a stale epoch-1 send is STILL refused...
  const frames = [];
  alice2.ws.addEventListener("message", (event) => {
    try {
      const f = JSON.parse(String(event.data));
      if (f.t === "error") frames.push(f);
    } catch {}
  });
  const staleId = hex32();
  const ts = Date.now();
  const staleBody = await lineX.seal({ text: "stale after restart", files: [], thread: staleId, title: "", type: "message", patch: null, branch: null }, { sender: "ashish/board", to: "*", id: staleId, replyTo: null }, { n: 1, key: E1 });
  alice2.ws.send(JSON.stringify({ t: "send", id: staleId, to: "*", body: staleBody, mentions: [], ts, epoch: 1, sig: identity.signEnvelope(alice.key.private, { line: lineX.lineId, from: "ashish/board", to: "*", id: staleId, replyTo: "", ts, hash: identity.bodyHash(staleBody), epoch: 1 }) }));
  await waitFor(() => frames.some((f) => f.id === staleId), "the stale-epoch refusal after restart");
  assert.match(frames.find((f) => f.id === staleId)?.error || "", /stale epoch 1.*epoch 2/);

  // ...while current-epoch traffic flows for current members.
  let got = null;
  carol2.onMessage = (m) => (got = m);
  await alice2.send("*", "post-restart epoch two");
  got = await waitFor(() => got, "post-restart delivery");
  assert.equal(got.text, "post-restart epoch two");
  alice2.close();
  carol2.close();
});

test("9. crash atomicity: a failed revoke+rotate commits NOTHING; retry completes it, even across a relay restart", async () => {
  // A separate line so the shared chamber is untouched. fay + gus at epoch 1.
  const lineF = await Invite.create(relay).ready();
  const fay = { owner: "fay", ...(await v3member(lineF, "fay")) };
  const gus = { owner: "gus", ...(await v3member(lineF, "gus")) };
  fay.client = await tryJoin(fay.invite, "fay/board");
  gus.client = await tryJoin(gus.invite, "gus/board");
  assert.equal(fay.client.connected && gus.client.connected, true);
  const E1F = epochLib.generateEpochKey();
  await fay.client.sendDistribution(admin.private, 1, E1F, wrapSet(fay, gus));
  await rotate(lineF, 1);
  await waitFor(() => fay.client.epochs.current === 1 && gus.client.epochs.current === 1, "epoch 1 on both");

  // Inject a failure INSIDE the transition transaction, then send the atomic revoke+rotate order.
  const arm = await fetch(`${relay}/v1/lines/${lineF.lineId}/test-fail-transition`, { method: "POST", headers: lineF.auth });
  assert.equal(arm.status, 200, "the test hook armed the injected failure");
  const failed = await fetch(`${relay}/v1/lines/${lineF.lineId}/admin`, { method: "POST", headers: { ...lineF.auth, "content-type": "application/json" }, body: JSON.stringify({ order: revocation(admin.private, lineF.lineId, "gus", "revoke", 2) }) });
  assert.equal(failed.status, 500, "the injected failure fails the order");
  assert.match((await failed.json()).error || "", /NOT applied/, "the relay says nothing changed");

  // ROLLBACK PROOF: the exact old consistent state - gus not revoked, epoch not advanced. His
  // socket is alive and an epoch-1 send still lands (both facts together rule out either partial
  // state: revoked-but-not-rotated AND rotated-but-not-revoked).
  await sleep(400);
  assert.equal(gus.client.connected, true, "the revoked-member rollback leaves the member's socket up");
  let got = null;
  fay.client.onMessage = (m) => (got = m);
  await gus.client.send("*", "still here at epoch one");
  got = await waitFor(() => got, "epoch-1 traffic still flows after the rolled-back order");
  assert.equal(got.text, "still here at epoch one");

  // Hard-kill the relay and respawn on the same storage: still exactly the old consistent state,
  // never a pending rotation - the member rejoins and epoch-1 sends are accepted, because the
  // revocation genuinely never happened.
  for (const c of [...clients]) {
    try {
      c.close();
    } catch {}
  }
  await sleep(300);
  process.kill(-relayProc.pid, "SIGKILL");
  await sleep(500);
  await spawnRelay();
  const gus2 = await tryJoin(gus.invite, "gus/board");
  assert.equal(gus2.connected, true, "after the restart the never-applied revocation admits the member");
  const fay2 = await tryJoin(fay.invite, "fay/board");
  assert.equal(fay2.connected, true);
  got = null;
  fay2.onMessage = (m) => (got = m);
  await gus2.send("*", "epoch one after the crash");
  got = await waitFor(() => got, "epoch-1 send accepted after restart: old state, nothing pending");
  assert.equal(got.text, "epoch one after the crash");

  // Retry the SAME order (the hook was consumed once): now it commits - revocation and rotation
  // together - and both boundaries hold: the member is out and the old epoch is dead for sends.
  const retry = await fetch(`${relay}/v1/lines/${lineF.lineId}/admin`, { method: "POST", headers: { ...lineF.auth, "content-type": "application/json" }, body: JSON.stringify({ order: revocation(admin.private, lineF.lineId, "gus", "revoke", 2) }) });
  assert.equal(retry.status, 200, "the retry commits after the injected failure");
  assert.equal((await retry.json()).epoch, 2);
  for (let i = 0; i < 40 && gus2.connected; i++) await sleep(100);
  assert.equal(gus2.connected, false, "the committed revocation drops the socket");
  const gus3 = await tryJoin(gus.invite, "gus/board");
  assert.equal(gus3.connected, false);
  assert.match(gus3.problem, /removed/, "the committed revocation survives");
  gus3.close();

  const frames = [];
  fay2.ws.addEventListener("message", (event) => {
    try {
      const f = JSON.parse(String(event.data));
      if (f.t === "error") frames.push(f);
    } catch {}
  });
  const staleId = hex32();
  const ts = Date.now();
  const staleBody = await lineF.seal({ text: "stale after retry", files: [], thread: staleId, title: "", type: "message", patch: null, branch: null }, { sender: "fay/board", to: "*", id: staleId, replyTo: null }, { n: 1, key: E1F });
  fay2.ws.send(JSON.stringify({ t: "send", id: staleId, to: "*", body: staleBody, mentions: [], ts, epoch: 1, sig: identity.signEnvelope(fay.key.private, { line: lineF.lineId, from: "fay/board", to: "*", id: staleId, replyTo: "", ts, hash: identity.bodyHash(staleBody), epoch: 1 }) }));
  await waitFor(() => frames.some((f) => f.id === staleId), "the stale-epoch refusal after the committed retry");
  assert.match(frames.find((f) => f.id === staleId)?.error || "", /stale epoch 1.*epoch 2/);
  fay2.close();
});
