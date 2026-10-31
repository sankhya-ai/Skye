// The board's API, served by `chamber board` on this machine.

import type { BoardData, ChamberSummary, Talk } from "./board.ts";

/** A team's place on Chamber Cloud: its trial or plan, and (for the admin) the signed billing link. */
export interface CloudPlan {
  registered: boolean;
  trial: boolean;
  trialEnds: number;
  status: "trial" | "active" | "cancelled" | "enterprise";
  seats: number;
  members: number;
  access: boolean;
  reason: string;
  admin: boolean;
  billing: string | null;
}

export interface AgentTool {
  id: "claude" | "codex";
  label: string;
  installed: boolean;
  version: string | null;
  signedIn: boolean;
  job: { action: "install" | "login"; running: boolean; ok: boolean | null; output: string; link: string | null } | null;
}

export interface WakeRule {
  who: "nobody" | "team" | string[];
  can: "answer" | "work";
}

export interface WakeEntry {
  id: string;
  at: number;
  agent: string;
  from: string;
  message: string | null;
  result: "woke" | "refused";
  reason?: string;
  can?: "answer" | "work";
  ended?: number;
  code?: number | null;
}

export interface Dream {
  ts: number;
  ms: number;
  ok: boolean;
  message: string;
  reason: string;
  inbox: number;
  steps: number;
  cost: number;
  model: string;
  changes: string[];
}

let cachedSessionToken = "";

async function getSessionToken(): Promise<string> {
  if (cachedSessionToken) return cachedSessionToken;
  try {
    const meta = document.querySelector<HTMLMetaElement>('meta[name="chamber-token"]')?.content;
    if (meta) {
      cachedSessionToken = meta;
      return cachedSessionToken;
    }
  } catch {}
  try {
    const res = await fetch("/api/board/session");
    if (res.ok) {
      const data = await res.json();
      if (data?.token) {
        cachedSessionToken = data.token;
        return cachedSessionToken;
      }
    }
  } catch {}
  return "";
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await getSessionToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(token ? { "x-chamber-token": token } : {}),
    ...((init.headers as Record<string, string>) ?? {}),
  };
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new Error(String(detail));
  }
  return response.json() as Promise<T>;
}

const post = <T>(path: string, body: unknown = {}) => request<T>(path, { method: "POST", body: JSON.stringify(body) });

/** One entry in the decision ledger (lib/decisions.mjs). */
export interface Decision {
  id: string;
  title: string;
  status: "proposed" | "approved" | "rejected" | "superseded";
  rationale?: string;
  affects?: string;
  source?: string;
  "proposed-by"?: string;
  added?: string;
  conflicts?: string;
  supersedes?: string;
  "superseded-by"?: string;
  reviews?: string[];
  "decided-by"?: string;
  "decided-on"?: string;
  reason?: string;
}

/** A contract in the registry (lib/contracts.mjs), with each consumer's standing on the current version. */
export interface Contract {
  name: string;
  title: string;
  version: number;
  summary: string;
  producer: string;
  updated: string;
  decision: string;
  consumers: { agent: string; state: "outstanding" | "seen" | "adapted" | "object"; note: string; date: string; lastVersion: number }[];
  objections: { agent: string; version: number; note: string; date: string }[];
}

export interface AttachedFile {
  name: string;
  data: string; // base64
  size: number;
}

