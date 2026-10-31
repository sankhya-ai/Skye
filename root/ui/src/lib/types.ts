// The host's event envelope and the runtime payloads the UI renders.
// Runtime payload shapes are defined in runtime/kimi-agent/src/wire/types.rs
// and runtime/kosong/src/{message,tooling}.rs.

export type PermissionMode = "default" | "accept_edits" | "plan" | "yolo";

export interface WireEvent {
  seq: number;
  ts: number;
  turn: string | null;
  type: string;
  payload: any;
}

export interface DisplayBlock {
  type: string;
  text?: string;
  path?: string;
  old_text?: string;
  new_text?: string;
  items?: { title: string; status: string }[];
  language?: string;
  command?: string;
  [key: string]: unknown;
}

export interface ToolReturnValue {
  is_error: boolean;
  output: string | { type: string; text?: string }[];
  message: string;
  display: DisplayBlock[];
  extras?: Record<string, unknown> | null;
}

export interface TrustInfo {
  key: string;
  promotable: boolean;
  approvals: number;
  promote_after: number;
  tier?: "read" | "verify" | "needs_trust";
  effects?: string[];
  reasons?: string[];
  /** Set when the request reaches outside the session's workspace. */
  outside?: string;
}

export interface ApprovalRequest {
  id: string;
  tool_call_id: string;
  sender: string;
  action: string;
  description: string;
  display: DisplayBlock[];
  trust?: TrustInfo;
}

/** An owner-reviewed job proposal stored by ``recommend_job``: the session proposed; the owner's accept is the only start. */
export interface JobRequest {
  id: string;
  goal: string;
  role: string;
  role_version: number;
  workspace: string;
  reason: string;
  requested_by: string;
  /** The role's effective tool scope resolved at proposal time (display-only; the accept re-resolves the pinned role). */
  scope?: string[];
  /** Job-specific limits pinned at proposal time; the accept applies exactly these. */
  budget?: { max_steps?: number; timeout_seconds?: number; max_tokens?: number; max_cost_usd?: number };
  status: "pending" | "accepted" | "dismissed";
  created_at: number;
  job_id?: string;
  /** Set when a job (not a chat session) proposed this: the parent job's id and this request's delegation depth. */
  parent_job_id?: string;
  depth?: number;
  /** True when the proposed role's scope reaches beyond the proposer's own scope: the owner decides whether to widen. */
  escalation?: boolean;
}

export interface TokenUsage {
  input_other: number;
  output: number;
  input_cache_read?: number;
  input_cache_creation?: number;
  cost_usd?: number | null;
}

export interface MemoryItem {
  id: string;
  memory: string;
  scope: "project" | "owner";
  score?: number;
  created_at?: string | null;
  /** What Dhee filed it as: decision, convention, command, preference, fact, gotcha. */
  metadata?: { kind?: string };
}

export interface SlashCommand {
  name: string;
  description: string;
  aliases: string[];
}

export interface QueuedMessage {
  id: string;
  text: string;
  /** How many files ride along. */
  attachments: number;
}

export interface Session {
  id: string;
  workspace: string;
  title: string;
  created_at: number;
  updated_at: number;
  permission_mode: PermissionMode;
  turns: number;
  last_status: string;
  grants: string[];
  kind: "session" | "candidate" | "line";
  parent: string;
  role: string;
  /** A scheduled run nobody watches: what would ask the owner is declined. */
  unattended?: boolean;
  /** The schedule that started this session. */
  schedule?: string;
  running: boolean;
  /** Approvals waiting for the owner right now. */
  pending_approvals?: number;
  /** Messages sent during a turn, run in order when it finishes (paused after a cancel or error). */
  queued?: QueuedMessage[];
  runtime_state: string;
  runtime: {
    model?: string;
    director?: string;
    memory?: string;
    sandbox?: string;
    slash_commands?: SlashCommand[];
    server?: { name?: string; version?: string };
  };
}

