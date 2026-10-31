// Joining and leaving without a terminal: the invite code the app shows, the old curl command it still
// accepts, and logging out (the admin key stays, so an admin who logs out is still the admin on return).

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { test } from "node:test";

import { Invite } from "../lib/protocol.mjs";

const SECRET = "a".repeat(43);
const BIN = resolve(import.meta.dirname, "../bin/chamber.mjs");

test("an invite code, a link and the old curl command all name the same chamber", () => {
  for (const text of [
    `https://relay.example.workers.dev/#${SECRET}~pass`,
    `  https://relay.example.workers.dev/#${SECRET}~pass\n`,
    `curl -fsSL https://relay.example.workers.dev/install | sh -s -- ${SECRET}~pass`,
  ]) {
    const invite = Invite.fromInput(text);
    assert.equal(invite.relay, "https://relay.example.workers.dev");
    assert.equal(invite.secret, SECRET);
    assert.equal(invite.pass, "pass");
  }
  assert.throws(() => Invite.fromInput("not an invite"));
  // Only the key, without the relay address: say what is missing.
  assert.throws(() => Invite.fromInput(`${SECRET}~pass.sig`), /missing the start/);
});

test("logout archives the config, removes it, and keeps the admin key", () => {
  const home = mkdtempSync(join(tmpdir(), "chamber-logout-"));
  writeFileSync(join(home, "config.json"), JSON.stringify({ invite: `https://relay.example.workers.dev/#${SECRET}`, owner: "tester" }));
  writeFileSync(join(home, "relay.json"), JSON.stringify({ relay: "https://relay.example.workers.dev", secret: SECRET }));
  execFileSync(process.execPath, [BIN, "logout"], { env: { ...process.env, CHAMBER_HOME: home } });
  assert.equal(existsSync(join(home, "config.json")), false, "the chamber is gone from this machine");
  assert.equal(existsSync(join(home, "relay.json")), true, "the admin key stays");
  const [archive] = readdirSync(join(home, "logged-out"));
  assert.deepEqual(readdirSync(join(home, "logged-out", archive)).sort(), ["config.json", "relay.json"]);
});
