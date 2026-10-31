import "../src/styles/app.css";
import { createRoot } from "react-dom/client";
import { createTranscript, applyEvent } from "../src/lib/transcript.ts";
import { groupBlocks } from "../src/lib/activity.ts";
import { Items } from "../src/components/Transcript.tsx";

// The real pipeline: the persisted session event -> transcript state -> rendered items.
const state = createTranscript();
applyEvent(state, {
  type: "RegistryChanged",
  payload: { kind: "tool", name: "pdf_digest", version: 1, status: "active", lifetime: "session", description: "Read a PDF and file a digest comment." },
} as never);
applyEvent(state, {
  type: "RegistryChanged",
  payload: { kind: "role", name: "docs_reviewer", version: 2, status: "active", lifetime: "durable", description: "Reads documents and reviews them with evidence." },
} as never);
applyEvent(state, {
  type: "RegistryChanged",
  payload: { kind: "plugin", name: "numstats", version: 1, status: "verified", lifetime: "inert until the owner activates hash 3c434fc4cf80...", description: "stats_summary" },
} as never);
applyEvent(state, {
  type: "RoleAdopted",
  payload: { role: "docs_reviewer", version: 2, tools: 5, guides: ["evidence"], reversible: false },
} as never);
applyEvent(state, {
  type: "CapabilityGap",
  payload: { queries: ["translate speech in real time", "ocr scanned labels", "resize a video"], misses: 3 },
} as never);
const items = groupBlocks(state.blocks, { running: false, workspace: "/tmp/demo" });

createRoot(document.getElementById("root")!).render(
  <div style={{ maxWidth: 720, margin: "24px auto", padding: "0 16px" }}>
    <Items items={items} workspace="/tmp/demo" running={false} onApprove={() => {}} />
  </div>,
);
