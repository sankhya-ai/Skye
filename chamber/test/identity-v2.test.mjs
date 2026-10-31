// Identity v2 end to end against the real relay (workerd, via `wrangler dev`): member keys,
// enrollment, signed envelopes, and the whole adversarial matrix from docs/identity-v2.md.
// v2 proves WHO said it; it is NOT read confidentiality (the shared line key still opens every
// body): brick three (read-scope key wrapping/rotation) stays explicitly incomplete.

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync, mkdtempSync, statSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { after, before, test } from "node:test";

process.env.CHAMBER_HOME = mkdtempSync(join(tmpdir(), "chamber-v2-home-")); // member keys land here (0600)

const ROOT = resolve(import.meta.dirname, "..");
const WRANGLER = join(ROOT, "relay", "node_modules", ".bin", "wrangler");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const tmp = (name) => mkdtempSync(join(tmpdir(), `chamber-v2-${name}-`));
const hex32 = () => randomBytes(16).toString("hex");
const clients = new Set();

const { Invite } = await import("../lib/protocol.mjs");
const { LineClient } = await import("../lib/client.mjs");
const { newAdminKey, issuePass, issueCertificate, revocation } = await import("../lib/admin.mjs");
const identity = await import("../lib/identity.mjs");

let relay; // http url
let relayProc;
const admin = newAdminKey();

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
  const port = await freePort();
  relayProc = spawn(WRANGLER, ["dev", "--local", "--ip", "127.0.0.1", "--port", String(port), "--persist-to", tmp("relay"), "--var", `ADMIN_KEY:${admin.public}`], {
    cwd: join(ROOT, "relay"), stdio: "ignore", detached: true,
  });
  relay = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 120; i++) {
    try {
      if ((await fetch(relay)).ok) break;
    } catch {}
    await sleep(500);
  }
});

after(() => {
  for (const c of clients) c.close(); // close() wakes the retry backoff, so the process exits on its own
  if (relayProc) process.kill(-relayProc.pid, "SIGTERM");
});

