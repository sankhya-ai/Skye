// Turns a task's journal into what the task screen draws: one record per agent step with
// its conversation (brief, streamed messages, grouped tool calls, final answer) and the
// facts the workflow card shows. Everything here is derived from recorded events - a
// step with no record is "pending", nothing is invented for display.
import type { GraphNode, JournalEntry, MissionDetail, MissionGraph } from "../api";

export type ToolCall = { name: string; detail?: string };

// One attempt at a step: a dispatch paired with its terminal record. A step that was run
// again (a follow-up, a recovery) keeps every attempt - the Runs tab reads this.
export type StepRun = {
  startedAt: number;
  endedAt?: number;
  label: string;
  /** running | done | failed | needs-owner | interrupted | cancelled | skipped | resume-rejected */
  state: string;
  reason?: string;
};

export type ChatItem =
  | { type: "run"; ts: number; label: string }
  | { type: "user"; ts: number; text: string }
  | { type: "agent"; ts: number; text: string }
  | { type: "tools"; ts: number; calls: ToolCall[] }
  | { type: "plan"; ts: number }
  | { type: "answer"; ts: number; text: string; partial?: boolean }
  | { type: "status"; ts: number; tone: "error" | "warn" | "info"; text: string };

export type StepView = {
  id: string;
  role: string;
  adapter: string;
  model: string;
  /** pending | running | done | failed | needs-owner | interrupted | cancelled | skipped | resume-rejected */
  state: string;
  goal: string;
  brief: string;
  origin: "authored" | "proposed";
  proposedBy?: string;
  items: ChatItem[];
  toolCount: number;
  startedAt?: number;
  endedAt?: number;
  /** the newest thing the agent said or did - the card's live line */
  latest?: string;
  answer?: string;
  reason?: string;
  followupable: boolean;
  hasSession: boolean;
  /** The lead step this failed step was handed to for recovery, if the owner did that. */
  handedTo?: string;
  handedToRole?: string;
  runs: StepRun[];
};

const RUN_LABEL: Record<string, string> = {
  dispatched: "Started",
  redispatch: "Run again by you",
  reattached: "Reattached after a restart",
};

// A planner's reply is a propose_steps JSON payload (older records keep only its tail).
const isPlanText = (text: string) => /_(steps|features)"\s*:\s*\[/.test(text);

