import type {
  Checkpoint,
  GeneratedPluginListing,
  GeneratedPluginVersion,
  PluginReview,
  Connections,
  DeviceFlow,
  Flow,
  JobRequest,
  FlowReview,
  Health,
  KeikiStatus,
  LearnedSkill,
  LearningCandidate,
  Me,
  MemoryItem,
  ModelInfo,
  McpServerInfo,
  PluginCatalog,
  ProcessInfo,
  RollbackResult,
  Schedule,
  ScheduleMode,
  SearchHit,
  Session,
  Settings,
  SlackStatus,
  TrustEntry,
  WireEvent,
} from "./types.ts";

let bootstrapped: Promise<void> | null = null;

/** The local app's same-site cookie. A hosted server answers 403: sign in instead. */
export function bootstrap(): Promise<void> {
  bootstrapped ??= fetch("/api/auth/bootstrap", { method: "POST", credentials: "same-origin" }).then((r) => {
    if (!r.ok && r.status !== 403) throw new Error(`Could not reach the root server (${r.status}).`);
  });
  return bootstrapped;
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  await bootstrap();
  const response = await fetch(path, {
    credentials: "same-origin",
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(String(detail), response.status);
  }
  return response.json() as Promise<T>;
}

const q = (params: Record<string, string>) => new URLSearchParams(params).toString();
const post = <T>(path: string, body: unknown = {}) => request<T>(path, { method: "POST", body: JSON.stringify(body) });

export const api = {
  health: () => request<Health>("/api/health"),
  me: () => request<Me>("/api/me"),
  login: (token: string) => post<{ ok: boolean }>("/api/auth/login", { token }),
  logout: () => post<{ ok: boolean }>("/api/auth/logout"),
  settings: () => request<Settings>("/api/settings"),
  saveSettings: (changes: Partial<Settings> & Record<string, unknown>) => request<Settings>("/api/settings", { method: "PUT", body: JSON.stringify(changes) }),
  models: (refresh = false) => request<{ models: ModelInfo[]; error: string }>(`/api/models${refresh ? "?refresh=true" : ""}`),
  sessions: () => request<{ sessions: Session[] }>("/api/sessions"),
  createSession: (workspace: string) => post<Session>("/api/sessions", { workspace }),
  updateSession: (id: string, changes: { title?: string; permission_mode?: string }) =>
    request<Session>(`/api/sessions/${id}`, { method: "PATCH", body: JSON.stringify(changes) }),
  deleteSession: (id: string) => request<{ ok: boolean }>(`/api/sessions/${id}`, { method: "DELETE" }),
  events: (id: string, before: number) => request<{ events: WireEvent[]; has_more: boolean; first_seq: number | null }>(`/api/sessions/${id}/events?${q({ before: String(before) })}`),
  director: (id: string) => request<{ decisions: any[] }>(`/api/sessions/${id}/director`),
  jobRequests: () => request<{ requests: JobRequest[] }>("/api/job-requests"),
  acceptJobRequest: (id: string, options: { isolation?: string; accept_unprotected_credentials?: boolean } = {}) =>
    post<{ request: JobRequest; job: { id: string } }>(`/api/job-requests/${id}/accept`, options),
  dismissJobRequest: (id: string) => post<{ request: JobRequest }>(`/api/job-requests/${id}/dismiss`),
  revokeGrant: (id: string, key: string) => post<Session>(`/api/sessions/${id}/grants/revoke`, { key }),
  processes: (id: string) => request<{ processes: ProcessInfo[] }>(`/api/sessions/${id}/processes`),
  processLog: (id: string, pid: string) => request<{ lines: string[]; total_lines: number; status: string }>(`/api/sessions/${id}/processes/${pid}/log`),
  killProcess: (id: string, pid: string) => post<ProcessInfo>(`/api/sessions/${id}/processes/${pid}/kill`),
  files: (workspace: string) => request<{ files: string[]; truncated: boolean; git: boolean }>(`/api/workspace/files?${q({ path: workspace })}`),
  file: (workspace: string, file: string) =>
    request<{ path: string; binary: boolean; truncated: boolean; content: string }>(`/api/workspace/file?${q({ path: workspace, file })}`),
  changes: (workspace: string) =>
    request<{ git: boolean; branch: string; files: { path: string; status: string }[]; diff: string }>(`/api/workspace/changes?${q({ path: workspace })}`),
  dirs: (path?: string) =>
    request<{ path: string; parent: string | null; git: boolean; entries: { name: string; path: string; git: boolean }[] }>(`/api/fs/dirs${path ? `?${q({ path })}` : ""}`),
  /** The host's native folder dialog (local mode on macOS); null when the owner cancels. */
  pickFolder: (start?: string) => post<{ path: string | null }>("/api/fs/pick", { start: start ?? "" }),
  workspaces: () => request<{ workspaces: { name: string; path: string; scope: "personal" | "shared" }[]; personal: { name: string; path: string; scope: "personal" }[]; shared: { name: string; path: string; scope: "shared" }[] }>("/api/workspaces"),
  createWorkspace: (name: string, gitUrl = "", scope = "personal") => post<{ path: string; scope: string; name: string }>("/api/workspaces", { name, git_url: gitUrl, scope }),
  deleteWorkspace: (path: string) => request<{ ok: boolean }>(`/api/workspaces?${q({ path })}`, { method: "DELETE" }),
  memory: (workspace: string, query?: string) =>
    request<{ items: MemoryItem[]; status: Health["memory"]; error?: string }>(`/api/memory?${q(query ? { path: workspace, q: query } : { path: workspace })}`),
  forget: (id: string, workspace: string) => request<{ ok: boolean }>(`/api/memory/${encodeURIComponent(id)}?${q({ path: workspace })}`, { method: "DELETE" }),
  trust: () => request<{ entries: TrustEntry[]; promote_after: number }>("/api/trust"),
  revokeTrust: (key: string) => post<{ ok: boolean }>("/api/trust/revoke", { key }),
  classify: (command: string) => post<{ tier: string; key: string; effects: string[]; reasons: string[] }>("/api/classify", { command }),
  learning: () => request<{ mode: Settings["learning_mode"]; candidates: LearningCandidate[]; skills: LearnedSkill[] }>("/api/learning"),
  promote: (id: string) => post<LearningCandidate>(`/api/learning/${id}/promote`),
  reject: (id: string) => post<LearningCandidate>(`/api/learning/${id}/reject`),
  skill: (name: string) => request<{ name: string; text: string }>(`/api/skills/${encodeURIComponent(name)}`),
  rollbackSkill: (name: string) => post<{ ok: boolean }>(`/api/skills/${encodeURIComponent(name)}/rollback`),
  forgetSkill: (name: string) => request<{ ok: boolean }>(`/api/skills/${encodeURIComponent(name)}`, { method: "DELETE" }),
  generatedPlugins: () => request<GeneratedPluginListing>("/api/registry/plugins"),
  reviewGeneratedPlugin: (name: string, version?: number) =>
    request<PluginReview>(`/api/registry/plugins/${encodeURIComponent(name)}/review${version ? `?${q({ version: String(version) })}` : ""}`),
  activateGeneratedPlugin: (name: string, version: number, sha256: string) =>
    post<GeneratedPluginVersion>(`/api/registry/plugins/${encodeURIComponent(name)}/activate`, { version, sha256 }),
  promoteGeneratedPlugin: (name: string) => post<GeneratedPluginVersion>(`/api/registry/plugins/${encodeURIComponent(name)}/promote`),
  revokeGeneratedPlugin: (name: string) => post<{ ok: boolean }>(`/api/registry/plugins/${encodeURIComponent(name)}/revoke`),
  flows: () => request<{ flows: Flow[] }>("/api/flows"),
  createFlow: (body: { task: string; repo?: string; verification_commands?: string[]; candidate_count?: number; auto_start?: boolean }) =>
    post<{ flow: Flow }>("/api/flows", body),
  flow: (id: string) => request<Flow>(`/api/flows/${id}`),
  flowReview: (id: string) => request<FlowReview>(`/api/flows/${id}/review`),
  flowAction: (id: string, action: "start" | "stop" | "cancel") => post<Flow>(`/api/flows/${id}/${action}`),
  flowSelect: (id: string, candidate: string) => post<Flow>(`/api/flows/${id}/select`, { candidate_id: candidate }),
  flowApply: (id: string, sha: string) => post<Flow>(`/api/flows/${id}/apply`, { review_sha256: sha }),
  flowRollback: (id: string, commit: string) => post<Flow>(`/api/flows/${id}/rollback`, { applied_commit: commit }),

  // ── Checkpoints ──
  checkpoints: (id: string) => request<{ checkpoints: Checkpoint[]; disabled: string }>(`/api/sessions/${id}/checkpoints`),
  checkpointChanges: (id: string, checkpoint: string) => request<{ changes: { path: string; status: string }[] }>(`/api/sessions/${id}/checkpoints/${checkpoint}/changes`),
  rollback: (id: string, checkpoint = "") => post<RollbackResult>(`/api/sessions/${id}/rollback`, { checkpoint }),

  // ── Connections ──
  connections: () => request<Connections>("/api/connections"),
  githubToken: (token: string) => request<{ login: string }>("/api/connections/github", { method: "PUT", body: JSON.stringify({ token }) }),
  githubDevice: () => post<DeviceFlow>("/api/connections/github/device"),
  githubDevicePoll: (device_code: string) => post<{ pending: boolean; login?: string }>("/api/connections/github/device/poll", { device_code }),
  githubDisconnect: () => request<{ removed: boolean }>("/api/connections/github", { method: "DELETE" }),
  googleStart: () => post<{ url: string }>("/api/connections/google/start"),
  googleDisconnect: () => request<{ removed: boolean }>("/api/connections/google", { method: "DELETE" }),
  slack: () => request<SlackStatus>("/api/slack"),
  slackManifest: () => request<Record<string, unknown>>("/api/slack/manifest"),
  slackConnect: (bot_token: string, app_token: string) => request<SlackStatus>("/api/slack", { method: "PUT", body: JSON.stringify({ bot_token, app_token }) }),
  slackDisconnect: () => request<SlackStatus>("/api/slack", { method: "DELETE" }),
  keiki: () => request<KeikiStatus>("/api/keiki"),
  keikiConnect: (api_key: string) => request<KeikiStatus>("/api/keiki", { method: "PUT", body: JSON.stringify({ api_key }) }),
  keikiDisconnect: () => request<KeikiStatus>("/api/keiki", { method: "DELETE" }),

  // ── History and schedules ──
  search: (query: string) => request<{ results: SearchHit[] }>(`/api/search?${q({ q: query })}`),
  schedules: () => request<{ schedules: Schedule[]; modes: ScheduleMode[] }>("/api/schedules"),
  createSchedule: (body: { name: string; workspace: string; prompt: string; when: string; mode: ScheduleMode }) => post<Schedule>("/api/schedules", body),
  updateSchedule: (id: string, changes: Partial<Pick<Schedule, "name" | "prompt" | "when" | "mode" | "enabled" | "workspace">>) =>
    request<Schedule>(`/api/schedules/${id}`, { method: "PATCH", body: JSON.stringify(changes) }),
  deleteSchedule: (id: string) => request<{ removed: boolean }>(`/api/schedules/${id}`, { method: "DELETE" }),
  runSchedule: (id: string) => post<{ session: string }>(`/api/schedules/${id}/run`),

  // ── Plugins and produced files ──
  plugins: () => request<PluginCatalog>("/api/plugins"),
  switchPlugin: (id: string, enabled: boolean) => request<{ id: string; enabled: boolean }>(`/api/plugins/${id}`, { method: "PUT", body: JSON.stringify({ enabled }) }),
  toolboxPackages: () => request<{ packages: { name: string; version: string }[] }>("/api/plugins/toolbox/packages"),
  saveMcp: (name: string, server: { command?: string; args?: string[]; url?: string; env?: Record<string, string>; enabled?: boolean }) =>
    request<{ mcp: McpServerInfo[] }>(`/api/plugins/mcp/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify(server) }),
  deleteMcp: (name: string) => request<{ mcp: McpServerInfo[] }>(`/api/plugins/mcp/${encodeURIComponent(name)}`, { method: "DELETE" }),
  fileUrl: (sessionId: string, path: string, scope: "scratch" | "workspace", download = false) =>
    `/api/sessions/${sessionId}/file?${q({ path, scope, ...(download ? { download: "1" } : {}) })}`,
};


