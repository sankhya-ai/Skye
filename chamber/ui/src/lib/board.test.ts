import assert from "node:assert/strict";
import { test } from "node:test";

import { address, addressFor, ago, defaultRecipient, inView, people, who, type BoardData, type Card, type Talk } from "./board.ts";

const card = (id: string, status: Card["status"] | "done", peopleIn: string[], channel: string | null = null): Card => ({
  id, title: id, status: status === "done" ? "talking" : status, unread: status === "waiting", people: peopleIn, channel,
  with: peopleIn.filter((p) => !p.startsWith("ashish/") && !p.startsWith("#")),
  count: 1, last: { from: peopleIn[0], text: "", ts: 0 }, updated: 0,
});

test("names read as people", () => {
  assert.equal(who("ashish/board", "ashish"), "you");
  assert.equal(who("ashish/root", "ashish"), "your root");
  assert.equal(who("ashish/claude-code.api", "ashish"), "your Claude · api");
  assert.equal(who("nitin/claude-code.web", "ashish"), "Nitin's Claude · web");
  assert.equal(who("nitin/board", "ashish"), "Nitin");
  assert.equal(who("#general", "ashish"), "#general");
  assert.equal(who("nitin/codex.tura", "ashish"), "Nitin's Codex · tura");
  assert.equal(who("ashish/antigravity.root", "ashish"), "your Antigravity · root");
  assert.equal(who("ashish/dsh", "ashish"), "your DeepSeek");
});

test("each view shows its own talks", () => {
  const cards = [
    card("dm", "waiting", ["nitin/claude-code.web", "ashish/board"]),
    card("chan", "talking", ["nitin/board", "#backend"], "#backend"),
    card("mine", "done", ["ashish/claude-code.api", "mira/root"]),
  ];
  assert.deepEqual(inView(cards, { kind: "inbox" }).map((c) => c.id), ["dm", "chan", "mine"]);
  assert.deepEqual(inView(cards, { kind: "channel", name: "#backend" }).map((c) => c.id), ["chan"]);
  assert.deepEqual(inView(cards, { kind: "person", owner: "nitin" }).map((c) => c.id), ["dm"]);
  assert.deepEqual(inView(cards, { kind: "agent", agent: "ashish/claude-code.api" }).map((c) => c.id), ["mine"]);
  assert.equal(addressFor({ kind: "channel", name: "#backend" }), "#backend");
  assert.equal(addressFor({ kind: "person", owner: "nitin" }), "@nitin");
  const projects = [{ path: "/w/api", name: "api", agents: [{ agent: "ashish/claude-code.api", kind: "claude-code", online: true }, { agent: "ashish/codex.api", kind: "codex", online: false }] }];
  assert.deepEqual(inView(cards, { kind: "project", path: "/w/api" }, projects).map((c) => c.id), ["mine"]);
});

test("people: teammates seen anywhere, online first, never you", () => {
  const data = {
    me: "ashish",
    agents: [{ agent: "nitin/board", kind: "human", label: "", answers: false }, { agent: "ashish/board", kind: "human", label: "", answers: false }],
    cards: [card("x", "talking", ["mira/claude-code.atlas", "ashish/board"])],
    channels: [{ name: "#general", members: ["ashish", "nitin", "rohan"], joined: true }],
  } as unknown as BoardData;
  assert.deepEqual(people(data), [{ owner: "nitin", online: true }, { owner: "mira", online: false }, { owner: "rohan", online: false }]);
});

test("replies stay in their channel, else go to whoever spoke last", () => {
  const talk = (channel: string | null): Talk => ({
    ...card("t", "waiting", ["nitin/claude-code.web", "ashish/claude-code.api"], channel),
    messages: [{ id: "2", from: "nitin/claude-code.web", to: channel ?? "ashish/claude-code.api", text: "a", ts: 2, files: [] }],
  });
  assert.equal(defaultRecipient(talk(null), "ashish"), "nitin/claude-code.web");
  assert.equal(defaultRecipient(talk("#backend"), "ashish"), "#backend");
  assert.equal(address(" Nitin "), "@nitin");
  assert.equal(address("#general"), "#general");
  assert.equal(ago(0, 5 * 60_000), "5m");
});

test("routes round-trip through the URL", async () => {
  const { format, parse } = await import("./route.ts");
  for (const hash of ["#/inbox", "#/all?t=abc", "#/c/backend", "#/p/nitin?t=new", "#/a/ashish/claude-code.api", "#/memory", "#/project/%2FUsers%2Fa%2Ftura"]) {
    assert.equal(format(parse(hash)), hash);
  }
  assert.deepEqual(parse("#/c/backend").view, { kind: "channel", name: "#backend" });
  assert.deepEqual(parse("").view, { kind: "work" });
});