function firstLine(text: string): string {
  for (const raw of text.split("\n")) {
    const line = raw.replace(/^#+\s*/, "").replace(/\*\*/g, "").replace(/`/g, "").trim();
    if (line && !/^\|?[-:| ]+\|?$/.test(line)) return line;
  }
  return "";
}

function closeRun(step: StepView, ts: number, state: string, reason?: string) {
  const open = [...step.runs].reverse().find((r) => r.state === "running" && !r.endedAt);
  if (open) {
    open.endedAt = ts;
    open.state = state;
    open.reason = reason;
  }
}

export function buildSteps(detail: MissionDetail, graph: MissionGraph | null,
                           roleModels: Record<string, string>): StepView[] {
  const statusById = new Map((detail.status.steps ?? []).map((s) => [s.id, s]));
  const graphById = new Map((graph?.nodes ?? []).map((n) => [n.id, n]));
  const defs: Array<Record<string, unknown> & { dynamic: boolean }> = [
    ...detail.mission.steps.map((s) => ({ ...s, dynamic: false })),
    ...detail.dynamic_steps.map((s) => ({ ...s, dynamic: true })),
  ];
  const live = Boolean(detail.live?.running);
  const overall = detail.status.status;

  const steps = new Map<string, StepView>();
  for (const def of defs) {
    const id = String(def.id);
    const node: GraphNode | undefined = graphById.get(id);
    const status = statusById.get(id);
    const role = String(def.role ?? node?.role ?? "");
    steps.set(id, {
      id,
      role,
      adapter: String(def.adapter ?? node?.adapter ?? ""),
      model: status?.model || roleModels[role] || "",
      state: "pending",
      goal: String(def.goal ?? node?.goal ?? ""),
      brief: String(def.brief ?? def.goal ?? ""),
      origin: node?.origin ?? (def.dynamic ? "proposed" : "authored"),
      proposedBy: node?.proposed_by,
      items: [],
      toolCount: 0,
      followupable: Boolean(status?.followupable),
      hasSession: Boolean(status?.has_session),
      handedTo: status?.handed_to || undefined,
      runs: [],
    });
  }

  let unaddressedItems: Map<string, number[]> | undefined;
  const resolvedSteps = new Set<string>();
  for (const e of detail.journal as JournalEntry[]) {
    const step = e.step ? steps.get(e.step) : undefined;
    if (!step) continue;
    const ts = e.ts;
    const last = step.items[step.items.length - 1];
    switch (e.event) {
      case "dispatched":
      case "redispatch":
      case "reattached":
        step.state = "running";
        step.startedAt = ts; // the latest run - a follow-up hours later is a new run
        step.endedAt = undefined;
        step.reason = undefined;
        step.items.push({ type: "run", ts, label: RUN_LABEL[e.event] });
        step.runs.push({ startedAt: ts, label: RUN_LABEL[e.event], state: "running" });
        break;
      case "follow-up":
      case "follow-up-submitted": {
        const text = typeof e.text === "string" ? e.text : "";
        if (text && !(last?.type === "user" && last.text === text)) {
          step.items.push({ type: "user", ts, text });
        }
        break;
      }
      case "worker-progress": {
        const kind = String(e.kind ?? "");
        const text = typeof e.text === "string" ? e.text : "";
        if (kind === "message" && text) {
          if (isPlanText(text)) {
            if (!step.items.some((i) => i.type === "plan")) step.items.push({ type: "plan", ts });
            step.latest = "Proposed the workflow";
          } else {
            step.items.push({ type: "agent", ts, text });
            step.latest = firstLine(text);
          }
        } else if (kind === "tool") {
          const call: ToolCall = { name: text || "tool",
                                   detail: typeof e.detail === "string" ? e.detail : undefined };
          if (last?.type === "tools") last.calls.push(call);
          else step.items.push({ type: "tools", ts, calls: [call] });
          step.toolCount += 1;
          step.latest = call.detail ? `${call.name}: ${call.detail}` : `Using ${call.name}`;
        } else if (kind === "error" && text) {
          step.items.push({ type: "status", ts, tone: "error", text });
        }
        break;
      }
      case "settled": {
        step.endedAt = ts;
        const done = e.status === "done";
        step.state = done ? "done" : "failed";
        step.reason = e.reason || undefined;
        closeRun(step, ts, step.state, e.reason || undefined);
        const answer = typeof e.answer === "string" && e.answer ? e.answer : "";
        const output = typeof e.output === "string" ? e.output : "";
        const text = answer || output;
        if (done && text) {
          if (isPlanText(text)) {
            if (!step.items.some((i) => i.type === "plan")) step.items.push({ type: "plan", ts });
          } else {
            // The final message usually streamed already: promote it to the answer
            // instead of showing it twice.
            const idx = [...step.items].reverse().findIndex((i) => i.type === "agent");
            const at = idx === -1 ? -1 : step.items.length - 1 - idx;
            const prev = at >= 0 ? (step.items[at] as { text: string }) : null;
            const same = prev && text.slice(0, 160).trim() === prev.text.slice(0, 160).trim();
            if (same && at >= 0) step.items.splice(at, 1);
            // Records written before answers were kept whole carry only an output tail.
            step.items.push({ type: "answer", ts, text, partial: !answer && output.length >= 3990 });
            step.answer = text;
            step.latest = firstLine(text);
          }
        } else if (!done) {
          step.items.push({ type: "status", ts, tone: "error",
                            text: e.reason || `Finished with status ${e.status ?? "unknown"}` });
        }
        break;
      }
      case "needs-owner":
        step.state = "needs-owner";
        step.reason = e.reason;
        closeRun(step, ts, "needs-owner", e.reason || undefined);
        step.items.push({ type: "status", ts, tone: "warn",
                          text: (e.reason ?? "Waiting for you").split(" Contract fingerprint")[0] });
        break;
      case "interrupted":
      case "cancelled":
      case "resume-rejected":
        step.state = e.event;
        step.endedAt = ts;
        step.reason = e.reason;
        closeRun(step, ts, e.event, e.reason || undefined);
        step.items.push({ type: "status", ts, tone: e.event === "cancelled" ? "warn" : "error",
                          text: e.reason || (e.event === "cancelled" ? "Stopped by you" : "Interrupted") });
        break;
      case "skipped":
        step.state = "skipped";
        closeRun(step, ts, "skipped", e.reason || undefined);
        step.items.push({ type: "status", ts, tone: "info", text: e.reason || "Skipped" });
        break;
      case "recovery-handoff": {
        // The owner handed this failed step to the mission's lead: recorded here so the
        // card and panel can say so, and the conversation shows the decision in place.
        const lead = e.to ? steps.get(String(e.to)) : undefined;
        step.handedTo = String(e.to || "") || undefined;
        step.handedToRole = lead?.role;
        step.items.push({ type: "status", ts, tone: "warn",
                          text: `You handed this failure to the ${lead ? roleName(lead.role).toLowerCase() : "lead"} to recover.` });
        break;
      }
      case "recovery-resolved": {
        resolvedSteps.add(step.id);
        const how = e.resolution === "waived"
          ? `waived it${e.reason ? `: ${e.reason}` : ""}`
          : "proposed recovery work for it.";
        step.items.push({ type: "status", ts, tone: "info",
                          text: `The lead answered the handoff - ${how}` });
        break;
      }
      case "recovery-unaddressed":
        step.items.push({ type: "status", ts, tone: "warn",
                          text: "The lead finished without addressing this failure - the handoff stays open." });
        (unaddressedItems ??= new Map()).set(step.id,
          [...(unaddressedItems.get(step.id) ?? []), step.items.length - 1]);
        break;
      case "review-round":
        step.items.push({ type: "status", ts, tone: "info",
                          text: `The reviewer sent this back${typeof e.feedback === "string" && e.feedback ? `: ${e.feedback}` : ""}` });
        break;
      default:
        break;
    }
  }

  // A handoff the lead later answered: earlier "unaddressed" warnings are history,
  // not the current state - keep them on the record, but quiet and past-tense.
  for (const stepId of resolvedSteps) {
    const step = steps.get(stepId);
    for (const idx of unaddressedItems?.get(stepId) ?? []) {
      const item = step?.items[idx];
      if (item?.type === "status") {
        item.tone = "info";
        item.text = "Earlier, the lead finished without addressing this failure - the handoff stayed open until the answer below.";
      }
    }
  }

  // A dispatch recorded before the task stopped can never settle: show the task's
  // outcome rather than a spinner implying live work. The open run closes as
  // interrupted with no end time - the attempt is on the record, its duration unknown.
  for (const step of steps.values()) {
    if (step.state === "running" && !live && ["interrupted", "failed"].includes(overall)) {
      step.state = "interrupted";
      const open = [...step.runs].reverse().find((r) => r.state === "running" && !r.endedAt);
      if (open) open.state = "interrupted";
    }
  }
  return [...steps.values()];
}

export type Layout = {
  pos: Map<string, { x: number; y: number }>;
  edges: Array<{ from: string; to: string; kind: "needs" | "proposed" | "start" | "end" }>;
  width: number;
  height: number;
};

export const CARD_W = 244;
export const CARD_H = 158;
const COL_GAP = 64;
const ROW_GAP = 28;
const PAD = 40;
export const REQUEST_ID = "__request";
export const RESULT_ID = "__result";

/** Left-to-right layers like a flow editor: request -> agents by dependency -> result.
 * A planner-proposed step with no declared dependency hangs off the planner that proposed it. */
export function layoutFlow(steps: StepView[], graph: MissionGraph | null): Layout {
  const ids = new Set(steps.map((s) => s.id));
  const deps = new Map<string, string[]>();
  const edges: Layout["edges"] = [];
  const add = (from: string, to: string, kind: Layout["edges"][number]["kind"]) => {
    deps.set(to, [...(deps.get(to) ?? []), from]);
    edges.push({ from, to, kind });
  };
  for (const e of graph?.edges ?? []) {
    if (ids.has(e.from) && ids.has(e.to)) add(e.from, e.to, "needs");
  }
  for (const s of steps) {
    if (!deps.has(s.id) && s.proposedBy && ids.has(s.proposedBy)) add(s.proposedBy, s.id, "proposed");
  }
  const depth = new Map<string, number>();
  const visit = (id: string, seen: Set<string>): number => {
    if (depth.has(id)) return depth.get(id)!;
    if (seen.has(id)) return 1;
    seen.add(id);
    const d = (deps.get(id) ?? []).reduce((acc, dep) => Math.max(acc, visit(dep, seen) + 1), 1);
    depth.set(id, d);
    return d;
  };
  for (const s of steps) visit(s.id, new Set());
  const hasOut = new Set(edges.map((e) => e.from));
  for (const s of steps) {
    if (!deps.has(s.id)) edges.push({ from: REQUEST_ID, to: s.id, kind: "start" });
    if (!hasOut.has(s.id)) edges.push({ from: s.id, to: RESULT_ID, kind: "end" });
  }
  if (steps.length === 0) edges.push({ from: REQUEST_ID, to: RESULT_ID, kind: "start" });

  const lastCol = Math.max(0, ...depth.values()) + 1;
  const columns = new Map<number, string[]>();
  columns.set(0, [REQUEST_ID]);
  for (const s of steps) {
    const col = depth.get(s.id) ?? 1;
    columns.set(col, [...(columns.get(col) ?? []), s.id]);
  }
  columns.set(lastCol, [RESULT_ID]);
  const tallest = Math.max(...[...columns.values()].map((c) => c.length));
  const height = PAD * 2 + tallest * CARD_H + (tallest - 1) * ROW_GAP;
  const pos = new Map<string, { x: number; y: number }>();
  for (const [col, members] of columns) {
    const block = members.length * CARD_H + (members.length - 1) * ROW_GAP;
    const top = (height - block) / 2;
    members.forEach((id, row) => {
      pos.set(id, { x: PAD + col * (CARD_W + COL_GAP), y: top + row * (CARD_H + ROW_GAP) });
    });
  }
  const width = PAD * 2 + (lastCol + 1) * CARD_W + lastCol * COL_GAP;
  return { pos, edges, width, height };
}

const ROLE_NAMES: Record<string, string> = {
  pm: "Planner",
  "senior-dev": "Senior developer",
  "backend-dev": "Backend developer",
  "frontend-dev": "Frontend developer",
  devops: "DevOps",
};

export function roleName(role: string): string {
  if (ROLE_NAMES[role]) return ROLE_NAMES[role];
  const words = role.replace(/[-_]+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "Agent";
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "";
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}
