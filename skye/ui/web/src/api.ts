// API client for the Skye UI server. The session token arrives in the page URL
// (printed by `skye ui`) and is sent as a header on every API call; the server
// sets no cookies, so nothing ambient rides along cross-origin.

export type MissionRow = {
  id: string;
  goal: string;
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

export type MissionDetail = {
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
  if (!res.ok) throw new Error(body.error ?? `request failed (${res.status})`);
  return body as T;
}

export const getMissions = () =>
  api<{ missions: MissionRow[]; live: Record<string, { running?: boolean }> }>("/api/missions");
export const getMission = (id: string) => api<MissionDetail>(`/api/missions/${id}`);
export const approveStep = (mission: string, step: string) =>
  api<{ approved: boolean }>("/api/approve", { method: "POST", body: JSON.stringify({ mission, step }) });
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
export const getPolicy = () =>
  api<{ raw: Record<string, unknown>; path: string; max_concurrent: number }>("/api/policy");
export const savePolicy = (policy: Record<string, unknown>) =>
  api<{ saved: boolean }>("/api/policy", { method: "POST", body: JSON.stringify({ policy }) });
export const createMission = (spec: { goal: string; plan?: boolean; run?: boolean }) =>
  api<{ id: string; running: boolean }>("/api/missions", {
    method: "POST",
    body: JSON.stringify(spec),
  });