export interface Settings {
  openrouter_api_key: string;
  api_key_set: boolean;
  api_key_hint: string;
  api_key_source: "environment" | "settings" | "missing";
  base_url: string;
  model: string;
  /** Tried in order when the model fails (OpenRouter's `models`). */
  fallback_models: string[];
  max_context_size: number;
  reasoning_effort: string;
  provider_sort: string;
  max_steps_per_turn: number;
  director_mode: "on" | "shadow" | "off";
  memory_mode: "local" | "openrouter" | "off";
  memory_llm_model: string;
  memory_embedding_model: string;
  memory_embedding_dims: number;
  web_search: boolean;
  parallel_api_key: string;
  firecrawl_api_key: string;
  parallel_key_set: boolean;
  firecrawl_key_set: boolean;
  web_search_paid_fallback: boolean;
  search_model: string;
  learning_mode: "review" | "auto" | "off";
  learning_model: string;
  default_permission_mode: PermissionMode;
  recent_workspaces: string[];
  /** The most parallel candidates the agent may give a coding flow. */
  flow_max_candidates: number;
  /** Plugins switched off: the agent gets none of their tools. */
  disabled_plugins: string[];
}

export interface ModelInfo {
  id: string;
  name: string;
  context_length?: number | null;
  tools: boolean;
  reasoning: boolean;
  capabilities: string[];
  prompt_price?: string | null;
  completion_price?: string | null;
}

export interface Health {
  version: string;
  mode: "local" | "hosted";
  signed_in: boolean;
  runtime: { binary: string; error: string };
  memory: { requested_mode: string; active_mode: string; installed: boolean; version: string; ready: boolean; error: string };
  api_key_set: boolean;
  scripted: boolean;
  /** Where root keeps its data (local installs). */
  data_dir?: string;
  /** The host can show the native folder dialog (local mode on macOS). */
  folder_picker?: boolean;
}

export type ServerMessage =
  | { type: "hello"; session: Session; history: WireEvent[]; has_more: boolean; pending_approvals: ApprovalRequest[] }
  | { type: "event"; event: WireEvent }
  | { type: "session"; session: Session }
  | { type: "error"; message: string }
  | { type: "pong" };

export interface Attachment {
  name: string;
  mime: string;
  data: string; // base64
  preview?: string; // object URL for images
}

export interface Me {
  mode: "local" | "hosted";
  user: { id: string; name: string; admin: boolean };
  sandbox: string;
}

export interface TrustEntry {
  key: string;
  approvals: number;
  rejections: number;
  promoted: boolean;
  updated_at?: number;
}

export interface ProcessInfo {
  id: string;
  label: string;
  command: string;
  status: "running" | "exited" | "killed";
  exit_code: number | null;
  pid: number | null;
  runtime_seconds: number;
  output_tail?: string;
}

export interface LearningCandidate {
  id: string;
  kind: "skill" | "memory";
  status: "candidate" | "promoted" | "rejected";
  title: string;
  body: {
    name?: string;
    trigger?: string;
    lesson?: string;
    steps?: string[];
    verification?: string[];
    pitfalls?: string[];
    content?: string;
    scope?: string;
    kind?: string;
  };
  review_summary: string;
  updates_existing: boolean;
  session_id: string;
  workspace: string;
  created_at: number;
}

export interface LearnedSkill {
  name: string;
  description: string;
  updated_at: number;
  versions: number;
  /** Clean-turn record of the current version, against what it replaced. */
  outcome: {
    version: string;
    uses: number;
    clean: number;
    rate: number | null;
    against: string;
    reference_rate: number | null;
    underperforming: boolean;
  } | null;
}

export interface FlowCandidate {
  id: string;
  status: string;
  session_id: string;
  attempts: number;
  verification: { command: string; exit_code: number | null; output_tail: string }[];
  diff: { sha256: string; changed_paths: string[]; stat: string; bytes: number } | null;
}

