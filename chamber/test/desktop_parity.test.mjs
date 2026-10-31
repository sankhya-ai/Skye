import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Board } from "../lib/board.mjs";

test("Board: message emoji reactions toggle and persist", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "chamber-test-board-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));

  const board = new Board("alice", []);
  // Override reactionsPath to isolate in temp directory
  board.reactionsPath = () => join(dir, "reactions.json");

  // Initial is empty
  assert.deepEqual(board.getReactions(), {});

  // Alice reacts with 👍
  let res = board.toggleReaction("msg-1", "👍", "alice");
  assert.deepEqual(res, { "👍": ["alice"] });

  // Bob reacts with 👍
  res = board.toggleReaction("msg-1", "👍", "bob");
  assert.deepEqual(res, { "👍": ["alice", "bob"] });

  // Bob reacts with 🚀
  res = board.toggleReaction("msg-1", "🚀", "bob");
  assert.deepEqual(res, { "👍": ["alice", "bob"], "🚀": ["bob"] });

  // Alice untoggles 👍
  res = board.toggleReaction("msg-1", "👍", "alice");
  assert.deepEqual(res, { "👍": ["bob"], "🚀": ["bob"] });

  // Bob untoggles 👍
  res = board.toggleReaction("msg-1", "👍", "bob");
  assert.deepEqual(res, { "🚀": ["bob"] });

  // Bob untoggles 🚀 -> message entry cleaned up
  res = board.toggleReaction("msg-1", "🚀", "bob");
  assert.deepEqual(res, {});
  assert.deepEqual(board.getReactions(), {});
});
