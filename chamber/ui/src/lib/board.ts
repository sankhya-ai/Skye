// The board's data: conversations as cards, people, channels, and what each view shows.
// Pure helpers, tested in board.test.ts.

export type Status = "waiting" | "talking" | "done";

export interface Card {
  id: string;
  title: string;
  status: Status;
  unread: boolean; // someone else said something since you last opened it (Gmail's bold)
  people: string[];
  with: string[];
  channel: string | null;
  branch?: string | null;
  count: number;
  last: { from: string; text: string; ts: number };
  updated: number;
}

export interface TalkMessage {
  id: string;
  from: string;
  to: string;
  text: string;
  type?: string;
  patch?: string | null;
  branch?: string | null;
  ts: number;
  files: { name: string; size?: number; path?: string; error?: string }[];
  mentions?: string[];
  reactions?: Record<string, string[]>;
}

export interface Talk extends Card {
  messages: TalkMessage[];
}

export interface Workspace {
  agent: string;
  name: string;
  kind: string;
  path: string;
  online: boolean;
}

export interface Project {
  path: string; // "" for an agent with no folder (e.g. DeepSeek Harness)
  name: string;
  agents: { agent: string; kind: string; online: boolean }[];
}

export interface Channel {
  name: string;
  members: string[];
  joined: boolean;
}

export interface Agent {
  agent: string;
  owner?: string;
  kind: string;
  label: string;
  answers: boolean;
}

export interface BoardData {
  configured: boolean;
  me: string;
  admin: boolean;
  connected: boolean;
  problem: string;
  agents: Agent[];
  channels: Channel[];
  workspaces: Workspace[];
  projects: Project[];
  removed?: string[]; // names the admin removed from the chamber
  quota?: import("./api").QuotaInfo | null;
  cards: Card[];
  chamber?: { id: string; name: string }; // the chamber in use
  chambers?: ChamberSummary[]; // every chamber on this machine, like Slack's workspaces
  adding?: boolean; // adding another chamber: the onboarding can go back to the one you were in
}

export interface ChamberSummary {
  id: string;
  name: string;
  relay: string;
  owner: string;
  admin: boolean;
  active: boolean;
}

/** What the main area shows. */
export type View =
  | { kind: "work" }
  | { kind: "inbox" }
  | { kind: "all" }
  | { kind: "channel"; name: string }
  | { kind: "person"; owner: string }
  | { kind: "agent"; agent: string }
  | { kind: "project"; path: string }
  | { kind: "memory" };

export const STATUSES: { status: Status; label: string }[] = [
  { status: "waiting", label: "Waiting on you" },
  { status: "talking", label: "Agents working" },
  { status: "done", label: "Done" },
];

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** Short names for the kinds of coding agent. */
export const KIND: Record<string, string> = { "claude-code": "Claude", codex: "Codex", antigravity: "Antigravity", dsh: "DeepSeek", root: "root" };
export const ownerOf = (agent: string) => (agent.startsWith("@") ? agent.slice(1) : agent.split("/")[0]);
export const isMine = (agent: string, me: string) => agent.startsWith(`${me}/`) || agent === `@${me}`;

/** Detect agent harness brand/kind for visual badging. */
export function harnessOf(agent: string): string | null {
  if (!agent || agent.startsWith("#") || agent.startsWith("@")) return null;
  const parts = agent.split("/")[1]?.split(".");
  if (!parts) return null;
  const kind = parts[0];
  if (kind === "root" || kind === "board" || kind === "terminal") return null;
  return KIND[kind] ?? kind;
}

/** "you", "your root", "your Claude · api", "Nitin", "Nitin's Claude · web", "#general". */
export function who(agent: string, me: string, { thirdPerson = false }: { thirdPerson?: boolean } = {}): string {
  if (!agent) return "";
  if (agent === "*") return "everyone";
  if (agent.startsWith("#")) return agent;
  if (agent.startsWith("@")) return agent.slice(1) === me ? (thirdPerson ? cap(me) : "you") : cap(agent.slice(1));
  const [owner, name = ""] = agent.split("/");
  const mine = owner === me;
  if (name === "board" || name === "terminal") return mine ? (thirdPerson ? cap(owner) : "you") : cap(owner);
  const [kind, ...rest] = name.split(".");
  const where = rest.join(".");
  const thing = name === "root" ? "root" : name === "dsh" ? "DeepSeek" : KIND[kind] ? `${KIND[kind]}${where ? ` · ${where}` : ""}` : name;
  return mine && !thirdPerson ? `your ${thing}` : `${cap(owner)}'s ${thing}`;
}

