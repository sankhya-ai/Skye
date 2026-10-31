// Several chambers on one machine, like Slack workspaces: the first stays in ~/.chamber, each one
// added after it gets a folder of its own, joining one you're in already reuses its folder, and an
// agent's commands name their chamber so it never follows the one picked in the app.

import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

process.env.CHAMBER_HOME = mkdtempSync(join(tmpdir(), "chambers-"));
const inbox = await import("../lib/inbox.mjs");
const { mcpArgs } = await import("../lib/harnesses.mjs");
const { Invite } = await import("../lib/protocol.mjs");

test("chambers are added beside the first, listed in order, switched and left", async () => {
  const { ROOT } = inbox;
  assert.equal(inbox.newChamberHome(), ROOT);
  assert.deepEqual(inbox.listChambers(), []);

  const first = Invite.create("https://chamber.acme.workers.dev");
  inbox.useChamber(inbox.newChamberHome());
  inbox.saveConfig(first, "nitin");
  assert.deepEqual(mcpArgs("claude-code.api").slice(1), ["mcp", "--agent", "claude-code.api"]);

  const second = Invite.create("https://chamber-3f9a.acme.workers.dev");
  const home = inbox.useChamber(inbox.homeFor(second));
  assert.notEqual(home, ROOT);
  inbox.saveConfig(second, "nitin", "Side project");
  // Its agents name it, so they stay in it whichever chamber the app shows.
  assert.deepEqual(mcpArgs("claude-code.web").slice(-2), ["--home", home]);

  const chambers = inbox.listChambers();
  assert.deepEqual(chambers.map((c) => [c.id, c.name]), [["default", "chamber"], [inbox.chamberId(home), "Side project"]]);
  assert.equal(inbox.chamberHome(chambers[1].id), home);
  assert.throws(() => inbox.chamberHome("../../etc"));

  // Joining a chamber you're in already goes to where it is, not a new folder.
  assert.equal(inbox.homeFor(Invite.parse(String(second))), home);

  // A rename keeps the chamber's name; picking one sticks; leaving the first picks the next.
  inbox.rename("nitin.k");
  assert.equal(inbox.loadConfig().name, "Side project");
  inbox.setActiveChamber(home);
  assert.equal(inbox.activeChamber(), home);
  inbox.useChamber(ROOT);
  inbox.logout();
  inbox.setActiveChamber(ROOT);
  assert.equal(inbox.activeChamber(), home);
  assert.equal(inbox.newChamberHome(), ROOT);
});
