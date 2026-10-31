// Tool calls as Chotu's coding mode showed them: quiet trace lines under the
// answer, one per run of similar work — "Read 2 files, searched code",
// "Edited stats.py", "Ran a command" — each opening to the detail on demand.
//
// Ported from Chotu's AssistantMessage.jsx (groupToolSteps, inspectionGroupLabel,
// editGroupLabel), keyed on kimi-agent's tool names instead of the hub's.
//
// Thinking between calls folds into the line it sits in: a weak model thinks
// before nearly every call, and a "Thinking" row between each would break one
// "Read 8 files" into eight.

import { describeTool } from "./tools.ts";
import { parseArgs, type Block, type ToolBlock } from "./transcript.ts";

export type ThinkBlock = Extract<Block, { kind: "think" }>;

export type StepKind = "read" | "search" | "command" | "web" | "edit" | "plan" | "memory" | "remember" | "agent" | "flow" | "process" | "tool";
export type Tone = "done" | "working" | "permission" | "error" | "muted";

export interface Activity {
  kind: "activity";
  key: string;
  groupKind: string;
  tone: Tone;
  label: string;
  icon: StepKind;
  steps: ToolBlock[];
  /** The steps with the thinking between them, in order. */
  rows: (ToolBlock | ThinkBlock)[];
}

export type RenderItem = Exclude<Block, ToolBlock> | Activity;

const INSPECTION: ReadonlySet<StepKind> = new Set(["read", "search", "command", "web", "memory", "process"]);

export function stepKind(name: string): StepKind {
  switch (name) {
    case "ReadFile":
    case "ReadMediaFile":
    case "read_document":
      return "read";
    case "Grep":
    case "Glob":
      return "search";
    case "Shell":
      return "command";
    case "SearchWeb":
    case "FetchURL":
      return "web";
    case "WriteFile":
    case "StrReplaceFile":
    case "edit_spreadsheet":
    case "edit_pdf":
      return "edit";
    case "SetTodoList":
      return "plan";
    case "memory_recall":
    case "memory_checkpoint":
    case "session_search":
      return "memory";
    case "memory_remember":
      return "remember";
    case "Task":
    case "CreateSubagent":
      return "agent";
    case "coding_flow":
      return "flow";
    case "process":
      return "process";
    default:
      return "tool";
  }
}

export function stepTone(step: ToolBlock, running: boolean): Tone {
  if (step.approval?.status === "pending") return "permission";
  if (step.approval?.status === "reject") return "muted";
  if (step.result?.is_error) return "error";
  if (!step.result) return running ? "working" : "muted";
  return "done";
}

function groupKindOf(kind: StepKind, tone: Tone): string {
  if (tone === "permission" || tone === "error") return tone;
  if (INSPECTION.has(kind)) return "inspection";
  return kind;
}

function mergedTone(a: Tone, b: Tone): Tone {
  for (const tone of ["error", "permission", "working", "muted"] as Tone[]) if (a === tone || b === tone) return tone;
  return "done";
}

