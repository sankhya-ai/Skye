import "../src/styles/app.css";
import { createRoot } from "react-dom/client";
import { createTranscript, applyEvent } from "../src/lib/transcript.ts";
import { groupBlocks } from "../src/lib/activity.ts";
import { Items } from "../src/components/Transcript.tsx";

// FIXTURE (not runtime evidence): the API answers "pending"; the accept POST records the exact request body the
// card sends and refuses once (jail unavailable on this host), so the card's refusal state renders.
let acceptBody = "";
const originalFetch = window.fetch.bind(window);
window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  if (url.includes("/api/auth/bootstrap")) {
    return Promise.resolve(new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }));
  }
  if (url.includes("/accept") && init?.method === "POST") {
    acceptBody = String(init.body || "");
    return Promise.resolve(
      new Response(JSON.stringify({ detail: "isolation `bwrap-model-only` is not available here, so the job was not started: bubblewrap cannot create namespaces on this host" }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      }),
    );
  }
  if (url.includes("/api/job-requests") && !init?.method) {
    return Promise.resolve(new Response(JSON.stringify({ requests: [{
      id: "f3a91c0d2e4b", goal: "Build the digest feature and run its tests", role: "developer", role_version: 0,
      workspace: "/tmp/demo", reason: "code changes plus a test run", requested_by: "sess-1", status: "pending", created_at: 1,
      scope: ["ReadFile", "Grep", "Shell"],
      budget: { max_steps: 0, timeout_seconds: 900, max_tokens: 0, max_cost_usd: 0 },
      parent_job_id: "job-a1b2c3d4", depth: 1, escalation: false,
    }] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  }
  return originalFetch(input, init);
};

// The real pipeline: the persisted session event -> transcript state -> rendered items.
const state = createTranscript();
applyEvent(state, {
  type: "JobRecommended",
  payload: { id: "f3a91c0d2e4b", goal: "Build the digest feature and run its tests", role: "developer", version: null, reason: "code changes plus a test run", workspace: "/tmp/demo" },
} as never);
const items = groupBlocks(state.blocks, { running: false, workspace: "/tmp/demo" });

createRoot(document.getElementById("root")!).render(
  <div style={{ maxWidth: 720, margin: "24px auto", padding: "0 16px" }}>
    <Items items={items} workspace="/tmp/demo" running={false} onApprove={() => {}} />
    <pre id="payload" style={{ fontSize: 11, color: "#666" }}></pre>
  </div>,
);

// Drive the interaction the fixture must show: the owner picks the jail, clicks Start, the server refuses,
// and the exact payload the card sent is rendered for inspection.
if (location.hash !== "#initial")
setTimeout(() => {
  const radio = document.querySelector<HTMLInputElement>('input[name^="protect-"]');
  radio?.click();
  setTimeout(() => {
    const start = [...document.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent?.includes("Start job"));
    start?.click();
    setTimeout(() => {
      const pre = document.getElementById("pre") ?? document.querySelector("pre");
      if (pre) pre.textContent = "accept payload the card sent: " + acceptBody;
    }, 400);
  }, 200);
}, 600);