/** The person behind an address, for avatars: "Nitin", or "You". */
export function personOf(agent: string, me: string): string {
  if (agent.startsWith("#")) return agent;
  const owner = ownerOf(agent);
  return owner === me ? "You" : cap(owner);
}

/** Your teammates: everyone but you seen online or in a conversation, online ones first. */
export function people(data: BoardData | null): { owner: string; online: boolean }[] {
  if (!data) return [];
  const online = new Set(data.agents.map((a) => ownerOf(a.agent)));
  const seen = new Set<string>(online);
  for (const card of data.cards) for (const p of card.people) if (!p.startsWith("#") && p !== "*") seen.add(ownerOf(p));
  for (const c of data.channels) for (const m of c.members) seen.add(m);
  seen.delete(data.me);
  for (const r of data.removed ?? []) seen.delete(r);
  return [...seen].sort((a, b) => Number(online.has(b)) - Number(online.has(a)) || a.localeCompare(b)).map((owner) => ({ owner, online: online.has(owner) }));
}

/** The cards a view shows (`projects` resolves a project view to its agents). */
export function inView(cards: Card[], view: View, projects: Project[] = []): Card[] {
  switch (view.kind) {
    case "work":
      return [];
    case "inbox":
      return cards; // everything, newest first; unread ones stand out
    case "all":
      return cards;
    case "channel":
      return cards.filter((c) => c.channel === view.name);
    case "person":
      return cards.filter((c) => !c.channel && c.with.some((p) => ownerOf(p) === view.owner));
    case "agent":
      return cards.filter((c) => c.people.includes(view.agent));
    case "project": {
      const agents = new Set(projects.find((p) => p.path === view.path)?.agents.map((a) => a.agent) ?? []);
      return cards.filter((c) => c.people.some((p) => agents.has(p)));
    }
    case "memory":
      return [];
  }
}

/** Where a new message typed in a view goes. */
export function addressFor(view: View): string {
  if (view.kind === "channel") return view.name;
  if (view.kind === "project" || view.kind === "memory" || view.kind === "work") return "";
  if (view.kind === "person") return `@${view.owner}`;
  if (view.kind === "agent") return view.agent;
  return "";
}

export function titleOf(view: View, me: string): string {
  if (view.kind === "work") return "My team's work";
  if (view.kind === "inbox") return "Inbox";
  if (view.kind === "all") return "All talks";
  if (view.kind === "channel") return view.name;
  if (view.kind === "person") return cap(view.owner);
  if (view.kind === "memory") return "Memory";
  if (view.kind === "project") return view.path.split("/").pop() || view.path;
  return who(view.agent, me);
}

/** Who a message typed into a talk goes to by default: its channel, else whoever spoke last. */
export function defaultRecipient(talk: Talk, me: string): string {
  if (talk.channel) return talk.channel;
  for (const m of [...talk.messages].reverse()) {
    if (!isMine(m.from, me)) return m.from;
    if (!isMine(m.to, me) && m.to !== "*") return m.to;
  }
  return talk.with[0] ?? "";
}

/** What was typed into "to", as an address: a bare name means that person (`nitin` → `@nitin`). */
export function address(typed: string): string {
  const v = typed.trim().toLowerCase();
  if (!v || v.includes("/") || v.startsWith("@") || v.startsWith("#") || v === "*") return v;
  return `@${v}`;
}

export function ago(ts: number, now = Date.now()): string {
  const s = Math.max(0, Math.round((now - ts) / 1000));
  if (s < 45) return "now";
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

/** A stable, quiet avatar colour per person. */
export function hue(name: string): number {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return h;
}
