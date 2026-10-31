import assert from "node:assert/strict";
import { test } from "node:test";

import { stepDuration, stepMeta, tidyThought } from "./meta.ts";
import type { ToolBlock } from "./transcript.ts";

const step = (name: string, result: any, extra: Partial<ToolBlock> = {}): ToolBlock => ({
  kind: "tool",
  key: "k",
  turn: null,
  id: "1",
  name,
  args: "{}",
  result: result && { is_error: false, output: "", message: "", display: [], ...result },
  ...extra,
});

test("reads say how much they read, or that they were already read", () => {
  assert.equal(stepMeta(step("ReadFile", { message: "598 lines read from file starting from line 1. End of file reached." })).text, "598 lines");
  assert.equal(stepMeta(step("ReadFile", { display: [{ type: "brief", text: "Repeated read suppressed" }] })).text, "already read");
});

test("edits count added and removed lines", () => {
  const meta = stepMeta(step("StrReplaceFile", { display: [{ type: "diff", path: "a", old_text: "a\nb\nc\n", new_text: "a\nB\nc\nd\n" }] }));
  assert.deepEqual([meta.added, meta.removed], [2, 1]);
});

test("commands show their exit code when they fail and output size when not", () => {
  const failed = stepMeta(step("Shell", { is_error: true, message: "Command failed with exit code: 2." }));
  assert.deepEqual([failed.text, failed.error], ["exit 2", true]);
  assert.equal(stepMeta(step("Shell", { output: "a\nb\n" })).text, "2 lines");
  assert.equal(stepMeta(step("Shell", { output: "" })).text, "");
});

test("searches, the running and the rejected", () => {
  assert.equal(stepMeta(step("Grep", { message: "No matches found" })).text, "no matches");
  assert.equal(stepMeta(step("Glob", { output: "a.py\nb.py" })).text, "2 files");
  assert.equal(stepMeta(step("Shell", undefined)).text, "");
  assert.equal(stepMeta(step("Shell", undefined, { approval: { status: "reject", auto: false, request: {} as any } })).text, "rejected");
});

test("durations are shown only when worth saying", () => {
  assert.equal(stepDuration(step("Shell", {}, { started: 10, finished: 10.4 })), "");
  assert.equal(stepDuration(step("Shell", {}, { started: 10, finished: 14.25 })), "4.3s");
  assert.equal(stepDuration(step("Shell", {}, { started: 0, finished: 95 })), "1m 35s");
});

test("thinking broken one word per line reads as prose; real paragraphs survive", () => {
  assert.equal(tidyThought("The\n file\n\n\n content\n\n\n was\n\n\n already\n shown."), "The file content was already shown.");
  assert.equal(tidyThought("First, read the file.\n\n\n\nThen run the tests."), "First, read the file.\n\nThen run the tests.");
  assert.equal(tidyThought("One line of thought."), "One line of thought.");
});

import { sessionNews } from "./native.ts";

test("the app hears about finished turns and new approvals between polls", () => {
  const before = new Map([
    ["a", { running: true, pending: 0 }],
    ["b", { running: true, pending: 0 }],
  ]);
  const news = sessionNews(before, [
    { id: "a", title: "Fix CI", running: false, pending_approvals: 0 },
    { id: "b", title: "Refactor", running: true, pending_approvals: 1 },
    { id: "c", title: "New", running: false },
  ]);
  assert.deepEqual(news.finished, [{ id: "a", title: "Fix CI" }]);
  assert.deepEqual(news.asking, [{ id: "b", title: "Refactor" }]);
  assert.equal(news.pending, 1);
});
