// Sidebar chrome adapted from block/berd's PrimaryNavigationSurface + SidebarNavItem
// (Apache-2.0, see skye/ui/NOTICE): a FLOATING glass panel over the dot-grid canvas
// (rounded-md, translucent card, 18px backdrop blur), nav rows h-7 px-3 py-1 gap-2 mx-1.5
// rounded-sm text-sm, and Projects / Chats sections - backed by Skye's real registries:
// projects are workspaces, chats are missions (their journal is the conversation record).
import { ChevronDown, Folder, Home, ListTodo, MessageSquare, Plus, Settings, Users, Workflow } from "lucide-react";
import { useState } from "react";
import { createProject, type MissionRow, type Project } from "../api";
import { formatRelative, humanError } from "../lib";
import { Button } from "./ui/Button";

const NAV = [
  { path: "/", label: "Home", icon: Home },
  { path: "/missions", label: "Missions", icon: Workflow },
  { path: "/backlog", label: "Backlog", icon: ListTodo },
  { path: "/agents", label: "Agents", icon: Users },
  { path: "/settings", label: "Settings", icon: Settings },
];

const ROW =
  "flex h-7 w-full items-center gap-2 rounded-sm px-3 py-1 text-sm font-normal leading-normal transition-[background-color,color] duration-200";
const ROW_ACTIVE = "text-foreground";
const ROW_IDLE = "text-foreground/80 hover:text-foreground";

function NavRow({
  label,
  icon: Icon,
  active,
  href,
  onClick,
}: {
  label: string;
  icon?: typeof Home;
  active?: boolean;
  href?: string;
  onClick?: () => void;
}) {
  const cls = `${ROW} ${active ? ROW_ACTIVE : ROW_IDLE}`;
  const style = active
    ? { background: "var(--sidebar-row-active)" }
    : undefined;
  const inner = (
    <>
      {Icon ? <Icon className="size-4 shrink-0" /> : null}
      <span className="truncate">{label}</span>
    </>
  );
  return (
    <div className="mx-1.5">
      {href ? (
        <a href={href} onClick={onClick} className={cls} style={style}>
          {inner}
        </a>
      ) : (
        <button type="button" onClick={onClick} className={cls} style={style}>
          {inner}
        </button>
      )}
    </div>
  );
}

function chatStatus(mission: MissionRow): string {
  if (mission.live) return "running";
  if (mission.needs_owner || mission.overall === "needs-owner")
    return mission.approved ? "approved" : "needs you";
  switch (mission.overall) {
    case "done":
      return "done";
    case "interrupted":
      return "interrupted";
    case "not-started":
      return "not started";
    case "running":
      return "in progress";
    case "failed":
    case "gate-failed":
      return "failed";
    default:
      // An unrecognized status is unknown, not a failure.
      return "unknown";
  }
}

function SectionLabel({ children }: { children: string }) {
  return (
    <p className="px-3 pt-3.5 pb-0 text-sm font-normal leading-normal text-muted-foreground">
      {children}
    </p>
  );
}

