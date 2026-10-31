// Chamber Cloud: one hosted relay serves many chambers. Each registers itself with its own admin
// key and gets a trial; after the trial its members need an active plan with a seat each, which
// only the billing service (HOSTED_SECRET) can set. The admin can always manage the chamber.

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { after, before, test } from "node:test";

import { claimRelay, issuePass } from "../lib/admin.mjs";
import { LineClient } from "../lib/client.mjs";
import { Invite } from "../lib/protocol.mjs";

const SERVE = resolve(import.meta.dirname, "..", "relay", "serve.mjs");
const SECRET = "billing-secret-for-tests";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let relay;
let child;

before(async () => {
  const port = await new Promise((done) => {
    const server = createServer().listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => done(port));
    });
  });
  child = spawn(process.execPath, [SERVE], {
    env: { ...process.env, HOST: "127.0.0.1", PORT: String(port), CHAMBER_RELAY_DATA: mkdtempSync(join(tmpdir(), "chamber-hosted-")), CHAMBER_RELAY_VARS: `HOSTED:1,HOSTED_SECRET:${SECRET},TRIAL_SECONDS:4` },
    stdio: "ignore",
    detached: true,
  });
  relay = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 120; i++) {
    try {
      if ((await fetch(`${relay}/v1/relay`)).ok) return;
    } catch {}
    await sleep(500);
  }
  throw new Error("relay did not start");
});

after(() => {
  try { process.kill(-child.pid, "SIGKILL"); } catch {}
  setTimeout(() => process.exit(0), 100).unref();
});

/** Connect as `agent` with a pass; resolves to null once in, or to the relay's refusal. */
async function tryConnect(invite, agent) {
  const client = new LineClient(invite, agent, { kind: "human", listens: true });
  client.run();
  for (let i = 0; i < 40; i++) {
    if (client.connected) {
      client.close();
      return null;
    }
    if (client.problem) {
      client.close();
      return client.problem;
    }
    await sleep(100);
  }
  client.close();
  return "timed out";
}

const status = (lineId, auth = SECRET) => fetch(`${relay}/v1/hosted/status?line=${lineId}`, { headers: { authorization: `Bearer ${auth}` } }).then((r) => r.json());
const billing = (body, auth = SECRET) => fetch(`${relay}/v1/hosted/billing`, { method: "POST", headers: { authorization: `Bearer ${auth}`, "content-type": "application/json" }, body: JSON.stringify(body) });

test("many chambers on one hosted relay, each with its own admin, a trial, then seats", async () => {
  assert.deepEqual(await (await fetch(`${relay}/v1/relay`)).json(), { claimed: false, hosted: true, files: true, relay: 9 });

  // Two teams register; each is its own chamber with its own admin key.
  const a = await Invite.create(relay).ready();
  const b = await Invite.create(relay).ready();
  const adminA = await claimRelay(a);
  const adminB = await claimRelay(b);
  assert.ok(adminA.key && adminB.key);
  await assert.rejects(claimRelay(a).then(() => { throw new Error("re-claimed"); }).catch((e) => { throw e; }), /already exists|re-claimed/, "someone else can't take over a chamber");

  // One pass per person, as the admin's invite gives them.
  const passes = new Map();
  const member = (invite, key, who) => {
    const id = `${invite.lineId}:${who}`;
    if (!passes.has(id)) passes.set(id, invite.withPass(issuePass(key.private, invite.lineId)));
    return passes.get(id);
  };
  assert.equal(await tryConnect(member(a, adminA.key, "ashish"), "ashish/board"), null, "in the trial, members connect");
  assert.equal(await tryConnect(member(b, adminB.key, "nitin"), "nitin/board"), null);
  assert.match(await tryConnect(member(a, adminB.key, "mallory"), "mallory/board"), /personal invite/, "B's admin can't let anyone into A");

  let s = await status(a.lineId);
  assert.equal(s.trial, true);
  assert.equal(s.members, 1);
  assert.equal((await status(a.lineId, "wrong")).error, "unauthorized");
  assert.equal((await status(a.lineId, a.token)).trial, true, "a member can read their chamber's plan with its token");
  assert.equal((await billing({ line: a.lineId, status: "active", seats: 9 }, "wrong")).status, 401, "only the billing service sets plans");

  // The trial ends: members are refused with the reason; the admin can still manage the chamber.
  await sleep(4500);
  assert.match(await tryConnect(member(a, adminA.key, "ashish"), "ashish/board"), /7-day trial has ended.*subscribe/);
  const members = await fetch(`${relay}/v1/lines/${a.lineId}/members`, { headers: a.auth });
  assert.equal(members.status, 200, "managing the chamber still works");

  // A plan with 2 seats: the existing member and one more get in; a third is told to add a seat.
  assert.equal((await billing({ line: a.lineId, status: "active", seats: 2, paidUntil: Date.now() + 30 * 864e5 })).status, 200);
  assert.equal(await tryConnect(member(a, adminA.key, "ashish"), "ashish/board"), null);
  assert.equal(await tryConnect(member(a, adminA.key, "maya"), "maya/board"), null);
  assert.match(await tryConnect(member(a, adminA.key, "dev"), "dev/board"), /2 seats, all taken/);
  s = await status(a.lineId);
  assert.deepEqual([s.status, s.seats, s.members, s.access], ["active", 2, 2, true]);

  // B never paid: still locked out, separately from A.
  assert.match(await tryConnect(member(b, adminB.key, "nitin"), "nitin/board"), /trial has ended/);
});
