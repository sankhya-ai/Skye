// API client for the Skye UI server. The session token arrives in the page URL
// (printed by `skye ui`) and is sent as a header on every API call; the server
// sets no cookies, so nothing ambient rides along cross-origin.

export type MissionRow = {
  id: string;
  goal: string;
  project?: string;
  team?: string; // saved team name, when the task was staffed by one
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
  model?: string;
  id: string;
  role: string;
  adapter: string;
  state: string;
  status: string;
  reason: string;
  // Session vocabulary: how the last run related to the vendor session (fresh/resumed/
  // reattached), whether a session exists to follow up on, and the latest streamed
  // progress line (already redacted server-side).
  continuity?: string;
  has_session?: boolean;
  followupable?: boolean;
  progress?: string;
  // Set when the owner handed this failed step to the mission's lead for recovery:
  // the lead step's id. The failure stays on the record but no longer fails the task.
  handed_to?: string;
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

export type RepoArtifacts = {
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
  // Multi-repo missions: the same derived diff per recorded repository, keyed by the
  // mission's repo name. Identical relative paths across repos stay unambiguous.
  repos?: Record<string, RepoArtifacts>;
};

export type RepoWorkspace = {
  mode: "worktree" | "shared";
  reason: string;
  branch: string;
  base_rev?: string;
  recovered_from_branch?: boolean;
  uncommitted_lost?: boolean;
};

export type WorkspaceInfo = {
  mode: "worktree" | "shared" | "multi-worktree";
  repos?: Record<string, RepoWorkspace>;
  reason: string;
  branch: string;
  base_rev?: string;
  recovered_from_branch?: boolean;
  recovery_pending: boolean;
  recovery_acknowledged: { at: number; by: string; base_rev: string; branch: string } | null;
};

export type MissionDetail = {
  artifacts?: MissionArtifacts;
  workspace?: WorkspaceInfo;
  mission: { id: string; goal: string; steps: Array<Record<string, unknown>>;
             project?: string; repos?: Record<string, string>; repos_source?: string;
             team?: { id: string; name: string; lead: string; members: string[] } };
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

export type GraphNode = {
  id: string;
  role: string;
  adapter: string;
  repo: string;
  goal: string;
  origin: "authored" | "proposed";
  state: string;
  reason?: string;
  proposed_by?: string;
  repo_reason?: string;
  worker_job?: string;
  continuity?: string;
  has_session?: boolean;
};

export type MissionGraph = {
  meta: { id: string; goal: string; status: string; repos: string[]; workspace: string };
  nodes: GraphNode[];
  edges: { from: string; to: string; kind: string }[];
  timeline: Array<{ ts?: number; step?: string; event?: string; status?: string; reason?: string }>;
};

export type ProjectRepoDiscovery = {
  project: string;
  discovery: {
    root: string;
    root_exists: boolean;
    root_is_repo: boolean;
    has_commits?: boolean;
    root_marker_invalid?: boolean;
    repos: Record<string, string>;
    skipped: number;
    overflow: string[];
    outside_scope: string[];
    inaccessible: string[];
    invalid: string[];
    error?: string;
  };
};

export const getProjectRepos = (id: string) =>
  api<ProjectRepoDiscovery>(`/api/projects/${encodeURIComponent(id)}/repos`);

export const getMissionGraph = (id: string) =>
  api<MissionGraph>(`/api/missions/${encodeURIComponent(id)}/graph`);
export const approveStep = (mission: string, step: string) =>
  api<{ approved: boolean }>("/api/approve", { method: "POST", body: JSON.stringify({ mission, step }) });
export const ackRecovery = (id: string) =>
  api<{ acknowledged: boolean; branch: string; base_rev: string }>(
    `/api/missions/${encodeURIComponent(id)}/ack-recovery`, { method: "POST", body: "{}" });
export const resumeMission = (id: string) =>
  api<{ resumed: boolean }>(`/api/missions/${id}/resume`, { method: "POST", body: "{}" });
export const redispatchStep = (id: string, step: string, fresh = false) =>
  api<{ redispatched: string; continuity?: string }>(
    `/api/missions/${id}/redispatch`,
    { method: "POST", body: JSON.stringify(fresh ? { step, fresh: true } : { step }) });
// Hand a failed step to the mission's lead for recovery: journaled, then the lead's
// session resumes with the failure as context and answers with revised or replacement
// steps. The failed step never re-runs on its own.
export const handoffStep = (id: string, step: string) =>
  api<{ handed: string; to: string; resumed: boolean }>(
    `/api/missions/${id}/handoff`,
    { method: "POST", body: JSON.stringify({ step }) });
// Continue a settled recorded session in a new process, or submit supported live input.
// A submitted live follow-up is not a provider-consumption receipt.
export const followUpStep = (id: string, step: string, text: string) =>
  api<{ accepted: boolean; step: string; continuity?: string;
        delivery?: "submitted" | "queued"; receipt?: string }>(
    `/api/missions/${id}/follow-up`,
    { method: "POST", body: JSON.stringify({ step, text }) });
// Operator stop: SIGTERM->SIGKILL to live steps plus a no-new-dispatch marker. The
// response distinguishes confirmed process exit, unconfirmed exit, and a dispatch marker.
export const cancelMission = (id: string) =>
  api<{ cancel: "stopped" | "stop-unconfirmed" | "marked"; steps: string[] }>(
    `/api/missions/${id}/cancel`, { method: "POST", body: "{}" });

export type RoleRow = {
  name: string;
  source: string;
  description: string;
  steps: number;
  done: number;
  failed: number;
  model?: string;
  catalogue: { tools: string[]; writable: boolean };
};

export type ModelChoice = { value: string; label: string; hint?: string };

export const setRoleModel = (role: string, model: string) =>
  api<{ saved: boolean; role: string; model: string }>("/api/policy/models", {
    method: "POST",
    body: JSON.stringify({ role, model }),
  });

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

export type TeamRow = {
  id: string;
  name: string;
  lead: string;
  members: string[];
  description: string;
  handoff: string;
  projects: string[]; // project ids the team is assigned to (empty: any project)
  lead_description?: string;
  member_details?: { name: string; description: string }[];
  project_names?: string[];
  revision?: number; // bumped on every save; send it back so a stale edit is refused
};

export const getTeams = () => api<{ teams: TeamRow[] }>("/api/teams");
export const saveTeam = (team: {
  id?: string;
  name: string;
  lead: string;
  members: string[];
  description?: string;
  handoff?: string;
  projects?: string[];
  revision?: number;
}) => api<{ saved: boolean; team: TeamRow }>("/api/teams", {
  method: "POST",
  body: JSON.stringify({ team }),
});
export const deleteTeam = (id: string) =>
  api<{ deleted: boolean; team: string }>("/api/teams/delete", {
    method: "POST",
    body: JSON.stringify({ id }),
  });

export type AdapterDetail = {
  name: string;
  available: boolean;
  probe: string;
  binary: string;
  auth: string;
  hint: string;
};
export const getRoles = () =>
  api<{ roles: RoleRow[]; adapters: string[]; adapter_details?: AdapterDetail[];
         model_choices?: ModelChoice[] }>(
    "/api/roles");
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
export type MissionSetup = {
  fields: Record<string, { type: string; required: boolean | string; note?: string }>;
  workspaces: { note: string; payload: string };
};
export const getMissionSetup = () => api<MissionSetup>("/api/mission-setup");

export const createMission = (spec: {
  goal: string;
  plan?: boolean;
  solo?: boolean;
  team?: string; // saved team id: implies plan, lead plans, roster is enforced
  run?: boolean;
  project?: string;
  repo?: string;
  repos?: Record<string, string>;
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
export async function getArtifactFileUrl(mission: string, path: string, repo?: string): Promise<string> {
  const token = new URLSearchParams(window.location.search).get("token") ?? "";
  const repoQ = repo ? `&repo=${encodeURIComponent(repo)}` : "";
  const res = await fetch(`/api/mission-artifact/${mission}?path=${encodeURIComponent(path)}${repoQ}`, {
    headers: { "X-Skye-Token": token },
  });
  if (!res.ok) throw new Error(`artifact preview failed (${res.status})`);
  return URL.createObjectURL(await res.blob());
}

export async function getArtifactText(mission: string, path: string, repo?: string): Promise<string> {
  const token = new URLSearchParams(window.location.search).get("token") ?? "";
  const repoQ = repo ? `&repo=${encodeURIComponent(repo)}` : "";
  const res = await fetch(`/api/mission-artifact/${mission}?path=${encodeURIComponent(path)}${repoQ}`, {
    headers: { "X-Skye-Token": token },
  });
  if (!res.ok) throw new Error(`artifact preview failed (${res.status})`);
  return res.text();
}

export type UsageWindow = {
  window: string;
  status: string;
  resetsAt?: number;
  utilization?: number;
  isUsingOverage?: boolean;
  surpassedThreshold?: number;
  seen_at?: number;
  expired?: boolean;
};

export type ClaudeAccount = {
  installed: boolean;
  logged_in: boolean;
  plan?: string;
  auth_method?: string;
  version?: string;
  hint?: string;
  error?: string;
};

export type ClaudeUsage = {
  account: ClaudeAccount;
  windows: UsageWindow[];
  skye_totals: { runs?: number; turns?: number; cost_usd?: number; last_run_at?: number };
  recent_runs: Array<{ at: number; role: string; model: string; turns?: number; cost_usd?: number }>;
  usage_page: string;
  default_adapter: string;
};

export const getClaudeUsage = (refresh = false) =>
  api<ClaudeUsage>(`/api/claude${refresh ? "?refresh=1" : ""}`);

export type DeliveryRepo = {
  branch: string;
  repo: string;
  uncommitted_files: number;
  commits_ahead: number;
};
export type MergeResult = {
  at: number;
  ok: boolean;
  repos: Record<string, { ok: boolean; merged?: boolean; detail: string; into?: string }>;
};
export const getDelivery = (id: string) =>
  api<{ repos: Record<string, DeliveryRepo>; last_merge?: MergeResult }>(
    `/api/missions/${encodeURIComponent(id)}/delivery`);
export const mergeMission = (id: string) =>
  api<MergeResult>(`/api/missions/${encodeURIComponent(id)}/merge`, {
    method: "POST",
    body: JSON.stringify({}),
  });
export const pickFolder = () =>
  api<{ path: string; supported: boolean }>("/api/pick-folder", {
    method: "POST",
    body: JSON.stringify({}),
  });
export const initProjectGit = (id: string) =>
  api<{ ok: boolean; detail: string }>(`/api/projects/${encodeURIComponent(id)}/init-git`, {
    method: "POST",
    body: JSON.stringify({}),
  });