export const api = {
  board: () => request<BoardData>("/api/board"),
  session: () => request<{ token: string; me: string }>("/api/board/session"),
  talk: (id: string) => request<Talk>(`/api/board/talks/${encodeURIComponent(id)}?seen=1`), // opening a talk marks it read
  say: (id: string, to: string, text: string, files: AttachedFile[] = []) =>
    post<{ id: string; thread: string; held?: boolean; wake?: { woke: boolean; reason?: string } }>(`/api/board/talks/${encodeURIComponent(id)}`, { to, text, files }),
  startTalk: (to: string, text: string, title: string, files: AttachedFile[] = []) =>
    post<{ id: string; thread: string; held?: boolean; wake?: { woke: boolean; reason?: string } }>("/api/board/talks", { to, text, title, files }),
  markDone: (id: string, done = true) => post<{ ok: boolean; done: boolean }>(`/api/board/talks/${encodeURIComponent(id)}/done`, { done }),
  channel: (op: "join" | "leave" | "add", name: string, owner?: string) => post<{ ok: boolean; channel: string }>("/api/board/channels", { op, name, owner }),
  addWorkspace: (path: string) => post<{ output: string }>("/api/board/workspaces", { path }),
  pickFolder: () => post<{ path?: string; cancelled?: boolean }>("/api/board/workspaces/pick"),
  removeWorkspace: (path: string) => post<{ output: string }>("/api/board/workspaces/remove", { path }),
  logout: () => post<{ ok: boolean }>("/api/board/logout", {}),
  chambers: () => request<{ chambers: ChamberSummary[] }>("/api/board/chambers"),
  switchChamber: (id: string) => post<{ ok: boolean }>("/api/board/chambers/switch", { id }),
  addChamber: () => post<{ ok: boolean }>("/api/board/chambers/add"),
  cancelAddChamber: () => post<{ ok: boolean }>("/api/board/chambers/cancel-add"),
  renameChamber: (name: string) => post<{ ok: boolean }>("/api/board/chambers/rename", { name }),
  invite: () => request<{ command: string; code?: string }>("/api/board/invite"),
  memory: () => request<{ files: { path: string; version: number; author: string; ts: number }[] }>("/api/board/memory"),
  memoryFile: (path: string, version = 0) => request<{ path: string; version: number; text: string | null }>(`/api/board/memory/file?path=${encodeURIComponent(path)}${version ? `&version=${version}` : ""}`),
  saveMemoryFile: (path: string, text: string, message?: string, base?: number) =>
    post<{ ok: boolean; seq?: number; current?: unknown }>("/api/board/memory/file", { path, text, message, base }),
  memoryHistory: () => request<{ commits: { seq: number; author: string; message: string; ts: number; changes: { path: string; from: number; to: number; deleted: boolean }[] }[] }>("/api/board/memory/history"),
  memorySearch: (q: string) => request<{ hits: { path: string; line: number; text: string }[] }>(`/api/board/memory/search?q=${encodeURIComponent(q)}`),
  decisions: () => request<{ entries: Decision[] }>("/api/board/decisions"),
  decide: (id: string, outcome: "approve" | "reject", reason = "") => post<{ entry: Decision }>("/api/board/decisions/decide", { id, outcome, reason }),
  contracts: () => request<{ contracts: Contract[] }>("/api/board/contracts"),
  dreams: () => request<{ dreams: Dream[]; running: { started: number; log: { kind: string; text: string; ts: number }[] } | null }>("/api/board/memory/dreams"),
  dream: () => post<{ started: boolean }>("/api/board/memory/dream"),
  settings: () => request<{ hasKey: boolean; dreamModel: string; lastDream: number }>("/api/board/settings"),
  saveSettings: (changes: { openrouterKey?: string; dreamModel?: string }) => post<{ hasKey: boolean; dreamModel: string; lastDream: number }>("/api/board/settings", changes),
  members: () => request<{ personal: boolean; members: { owner: string; online: boolean; since: number }[]; removed?: { owner: string; at: number }[] }>("/api/board/members"),
  revoke: (owner: string, op: "revoke" | "restore" = "revoke") => post<{ ok: boolean }>("/api/board/revoke", { owner, op }),
  setName: (name: string) => post<{ name: string }>("/api/board/name", { name }),
  search: (q: string, channel = "", type = "") =>
    request<{ hits: SearchHit[] }>(`/api/board/search?q=${encodeURIComponent(q)}${channel ? `&channel=${encodeURIComponent(channel)}` : ""}${type ? `&type=${encodeURIComponent(type)}` : ""}`),
  gates: () => request<{ gates: Gate[] }>("/api/board/gates"),
  resolveGate: (gateId: string, decision: "approve" | "reject", token?: string) =>
    post<{ gate: Gate }>("/api/board/gates", { gateId, decision, token }),
  awayStatus: () => request<{ running: boolean; status: string }>("/api/board/away"),
  setAway: (enabled: boolean, status?: string) => post<{ running: boolean; status: string }>("/api/board/away", { enabled, status }),
  reviewPatch: (patch: string, workspacePath?: string) =>
    post<{ appliesCleanly: boolean; stat: string; error?: string | null }>("/api/board/review_patch", { patch, workspacePath }),
  applyPatch: (patch: string, workspacePath?: string) =>
    post<{ success: boolean; output: string }>("/api/board/apply_patch", { patch, workspacePath }),
  coordinationTasks: (status?: string, op?: string) =>
    request<{ tasks: CoordinationTask[] }>(`/api/board/coordination${status || op ? `?${status ? `status=${encodeURIComponent(status)}&` : ""}${op ? `op=${encodeURIComponent(op)}` : ""}` : ""}`),
  claimCoordination: (id: string, agent?: string) =>
    post<{ task: CoordinationTask }>("/api/board/coordination", { action: "claim", id, agent }),
  resolveCoordination: (id: string, resolution: string, evidence?: any, contract?: string) =>
    post<{ task: CoordinationTask }>("/api/board/coordination", { action: "resolve", id, resolution, evidence, contract }),
  rejectCoordination: (id: string, reason?: string) =>
    post<{ task: CoordinationTask }>("/api/board/coordination", { action: "reject", id, reason }),
  quota: () => request<QuotaInfo>("/api/board/quota"),
  joinOnboarding: (invite: string, as?: string, project?: string, name?: string) =>
    post<{ ok: boolean; owner: string; relay: string }>("/api/board/onboarding/join", { invite, as, project, name }),
  newOnboarding: (relay: string, as?: string, project?: string, name?: string) =>
    post<{ ok: boolean; owner: string; relay: string; admin?: boolean }>("/api/board/onboarding/new", { relay, as, project, name }),
  cloudflareAuth: () =>
    request<{ authenticated: boolean; account: string | null }>("/api/board/onboarding/cloudflare-auth"),
  plan: () => request<{ plan: CloudPlan | null }>("/api/board/plan"),
  openExternal: (url: string) => post<{ ok: boolean }>("/api/board/open", { url }),
  agentTools: () => request<{ tools: AgentTool[] }>("/api/board/agent-tools"),
  startAgentTool: (tool: string, action: "install" | "login") => post<{ tools: AgentTool[] }>("/api/board/agent-tools", { tool, action }),
  wakeRules: () => request<{ agents: Record<string, WakeRule>; log: WakeEntry[] }>("/api/board/wake"),
  setWakeRule: (agent: string, rule: WakeRule) => post<{ agents: Record<string, WakeRule>; log: WakeEntry[] }>("/api/board/wake", { agent, ...rule }),
  wakeRun: (id: string) => request<{ events: any[] }>(`/api/board/wake/${id}`),
  cloudflareProgress: () => request<{ running: boolean; progress: string }>("/api/board/onboarding/cloudflare-progress"),
  cloudflareCancel: () => post<{ ok: boolean }>("/api/board/onboarding/cloudflare-cancel"),
  cloudflareSetup: (as?: string, project?: string, storage = true, name?: string) =>
    post<{ ok: boolean; relay: string; owner: string; inviteCommand: string; files: boolean }>("/api/board/onboarding/cloudflare-setup", { as, project, storage, name }),
  cloudflareLogin: () => post<{ authenticated: boolean; account: string | null }>("/api/board/onboarding/cloudflare-login"),
  r2Status: () => request<R2Status>("/api/board/onboarding/r2-status"),
  storage: () => request<StorageInfo>("/api/board/storage"),
  enableStorage: () => post<{ ok: boolean; files: boolean }>("/api/board/storage/enable"),
  detectHarnesses: () =>
    request<{ harnesses: Record<string, boolean>; project: string }>("/api/board/detect"),
  toggleReaction: (messageId: string, emoji: string) =>
    post<{ messageId: string; reactions: Record<string, string[]> }>("/api/board/reactions", { messageId, emoji }),
  getUserStatus: () =>
    request<{ emoji?: string; text?: string }>("/api/board/user_status"),
  setUserStatus: (emoji: string, text: string) =>
    post<{ emoji: string; text: string }>("/api/board/user_status", { emoji, text }),
};

