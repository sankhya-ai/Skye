import assert from "node:assert/strict";
import { test } from "node:test";

import { lineDiff, parseUnifiedDiff } from "./diff.ts";
import { applyEvent, createTranscript, loadHistory, parseArgs, producedFiles } from "./transcript.ts";
import type { WireEvent } from "./types.ts";

let seq = 0;
function ev(type: string, payload: any = {}, turn = "t1"): WireEvent {
  seq += 1;
  return { seq, ts: 0, turn, type, payload };
}

test("streaming deltas become one text block and tool arguments assemble", () => {
  const state = createTranscript();
  applyEvent(state, ev("UserPrompt", { text: "hi", attachments: [], memory: [] }));
  for (const chunk of ["Hel", "lo"]) applyEvent(state, ev("ContentPart", { type: "text", text: chunk }));
  applyEvent(state, ev("ToolCall", { id: "c1", function: { name: "Shell", arguments: '{"com' } }));
  applyEvent(state, ev("ToolCallPart", { arguments_part: 'mand":"ls"}' }));
  applyEvent(state, ev("ToolResult", { tool_call_id: "c1", return_value: { is_error: false, output: "a\n", message: "", display: [] } }));
  assert.equal(state.running, true);
  assert.deepEqual(state.blocks.map((b) => b.kind), ["user", "text", "tool"]);
  const tool = state.blocks[2];
  assert.ok(tool.kind === "tool");
  assert.deepEqual(parseArgs(tool.args), { command: "ls" });
  assert.equal(tool.result?.output, "a\n");
  applyEvent(state, ev("TurnFinished", { status: "finished", duration: 1.5, files_touched: [] }));
  assert.equal(state.running, false);
});

test("approvals attach to their tool and resolve", () => {
  const state = createTranscript();
  applyEvent(state, ev("ToolCall", { id: "c1", function: { name: "WriteFile", arguments: "{}" } }));
  applyEvent(state, ev("ApprovalRequest", { id: "r1", tool_call_id: "c1", sender: "WriteFile", action: "edit file", description: "Write", display: [] }));
  const tool = state.blocks[0];
  assert.ok(tool.kind === "tool");
  assert.equal(tool.approval?.status, "pending");
  assert.equal(state.pending.size, 1);
  applyEvent(state, ev("ApprovalResolved", { request_id: "r1", response: "approve", auto: false }));
  assert.equal(tool.approval?.status, "approve");
  assert.equal(state.pending.size, 0);
});

test("a restarted turn drops the abandoned words but keeps the tools", () => {
  const state = createTranscript();
  applyEvent(state, ev("UserPrompt", { text: "go" }));
  applyEvent(state, ev("TurnBegin", { user_input: "go" }));
  applyEvent(state, ev("ContentPart", { type: "text", text: "draft that broke" }));
  applyEvent(state, ev("ToolCall", { id: "c1", function: { name: "ReadFile", arguments: "{}" } }));
  applyEvent(state, ev("TurnBegin", { user_input: "go" }));
  applyEvent(state, ev("ContentPart", { type: "text", text: "real answer" }));
  const kinds = state.blocks.map((b) => b.kind);
  assert.deepEqual(kinds, ["user", "tool", "text"]);
  const text = state.blocks[2];
  assert.ok(text.kind === "text" && text.text === "real answer");
});

test("todos come from SetTodoList display blocks; usage accumulates", () => {
  const state = createTranscript();
  applyEvent(state, ev("ToolCall", { id: "t", function: { name: "SetTodoList", arguments: "{}" } }));
  applyEvent(state, ev("ToolResult", { tool_call_id: "t", return_value: { is_error: false, output: "", message: "", display: [{ type: "todo", items: [{ title: "a", status: "done" }] }] } }));
  applyEvent(state, ev("StatusUpdate", { context_usage: 0.25, token_usage: { input_other: 100, output: 20, input_cache_read: 50, cost_usd: 0.01 } }));
  assert.deepEqual(state.todos, [{ title: "a", status: "done" }]);
  assert.equal(state.contextUsage, 0.25);
  assert.equal(state.usage.input, 150);
  assert.equal(state.usage.output, 20);
});

