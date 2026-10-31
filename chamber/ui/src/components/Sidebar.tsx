import { BookOpen, Bot, Check, Cpu, Folder, Hash, Inbox, Layers, Moon, Plus, Search, Settings, Shield, Sun, UserPlus, Users, X } from "lucide-react";
import { useState } from "react";

import { Avatar } from "@/components/Avatar";
import { ChamberSwitcher } from "@/components/ChamberSwitcher";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { inView, KIND, people, type BoardData, type View } from "@/lib/board";
import { PlanNote } from "@/components/PlanNote";
import { cn } from "@/lib/utils";

interface Props {
  data: BoardData | null;
  view: View;
  dark: boolean;
  pendingGatesCount?: number;
  activeCoordinationCount?: number;
  awayActive?: boolean;
  awayStatusText?: string;
  onView: (view: View) => void;
  onSearch?: () => void;
  onNewChannel: () => void;
  onInvite: () => void;
  onMembers: () => void;
  onGates?: () => void;
  onCoordination?: () => void;
  onAway?: () => void;
  onSettings?: () => void;
  onTheme: () => void;
  onOpenAgents?: () => void;
  onChanged: () => void;
}

const same = (a: View, b: View) => JSON.stringify(a) === JSON.stringify(b);
const FOLDERS_SHOWN = 5;

function Row({ active, onClick, children, end }: { active: boolean; onClick: () => void; children: React.ReactNode; end?: React.ReactNode }) {
  return (
    <div
      role="button"
      onClick={onClick}
      className={cn(
        "group flex h-7 cursor-pointer items-center gap-2 rounded-md px-2 text-[13px] text-muted-foreground hover:bg-accent hover:text-foreground",
        active && "bg-accent font-medium text-foreground",
      )}
    >
      {children}
      {end ? <span className="ml-auto flex items-center">{end}</span> : null}
    </div>
  );
}

function Section({ title, action, children }: { title: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="mt-5">
      <div className="mb-1 flex h-6 items-center justify-between px-2 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
        {title}
        {action}
      </div>
      <div className="flex flex-col gap-px">{children}</div>
    </div>
  );
}

