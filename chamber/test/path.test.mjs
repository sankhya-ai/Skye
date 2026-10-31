// An app opened from the Finder has the bare system PATH: the daemon adds the login shell's and the
// places Claude Code and Codex install to, so it finds them like a terminal does.

import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter } from "node:path";
import { test } from "node:test";

import { fullPath } from "../lib/path.mjs";

test("the login shell's PATH is added after what is there, once each, only folders that exist", () => {
  const nvm = mkdtempSync(`${tmpdir()}/nvm-bin-`);
  const path = fullPath(["/usr/bin", "/bin"].join(delimiter), { shell: [nvm, "/usr/bin", "/nowhere/at/all"].join(delimiter) }).split(delimiter);
  assert.deepEqual(path.slice(0, 3), ["/usr/bin", "/bin", nvm], "what was there comes first");
  assert.equal(path.filter((d) => d === "/usr/bin").length, 1);
  assert.ok(!path.includes("/nowhere/at/all"));
  assert.equal(fullPath(path.join(delimiter), { shell: "" }), path.join(delimiter), "idempotent");
});