test("replayed events are ignored and pending approvals revive on load", () => {
  const events = [
    ev("ToolCall", { id: "c9", function: { name: "Shell", arguments: '{"command":"rm x"}' } }),
    ev("ApprovalRequest", { id: "r9", tool_call_id: "c9", sender: "Shell", action: "run command", description: "Run", display: [] }),
  ];
  const state = loadHistory(events, [events[1].payload]);
  assert.equal(state.pending.size, 1);
  assert.equal(applyEvent(state, events[0]), false);
  assert.equal(state.blocks.length, 1);
});

test("subagent events nest under the Task tool", () => {
  const state = createTranscript();
  applyEvent(state, ev("ToolCall", { id: "task1", function: { name: "Task", arguments: "{}" } }));
  applyEvent(state, ev("SubagentEvent", { task_tool_call_id: "task1", event: { type: "ToolCall", payload: { id: "s1", function: { name: "Grep", arguments: "{}" } } } }));
  const tool = state.blocks[0];
  assert.ok(tool.kind === "tool");
  assert.equal(tool.children?.blocks.length, 1);
});

test("line diff numbers lines and folds unchanged runs", () => {
  const before = Array.from({ length: 20 }, (_, i) => `line ${i}`).join("\n");
  const after = before.replace("line 10", "line ten");
  const { rows, added, removed } = lineDiff(before, after);
  assert.equal(added, 1);
  assert.equal(removed, 1);
  assert.ok(rows.some((r) => r.kind === "gap"));
  const del = rows.find((r) => r.kind === "del");
  assert.ok(del && del.kind === "del" && del.oldNo === 11);
  assert.deepEqual(lineDiff("", "a\nb\n").rows.map((r) => r.kind), ["add", "add"]);
});

test("unified diff parsing", () => {
  const files = parseUnifiedDiff(
    "diff --git a/x.py b/x.py\nindex 1..2 100644\n--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@ def f\n a\n-b\n+c\n",
  );
  assert.equal(files.length, 1);
  assert.equal(files[0].path, "x.py");
  assert.equal(files[0].added, 1);
  assert.equal(files[0].removed, 1);
  assert.deepEqual(files[0].rows.map((r) => r.kind), ["gap", "context", "del", "add"]);
});

test("a turn keeps its checkpoint and a rollback says what it restored", () => {
  const state = createTranscript();
  applyEvent(state, ev("UserPrompt", { text: "edit", attachments: [], memory: [] }));
  applyEvent(state, ev("Checkpoint", { id: "c0ffee", label: "Before: edit", created_at: 1 }));
  applyEvent(state, ev("TurnFinished", { status: "finished", files_touched: ["a.py"], checkpoint: "c0ffee" }));
  const end = state.blocks.find((b) => b.kind === "turn_end");
  assert.ok(end && end.kind === "turn_end");
  assert.equal(end.checkpoint, "c0ffee");
  applyEvent(
    state,
    ev("Rollback", { checkpoint: { id: "c0ffee", label: "Before: edit" }, undo: { id: "beef", label: "Before restoring" }, restored: ["a.py"], removed: ["b.py"] }, ""),
  );
  const rollback = state.blocks[state.blocks.length - 1];
  assert.ok(rollback.kind === "rollback");
  assert.deepEqual([rollback.label, rollback.undo, rollback.restored, rollback.removed], ["edit", "beef", ["a.py"], ["b.py"]]);
});

test("a registry creation becomes a visible notice, not a silent event", () => {
  const state = createTranscript();
  applyEvent(state, ev("RegistryChanged", { kind: "tool", name: "pdf_digest", version: 1, status: "active", lifetime: "session", description: "Read a PDF and file a digest comment." }));
  const notice = state.blocks[state.blocks.length - 1];
  assert.ok(notice.kind === "notice" && notice.tone === "info");
  assert.match(notice.text, /pdf_digest/);
  assert.match(notice.text, /this session only/);
  applyEvent(state, ev("RegistryChanged", { kind: "role", name: "docs_reviewer", version: 2, status: "active", lifetime: "durable", description: "Reviews docs." }));
  const roleNotice = state.blocks[state.blocks.length - 1];
  assert.ok(roleNotice.kind === "notice");
  assert.match(roleNotice.text, /role `docs_reviewer` v2/);
  assert.match(roleNotice.text, /durable catalogue/);
});