function IconButton({ label, onClick, children }: { label: string; onClick: (e: React.MouseEvent) => void; children: React.ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button aria-label={label} onClick={onClick} className="inline-flex size-6 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground">
          {children}
        </button>
      </TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}

/** Slack's sidebar: inbox, channels, people, approval gates, your agents. */
export function Sidebar({
  data,
  view,
  dark,
  pendingGatesCount = 0,
  activeCoordinationCount = 0,
  awayActive = false,
  awayStatusText = "",
  onView,
  onSearch,
  onNewChannel,
  onInvite,
  onMembers,
  onGates,
  onCoordination,
  onAway,
  onSettings,
  onTheme,
  onOpenAgents,
  onChanged,
}: Props) {
  const [naming, setNaming] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [showAllFolders, setShowAllFolders] = useState(false);
  // Folders with an agent running first; past a handful, the rest wait behind "Show more".
  const projects = [...(data?.projects ?? [])].sort((a, b) => Number(b.agents.some((x) => x.online)) - Number(a.agents.some((x) => x.online)));
  const me = data?.me ?? "";
  const waiting = data ? data.cards.filter((c) => c.unread).length : 0;
  const unread = (v: View) => (data ? inView(data.cards, v, data.projects).filter((c) => c.unread).length : 0);

  const act = async (work: () => Promise<unknown>, done?: string) => {
    try {
      await work();
      if (done) setNote(done);
      onChanged();
    } catch (e) {
      setNote(String((e as Error).message));
    }
  };

  const addProject = () =>
    act(async () => {
      const picked = await api.pickFolder();
      if (!picked.path) return;
      await api.addWorkspace(picked.path);
      setNote(`Added ${picked.path.split("/").pop()}. Restart its coding agents to connect.`);
    });

  return (
    <aside className="flex h-full w-60 shrink-0 flex-col px-2 pt-3 pb-2">
      <div className="flex items-center justify-between gap-2 px-1 pb-3">
        <ChamberSwitcher data={data} onChanged={onChanged} />
        <div className="flex shrink-0 items-center gap-1.5">
          {data?.quota ? (
            <span
              className={cn(
                "rounded px-1.5 py-0.5 text-[10px] font-medium tracking-tight",
                data.quota.warning
                  ? "bg-[#c5b4e3]/20 text-[#604988] dark:text-[#c5b4e3]"
                  : "bg-muted text-muted-foreground"
              )}
              title={`Cloudflare DO Daily Quota: ${data.quota.dailyRequests.toLocaleString()} / ${data.quota.dailyRequestsLimit.toLocaleString()} requests (${data.quota.requestsPercentage}%)`}
            >
              CF {data.quota.requestsPercentage}%
            </span>
          ) : null}
          <span className={cn("size-2 rounded-full", data?.connected ? "bg-online" : "bg-waiting")} title={data?.connected ? "Connected" : data?.problem || "Connecting…"} />
        </div>
      </div>

      <nav className="scroll-thin flex-1 overflow-y-auto">
        <Row
          active={false}
          onClick={() => onSearch?.()}
          end={
            <span className="rounded bg-muted/60 px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground border border-border/60">
              ⌘K
            </span>
          }
        >
          <Search className="size-4 text-muted-foreground" /> Search
        </Row>
        <Row
          active={same(view, { kind: "work" })}
          onClick={() => onView({ kind: "work" })}
          end={
            pendingGatesCount > 0 ? (
              <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-semibold text-primary-foreground">
                {pendingGatesCount}
              </span>
            ) : activeCoordinationCount > 0 ? (
              <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-muted px-1 text-[10px] font-semibold text-foreground">
                {activeCoordinationCount}
              </span>
            ) : null
          }
        >
          <Cpu className="size-4" /> Team Work
        </Row>
        <Row active={same(view, { kind: "inbox" })} onClick={() => onView({ kind: "inbox" })} end={waiting ? <Count n={waiting} /> : null}>
          <Inbox className="size-4" /> Inbox
        </Row>
        <Row active={same(view, { kind: "all" })} onClick={() => onView({ kind: "all" })}>
          <Layers className="size-4" /> All talks
        </Row>
        <Row
          active={false}
          onClick={() => onCoordination?.()}
          end={
            activeCoordinationCount > 0 ? (
              <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-semibold text-primary-foreground">
                {activeCoordinationCount}
              </span>
            ) : null
          }
        >
          <Cpu className="size-4 text-muted-foreground" /> Agent Tasks
        </Row>
        <Row
          active={false}
          onClick={() => onGates?.()}
          end={
            pendingGatesCount > 0 ? (
              <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-semibold text-primary-foreground">
                {pendingGatesCount}
              </span>
            ) : null
          }
        >
          <Shield className="size-4 text-muted-foreground" /> Approval Gates
        </Row>
        <Row active={same(view, { kind: "memory" })} onClick={() => onView({ kind: "memory" })}>
          <BookOpen className="size-4" /> Team Memory
        </Row>

        <Section title="Channels" action={<IconButton label="New or join a channel" onClick={onNewChannel}><Plus className="size-3.5" /></IconButton>}>
          {(data?.channels ?? []).filter((c) => c.joined).map((c) => {
            const v: View = { kind: "channel", name: c.name };
            return (
              <Row key={c.name} active={same(view, v)} onClick={() => onView(v)} end={unread(v) ? <Count n={unread(v)} /> : null}>
                <Hash className="size-3.5" /> {c.name.slice(1)}
              </Row>
            );
          })}
        </Section>

        <Section title="People">
          {people(data).map(({ owner, online }) => {
            const v: View = { kind: "person", owner };
            return (
              <Row key={owner} active={same(view, v)} onClick={() => onView(v)} end={unread(v) ? <Count n={unread(v)} /> : null}>
                <Avatar name={owner} size={18} online={online} /> <span className="capitalize">{owner}</span>
              </Row>
            );
          })}
          {people(data).length === 0 ? <p className="px-2 text-muted-foreground">Nobody yet. Invite someone.</p> : null}
        </Section>

        <Section
          title="Agents & Workspaces"
          action={
            <div className="flex items-center gap-0.5">
              {onOpenAgents ? (
                <IconButton label="Inspect coding agents" onClick={onOpenAgents}>
                  <Bot className="size-3.5" />
                </IconButton>
              ) : null}
              <IconButton label="Add a project folder" onClick={() => void addProject()}>
                <Plus className="size-3.5" />
              </IconButton>
            </div>
          }
        >
          {awayActive ? (
            <Row
              active={false}
              onClick={() => onAway?.()}
              end={
                <span className="rounded bg-muted px-1 text-[10px] text-muted-foreground font-medium">
                  auto-reply
                </span>
              }
            >
              <Bot className="size-3.5 text-muted-foreground shrink-0" />
              <span className="truncate">@{me}/away</span>
            </Row>
          ) : null}

          {(showAllFolders ? projects : projects.slice(0, FOLDERS_SHOWN)).map((p) => {
            const v: View = p.path ? { kind: "project", path: p.path } : { kind: "agent", agent: p.agents[0].agent };
            const running = p.agents.filter((a) => a.online).map((a) => KIND[a.kind] ?? a.kind);
            return (
              <Row
                key={p.path || p.agents[0].agent}
                active={same(view, v)}
                onClick={() => onView(v)}
                end={
                  <>
                    {running.length ? <span className="max-w-20 truncate text-[10px] text-[#36634d] group-hover:hidden dark:text-[#a8cbbb]">{running.join(", ")}</span> : null}
                    {p.path ? (
                      <button
                        aria-label="Remove from the chamber"
                        title="Remove from the chamber"
                        className="hidden size-4 shrink-0 items-center justify-center rounded text-muted-foreground group-hover:inline-flex hover:text-foreground"
                        onClick={(e) => (e.stopPropagation(), void act(() => api.removeWorkspace(p.path), `${p.name} is out of the chamber.`))}
                      >
                        <X className="size-3" />
                      </button>
                    ) : null}
                  </>
                }
              >
                <Folder className={cn("size-3.5 shrink-0", running.length ? "text-online" : "text-muted-foreground/60")} />
                <span className="truncate" title={`${p.path || p.name}\n${p.agents.map((a) => `${KIND[a.kind] ?? a.kind}${a.online ? " (running)" : ""}`).join(", ")}`}>
                  {p.path ? (p.name || p.path.split(/[\\/]/).pop() || "project") : (KIND[p.agents[0]?.kind] ?? p.name ?? "agent")}
                </span>
              </Row>
            );
          })}
          {projects.length > FOLDERS_SHOWN ? (
            <button type="button" onClick={() => setShowAllFolders(!showAllFolders)} className="px-2 py-1 text-left text-[12px] text-muted-foreground hover:text-foreground">
              {showAllFolders ? "Show fewer" : `Show ${projects.length - FOLDERS_SHOWN} more`}
            </button>
          ) : null}
          {!(data?.projects ?? []).length && !awayActive ? <p className="px-2 text-muted-foreground">Add a project folder to put its coding agents in the chamber.</p> : null}
        </Section>
      </nav>

      {note ? <p className="mx-2 mb-2 cursor-pointer text-[12px] text-muted-foreground" onClick={() => setNote("")}>{note}</p> : null}

      <div className="flex flex-col gap-1 border-t px-1 pt-2">
        <div className="flex items-center gap-2">
        {naming === null ? (
          <button onClick={() => setNaming(me)} title="Your name: teammates reach you as @name. Click to change." className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-1 py-1.5 text-left hover:bg-accent">
            <Avatar name={me || "?"} size={24} online={data?.connected ?? true} />
            <span className="truncate text-sm">@{me}</span>
          </button>
        ) : (
          <form className="flex flex-1 items-center gap-1" onSubmit={(e) => (e.preventDefault(), void act(() => api.setName(naming), `You are @${naming.toLowerCase()} now.`).then(() => setNaming(null)))}>
            <input autoFocus value={naming} onChange={(e) => setNaming(e.target.value)} onKeyDown={(e) => e.key === "Escape" && setNaming(null)} className="h-7 min-w-0 flex-1 rounded-md border bg-background px-2 outline-none focus:border-ring text-xs" />
            <IconButton label="Save" onClick={() => undefined}><Check className="size-3.5" /></IconButton>
          </form>
        )}

        <button
          onClick={onAway}
          title={awayActive ? `Away responder active: ${awayStatusText}` : "Click to enable Away Mode"}
          className={cn(
            "flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] font-medium border transition-colors",
            awayActive
              ? "border-border bg-muted/40 text-foreground"
              : "border-transparent text-muted-foreground hover:bg-accent hover:text-foreground"
          )}
        >
          <span className={cn("size-2 rounded-full", awayActive ? "bg-[#8db8d5]" : "bg-[#a8cbbb]")} />
          <span>{awayActive ? "Away" : "Active"}</span>
        </button>
        </div>

        <PlanNote />
        <div className="flex items-center justify-between px-1">
        {data?.admin ? <IconButton label="Invite a teammate" onClick={onInvite}><UserPlus className="size-4" /></IconButton> : null}
        <IconButton label="Members" onClick={onMembers}><Users className="size-4" /></IconButton>
        {onSettings ? <IconButton label="Settings" onClick={onSettings}><Settings className="size-4" /></IconButton> : null}
        <IconButton label={dark ? "Light mode" : "Dark mode"} onClick={onTheme}>{dark ? <Sun className="size-4" /> : <Moon className="size-4" />}</IconButton>
        </div>
      </div>
    </aside>
  );
}

function Count({ n }: { n: number }) {
  return <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-waiting px-1 text-[10px] font-semibold text-white">{n}</span>;
}
