// Regression coverage for the approval-pause warning lifecycle in buildSteps:
// a needs-owner warning is current ONLY while the step has not run again.
import assert from "node:assert/strict";
import { test } from "node:test";

import { buildSteps } from "./model.ts";
import type { MissionDetail } from "../api";

const WARN = "codex/pm is not on the team's unattended list, so this run needs your approval before it starts";

const detail = (journal: Array<Record<string, unknown>>): MissionDetail =>
  ({
    mission: { id: "m1", goal: "g",
               steps: [{ id: "plan", role: "pm", adapter: "codex", goal: "g", brief: "g" }] },
    status: { status: "done", steps: [] },
    dynamic_steps: [],
    journal,
    live: { running: false },
  }) as unknown as MissionDetail;

const statuses = (journal: Array<Record<string, unknown>>) =>
  buildSteps(detail(journal), null, {})[0].items.filter((i) => i.type === "status");

test("an approval pause goes quiet past-tense once the approved run dispatches", () => {
  const items = statuses([
    { event: "dispatched", step: "plan", ts: 1 },
    { event: "needs-owner", step: "plan", ts: 2, reason: WARN },
    { event: "dispatched", step: "plan", ts: 3 },
    { event: "settled", step: "plan", ts: 4, status: "done", answer: "Published the build." },
  ]);
  assert.equal(items.length, 1);
  assert.equal(items[0].type === "status" && items[0].tone, "info");
  assert.ok(items[0].type === "status" && items[0].text.includes("paused for owner review"),
            `past-tense text, got: ${items[0].type === "status" ? items[0].text : ""}`);
});

test("a warning with no later run stays current and untouched", () => {
  const items = statuses([
    { event: "dispatched", step: "plan", ts: 1 },
    { event: "needs-owner", step: "plan", ts: 2, reason: WARN },
  ]);
  assert.equal(items.length, 1);
  assert.equal(items[0].type === "status" && items[0].tone, "warn");
  assert.ok(items[0].type === "status" && items[0].text.includes("not on the team's unattended list"));
});

test("a second pause after a resumed run warns again on its own", () => {
  const items = statuses([
    { event: "dispatched", step: "plan", ts: 1 },
    { event: "needs-owner", step: "plan", ts: 2, reason: WARN },
    { event: "dispatched", step: "plan", ts: 3 },
    { event: "worker-progress", step: "plan", ts: 3.5, kind: "message", text: "Working" },
    { event: "needs-owner", step: "plan", ts: 4, reason: WARN },
  ]);
  assert.equal(items.length, 2);
  assert.equal(items[0].type === "status" && items[0].tone, "info");
  assert.equal(items[1].type === "status" && items[1].tone, "warn");
});


test("dispatch alone does not claim approval or quiet a still-blocked warning", () => {
  const items = statuses([
    { event: "needs-owner", step: "plan", ts: 1, reason: WARN },
    { event: "dispatched", step: "plan", ts: 2 },
  ]);
  assert.equal(items[0].tone, "warn");
  assert.equal(items[0].text, WARN);
});

test("repeated blocked dispatch preserves both unresolved warnings", () => {
  const items = statuses([
    { event: "needs-owner", step: "plan", ts: 1, reason: WARN },
    { event: "dispatched", step: "plan", ts: 2 },
    { event: "needs-owner", step: "plan", ts: 3, reason: WARN },
  ]);
  assert.deepEqual(items.map(i => i.tone), ["warn", "warn"]);
});

test("worker progress quiets history without inventing the authorization route", () => {
  const items = statuses([
    { event: "needs-owner", step: "plan", ts: 1, reason: WARN },
    { event: "dispatched", step: "plan", ts: 2 },
    { event: "worker-progress", step: "plan", ts: 3, kind: "tool", text: "Read" },
  ]);
  assert.equal(items[0].tone, "info");
  assert.doesNotMatch(items[0].text, /approved/);
});