/** The signed-in Cloudflare account's R2: off until its owner turns it on in the dashboard. */
export interface R2Status {
  authenticated: boolean;
  enabled: boolean;
  bucket: boolean;
  dashboard: string;
  reason?: string;
}

/** Whether the chamber's relay holds files up to 25 MB (null: an older relay that doesn't say). */
export interface StorageInfo {
  files: boolean | null;
  cloudflare: boolean;
  admin: boolean;
  progress: string;
}

export interface CoordinationTask {
  id: string;
  op: "ask" | "delegate" | "review" | "propose" | "resolve" | "handoff";
  status: "requested" | "claimed" | "in_progress" | "resolved" | "rejected" | "timed_out";
  from: string;
  to: string;
  title: string;
  description: string;
  branch?: string | null;
  contract?: string | null;
  files: string[];
  evidence?: any;
  resolution?: string | null;
  rejectionReason?: string | null;
  claimedBy?: string | null;
  deadlineMs: number;
  createdAt: number;
  updatedAt: number;
}

export interface QuotaInfo {
  day: string;
  dailyRequests: number;
  dailyRequestsLimit: number;
  requestsPercentage: number;
  r2StorageBytes: number;
  r2StorageLimitBytes: number;
  warning: boolean;
  status: "healthy" | "warning" | "unknown";
}

export interface SearchHit {
  id: string;
  talkId: string;
  messageId: string;
  sender: string;
  channel: string;
  type: string;
  title: string;
  text: string;
  createdAt: string;
  score: number;
  citation: string;
}

export interface GateActionDetails {
  command?: string;
  diff?: string;
  files?: string[];
  environment?: string;
  cwd?: string;
  approvers?: string[];
  policy?: string;
}

export interface Gate {
  id: string;
  token?: string;
  lineId?: string;
  mobileUrl?: string;
  description: string;
  action: string | GateActionDetails;
  requestedBy: string;
  status: "pending" | "approved" | "rejected" | "expired";
  createdAt: number;
  expiresAt: number;
  resolvedAt?: number;
  resolvedBy?: string;
}