export interface Flow {
  id: string;
  status: "preparing" | "working" | "needs_review" | "ready_to_apply" | "applied" | "rolled_back" | "failed" | "cancelled";
  task: string;
  acceptance: string[];
  verification_commands: string[];
  repo: string;
  base_commit: string;
  owner_session: string;
  created_at: number;
  updated_at?: number;
  candidates: FlowCandidate[];
  selected: string | null;
  judgment: { winner: string; summary: string } | null;
  judge_error: string;
  apply: { applied_commit: string; diff_sha256: string } | null;
  rollback: { rollback_commit: string } | null;
  controls: { can_start: boolean; can_stop: boolean; can_select: boolean; can_review: boolean; can_apply: boolean; can_cancel: boolean; can_rollback: boolean };
  summary?: string;
  running?: boolean;
}

export interface FlowReview {
  flow_id: string;
  candidate_id: string;
  base_commit: string;
  diff: { sha256: string; changed_paths: string[]; stat: string; patch: string };
  judgment: Flow["judgment"];
}

// ── Checkpoints, connections, history, schedules ──

export interface Checkpoint {
  id: string;
  created_at: number;
  label: string;
}

export interface RollbackResult {
  checkpoint: Checkpoint;
  undo: Checkpoint;
  restored: string[];
  removed: string[];
}

export interface Connections {
  github: { connected: boolean; source: string; login?: string; how?: string; device_flow: boolean; connected_at?: number };
  gmail: { connected: boolean; configured: boolean; email?: string; connected_at?: number };
  /** Where credentials live: "keychain" (the macOS app) or "file". */
  secrets: "keychain" | "file";
}

export interface SlackStatus {
  configured: boolean;
  team: string;
  bot: string;
  connected: boolean;
  answers: boolean;
  problem: string;
}

export interface KeikiStatus {
  configured: boolean;
  base: string;
  connected: boolean;
  answers: boolean;
  turns_answered: number;
  problem: string;
}

export interface DeviceFlow {
  device_code: string;
  user_code: string;
  verification_uri: string;
  interval: number;
  expires_in: number;
}

export interface SearchHit {
  session_id: string;
  turn_id: string;
  finished_at: number;
  workspace: string;
  title: string;
  /** Snippets with [matched] words. */
  prompt: string;
  reply: string;
}

export type ScheduleMode = "plan" | "default" | "accept_edits";

export interface Schedule {
  id: string;
  name: string;
  workspace: string;
  prompt: string;
  when: string;
  cron: string;
  mode: ScheduleMode;
  enabled: boolean;
  created_at: number;
  created_by: "owner" | "agent";
  next_run: number | null;
  last_run: number | null;
  last_status: string;
  last_session: string;
  runs: number;
  running: boolean;
}

export interface Plugin {
  id: string;
  name: string;
  kind: "built-in" | "connection";
  icon: string;
  summary: string;
  capabilities: string[];
  enabled: boolean;
}

export interface McpServerInfo {
  name: string;
  enabled: boolean;
  command: string;
  args: string[];
  url: string;
  /** Names of its environment variables (values never leave the server). */
  env: string[];
}

export interface PluginCatalog {
  plugins: Plugin[];
  mcp: McpServerInfo[];
  toolbox: { path: string; ready: boolean };
}

export interface ProducedFile {
  path: string;
  scope: "scratch" | "workspace";
  size: number;
}

/** A generated plugin version as listed by GET /api/registry/plugins. */
export interface GeneratedPluginVersion {
  version: number;
  status: string;
  lifetime?: string;
  session?: string;
  sha256?: string;
  created_by?: string;
  created_at?: string;
  activated_at?: string | null;
  verified?: { ok?: boolean } | null;
}

export interface GeneratedPluginEntry {
  active_version: number | null;
  versions: GeneratedPluginVersion[];
}

export type GeneratedPluginListing = Record<string, GeneratedPluginEntry>;

/** The full owner review payload: exactly what activation would run. */
export interface PluginReview {
  name: string;
  version: number;
  status: string;
  lifetime?: string;
  session?: string;
  created_by?: string;
  created_at?: string;
  activated_at?: string | null;
  verified?: { ok?: boolean; results?: { name: string; ok: boolean }[] } | null;
  sha256: string;
  on_disk_sha256: string;
  hash_matches_record: boolean;
  files: Record<string, string>;
  tools: { name: string; description?: string; parameters?: unknown }[];
  execution: { available: boolean; reason: string; isolation: string };
  activation: string;
}