export function Sidebar({
  activePath,
  projects,
  missions,
  unavailable = false,
  projectsOk = true,
  missionsOk = true,
  onProjectsChanged,
}: {
  activePath: string;
  projects: Project[];
  missions: MissionRow[];
  unavailable?: boolean;
  projectsOk?: boolean;
  missionsOk?: boolean;
  onProjectsChanged: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [repo, setRepo] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const addProject = async () => {
    setError(null);
    setSaving(true);
    try {
      await createProject({ name: name.trim(), repo: repo.trim() });
      setName("");
      setRepo("");
      setAdding(false);
      onProjectsChanged();
    } catch (e) {
      setError(humanError(e, "project"));
    } finally {
      setSaving(false);
    }
  };

  const chats = [...missions]
    .sort((a, b) => String(b.last_activity ?? "").localeCompare(String(a.last_activity ?? "")))
    .slice(0, 8);

  // Same-goal runs (a ticket's first mission and its repair) must be distinguishable:
  // number them oldest-first, only when a goal actually repeats.
  const runNumber = (() => {
    const byGoal = new Map<string, MissionRow[]>();
    for (const m of missions) {
      const key = m.goal || "";
      byGoal.set(key, [...(byGoal.get(key) ?? []), m]);
    }
    const out = new Map<string, number>();
    for (const group of byGoal.values()) {
      if (group.length < 2) continue;
      [...group]
        .sort((a, b) => String(a.last_activity ?? "").localeCompare(String(b.last_activity ?? "")))
        .forEach((m, i) => out.set(m.id, i + 1));
    }
    return out;
  })();

  return (
    <aside className="fixed top-2 bottom-2 left-2 z-10 flex w-60 flex-col overflow-hidden rounded-md border border-border/60 glass-panel pt-[3px] max-sm:static max-sm:m-2 max-sm:w-auto">
      <div className="flex items-center justify-between pr-1.5">
        <p className="px-3 pt-2 pb-1 text-sm font-semibold tracking-tight">Skye</p>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls="skye-nav-body"
          className="mt-1 flex items-center gap-1 rounded-sm px-2 py-1 text-xs text-muted-foreground hover:text-foreground sm:hidden"
        >
          Menu
          <ChevronDown
            className={`size-3.5 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
          />
        </button>
      </div>
      <div
        id="skye-nav-body"
        className={`flex min-h-0 flex-1 flex-col ${open ? "" : "max-sm:hidden"}`}
      >
      <nav className="space-y-0.5">
        {NAV.map((item) => (
          <NavRow
            key={item.path}
            label={item.label}
            icon={item.icon}
            href={`#${item.path}`}
            active={activePath === item.path}
            onClick={() => setOpen(false)}
          />
        ))}
      </nav>

      <div className="mx-3 mt-6 border-t border-border" />
      <div className="flex items-center justify-between pr-2">
        <SectionLabel>Projects</SectionLabel>
        <button
          type="button"
          onClick={() => setAdding((v) => !v)}
          className="mt-3 mr-1 rounded-sm p-0.5 text-muted-foreground hover:bg-foreground/[0.02] hover:text-foreground"
          aria-label="New project"
        >
          <Plus className="size-3.5" />
        </button>
      </div>
      {adding ? (
        <div className="mx-1.5 mt-1 space-y-1 rounded-sm border border-border bg-card p-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="project name"
            className="w-full rounded-sm border border-border bg-background px-2 py-1 text-xs outline-none"
          />
          <input
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            placeholder="repo path (must exist)"
            className="w-full rounded-sm border border-border bg-background px-2 py-1 font-mono text-xs outline-none"
          />
          {error ? <p className="text-xs text-destructive">{error}</p> : null}
          <Button
            size="xs"
            loading={saving}
            disabled={!name.trim() || !repo.trim()}
            onClick={() => void addProject()}
          >
            Add project
          </Button>
        </div>
      ) : null}
      <div className="space-y-0.5">
        {projects.map((project) => (
          <NavRow
            key={project.id}
            label={project.name}
            icon={Folder}
            href={`#/missions?project=${encodeURIComponent(project.id)}`}
            active={activePath === `/missions?project=${project.id}`}
          />
        ))}
        {projects.length === 0 && !adding ? (
          <p className="px-3 text-xs text-muted-foreground">
            {unavailable && !projectsOk ? "Workspaces unavailable - retrying." : "No workspaces yet."}
          </p>
        ) : null}
      </div>

      <div className="mx-3 mt-6 border-t border-border" />
      <SectionLabel>Chats</SectionLabel>
      <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto pb-2">
        {chats.map((mission) => (
          <div className="mx-1.5" key={mission.id}>
            <a
              href={`#/mission/${encodeURIComponent(mission.id)}`}
              className="flex w-full items-start gap-2 rounded-sm px-3 py-1.5 text-sm font-normal leading-normal text-foreground/80 transition-[background-color,color] duration-200 hover:text-foreground"
            >
              <MessageSquare className="mt-0.5 size-4 shrink-0" />
              <span className="min-w-0 flex-1">
                <span className="block truncate">{mission.goal || "Untitled mission"}</span>
                <span className="block truncate text-xs text-muted-foreground">
                  {runNumber.get(mission.id) ? `run ${runNumber.get(mission.id)} · ` : ""}
                  {chatStatus(mission)}
                  {mission.last_activity ? ` · ${formatRelative(mission.last_activity)}` : ""}
                </span>
              </span>
            </a>
          </div>
        ))}
        {chats.length === 0 ? (
          <p className="px-3 text-xs text-muted-foreground">
            {unavailable && !missionsOk
              ? "Conversations unavailable - retrying."
              : "No conversations yet - start one from Home."}
          </p>
        ) : null}
      </div>
      <p className="border-t border-border px-3 py-2 text-xs leading-4 text-muted-foreground">
        Local only. Interrupted work waits for you.
      </p>
      </div>
    </aside>
  );
}
