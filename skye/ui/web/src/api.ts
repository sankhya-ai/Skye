// API client for the Skye UI server. The session token arrives in the page URL
// (printed by `skye ui`) and is sent as a header on every API call; the server
// sets no cookies, so nothing ambient rides along cross-origin.

export type MissionRow = {
  id: string;
  goal: string;
  project?: string;
  overall: string;
  needs_owner: boolean;
  approved: boolean;
  steps: number;
  done: number;
  failed: number;
  last_activity: string | null;
  live: boolean;
};

export type StepStatus = {
  id: string;
  role: string;
  adapter: string;
  state: string;
  status: string;
  reason: string;
};

export type JournalEntry = {
  ts: number;
  step: string;
  event: string;
  status?: string;
  reason?: string;
  output?: string;
  role?: string;
  worker_job?: string;
  [key: string]: unknown;
};

export type MissionArtifacts = {
  recorded: boolean;
  repo?: string;
  complete?: boolean;
  start_truncated?: boolean;
  end_truncated?: boolean;
  added?: string[];
  modified?: string[];
  deleted?: string[];
  added_overflow?: number;
  modified_overflow?: number;
  deleted_overflow?: number;
};

export type MissionDetail = {
  artifacts?: MissionArtifacts;
  mission: { id: string; goal: string; steps: Array<Record<string, unknown>> };
  status: { mission: string; status: string; steps: StepStatus[] };
  journal: JournalEntry[];
  dynamic_steps: Array<Record<string, unknown>>;
  pending_contracts: Array<Record<string, unknown> & { step: string }>;
  approved: boolean;
  live: { running?: boolean; error?: string | null; finished?: number };
};

const token = new URLSearchParams(window.location.search).get("token") ?? "";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      "X-Skye-Token": token,
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, body.error ?? `request failed (${res.status})`);
  return body as T;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export const getMissions = () =>
  api<{ missions: MissionRow[]; live: Record<string, { running?: boolean }> }>("/api/missions");
export const getMission = (id: string) => api<MissionDetail>(`/api/missions/${id}`);
export const approveStep = (mission: string, step: string) =>
  api<{ approved: boolean }>("/api/approve", { method: "POST", body: JSON.stringify({ mission, step }) });
export const ackRecovery = (id: string) =>
  api<{ acknowledged: boolean; branch: string; base_rev: string }>(
    `/api/missions/${encodeURIComponent(id)}/ack-recovery`, { method: "POST", body: "{}" });
export const resumeMission = (id: string) =>
  api<{ resumed: boolean }>(`/api/missions/${id}/resume`, { method: "POST", body: "{}" });
export const redispatchStep = (id: string, step: string) =>
  api<{ redispatched: string }>(`/api/missions/${id}/redispatch`, { method: "POST", body: JSON.stringify({ step }) });

export type RoleRow = {
  name: string;
  source: string;
  description: string;
  steps: number;
  done: number;
  failed: number;
  catalogue: { tools: string[]; writable: boolean };
};

export type AgentDefinition = {
  name: string;
  version: number;
  description: string;
  instructions: string;
  extends: string;
  status: string; // "active" | "proposed" (awaiting the owner's promote decision)
  proposed_by: string;
  source: string; // "builtin" | "owner" | "registry-override" | "proposed"
};

export const getAgentDefinitions = () =>
  api<{ agents: AgentDefinition[] }>("/api/agents");

export const getRoles = () =>
  api<{ roles: RoleRow[]; adapters: string[] }>("/api/roles");
export const createRole = (role: {
  name: string;
  description: string;
  catalogue: { tools: string[]; writable: boolean };
}) => api<{ saved: boolean; role: RoleRow }>("/api/roles", {
  method: "POST",
  body: JSON.stringify({ role }),
});
export type AutoPickup = { enabled: boolean; include_reopened: boolean; max_missions: number };
export const getPolicy = () =>
  api<{ raw: Record<string, unknown>; path: string; max_concurrent: number;
        auto_pickup: AutoPickup; revision: string }>("/api/policy");
// Whole-policy replace requires the revision the writer based its edit on; the server
// rejects stale bases (409) so a loaded form can never silently wipe a newer change.
export const savePolicy = (policy: Record<string, unknown>, baseRevision: string) =>
  api<{ saved: boolean; revision: string }>("/api/policy",
    { method: "POST", body: JSON.stringify({ policy, base_revision: baseRevision }) });
