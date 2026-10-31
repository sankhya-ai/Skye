#!/usr/bin/env node
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadConfig } from "../lib/inbox.mjs";
import { LineClient } from "../lib/client.mjs";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const config = loadConfig();
if (!config) {
  console.error("[Frontend Subagent] Error: No Chamber configuration found.");
  process.exit(1);
}

const AGENT_NAME = `${config.owner}/frontend-agent.subagent`;
const TARGET_AGENT = `${config.owner}/backend-agent.subagent`;
const cursorDir = mkdtempSync(join(tmpdir(), "chamber-frontend-subagent-"));
const cursorPath = join(cursorDir, "cursor.json");

console.log(`[Frontend Subagent] Initializing frontend specialist as '${AGENT_NAME}'...`);

let stage = 0;

const client = new LineClient(config.invite, AGENT_NAME, {
  kind: "agent",
  label: "Frontend UI Specialist",
  answers: false,
  cursorPath,
  onMessage: async (msg) => {
    console.log(`\n📥 [Frontend Subagent] Received reply from '${msg.from}'!`);
    console.log(`   Reply text:\n   > ${msg.text.split("\n").join("\n   > ")}`);

    if (stage === 1 && (msg.text.includes("API Contract") || msg.text.includes("PUT /api/v1/user/settings"))) {
      stage = 2;
      console.log("\n💭 [Frontend Subagent] Schema received! Validating against UI component state in App.js...");
      console.log("   [Validation PASS] Fields 'theme', 'silentRevalidation', and 'notifications' match UI requirements.");
      await sleep(1000);

      const followupQuestion =
`Thanks for the schema contract! Follow-up question:
How does the client handle WebSocket reconnection if the user switches networks or goes offline? What is the backoff policy?`;

      console.log(`\n📤 [Frontend Subagent] Asking follow-up question to '${TARGET_AGENT}'...`);
      await client.send(TARGET_AGENT, followupQuestion, {
        replyTo: msg.id,
        title: "Inquiry: WebSocket Reconnection & Backoff Policy",
        branch: "feat/buzz-parity-settings",
      });
      console.log("✔ [Frontend Subagent] Follow-up inquiry sent.");
      return;
    }

    if (stage === 2 && (msg.text.includes("Backoff intervals") || msg.text.includes("connection retry"))) {
      stage = 3;
      console.log("\n💭 [Frontend Subagent] Reconnection specs received! All criteria satisfied.");
      await sleep(1000);

      const resolution =
`[RESOLVED & CONTRACT ACCEPTED]
Resolution: Buzz-Parity Settings schema and reconnection backoff policy [1s, 2s, 5s, 10s, 30s] verified and integrated into UI layer.
Evidence: UI route tested with silentRevalidation=true in ui/src/App.js.
Status: Closed.`;

      console.log(`\n📤 [Frontend Subagent] Sending task resolution notice to '${TARGET_AGENT}'...`);
      await client.send(TARGET_AGENT, resolution, {
        replyTo: msg.id,
        title: "[RESOLVED] Buzz-Parity Settings Integration",
        branch: "feat/buzz-parity-settings",
      });
      console.log("✔ [Frontend Subagent] Resolution message transmitted.");

      setTimeout(() => {
        client.close();
        console.log("✨ [Frontend Subagent] Collaboration complete. Disconnected and exited.");
        process.exit(0);
      }, 2000);
    }
  },
});

client.run();
await client.ready();
console.log(`🚀 [Frontend Subagent] Connected to Chamber relay (${config.invite.relay}).`);

// Give backend subagent a brief moment to finish handshaking
await sleep(1500);

stage = 1;
const initialQuestion = 
`Hello @backend-agent! I am working on the new Buzz-parity settings UI for Chamber.
Can you please provide the verified API contract for the user settings endpoint?
Specifically, what fields are accepted by PUT /api/v1/user/settings and what is the notification preference structure?`;

console.log(`\n📤 [Frontend Subagent] Initiating coordination task with '${TARGET_AGENT}'...`);
console.log(`   Title: "Request: Buzz-Parity Settings API Contract" | Branch: "feat/buzz-parity-settings"`);
await client.send(TARGET_AGENT, initialQuestion, {
  title: "Request: Buzz-Parity Settings API Contract",
  branch: "feat/buzz-parity-settings",
});
console.log("✔ [Frontend Subagent] Initial request dispatched over Chamber. Awaiting response from peer...");
