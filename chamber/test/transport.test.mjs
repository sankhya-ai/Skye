// Where WebSockets are refused (a company proxy, Maritime's public URLs), the client reaches the
// relay by long polling over plain HTTPS instead, on its own, and messages still arrive at once.

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { createServer, request } from "node:http";
import { createServer as netServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { after, before, test } from "node:test";

import { LineClient } from "../lib/client.mjs";
import { Invite } from "../lib/protocol.mjs";

const SERVE = resolve(import.meta.dirname, "..", "relay", "serve.mjs");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const freePort = () => new Promise((done) => {
  const server = netServer().listen(0, "127.0.0.1", () => {
    const { port } = server.address();
    server.close(() => done(port));
  });
});

let relay;
let proxy;
let blocked;
const clients = [];

before(async () => {
  const port = await freePort();
  relay = spawn(process.execPath, [SERVE], {
    env: { ...process.env, CHAMBER_TRANSPORT: "", HOST: "127.0.0.1", PORT: String(port), CHAMBER_RELAY_DATA: mkdtempSync(join(tmpdir(), "chamber-transport-")) },
    stdio: "ignore",
    detached: true,
  });
  for (let i = 0; i < 120; i++) {
    try {
      if ((await fetch(`http://127.0.0.1:${port}/v1/relay`)).ok) break;
    } catch {}
    await sleep(500);
  }
  // A proxy that passes HTTP through and refuses every WebSocket upgrade, like Maritime's edge.
  blocked = 0;
  proxy = createServer((req, res) => {
    const upstream = request({ host: "127.0.0.1", port, path: req.url, method: req.method, headers: req.headers }, (answer) => {
      res.writeHead(answer.statusCode, answer.headers);
      answer.pipe(res);
    });
    upstream.on("error", () => res.destroy());
    req.pipe(upstream);
  });
  proxy.on("upgrade", (req, socket) => {
    blocked++;
    socket.end("HTTP/1.1 403 Forbidden\r\ncontent-length: 0\r\n\r\n");
  });
  await new Promise((done) => proxy.listen(0, "127.0.0.1", done));
});

after(() => {
  for (const client of clients) client.close();
  proxy?.close();
  try { process.kill(-relay.pid, "SIGKILL"); } catch {}
  setTimeout(() => process.exit(0), 100).unref();
});

test("with WebSockets refused, clients switch to long polling by themselves and talk without delay", async () => {
  delete process.env.CHAMBER_TRANSPORT;
  const invite = await Invite.create(`http://127.0.0.1:${proxy.address().port}`).ready();
  const got = [];
  const ash = new LineClient(invite, "ashish/claude-code.api", { kind: "claude-code", answers: true, onMessage: (m) => got.push(m) });
  const maya = new LineClient(invite, "maya/board", { kind: "human", listens: true });
  clients.push(ash, maya);
  ash.run();
  maya.run();
  await ash.ready(20000);
  await maya.ready(20000);
  assert.equal(ash.transport, "poll");
  assert.equal(maya.transport, "poll");
  assert.ok(blocked >= 2, "each tried a WebSocket first");
  assert.ok(maya.roster.some((a) => a.agent === "ashish/claude-code.api"), "they see each other");

  const sentAt = Date.now();
  const receipt = await maya.send("ashish/claude-code.api", "Is expires_at in seconds?");
  assert.deepEqual(receipt.delivered, ["ashish/claude-code.api"]);
  while (!got.length && Date.now() - sentAt < 5000) await sleep(20);
  assert.equal(got[0]?.text, "Is expires_at in seconds?");
  assert.ok(Date.now() - sentAt < 2000, "a waiting long poll answers at once, not after its timeout");

  // A reply goes the other way, and the ask that waits for it gets it.
  ash.onMessage = (m) => ash.send(m.from, "Seconds.", { replyTo: m.id });
  const { reply } = await maya.ask("ashish/claude-code.api", "And the refresh token?", { timeout: 10000 });
  assert.equal(reply?.text, "Seconds.");

  // Leaving: the roster hears of it.
  ash.close();
  await sleep(1500);
  assert.ok(!maya.roster.some((a) => a.agent === "ashish/claude-code.api"));
});