test("a capability gap becomes a visible notice with the missed queries", () => {
  const state = createTranscript();
  applyEvent(state, ev("CapabilityGap", { queries: ["translate speech", "ocr labels", "resize video"], misses: 3 }));
  const notice = state.blocks[state.blocks.length - 1];
  assert.ok(notice.kind === "notice" && notice.tone === "info");
  assert.match(notice.text, /Capability gap: 3 tool searches found nothing/);
  assert.match(notice.text, /translate speech/);
  assert.match(notice.text, /activating generated code stays yours/);
});

test("adopting a role shows the narrowing, the pin and the one-way rule", () => {
  const state = createTranscript();
  applyEvent(state, ev("RoleAdopted", { role: "docs_reviewer", version: 2, tools: 5, guides: ["evidence"], reversible: false }));
  const notice = state.blocks[state.blocks.length - 1];
  assert.ok(notice.kind === "notice" && notice.tone === "info");
  assert.match(notice.text, /Adopted role `docs_reviewer` v2/);
  assert.match(notice.text, /narrowed to 5/);
  assert.match(notice.text, /One-way/);
  assert.match(notice.text, /advisory/);
});

test("JobRecommended becomes a jobrequest block the card renders from", () => {
  const state = createTranscript();
  applyEvent(state, ev("JobRecommended", { id: "abc123", goal: "summarise the repo", role: "explorer", version: null, reason: "read-only sweep", workspace: "/w" }));
  const block = state.blocks[state.blocks.length - 1];
  assert.ok(block.kind === "jobrequest");
  assert.equal(block.job.id, "abc123");
  assert.equal(block.job.goal, "summarise the repo");
  assert.equal(block.job.role, "explorer");
  // An event without an id cannot be tracked or decided, so it is dropped.
  const before = state.blocks.length;
  applyEvent(state, ev("JobRecommended", { goal: "no id" }));
  assert.equal(state.blocks.length, before);
});

test("files a run produced are collected per turn, once each", () => {
  const state = createTranscript();
  applyEvent(state, ev("UserPrompt", { text: "make a pdf", attachments: [], memory: [] }));
  applyEvent(state, ev("ToolCall", { id: "p1", function: { name: "run_python", arguments: "{}" } }));
  const files = [{ path: "report.pdf", scope: "scratch", size: 2048 }];
  const display = [{ type: "brief", text: "exit 0" }, { type: "files", data: { files } }];
  applyEvent(state, ev("ToolResult", { tool_call_id: "p1", return_value: { is_error: false, output: "", message: "", display } }));
  applyEvent(state, ev("ToolCall", { id: "p2", function: { name: "run_python", arguments: "{}" } }));
  applyEvent(state, ev("ToolResult", { tool_call_id: "p2", return_value: { is_error: false, output: "", message: "", display } }));
  assert.deepEqual(producedFiles(state.blocks), files);
});

test("provider retries fold into one notice that counts up", () => {
  const state = createTranscript();
  applyEvent(state, ev("UserPrompt", { text: "go", attachments: [], memory: [] }));
  for (const attempt of [1, 2, 3]) {
    applyEvent(state, ev("StatusUpdate", { retry: { attempt, max_attempts: 10, delay_seconds: 2 ** attempt, error: "500" } }));
  }
  const notices = state.blocks.filter((b) => b.kind === "notice");
  assert.equal(notices.length, 1);
  assert.ok(notices[0].kind === "notice" && notices[0].tone === "warn" && notices[0].text.includes("(4 of 10) in 8s"));
});

test("a turn records the permission mode it ran in", () => {
  const state = createTranscript();
  applyEvent(state, ev("TurnFinished", { status: "finished", files_touched: [], mode: "default" }));
  applyEvent(state, ev("ModeChanged", { mode: "plan" }));
  applyEvent(state, ev("TurnFinished", { status: "finished", files_touched: [], mode: "plan" }, "t2"));
  applyEvent(state, ev("TurnFinished", { status: "finished", files_touched: [] }, "t3"));
  const modes = state.blocks.filter((b) => b.kind === "turn_end").map((b) => (b.kind === "turn_end" ? b.mode : null));
  assert.deepEqual(modes, ["default", "plan", undefined]);
});
