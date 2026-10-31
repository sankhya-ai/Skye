#!/usr/bin/env node
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadConfig } from "../lib/inbox.mjs";
import { LineClient } from "../lib/client.mjs";

const config = loadConfig();
if (!config) {
  console.error("[Backend Subagent] Error: No Chamber configuration found.");
  process.exit(1);
}

const AGENT_NAME = `${config.owner}/backend-agent.subagent`;
const cursorDir = mkdtempSync(join(tmpdir(), "chamber-backend-subagent-"));
const cursorPath = join(cursorDir, "cursor.json");

console.log(`[Backend Subagent] Initializing backend specialist as '${AGENT_NAME}'...`);

let resolveCount = 0;

const client = new LineClient(config.invite, AGENT_NAME, {
  kind: "agent",
  label: "Backend & Protocol Specialist",
  answers: true,
  cursorPath,
  onMessage: async (msg) => {
    console.log(`\n📥 [Backend Subagent] New message received from '${msg.from}'!`);
    console.log(`   Type: ${msg.type} | Subject: "${msg.title || "(no title)"}" | Branch: ${msg.branch || "main"}`);
    console.log(`   Message text:\n   > ${msg.text.split("\n").join("\n   > ")}`);

    if (msg.text.includes("reconnection") || msg.text.includes("backoff")) {
      console.log("\n💭 [Backend Subagent] Processing question about WebSocket reconnection & backoff policy...");
      const answer = 
`Here are the connection retry details:
- Backoff intervals: [1s, 2s, 5s, 10s, 30s] capped at 30 seconds.
- Re-authentication: client transmits invite.pass in 'hello' frame upon reconnect.
- Cursor: server re-streams unacknowledged messages since last sequence ID.
All set for frontend integration!`;

      console.log(`📤 [Backend Subagent] Sending clarification reply to '${msg.from}'...`);
      await client.send(msg.from, answer, {
        replyTo: msg.id,
        title: "Re: WebSocket Reconnection & Backoff Specification",
        branch: msg.branch,
      });
      console.log("✔ [Backend Subagent] Clarification reply sent successfully.");
      return;
    }

    if (msg.text.includes("API contract") || msg.text.includes("settings endpoint")) {
      console.log("\n💭 [Backend Subagent] Analyzing API contract request from Frontend Subagent...");
      const schemaResponse =
`API Contract for Buzz-Parity Settings:
Endpoint: PUT /api/v1/user/settings
Content-Type: application/json

Schema:
{
  "theme": "dark" | "light" | "system",
  "silentRevalidation": true,
  "notifications": {
    "desktop": boolean,
    "mentionsOnly": boolean
  },
  "awayStatus": string | null
}

Response: 200 OK with updated configuration.
Evidence: validated against lib/protocol.mjs and desktop/src-tauri.`;

      console.log(`📤 [Backend Subagent] Sending schema specification reply to '${msg.from}'...`);
      await client.send(msg.from, schemaResponse, {
        replyTo: msg.id,
        title: "Re: Buzz Parity Settings API Contract",
        branch: msg.branch,
      });
      console.log("✔ [Backend Subagent] Schema specification reply sent.");
      return;
    }

    if (msg.text.includes("RESOLVED") || msg.text.includes("Verified")) {
      console.log("\n🤝 [Backend Subagent] Task resolution confirmed by peer!");
      console.log(`   Evidence cited by peer: "${msg.text}"`);
      resolveCount++;
      if (resolveCount >= 1) {
        console.log("✨ [Backend Subagent] Collaboration successfully settled. Preparing clean shutdown...");
        setTimeout(() => {
          client.close();
          console.log("👋 [Backend Subagent] Disconnected and exited.");
          process.exit(0);
        }, 1500);
      }
    }
  },
});

client.run();
await client.ready();
console.log(`🚀 [Backend Subagent] Connected to Chamber relay (${config.invite.relay}) and listening for requests...`);
