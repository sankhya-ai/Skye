// The relay on your own server (relay/serve.mjs, what vm.sh runs) keeps everything: messages, files
// and the chamber itself survive everyone disconnecting and the relay restarting. The same relay
// on Cloudflare's settings wipes files once nobody is connected.

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtempSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { after, test } from "node:test";

import { LineClient } from "../lib/client.mjs";
import { Invite } from "../lib/protocol.mjs";

const SERVE = resolve(import.meta.dirname, "..", "relay", "serve.mjs");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const running = new Set();

const freePort = () => new Promise((done) => {
  const server = createServer().listen(0, "127.0.0.1", () => {
    const { port } = server.address();
    server.close(() => done(port));
  });
});

/** Start serve.mjs on `data`; resolves to its address once it answers. */
async function serve(data, port, env = {}) {
  const child = spawn(process.execPath, [SERVE], {
    env: { ...process.env, HOST: "127.0.0.1", PORT: String(port), CHAMBER_RELAY_DATA: data, CHAMBER_RELAY_VARS: "IDLE_WIPE_SECONDS:1,BLOB_RETENTION_HOURS:0.0001", ...env },
    stdio: "ignore",
    detached: true,
  });
  running.add(child);
  const url = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 120; i++) {
    try {
      if ((await fetch(`${url}/v1/relay`)).ok) return { url, stop: () => stopRelay(child) };
    } catch {}
    await sleep(500);
  }
  throw new Error("relay did not start");
}

async function stopRelay(child) {
  try { process.kill(-child.pid, "SIGTERM"); } catch {}
  running.delete(child);
  await sleep(1000);
}

after(() => {
  for (const child of running) try { process.kill(-child.pid, "SIGKILL"); } catch {}
});

async function connect(invite, agent, options = {}) {
  const client = new LineClient(invite, agent, options);
  client.run();
  await client.ready();
  return client;
}

/** Send a file big enough for the relay's file store, let everyone leave past the wipe, and look again. */
async function fileAfterEveryoneLeft(relayUrl, restart) {
  const invite = await Invite.create(relayUrl).ready();
  const got = [];
  const ash = await connect(invite, "ashish/claude-code.api", { kind: "claude-code", answers: true, onMessage: (m) => got.push(m) });
  const maya = await connect(invite, "maya/claude-code.web", { kind: "claude-code", answers: true });
  const data = new Uint8Array(randomBytes(65536 + 10));
  await maya.send("ashish/claude-code.api", "the schema", { files: [{ name: "schema.bin", data }] });
  await sleep(500);
  assert.equal(got[0]?.files?.[0]?.size ?? got[0]?.files?.[0]?.data?.length, data.length, "delivered with its file");
  ash.close();
  maya.close();
  await sleep(4000); // past the idle wipe (1s) and the file's retention (~0.4s)
  const url = await restart();
  const later = new LineClient(await Invite.parse(`${url}/#${invite.secret}`).ready(), "ashish/laptop", { kind: "human", listens: true });
  const history = [];
  try {
    await later.syncHistory({ onMessage: (m) => history.push(m) });
  } catch (err) {
    return { history, error: err.message };
  }
  return { history };
}

test("on your own server: messages and files survive everyone leaving and a restart", async () => {
  const data = mkdtempSync(join(tmpdir(), "chamber-vm-"));
  const port = await freePort();
  let relay = await serve(data, port);
  const { history, error } = await fileAfterEveryoneLeft(relay.url, async () => {
    await relay.stop();
    relay = await serve(data, port);
    return relay.url;
  });
  assert.equal(error, undefined);
  const message = history.find((m) => m.text === "the schema");
  assert.ok(message, "the message is still there after the restart");
  assert.equal(message.files[0].name, "schema.bin");
  assert.ok(!message.files[0].error, "and so is its file");
  await relay.stop();
});

test("with Cloudflare's settings (CHAMBER_KEEP_FILES=0) the file is wiped once nobody is connected", async () => {
  const data = mkdtempSync(join(tmpdir(), "chamber-vm-wipe-"));
  const port = await freePort();
  const relay = await serve(data, port, { CHAMBER_KEEP_FILES: "0" });
  const { history, error } = await fileAfterEveryoneLeft(relay.url, async () => relay.url);
  const message = history.find((m) => m.text === "the schema");
  assert.ok(error || !message || message.files[0]?.error || !message.files[0]?.data, "the file is gone");
  await relay.stop();
});