export const createMission = (spec: {
  goal: string;
  plan?: boolean;
  run?: boolean;
  project?: string;
}) =>
  api<{ id: string; running: boolean }>("/api/missions", {
    method: "POST",
    body: JSON.stringify(spec),
  });

export type Project = { id: string; name: string; repo: string };

export const getProjects = () => api<{ projects: Project[] }>("/api/projects");
export const createProject = (project: { name: string; repo: string }) =>
  api<{ saved: boolean; project: Project }>("/api/projects", {
    method: "POST",
    body: JSON.stringify({ project }),
  });

export type ThreadEntry = {
  ts: number;
  actor: string;
  kind: string;
  body: string;
  refs: string[];
};

export type FeatureRow = {
  id: string;
  project: string;
  title: string;
  brief: string;
  status: string;
  labels: string[];
  proposed_by: string;
  proposed_at: number;
  decided_at: number | null;
  decision_note: string;
  mission_id: string;
  missions: string[];
  thread: ThreadEntry[];
  reopen_count: number;
  verification?: {
    journey_passed: boolean;
    evidence: string;
    verified_at: number;
    mission_id?: string;
    repo_revision?: string;
    journey_changed?: boolean;
  };
};

export const getBacklog = () => api<{ features: FeatureRow[] }>("/api/backlog");
// Same ticket records as /api/backlog, plus the read-only pickup queue. Listing due work
// never starts it - pickup is information, starting still rides the owner's action.
export const getTickets = () =>
  api<{ tickets: FeatureRow[]; pickup_due: string[] }>("/api/tickets");

export type DiscussionEntry = {
  seq: number;
  ts: number;
  id: string;
  author: string;
  kind: string; // note | broadcast | question | answer | contract-change | ack
  body: string;
  refs: string[];
  requires_ack?: string[];
  ack_of?: string;
  waiting_on?: string[]; // server-computed: roles still owing an ack
};

export const getDiscussion = (missionId: string, since = 0) =>
  api<{ entries: DiscussionEntry[]; cursor: number }>(
    `/api/missions/${encodeURIComponent(missionId)}/discussion?since=${since}`,
  );
export const decideFeature = (feature: string, decision: "approved" | "rejected", note = "") =>
  api<{ decided: boolean; feature: FeatureRow }>("/api/backlog/decide", {
    method: "POST",
    body: JSON.stringify({ feature, decision, note }),
  });
export const reworkFeature = (feature: string, note = "") =>
  api<{ sent_back: boolean; feature: FeatureRow }>("/api/backlog/rework", {
    method: "POST",
    body: JSON.stringify({ feature, note }),
  });
export const verifyFeature = (feature: string) =>
  api<{ verified: boolean; feature: FeatureRow; command: string; evidence: string }>(
    "/api/backlog/verify",
    { method: "POST", body: JSON.stringify({ feature }) },
  );
export const startFeature = (feature: string, run = true) =>
  api<{ id: string; running: boolean }>("/api/backlog/start", {
    method: "POST",
    body: JSON.stringify({ feature, run }),
  });

export type IssueRow = {
  id: string;
  kind: string;
  project: string;
  summary: string;
  detail: string;
  source: string;
  occurrences: number;
  first_seen: number;
  last_seen: number;
  feature_id: string;
};

export const getIssues = () => api<{ issues: IssueRow[] }>("/api/issues");
export const reportIssue = (issue: {
  kind: string;
  summary: string;
  detail?: string;
  project?: string;
  source?: string;
}) => api<{ recorded: boolean; issue: IssueRow }>("/api/issues", {
  method: "POST",
  body: JSON.stringify(issue),
});

// Raw artifact fetch (image/markdown bytes) for inline preview - same header token as api(),
// returned as an object URL the caller must revoke.
export async function getArtifactFileUrl(mission: string, path: string): Promise<string> {
  const token = new URLSearchParams(window.location.search).get("token") ?? "";
  const res = await fetch(`/api/mission-artifact/${mission}?path=${encodeURIComponent(path)}`, {
    headers: { "X-Skye-Token": token },
  });
  if (!res.ok) throw new Error(`artifact preview failed (${res.status})`);
  return URL.createObjectURL(await res.blob());
}

export async function getArtifactText(mission: string, path: string): Promise<string> {
  const token = new URLSearchParams(window.location.search).get("token") ?? "";
  const res = await fetch(`/api/mission-artifact/${mission}?path=${encodeURIComponent(path)}`, {
    headers: { "X-Skye-Token": token },
  });
  if (!res.ok) throw new Error(`artifact preview failed (${res.status})`);
  return res.text();
}
