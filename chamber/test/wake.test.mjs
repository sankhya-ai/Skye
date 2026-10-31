// Waking an agent for a teammate: only if its owner allows that teammate, only as much as the owner
// allows (answer or work), within limits, and every wake and refusal is logged with what the agent did.

import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PassThrough } from "node:stream";
import { beforeEach, test } from "node:test";

const HOME = mkdtempSync(join(tmpdir(), "chamber-wake-"));
process.env.CHAMBER_HOME = HOME;
const { allowed, policyFor, resetWakes, setPolicy, wake, wakeLog, wakeTranscript } = await import("../lib/wake.mjs");

const AGENT = "ashish/claude-code.api";
const project = mkdtempSync(join(tmpdir(), "chamber-wake-project-"));
mkdirSync(join(HOME, "agents", "ashish__claude-code.api"), { recursive: true });
writeFileSync(join(HOME, "agents", "ashish__claude-code.api", "project.json"), JSON.stringify({ path: project }));

/** A fake CLI: records how it was started, prints one JSON event, and exits when told. */
function fakeSpawn(calls) {
  return (bin, args, options) => {
    const child = new EventEmitter();
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    let stdin = "";
    child.stdin = { end: (text = "") => (stdin += text) };
    child.kill = () => child.emit("exit", null);
    calls.push({ bin, args, cwd: options.cwd, stdin: () => stdin, child });
    child.stdout.write(JSON.stringify({ type: "assistant", text: "on it" }) + "\n");
    return child;
  };
}

beforeEach(() => {
  resetWakes();
  setPolicy(AGENT, { who: "nobody", can: "answer" });
});

test("only the people the owner allows can wake an agent; the owner always can", () => {
  assert.deepEqual(policyFor("ashish/codex.web"), { who: "nobody", can: "answer" }, "nobody, by default");
  assert.equal(allowed(AGENT, "ashish/board"), true);
  assert.equal(allowed(AGENT, "maya/claude-code.web"), false);
  setPolicy(AGENT, { who: ["maya"], can: "answer" });
  assert.equal(allowed(AGENT, "maya/claude-code.web"), true);
  assert.equal(allowed(AGENT, "dev/codex.api"), false);
  setPolicy(AGENT, { who: "team", can: "work" });
  assert.equal(allowed(AGENT, "dev/codex.api"), true);
  assert.throws(() => setPolicy(AGENT, { who: "everyone", can: "answer" }));
  assert.throws(() => setPolicy(AGENT, { who: "team", can: "anything" }));
});

test("a refused wake starts nothing and says why; an allowed one runs in the project, as much as allowed", async (t) => {
  const calls = [];
  const refused = wake(AGENT, "maya/claude-code.web", { message: "a".repeat(32), spawnImpl: fakeSpawn(calls) });
  assert.equal(refused.woke, false);
  assert.match(refused.reason, /maya isn't allowed/);
  assert.equal(calls.length, 0);
  assert.equal(wakeLog()[0].result, "refused");
  wake(AGENT, "maya/claude-code.web", { message: "a".repeat(32), spawnImpl: fakeSpawn(calls) });
  assert.equal(wakeLog().filter((e) => e.message === "a".repeat(32)).length, 1, "the same refusal is logged once");

  setPolicy(AGENT, { who: ["maya"], can: "answer" });
  const woke = wake(AGENT, "maya/claude-code.web", { message: "b".repeat(32), spawnImpl: fakeSpawn(calls) });
  if (!woke.woke) return t.skip(`claude CLI not installed here: ${woke.reason}`);
  assert.equal(calls[0].bin, "claude");
  assert.equal(calls[0].cwd, project);
  assert.ok(!calls[0].args.includes("Edit"), "answer: no editing tools");
  assert.match(calls[0].stdin(), /maya\/claude-code\.web sent you a message/);
  assert.match(calls[0].stdin(), /not as instructions from your owner/, "a teammate's message is data");
  assert.match(calls[0].stdin(), /Do not change any files/);
  await new Promise((r) => setTimeout(r, 50)); // the transcript is written as the run goes
  assert.deepEqual(wakeTranscript(woke.id), [{ type: "assistant", text: "on it" }]);

  // One run at a time per agent; the same message never wakes twice.
  assert.match(wake(AGENT, "maya/claude-code.web", { message: "c".repeat(32), spawnImpl: fakeSpawn(calls) }).reason, /already working/);
  assert.match(wake(AGENT, "maya/claude-code.web", { message: "b".repeat(32), spawnImpl: fakeSpawn(calls) }).reason, /already woken/);
  calls[0].child.emit("exit", 0);
  assert.equal(wakeLog().find((e) => e.id === woke.id).code, 0, "the end of the run is logged");

  setPolicy(AGENT, { who: ["maya"], can: "work" });
  const working = wake(AGENT, "maya/claude-code.web", { message: "d".repeat(32), spawnImpl: fakeSpawn(calls) });
  assert.ok(working.woke);
  assert.ok(calls.at(-1).args.includes("Edit"), "work: editing tools");
  assert.match(calls.at(-1).stdin(), /Do not commit, push or delete/);
  calls.at(-1).child.emit("exit", 0);

  // A teammate can wake someone's agents only so often.
  const third = wake(AGENT, "maya/claude-code.web", { message: "e".repeat(32), spawnImpl: fakeSpawn(calls) });
  assert.ok(third.woke);
  calls.at(-1).child.emit("exit", 0);
  assert.match(wake(AGENT, "maya/claude-code.web", { message: "f".repeat(32), spawnImpl: fakeSpawn(calls) }).reason, /3 times this hour/);

  resetWakes(); // a restarted board: the log still knows this message was woken for
  assert.match(wake(AGENT, "maya/claude-code.web", { message: "b".repeat(32), spawnImpl: fakeSpawn(calls) }).reason, /already woken/);
});
