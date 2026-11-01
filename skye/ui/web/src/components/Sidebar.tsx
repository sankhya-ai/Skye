// Sidebar chrome adapted from block/berd's PrimaryNavigationSurface + SidebarNavItem
// (Apache-2.0, see skye/ui/NOTICE): a FLOATING glass panel over the dot-grid canvas
// (rounded-md, translucent card, 18px backdrop blur), nav rows h-7 px-3 py-1 gap-2 mx-1.5
// rounded-sm text-sm, and Projects / Chats sections - backed by Skye's real registries:
// projects are workspaces, chats are missions (their journal is the conversation record).
import { Folder, Home, ListTodo, MessageSquare, Plus, Settings, Users, Workflow } from "lucide-react";
import { useState } from "react";
import { createProject, type MissionRow, type Project } from "../api";

const NAV = [
  { path: "/", label: "Home", icon: Home },
  { path: "/missions", label: "Missions", icon: Workflow },
  { path: "/backlog", label: "Backlog", icon: ListTodo },
  { path: "/agents", label: "Agents", icon: Users },
  { path: "/settings", label: "Settings", icon: Settings },
];

const ROW =
  "flex h-7 w-full items-center gap-2 rounded-sm px-3 py-1 text-sm font-normal leading-normal transition-[background-color,color] duration-200";
const ROW_ACTIVE = "bg-foreground/[0.03] text-foreground";
const ROW_IDLE = "text-foreground/80 hover:bg-foreground/[0.02] hover:text-foreground";

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
  const inner = (
    <>
      {Icon ? <Icon className="size-4 shrink-0" /> : null}
      <span className="truncate">{label}</span>
    </>
  );
  return (
    <div className="mx-1.5">
      {href ? (
        <a href={href} className={cls}>
          {inner}
        </a>
      ) : (
        <button type="button" onClick={onClick} className={cls}>
          {inner}
        </button>
      )}
    </div>
  );
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
  onProjectsChanged,
}: {
  activePath: string;
  projects: Project[];
  missions: MissionRow[];
  onProjectsChanged: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [repo, setRepo] = useState("");
  const [error, setError] = useState<string | null>(null);

  const addProject = async () => {
    setError(null);
    try {
      await createProject({ name: name.trim(), repo: repo.trim() });
      setName("");
      setRepo("");
      setAdding(false);
      onProjectsChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const chats = [...missions]
    .sort((a, b) => (b.last_activity ?? "").localeCompare(a.last_activity ?? ""))
    .slice(0, 8);

  return (
    <aside className="fixed top-2 bottom-2 left-2 z-10 flex w-60 flex-col overflow-hidden rounded-md border border-border/60 bg-card/80 pt-[3px] backdrop-blur-md">
      <p className="px-3 pt-2 pb-1 text-sm font-semibold tracking-tight">Skye</p>
      <nav className="space-y-0.5">
        {NAV.map((item) => (
          <NavRow
            key={item.path}
            label={item.label}
            icon={item.icon}
            href={`#${item.path}`}
            active={activePath === item.path}
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
          {error ? <p className="text-[11px] text-destructive">{error}</p> : null}
          <button
            type="button"
            onClick={() => void addProject()}
            disabled={!name.trim() || !repo.trim()}
            className="rounded-sm bg-foreground px-2 py-1 text-[11px] font-medium text-background disabled:opacity-30"
          >
            Add project
          </button>
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
          <p className="px-3 text-xs text-muted-foreground">No workspaces yet.</p>
        ) : null}
      </div>

      <div className="mx-3 mt-6 border-t border-border" />
      <SectionLabel>Chats</SectionLabel>
      <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto pb-2">
        {chats.map((mission) => (
          <NavRow
            key={mission.id}
            label={mission.goal || mission.id}
            icon={MessageSquare}
            href={`#/mission/${encodeURIComponent(mission.id)}`}
          />
        ))}
        {chats.length === 0 ? (
          <p className="px-3 text-xs text-muted-foreground">
            No conversations yet - start one from Home.
          </p>
        ) : null}
      </div>
      <p className="border-t border-border px-3 py-2 text-[11px] leading-4 text-muted-foreground">
        Local only. Runs are journaled; interrupted work waits for an explicit operator decision.
      </p>
    </aside>
  );
}
