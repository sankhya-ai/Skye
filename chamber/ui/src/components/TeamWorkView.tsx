import {
  BookOpen,
  Bot,
  CheckCircle2,
  Clock,
  Cpu,
  ExternalLink,
  FolderGit2,
  GitBranch,
  Layers,
  Plus,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";
import { useEffect, useState } from "react";

import { api, type CoordinationTask, type Gate, type GateActionDetails } from "@/lib/api";
import { ago, type BoardData, harnessOf, KIND } from "@/lib/board";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface TeamWorkViewProps {
  data: BoardData | null;
  me?: string;
  onOpenTalk: (talkId: string) => void;
  onOpenGates: () => void;
  onChanged?: () => void;
  onNewTalk?: () => void;
  onAddWorkspace?: () => void;
}

function formatGateAction(action: string | GateActionDetails): string {
  if (typeof action === "string") return action || "Action Request";
  if (!action) return "Action Request";
  if (action.command) return action.command;
  if (action.policy) return action.policy;
  return JSON.stringify(action);
}

export function TeamWorkView({
  data,
  me: _me = "",
  onOpenTalk,
  onOpenGates,
  onChanged: _onChanged,
  onNewTalk,
  onAddWorkspace,
}: TeamWorkViewProps) {
  const [tasks, setTasks] = useState<CoordinationTask[]>([]);
  const [gates, setGates] = useState<Gate[]>([]);
  const [initialLoading, setInitialLoading] = useState(true);
  const [manualSyncing, setManualSyncing] = useState(false);
  // The workspaces panel: running ones first, five shown until asked for all.
  const [allWorkspaces, setAllWorkspaces] = useState(false);
  const workspaces = [...(data?.projects ?? [])].sort((x, y) => Number(y.agents.some((a) => a.online)) - Number(x.agents.some((a) => a.online)));
  const [filter, setFilter] = useState<"all" | "needs_you" | "in_progress" | "completed">("all");

  const loadData = async (isManual = false) => {
    if (isManual) setManualSyncing(true);
    try {
      const [taskRes, gateRes] = await Promise.all([
        api.coordinationTasks().catch(() => ({ tasks: [] })),
        api.gates().catch(() => ({ gates: [] })),
      ]);
      const nextTasks = taskRes.tasks || [];
      const nextGates = gateRes.gates || [];

      // Buzz pattern: Deep equality comparison prevents re-render flicker on identical polls
      setTasks((prev) => (JSON.stringify(prev) === JSON.stringify(nextTasks) ? prev : nextTasks));
      setGates((prev) => (JSON.stringify(prev) === JSON.stringify(nextGates) ? prev : nextGates));
    } catch {} finally {
      setInitialLoading(false);
      if (isManual) {
        setTimeout(() => setManualSyncing(false), 500);
      }
    }
  };

  useEffect(() => {
    void loadData();
    const interval = setInterval(() => void loadData(false), 4000);
    return () => clearInterval(interval);
  }, []);

  const pendingGates = gates.filter((g) => g.status === "pending");
  const inProgressTasks = tasks.filter((t) => t.status === "in_progress" || t.status === "claimed" || t.status === "requested");
  const completedTasks = tasks.filter((t) => t.status === "resolved");

  const totalNeedsYou = pendingGates.length;
  const totalInProgress = inProgressTasks.length;
  const totalCompleted = completedTasks.length;

  const agentCount = data?.projects?.reduce((acc, p) => acc + (p.agents?.length || 0), 0) ?? data?.workspaces?.length ?? 1;
  const projectCount = data?.projects?.length ?? 1;

  const agentLabel = (agentStr: string) => {
    const [owner, name = "agent"] = agentStr.split("/");
    const harness = harnessOf(agentStr);
    const brand = harness ? (KIND[harness] ?? harness) : name;
    return `${owner} · ${brand}`;
  };

  return (
    <div className="flex flex-col h-full overflow-y-auto bg-background/95">
      {/* Top Header */}
      <div className="border-b bg-card/40 px-6 py-3.5 backdrop-blur-md">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-2">
            <span className="flex size-7 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Layers className="size-4" />
            </span>
            <h1 className="text-base font-bold tracking-tight text-foreground">
              Team Work
            </h1>
            <span className="rounded-md border border-border/80 bg-muted/40 px-2 py-0.5 text-[11px] font-mono text-muted-foreground">
              {agentCount} {agentCount === 1 ? "Agent" : "Agents"} · {projectCount} {projectCount === 1 ? "Repository" : "Repositories"}
            </span>
          </div>

          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={onAddWorkspace}
              className="h-8 gap-1.5 text-xs font-medium"
            >
              <FolderGit2 className="size-3.5 text-muted-foreground" />
              <span>Add Workspace</span>
            </Button>

            <Button
              size="sm"
              onClick={() => onNewTalk?.()}
              className="h-8 gap-1.5 text-xs font-medium bg-primary text-primary-foreground shadow-xs hover:bg-primary/90"
            >
              <Plus className="size-3.5" />
              <span>New Task</span>
            </Button>
          </div>
        </div>

        {/* 4-Card Compact Metric Strip */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mt-3.5">
          {/* Metric 1: Tasks in Flight */}
          <div
            onClick={() => setFilter("in_progress")}
            className="cursor-pointer rounded-xl border bg-card p-3 transition-all hover:border-border/80 hover:shadow-xs group"
          >
            <div className="flex items-center justify-between text-muted-foreground mb-1">
              <span className="text-xs font-medium">Tasks</span>
              <Cpu className="size-3.5 text-[#604988] dark:text-[#c5b4e3] group-hover:scale-110 transition-transform" />
            </div>
            <div className="text-xl font-bold tracking-tight text-foreground">
              {totalInProgress}
            </div>
            <div className="text-[11px] text-muted-foreground mt-0.5">
              {totalInProgress > 0 ? (
                <span className="text-[#604988] dark:text-[#c5b4e3] font-medium">{totalInProgress} in flight</span>
              ) : (
                "None in flight"
              )}
            </div>
          </div>

          {/* Metric 2: Approval Gates */}
          <div
            onClick={onOpenGates}
            className={cn(
              "cursor-pointer rounded-xl border p-3 transition-all hover:shadow-xs group",
              totalNeedsYou > 0
                ? "border-primary/40 bg-muted/30"
                : "border-border bg-card hover:border-border/80"
            )}
          >
            <div className="flex items-center justify-between text-muted-foreground mb-1">
              <span className="text-xs font-medium">Approval Gates</span>
              <ShieldAlert
                className={cn(
                  "size-3.5 transition-transform group-hover:scale-110",
                  totalNeedsYou > 0 ? "text-foreground" : "text-muted-foreground/60"
                )}
              />
            </div>
            <div className="text-xl font-bold tracking-tight text-foreground flex items-center gap-1.5">
              <span>{totalNeedsYou}</span>
              {totalNeedsYou > 0 ? (
                <span className="text-[9px] font-bold bg-primary text-primary-foreground px-1.5 py-0.2 rounded-full">
                  ACTION
                </span>
              ) : null}
            </div>
            <div className="text-[11px] text-muted-foreground mt-0.5">
              {totalNeedsYou > 0 ? (
                <span className="text-foreground font-semibold">{totalNeedsYou} review required</span>
              ) : (
                "All clear"
              )}
            </div>
          </div>

          {/* Metric 3: Connected Agent Fleet */}
          <div
            onClick={onAddWorkspace}
            className="cursor-pointer rounded-xl border bg-card p-3 transition-all hover:border-border/80 hover:shadow-xs group"
          >
            <div className="flex items-center justify-between text-muted-foreground mb-1">
              <span className="text-xs font-medium">Agents</span>
              <Bot className="size-3.5 text-muted-foreground group-hover:scale-110 transition-transform" />
            </div>
            <div className="text-xl font-bold tracking-tight text-foreground">
              {agentCount}
            </div>
            <div className="text-[11px] text-muted-foreground font-medium mt-0.5">
              {projectCount} {projectCount === 1 ? "workspace" : "workspaces"}
            </div>
          </div>

          {/* Metric 4: Shared Memory */}
          <div className="rounded-xl border bg-card p-3 transition-all hover:border-border/80 hover:shadow-xs">
            <div className="flex items-center justify-between text-muted-foreground mb-1">
              <span className="text-xs font-medium">Channels</span>
              <BookOpen className="size-3.5 text-muted-foreground" />
            </div>
            <div className="text-xl font-bold tracking-tight text-foreground">
              {(data?.channels ?? []).length}
            </div>
            <div className="text-[11px] text-muted-foreground mt-0.5">
              Team memory
            </div>
          </div>
        </div>

        {/* Filter Navigation Tabs */}
        <div className="flex items-center justify-between mt-3.5 pt-1">
          <div className="inline-flex rounded-lg border bg-muted/30 p-0.5 text-xs font-medium">
            <button
              type="button"
              onClick={() => setFilter("all")}
              className={cn(
                "rounded-md px-2.5 py-1 transition-all",
                filter === "all" ? "bg-primary text-primary-foreground shadow-xs font-semibold" : "text-muted-foreground hover:text-foreground"
              )}
            >
              All ({totalNeedsYou + totalInProgress + totalCompleted})
            </button>
            <button
              type="button"
              onClick={() => setFilter("needs_you")}
              className={cn(
                "rounded-md px-2.5 py-1 transition-all flex items-center gap-1",
                filter === "needs_you"
                  ? "bg-primary text-primary-foreground shadow-xs font-semibold"
                  : totalNeedsYou > 0
                  ? "bg-muted text-foreground font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              <ShieldAlert className="size-3" />
              <span>Needs You ({totalNeedsYou})</span>
            </button>
            <button
              type="button"
              onClick={() => setFilter("in_progress")}
              className={cn(
                "rounded-md px-2.5 py-1 transition-all",
                filter === "in_progress" ? "bg-primary text-primary-foreground shadow-xs font-semibold" : "text-muted-foreground hover:text-foreground"
              )}
            >
              In Progress ({totalInProgress})
            </button>
            <button
              type="button"
              onClick={() => setFilter("completed")}
              className={cn(
                "rounded-md px-2.5 py-1 transition-all",
                filter === "completed" ? "bg-primary text-primary-foreground shadow-xs font-semibold" : "text-muted-foreground hover:text-foreground"
              )}
            >
              Completed ({totalCompleted})
            </button>
          </div>

          <Button
            size="sm"
            variant="ghost"
            onClick={() => void loadData(true)}
            disabled={manualSyncing}
            className="h-7 px-2 text-xs text-muted-foreground hover:text-foreground"
          >
            <RefreshCw className={cn("size-3 mr-1", manualSyncing && "animate-spin")} />
            Sync
          </Button>
        </div>
      </div>

      {/* Stream & Content Body */}
      <div className="flex-1 p-6 space-y-6 w-full">
        {/* Section: Needs You (Approval Gates) */}
        {(filter === "all" || filter === "needs_you") && pendingGates.length > 0 ? (
          <div className="space-y-2.5">
            <div className="flex items-center gap-2">
              <span className="size-2 rounded-full bg-primary" />
              <h2 className="text-xs font-bold text-foreground uppercase tracking-wider">
                Needs You · Decision Queue ({pendingGates.length})
              </h2>
            </div>

            <div className="grid gap-2.5">
              {pendingGates.map((gate) => (
                <div
                  key={gate.id}
                  className="rounded-xl border border-border/80 bg-card p-3.5 shadow-xs transition-all flex flex-col gap-2.5"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="space-y-0.5">
                      <div className="flex items-center gap-2">
                        <span className="rounded bg-muted text-foreground font-mono text-[10px] px-1.5 py-0.5 font-bold uppercase">
                          APPROVAL GATE
                        </span>
                        <h3 className="text-sm font-semibold text-foreground">
                          {formatGateAction(gate.action)}
                        </h3>
                      </div>
                      {gate.description && (
                        <p className="text-xs text-muted-foreground">
                          {gate.description}
                        </p>
                      )}
                    </div>

                    <Button
                      size="sm"
                      variant="default"
                      onClick={onOpenGates}
                      className="h-8 px-3 text-xs font-medium shrink-0"
                    >
                      Review & Authorize
                    </Button>
                  </div>

                  <div className="flex items-center justify-between pt-2 border-t border-border/40 text-[11px] text-muted-foreground">
                    <span>
                      Requested by <span className="font-medium text-foreground">{gate.requestedBy || "agent"}</span> · {ago(gate.createdAt)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {/* Section: In Progress Tasks */}
        {(filter === "all" || filter === "in_progress") && inProgressTasks.length > 0 ? (
          <div className="space-y-2.5">
            <div className="flex items-center gap-2">
              <span className="size-2 rounded-full bg-[#c5b4e3] animate-pulse" />
              <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                In Progress ({inProgressTasks.length})
              </h2>
            </div>

            <div className="grid gap-2.5">
              {inProgressTasks.map((t) => (
                <div
                  key={t.id}
                  className="rounded-xl border bg-card p-3.5 shadow-2xs hover:border-border transition-all flex flex-col gap-2.5"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="space-y-0.5">
                      <div className="flex items-center gap-2">
                        <span className="rounded bg-[#c5b4e3]/20 text-[#604988] dark:text-[#c5b4e3] font-mono text-[10px] px-1.5 py-0.5 font-bold uppercase">
                          {t.op}
                        </span>
                        <h3 className="text-sm font-semibold text-foreground">{t.title}</h3>
                      </div>
                      {t.description && (
                        <p className="text-xs text-muted-foreground line-clamp-2">
                          {t.description}
                        </p>
                      )}
                    </div>

                    <span className="inline-flex items-center rounded-md bg-[#8db8d5]/20 text-[#356f96] dark:text-[#8db8d5] px-2 py-0.5 text-xs font-medium shrink-0">
                      {t.status === "claimed" ? "Claimed" : t.status === "in_progress" ? "Working" : "Requested"}
                    </span>
                  </div>

                  <div className="flex flex-wrap items-center gap-3 pt-2 border-t text-xs text-muted-foreground">
                    <span className="flex items-center gap-1">
                      <Bot className="size-3.5 text-muted-foreground" />
                      {agentLabel(t.to)}
                    </span>

                    {t.branch ? (
                      <span className="inline-flex items-center gap-1 font-mono text-[11px] bg-muted/50 px-1.5 py-0.5 rounded">
                        <GitBranch className="size-3" />
                        {t.branch}
                      </span>
                    ) : null}

                    {t.deadlineMs ? (
                      <span className="inline-flex items-center gap-1 text-[11px]">
                        <Clock className="size-3 text-muted-foreground" />
                        {ago(t.createdAt)}
                      </span>
                    ) : null}

                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => onOpenTalk(t.id)}
                      className="ml-auto h-6 px-2 text-xs text-muted-foreground hover:text-foreground gap-1"
                    >
                      View Context <ExternalLink className="size-3" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {/* Section: Completed Tasks */}
        {(filter === "all" || filter === "completed") && completedTasks.length > 0 ? (
          <div className="space-y-2.5">
            <div className="flex items-center gap-2">
              <span className="size-2 rounded-full bg-[#a8cbbb]" />
              <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                Completed Outcomes ({completedTasks.length})
              </h2>
            </div>

            <div className="grid gap-2.5">
              {completedTasks.map((t) => (
                <div
                  key={t.id}
                  className="rounded-xl border bg-card p-3.5 shadow-2xs hover:border-[#a8cbbb]/40 transition-all flex flex-col gap-2"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <CheckCircle2 className="size-4 text-[#36634d] dark:text-[#a8cbbb] shrink-0" />
                        <h3 className="text-sm font-semibold text-foreground">{t.title}</h3>
                        <span className="text-xs text-muted-foreground">
                          · {agentLabel(t.to)}
                        </span>
                      </div>
                      {t.resolution && (
                        <p className="text-xs text-[#36634d] dark:text-[#a8cbbb] font-medium pl-6">
                          {t.resolution}
                        </p>
                      )}
                    </div>

                    <span className="inline-flex items-center rounded-md bg-[#a8cbbb]/20 px-2 py-0.5 text-xs font-medium text-[#36634d] dark:text-[#a8cbbb] shrink-0">
                      Completed
                    </span>
                  </div>

                  <div className="flex items-center justify-between pt-1.5 border-t text-[11px] text-muted-foreground pl-6">
                    <span>
                      Completed {ago(t.updatedAt || t.createdAt)}
                    </span>

                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => onOpenTalk(t.id)}
                      className="h-6 px-2 text-xs text-muted-foreground hover:text-foreground gap-1"
                    >
                      View Diff <ExternalLink className="size-3" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {/* Minimalist Empty State (When no tasks/gates are active) */}
        {!initialLoading && pendingGates.length === 0 && inProgressTasks.length === 0 && completedTasks.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border/70 bg-card/40 p-10 text-center flex flex-col items-center justify-center">
            <div className="size-9 rounded-full bg-muted flex items-center justify-center text-muted-foreground mb-2.5">
              <CheckCircle2 className="size-5 text-[#36634d] dark:text-[#a8cbbb]" />
            </div>
            <h3 className="text-sm font-semibold text-foreground">No active tasks</h3>
            <p className="text-xs text-muted-foreground mt-1 max-w-xs">
              Everything is quiet. Start a conversation or delegate a task to your local agents.
            </p>
            <Button
              size="sm"
              onClick={() => onNewTalk?.()}
              className="mt-3.5 h-8 text-xs gap-1.5 font-medium"
            >
              <Plus className="size-3.5" />
              <span>New Task</span>
            </Button>
          </div>
        ) : null}

        {(data?.projects ?? []).length === 0 ? (
          <div className="flex flex-col gap-3 rounded-xl border border-primary/30 bg-primary/5 p-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h4 className="text-sm font-semibold text-foreground">Connect your first project</h4>
              <p className="mt-0.5 text-xs text-muted-foreground">Pick a project folder and the coding agents in it (Claude Code, Codex, Antigravity) join the chamber. You can add more any time.</p>
            </div>
            <Button size="sm" onClick={onAddWorkspace} className="h-8 shrink-0 gap-1.5 text-xs font-medium">
              <FolderGit2 className="size-3.5" />
              <span>Choose a folder</span>
            </Button>
          </div>
        ) : null}

        {/* Workspaces & Agent Fleet Panel */}
        <div className="rounded-xl border bg-card p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <FolderGit2 className="size-4 text-muted-foreground" />
              <h4 className="text-xs font-semibold text-foreground">
                Workspaces ({data?.projects?.length ?? 0})
              </h4>
            </div>

            <Button
              size="sm"
              variant="outline"
              onClick={onAddWorkspace}
              className="h-7 text-xs gap-1"
            >
              <Plus className="size-3" />
              <span>Add Workspace</span>
            </Button>
          </div>

          <ul className="divide-y rounded-lg border">
            {(allWorkspaces ? workspaces : workspaces.slice(0, 5)).map((project) => {
              const running = project.agents.some((ag) => ag.online);
              return (
                <li key={project.path || project.name} className="flex items-center gap-3 px-3 py-2">
                  <span className={cn("size-1.5 shrink-0 rounded-full", running ? "bg-[#a8cbbb]" : "bg-muted-foreground/30")} />
                  <span className="truncate text-xs font-semibold text-foreground">{project.name}</span>
                  {project.path ? <span className="hidden min-w-0 truncate font-mono text-[10px] text-muted-foreground sm:inline" title={project.path}>{project.path.replace(/^\/Users\/[^/]+|^[A-Z]:\\Users\\[^\\]+/i, "~")}</span> : null}
                  <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">
                    {project.agents.map((ag, i) => (
                      <span key={ag.agent}>
                        {i ? " · " : ""}
                        <span className={cn(ag.online && "font-medium text-[#36634d] dark:text-[#a8cbbb]")}>{KIND[ag.kind] ?? ag.kind}</span>
                      </span>
                    ))}
                  </span>
                </li>
              );
            })}
          </ul>
          {workspaces.length > 5 ? (
            <button type="button" onClick={() => setAllWorkspaces(!allWorkspaces)} className="text-[11px] text-muted-foreground hover:text-foreground">
              {allWorkspaces ? "Show fewer" : `Show all ${workspaces.length}`}
            </button>
          ) : null}
          <div>
            {!(data?.projects ?? []).length ? (
              <div className="text-center py-4 text-xs text-muted-foreground">
                No repositories connected. Click "Add Workspace" to connect Claude Code, Codex, or Antigravity.
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