function basename(path: string): string {
  const parts = path.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

function sentence(parts: string[]): string {
  const clean = parts.filter(Boolean);
  if (!clean.length) return "Used a tool";
  const text = clean.length === 1 ? clean[0] : clean.length === 2 ? `${clean[0]}, ${clean[1]}` : `${clean.slice(0, -1).join(", ")}, ${clean[clean.length - 1]}`;
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

function inspectionLabel(steps: ToolBlock[]): string {
  const count = (k: StepKind) => steps.filter((s) => stepKind(s.name) === k).length;
  const reads = count("read");
  const searches = count("search");
  const commands = count("command");
  const processes = count("process");
  const web = count("web");
  const memory = count("memory");
  const parts: string[] = [];
  if (reads) parts.push(`read ${plural(reads, "file")}`);
  if (searches) parts.push("searched code");
  if (commands) parts.push(commands === 1 ? "ran a command" : `ran ${commands} commands`);
  if (processes) parts.push(processes === 1 ? "managed a process" : `managed ${processes} processes`);
  if (web) parts.push("searched the web");
  if (memory) parts.push("checked memory");
  return sentence(parts);
}

function editLabel(steps: ToolBlock[], workspace?: string): string {
  const targets = [...new Set(steps.map((s) => String(parseArgs(s.args)?.path ?? "")).filter(Boolean))];
  if (targets.length === 1) return `Edited ${basename(targets[0].replace(`${workspace ?? ""}/`, ""))}`;
  if (targets.length > 1) return `Edited ${targets.length} files`;
  return steps.length === 1 ? "Edited a file" : `Edited ${steps.length} files`;
}

function singleLabel(step: ToolBlock, tone: Tone, workspace?: string): string {
  const view = describeTool(step.name, step.args, workspace);
  const subject = view.subject ? `${view.verb} ${view.subject}` : view.verb;
  if (tone === "permission") return `Needs approval: ${subject}`;
  if (tone === "error") return `Failed: ${subject}`;
  if (tone === "working") return `${subject}…`;
  if (tone === "muted" && step.approval?.status === "reject") return `Rejected: ${subject}`;
  return subject;
}

export function activityLabel(groupKind: string, steps: ToolBlock[], tone: Tone, workspace?: string): string {
  if (steps.length === 1) {
    const kind = stepKind(steps[0].name);
    if (kind === "edit" && tone === "done") return editLabel(steps, workspace);
    return singleLabel(steps[0], tone, workspace);
  }
  if (tone === "working") {
    const running = steps.filter((s) => !s.result);
    const last = running[running.length - 1] ?? steps[steps.length - 1];
    return singleLabel(last, "working", workspace);
  }
  if (groupKind === "inspection") return inspectionLabel(steps);
  if (groupKind === "edit") return editLabel(steps, workspace);
  if (groupKind === "plan") return singleLabel(steps[steps.length - 1], tone, workspace);
  return `${steps.length} tool actions`;
}

function canMerge(group: Activity, kind: StepKind, tone: Tone): boolean {
  if (tone === "permission" || tone === "error" || group.tone === "permission" || group.tone === "error") return false;
  if (kind === "agent" || kind === "flow" || kind === "remember") return false;
  const groupKind = groupKindOf(kind, tone);
  if (groupKind !== group.groupKind) return false;
  // A running step joins a finished group only as its tail, and a finished
  // step after a running one means the running one finished: same line.
  return true;
}

/** Fold a transcript's blocks into render items, grouping runs of tool calls. */
export function groupBlocks(blocks: Block[], { running, workspace }: { running: boolean; workspace?: string }): RenderItem[] {
  const items: RenderItem[] = [];
  const groups: Activity[] = [];
  let current: Activity | null = null;
  // Thoughts with no line before them wait for the next one.
  let thoughts: ThinkBlock[] = [];
  const settle = () => {
    if (current) current.rows.push(...thoughts);
    else items.push(...thoughts);
    thoughts = [];
  };
  for (const block of blocks) {
    if (block.kind === "think") {
      if (!block.text.trim()) continue;
      if (current) current.rows.push(block);
      else thoughts.push(block);
      continue;
    }
    if (block.kind !== "tool") {
      settle();
      current = null;
      items.push(block);
      continue;
    }
    const kind = stepKind(block.name);
    const tone = stepTone(block, running);
    if (current !== null && canMerge(current, kind, tone)) {
      current.steps.push(block);
      current.rows.push(...thoughts, block);
      thoughts = [];
      current.tone = mergedTone(current.tone, tone);
      continue;
    }
    current = { kind: "activity", key: `act-${block.key}`, groupKind: groupKindOf(kind, tone), tone, label: "", icon: kind, steps: [block], rows: [...thoughts, block] };
    thoughts = [];
    groups.push(current);
    items.push(current);
  }
  settle();
  for (const group of groups) {
    group.label = activityLabel(group.groupKind, group.steps, group.tone, workspace);
    if (group.groupKind === "inspection" && group.steps.some((s) => stepKind(s.name) === "read")) group.icon = "read";
    else if (group.tone === "permission" || group.tone === "error") group.icon = stepKind(group.steps[0].name);
  }
  return items;
}

/** The pending approvals of a transcript, oldest first. */
export function pendingSteps(blocks: Block[]): ToolBlock[] {
  return blocks.filter((b): b is ToolBlock => b.kind === "tool" && b.approval?.status === "pending");
}

const PROGRESSIVE: Record<string, string> = {
  Read: "Reading",
  View: "Viewing",
  Run: "Running",
  Search: "Searching",
  Find: "Finding",
  Edit: "Editing",
  Write: "Writing",
  Append: "Appending to",
  Fetch: "Fetching",
  "Search the web": "Searching the web for",
  Recall: "Recalling",
  Remember: "Remembering",
  "Update plan": "Updating the plan",
};

/** What the agent is doing right now, for the working line: "Reading app.py",
 * "explorer · Searching useAuth", "Thinking". */
export function liveStatus(blocks: Block[], workspace?: string): string {
  for (let i = blocks.length - 1; i >= 0; i -= 1) {
    const block = blocks[i];
    if (block.kind === "think") return "Thinking";
    if (block.kind === "text") return block.text.trim() ? "Writing" : "Thinking";
    if (block.kind !== "tool") continue;
    if (block.result) return "Thinking";
    if (block.approval?.status === "pending") return "Waiting for your approval";
    if (stepKind(block.name) === "agent") {
      const name = String(parseArgs(block.args)?.subagent_name ?? "subagent");
      return `${name} · ${block.children?.blocks.length ? liveStatus(block.children.blocks, workspace) : "Starting"}`;
    }
    const view = describeTool(block.name, block.args, workspace);
    const verb = PROGRESSIVE[view.verb];
    if (!verb) return view.subject ? `${view.verb} ${view.subject}` : view.verb;
    const subject = stepKind(block.name) === "read" || stepKind(block.name) === "edit" ? basename(view.subject.split(" · ")[0]) : view.subject;
    return subject ? `${verb} ${subject}` : verb;
  }
  return "Working";
}
