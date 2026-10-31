// The sidebar as Tura's: the mark, a few places (each with its wayfinding
// colour), then the workspaces with their sessions nested under them.

import { AlarmClock, ChevronDown, ChevronRight, Folder, LogOut, Moon, PanelLeftClose, PanelLeftOpen, Plus, Search, Settings2, SquarePen, Sun, Trash2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import type { Session } from "../lib/types.ts";
import { Brand, TuraMark } from "./TuraBrand.tsx";
import { shortcut } from "../lib/keys.ts";

export type View = "home" | "session" | "schedules";

function basename(path: string): string {
  return path.split("/").filter(Boolean).pop() ?? path;
}

function NavItem({ icon, label, active, badge, onClick, collapsed }: { icon: ReactNode; label: string; active?: boolean; badge?: number; onClick: () => void; collapsed: boolean }) {
  return (
    <button className={`navItem${active ? " active" : ""}`} onClick={onClick} title={collapsed ? label : undefined} aria-label={label}>
      {icon}
      {collapsed ? null : <span>{label}</span>}
      {!collapsed && badge ? <span className="badge">{badge}</span> : null}
    </button>
  );
}

export function Rail({
  sessions,
  activeId,
  view,
  collapsed,
  theme,
  user,
  onToggleCollapsed,
  onNavigate,
  onSearch,
  onSelect,
  onNew,
  onDelete,
  onDeleteWorkspace,
  onSettings,
  onTheme,
  onSignOut,
}: {
  sessions: Session[];
  activeId: string | null;
  view: View;
  collapsed: boolean;
  theme: "dark" | "light";
  user: string | null;
  onToggleCollapsed: () => void;
  onNavigate: (view: "home" | "schedules") => void;
  onSearch: () => void;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (session: Session) => void;
  onDeleteWorkspace: (workspace: string, sessions: Session[]) => void;
  onSettings: () => void;
  onTheme: () => void;
  onSignOut?: () => void;
}) {
  const activeWorkspace = sessions.find((s) => s.id === activeId)?.workspace;
  const [openWorkspaces, setOpenWorkspaces] = useState<Set<string>>(() => new Set(activeWorkspace ? [activeWorkspace] : []));

  useEffect(() => {
    if (activeWorkspace) {
      setOpenWorkspaces((prev) => {
        if (prev.has(activeWorkspace)) return prev;
        const next = new Set(prev);
        next.add(activeWorkspace);
        return next;
      });
    }
  }, [activeWorkspace]);

  const groups = new Map<string, Session[]>();
  for (const session of sessions) groups.set(session.workspace, [...(groups.get(session.workspace) ?? []), session]);

  const nav = (
    <nav className="railNav">
      <NavItem collapsed={collapsed} icon={<SquarePen size={16} />} label="New session" active={view === "home"} onClick={() => onNavigate("home")} />
      {/* Expanded, search is the magnifier beside the brand (as in Tura); collapsed, the header has no room. */}
      {collapsed ? <NavItem collapsed icon={<Search size={16} />} label="Search" onClick={onSearch} /> : null}
      <NavItem collapsed={collapsed} icon={<AlarmClock size={16} />} label="Schedules" active={view === "schedules"} onClick={() => onNavigate("schedules")} />
    </nav>
  );

  const foot = (
    <div className="railFoot">
      <NavItem collapsed={collapsed} icon={theme === "dark" ? <Moon size={16} /> : <Sun size={16} />} label={theme === "dark" ? "Dark" : "Light"} onClick={onTheme} />
      <NavItem collapsed={collapsed} icon={<Settings2 size={16} />} label="Settings" onClick={onSettings} />
      {onSignOut ? <NavItem collapsed={collapsed} icon={<LogOut size={16} />} label={user ? `Sign out ${user}` : "Sign out"} onClick={onSignOut} /> : null}
    </div>
  );

  if (collapsed) {
    return (
      <aside className="card rail collapsed">
        <div className="railHead">
          <TuraMark />
        </div>
        <button className="iconButton" onClick={onToggleCollapsed} title="Expand sidebar">
          <PanelLeftOpen size={17} />
        </button>
        {nav}
        <div style={{ flex: 1 }} />
        {foot}
      </aside>
    );
  }

  return (
    <aside className="card rail">
      <div className="railHead">
        <Brand />
        <span className="railHeadActions">
          <button className="iconButton" onClick={onSearch} title={`Search (${shortcut("K")})`} aria-label="Search">
            <Search size={17} />
          </button>
          <button className="iconButton" onClick={onToggleCollapsed} title="Collapse sidebar">
            <PanelLeftClose size={17} />
          </button>
        </span>
      </div>
      {nav}
      <div className="sectionLabel">
        <span>Workspaces</span>
        <span className="railLabelActions">
          <span className="count">{groups.size}</span>
          <button className="iconButton" onClick={onNew} title="New session in a folder" aria-label="New session in a folder">
            <Plus size={14} />
          </button>
        </span>
      </div>
      <div className="sessionTree scroll">
        {sessions.length === 0 ? <p className="faint small" style={{ padding: "0 8px" }}>No sessions yet.</p> : null}
        {[...groups.entries()].map(([workspace, list]) => {
          const open = openWorkspaces.has(workspace);
          return (
            <div key={workspace}>
              <div
                className="workspaceRow"
                role="button"
                tabIndex={0}
                title={workspace}
                onClick={() =>
                  setOpenWorkspaces((current) => {
                    const next = new Set(current);
                    if (next.has(workspace)) next.delete(workspace);
                    else next.add(workspace);
                    return next;
                  })
                }
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setOpenWorkspaces((current) => {
                      const next = new Set(current);
                      if (next.has(workspace)) next.delete(workspace);
                      else next.add(workspace);
                      return next;
                    });
                  }
                }}
              >
                {open ? <ChevronDown size={13} className="faint" /> : <ChevronRight size={13} className="faint" />}
                <Folder size={14} />
                <span className="name">{basename(workspace)}</span>
                <span className="count">{list.length}</span>
                <button
                  type="button"
                  className="iconButton rowAction"
                  title={`Delete workspace “${basename(workspace)}”`}
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteWorkspace(workspace, list);
                  }}
                >
                  <Trash2 size={13} />
                </button>
              </div>
              {open ? (
                <div className="workspaceSessions">
                  {list.map((session) => (
                    <div
                      key={session.id}
                      className={`sessionRow${session.id === activeId && view === "session" ? " active" : ""}`}
                      role="button"
                      tabIndex={0}
                      onClick={() => onSelect(session.id)}
                      onKeyDown={(e) => e.key === "Enter" && onSelect(session.id)}
                    >
                      {session.running ? <span className="runningDot" title="Working" /> : null}
                      <span className="title" title={session.title}>
                        {session.title}
                      </span>
                      <button
                        className="iconButton rowAction"
                        title="Delete session"
                        onClick={(e) => {
                          e.stopPropagation();
                          onDelete(session);
                        }}
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
      {foot}
    </aside>
  );
}