/** A v2 member: key on this machine (0600), the admin's certificate, the full invite. */
async function v2member(chamber, owner) {
  identity.enrollOwner(chamber.lineId, owner);
  const key = identity.loadMemberKey(chamber.lineId, owner); // the full keypair, for crafting raw frames
  const cert = issueCertificate(admin.private, chamber.lineId, owner, key.public);
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

/** Frames a client got on its socket (sent/error), for raw-frame assertions. */
function sniff(client) {
  const frames = [];
  client.ws.addEventListener("message", (event) => {
    try {
      const f = JSON.parse(String(event.data));
      if (f.t === "sent" || f.t === "error") frames.push(f);
    } catch {}
  });
  return frames;
}

/** A raw v2 `send` frame, sealed and signed exactly like client.send would. */
async function rawFrame(invite, key, agent, { id = hex32(), to = "*", ts = Date.now(), body = null, text = "hello" } = {}) {
  const sealed = body || (await invite.seal({ text, files: [], thread: id, title: "", type: "message", patch: null, branch: null }, { sender: agent, to, id, replyTo: null }));
  const sig = identity.signEnvelope(key.private, { line: invite.lineId, from: agent, to, id, replyTo: "", ts, hash: identity.bodyHash(sealed) });
  return { frame: { t: "send", id, to, body: sealed, mentions: [], ts, sig }, sealed, sig };
}

// ----- the shared v2 chamber for the message-level rows ------------------------------------------

let lineX, alice, bob; // members: ashish + nitin, enrolled with their own keys

test("setup: a v2 chamber, two enrolled members, signed happy path", async () => {
  lineX = await Invite.create(relay).ready();
  alice = await v2member(lineX, "ashish");
  bob = await v2member(lineX, "nitin");
  const a = await tryJoin(alice.invite, "ashish/board");
  assert.equal(a.connected, true, "ashish joins with her certificate");
  assert.equal(a.lineVersion, 2, "the welcome names the line version");
  assert.match(a.identityNote, /v2/);
  const b = await tryJoin(bob.invite, "nitin/board");
  assert.equal(b.connected, true);
  let got = null;
  b.onMessage = (m) => (got = m);
  await a.send("*", "signed hello from ashish");
  for (let i = 0; i < 30 && !got; i++) await sleep(100);
  assert.equal(got?.text, "signed hello from ashish", "a properly signed message verifies and lands");
  assert.equal(got?.from, "ashish/board");
  alice.client = a;
  bob.client = b;
});

test("1. impersonation and name binding: a certificate is one member, one name, one key", async () => {
  // ashish's certificate cannot join as nitin: the CLIENT refuses at construction...
  assert.throws(() => new LineClient(alice.invite, "nitin/board"), /certificate is for ashish/);
  // ...and the RELAY refuses a bare socket that tries the same (no client-side guard helping it).
  const refusal = await new Promise((resolve) => {
    const ws = new WebSocket(lineX.wsUrl, { headers: lineX.auth });
    ws.onopen = () => ws.send(JSON.stringify({ t: "hello", agent: "nitin/board", pass: alice.cert }));
    ws.onmessage = (event) => {
      const f = JSON.parse(String(event.data));
      if (f.t === "error") {
        ws.close();
        resolve(f.error);
      }
    };
    ws.onerror = () => resolve("socket error");
  });
  assert.match(refusal, /certificate is for ashish/);
  // ...and a frame signed by a NON-enrolled key claiming to be ashish is dropped by clients,
  // even carrying a real sealed body from ashish.
  const attacker = identity.newMemberKey();
  const id = hex32();
  const ts = Date.now();
  const { sealed } = await rawFrame(alice.invite, alice.key, "ashish/board", { id, ts, text: "real body" });
  let got = null;
  bob.client.onMessage = (m) => (got = m);
  const forgedSig = identity.signEnvelope(attacker.private, { line: lineX.lineId, from: "ashish/board", to: "*", id, replyTo: "", ts, hash: identity.bodyHash(sealed) });
  await bob.client.testFrame({ t: "msg", seq: 90001, id, from: "ashish/board", to: "*", to_owner: null, reply_to: null, body: sealed, ts, sig: forgedSig });
  assert.equal(got, null, "a frame signed by a non-enrolled key is dropped");
  await bob.client.testFrame({ t: "msg", seq: 90002, id: hex32(), from: "ashish/board", to: "*", to_owner: null, reply_to: null, body: sealed, ts, sig: "" });
  assert.equal(got, null, "an unsigned frame on a v2 line is dropped");
  // Positive control: the same shape signed by ashish's real key IS accepted.
  await bob.client.testFrame({ t: "msg", seq: 90003, id: hex32(), from: "ashish/board", to: "*", to_owner: null, reply_to: null, body: sealed, ts, sig: identity.signEnvelope(alice.key.private, { line: lineX.lineId, from: "ashish/board", to: "*", id: "90003".padEnd(32, "0").slice(0, 32), replyTo: "", ts, hash: identity.bodyHash(sealed) }) });
  // (control frame carries a mismatched id inside the signature on purpose: it must ALSO be dropped)
  assert.equal(got, null, "an id-tampered signature is dropped");
  const good = await rawFrame(alice.invite, alice.key, "ashish/board", { text: "real body" });
  await bob.client.testFrame({ t: "msg", seq: 90004, id: good.frame.id, from: "ashish/board", to: "*", to_owner: null, reply_to: null, body: good.frame.body, ts: good.frame.ts, sig: good.frame.sig });
  assert.equal(got?.text, "real body", "a correctly signed frame verifies");
});

test("2. replay: the same id is rejected, and an old timestamp with a new id is rejected", async () => {
  const frames = sniff(alice.client);
  const id = hex32();
  const { frame } = await rawFrame(alice.invite, alice.key, "ashish/board", { id, text: "send once" });
  alice.client.ws.send(JSON.stringify(frame));
  for (let i = 0; i < 30 && !frames.some((f) => f.t === "sent" && f.id === id); i++) await sleep(100);
  assert.ok(frames.some((f) => f.t === "sent" && f.id === id), "the first send is accepted");
  alice.client.ws.send(JSON.stringify(frame)); // byte-identical replay
  for (let i = 0; i < 30 && !frames.some((f) => f.t === "error" && f.id === id); i++) await sleep(100);
  assert.match(frames.find((f) => f.t === "error" && f.id === id)?.error || "", /already used/);
  const stale = await rawFrame(alice.invite, alice.key, "ashish/board", { ts: Date.now() - 10 * 60 * 1000, text: "from the past" });
  alice.client.ws.send(JSON.stringify(stale.frame));
  for (let i = 0; i < 30 && !frames.some((f) => f.t === "error" && f.id === stale.frame.id); i++) await sleep(100);
  assert.match(frames.find((f) => f.t === "error" && f.id === stale.frame.id)?.error || "", /timestamp/);
});

test("3. cross-line replay: a frame valid on line X fails verification on line Y", async () => {
  const lineY = await Invite.create(relay).ready();
  const carol = await v2member(lineY, "ashish"); // same NAME, fresh key, other line
  const c = await tryJoin(carol.invite, "ashish/board");
  assert.equal(c.connected, true);
  // A certificate for line X does not admit to line Y at all (claims.line is checked)...
  const wrong = await tryJoin(await lineY.withCertificate(alice.cert, admin.public).ready(), "ashish/board");
  assert.equal(wrong.connected, false, "line X's certificate is worthless on line Y");
  assert.match(wrong.problem, /personal invite/);
  wrong.close();
  const frames = sniff(c);
  const { frame } = await rawFrame(alice.invite, alice.key, "ashish/board", { text: "valid on X only" });
  c.ws.send(JSON.stringify(frame)); // replayed on line Y
  for (let i = 0; i < 30 && !frames.some((f) => f.t === "error" && f.id === frame.id); i++) await sleep(100);
  assert.match(frames.find((f) => f.t === "error" && f.id === frame.id)?.error || "", /bad message signature/, "the line id is inside the signed payload");
  c.close();
});

test("4. revoked credentials: socket dropped, certificate dead, enrollment gone", async () => {
  const order = await fetch(`${relay}/v1/lines/${lineX.lineId}/admin`, { method: "POST", headers: { ...lineX.auth, "content-type": "application/json" }, body: JSON.stringify({ order: revocation(admin.private, lineX.lineId, "nitin") }) });
  assert.equal(order.status, 200);
  for (let i = 0; i < 40 && bob.client.connected; i++) await sleep(100);
  assert.equal(bob.client.connected, false, "the connected socket is dropped");
  const again = await tryJoin(bob.invite, "nitin/board");
  assert.equal(again.connected, false, "the certificate no longer admits");
  assert.match(again.problem, /removed/);
  again.close();
  const members = await (await fetch(`${relay}/v1/lines/${lineX.lineId}/members`, { headers: lineX.auth })).json();
  assert.equal(members.version, 2);
  assert.ok(!members.members.some((m) => m.owner === "nitin"), "the enrollment is gone");
  // And a raw signed frame from the revoked member never verifies at the relay again:
  // his socket is closed, and a fresh socket cannot admit (tested above).
});

test("5. legacy downgrade: v1 pass on a v2 line is refused; v2 on v1 runs v1 AND SAYS SO; requireV2 refuses", async () => {
  // v1 client on the v2 line: the explicit version error, never silent v1 semantics.
  const legacy = await tryJoin(await lineX.withPass(issuePass(admin.private, lineX.lineId)).ready(), "mia/board");
  assert.equal(legacy.connected, false);
  assert.match(legacy.problem, /needs chamber >= v2/);
  legacy.close();

  // A v1 line (claimed by a v1 pass): a v2-cert holder runs v1 semantics, loudly labelled.
  const lineV = await Invite.create(relay).ready();
  const mia = await tryJoin(await lineV.withPass(issuePass(admin.private, lineV.lineId)).ready(), "mia/board");
  assert.equal(mia.connected, true);
  assert.equal(mia.lineVersion, 1);
  const noah = await v2member(lineV, "noah");
  const n = await tryJoin(noah.invite, "noah/board");
  assert.equal(n.connected, true, "a v2 certificate still admits to a v1 line (it is a valid admin token)");
  assert.equal(n.lineVersion, 1, "but the line says what it is");
  assert.match(n.identityNote, /DOWNGRADE/);
  assert.match(n.identityNote, /shared key/);
  let got = null;
  mia.onMessage = (m) => (got = m);
  await n.send("*", "v1 semantics");
  for (let i = 0; i < 30 && !got; i++) await sleep(100);
  assert.equal(got?.text, "v1 semantics", "unsigned v1 frames flow on a v1 line");
  // A caller that REQUIRED v2 guarantees is refused instead of downgraded.
  const strict = await tryJoin(noah.invite, "noah/strict", { requireV2: true });
  assert.equal(strict.connected, false);
  assert.match(strict.problem, /v2 identity was required/);
  strict.close();
  mia.close();
  n.close();
});

test("6. enrollment authority: only the admin certifies; relay-supplied key substitution fails", async () => {
  const lineZ = await Invite.create(relay).ready();
  // A certificate signed by a NON-admin key is refused.
  const evil = newAdminKey();
  const fakeKey = identity.newMemberKey();
  const fakeCert = issueCertificate(evil.private, lineZ.lineId, "zoe", fakeKey.public);
  const zoe = await tryJoin(await lineZ.withCertificate(fakeCert, admin.public).ready(), "zoe/board");
  assert.equal(zoe.connected, false);
  assert.match(zoe.problem, /personal invite/);
  zoe.close();
  // A certificate for ANOTHER line is refused here.
  const cross = await tryJoin(await lineZ.withCertificate(alice.cert, admin.public).ready(), "ashish/board");
  assert.equal(cross.connected, false);
  cross.close();
  // Relay-supplied key substitution: a roster frame offering a substituted cert for ashish
  // (signed by the attacker's key) is rejected client-side; her verified key does not change.
  const before = bob.client.memberKeys["ashish"];
  const substituted = issueCertificate(evil.private, lineX.lineId, "ashish", fakeKey.public);
  await bob.client.testFrame({ t: "roster", agents: [], certs: { ashish: substituted } });
  assert.equal(bob.client.memberKeys["ashish"], before, "an unverifiable cert never replaces the enrolled key");
  // A forged frame signed with the substituted key is then dropped (row 1 covers the drop itself).
  // The real cert from the relay DOES verify: re-issuing the roster with alice's real cert is accepted.
  await bob.client.testFrame({ t: "roster", agents: [], certs: { ashish: alice.cert } });
  assert.equal(bob.client.memberKeys["ashish"], alice.key.public);
  // The invite never contains the member's PRIVATE key.
  assert.ok(!alice.invite.toString().includes(alice.key.private), "no private key material in the invite text");
  assert.ok(alice.invite.toString().includes(alice.cert), "the invite carries the certificate");
  assert.ok(alice.invite.toString().includes(admin.public), "and the admin's public key");
});

test("7. key custody: the private key is a 0600 local file and appears in no constructor output", async () => {
  const path = identity.keyPath(lineX.lineId, "ashish");
  assert.equal(statSync(path).mode & 0o777, 0o600, "the member key file is owner-only");
  const loaded = identity.loadMemberKey(lineX.lineId, "ashish");
  assert.equal(loaded.private, alice.key.private, "it round-trips");
  // Re-enrolling keeps the SAME key (no silent rotation that would orphan history).
  assert.equal(identity.enrollOwner(lineX.lineId, "ashish").public, alice.key.public);
  // Nothing that leaves the machine serializes the private key: invite, certificate, pubkey list.
  const bundle = JSON.stringify({ invite: alice.invite.toString(), cert: alice.cert, pubkey: alice.key.public });
  assert.ok(!bundle.includes(alice.key.private));
});
