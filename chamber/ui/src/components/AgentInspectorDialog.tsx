import { Bot, Plus, Search, ShieldCheck, Trash2, X } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { KIND, type BoardData, type Project } from "@/lib/board";
import { cn } from "@/lib/utils";
import { AgentTools } from "@/components/AgentTools";
import { useWake, WakeChoice, WakeLog, WakePeople } from "@/components/WakeRules";

/** A folder's path, short: ~ for your home folder. */
const shortPath = (path: string) => path.replace(/^\/Users\/[^/]+|^\/home\/[^/]+|^[A-Z]:\\Users\\[^\\]+/i, "~");

/** Past this many folders the list gets a filter. */
const FILTER_FROM = 6;

/**
 * Your coding agents, one compact row per project folder: which agents it has (the running one
 * lit), who may wake them, remove. Ten folders are ten rows, and one control sets waking for all.
 */
export function AgentInspectorDialog({
  open,
  onOpenChange,
  data,
  onChanged,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  data: BoardData | null;
  onChanged: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [note, setNote] = useState("");
  const [filter, setFilter] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);
  const [perFolder, setPerFolder] = useState(false);
  const wake = useWake(data);

  const addProject = async () => {
    setAdding(true);
    setNote("");
    try {
      const picked = await api.pickFolder();
      if (!picked.path) return;
      await api.addWorkspace(picked.path);
      setNote(`Added ${picked.path.split(/[\\/]/).pop()}. Restart your coding agents in that folder to connect.`);
      onChanged();
    } catch (e: any) {
      setNote(e?.message || "Failed to add folder");
    } finally {
      setAdding(false);
    }
  };

  const removeProject = async (p: Project) => {
    setConfirming(null);
    try {
      await api.removeWorkspace(p.path);
      setNote(`Removed ${p.name} from the chamber.`);
      onChanged();
    } catch (e: any) {
      setNote(e?.message || "Failed to remove folder");
    }
  };

  const projects = data?.projects || [];
  const q = filter.trim().toLowerCase();
  const shown = q ? projects.filter((p) => `${p.name} ${p.path}`.toLowerCase().includes(q)) : projects;
  const allMine = wake.mine(projects.flatMap((p) => p.agents));
  // One rule for every folder (the usual case): its people show once, at the top, not on every row.
  const sameEverywhere = wake.shared(allMine).choice !== "mixed";
  // Each folder's own wake choice shows only when asked for, or when folders already differ.
  const showPerFolder = perFolder || !sameEverywhere;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[85vh] flex flex-col p-0 overflow-hidden">
        <DialogHeader className="px-6 py-4 border-b flex-row items-center justify-between gap-4 pr-12">
          <div className="min-w-0">
            <DialogTitle className="flex items-center gap-2 text-base font-semibold">
              <Bot className="size-4 text-primary" />
              Your coding agents
            </DialogTitle>
            <p className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground">
              <ShieldCheck className="size-3.5 shrink-0 text-[#36634d] dark:text-[#a8cbbb]" />
              They run on this computer, in their own folders. Teammates can wake one only if you allow it.
            </p>
          </div>
          <Button size="sm" onClick={addProject} disabled={adding} className="h-8 shrink-0 gap-1.5 text-xs">
            <Plus className="size-3.5" /> Add folder
          </Button>
        </DialogHeader>

        {note ? (
          <div className="mx-6 mt-3 flex items-center justify-between rounded border border-primary/30 bg-primary/10 p-2.5 text-xs text-primary">
            <span>{note}</span>
            <button type="button" onClick={() => setNote("")} className="hover:opacity-80" aria-label="Dismiss">
              <X className="size-3" />
            </button>
          </div>
        ) : null}

        <div className="flex-1 space-y-5 overflow-y-auto px-6 py-4">
          <AgentTools />

          <section className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Folders ({projects.length})</h3>
              {allMine.length ? (
                <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  Who can wake them
                  <WakeChoice wake={wake} agents={allMine} label="Who can wake agents in every folder" mixed="Differs by folder" />
                </label>
              ) : null}
            </div>
            {allMine.length ? <WakePeople wake={wake} agents={allMine} /> : null}
            {allMine.length && projects.length > 1 && sameEverywhere ? (
              <button type="button" onClick={() => setPerFolder(!perFolder)} className="text-[11px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline">
                {perFolder ? "Same for every folder" : "Set per folder"}
              </button>
            ) : null}
            {wake.error ? <p className="text-xs text-destructive">{wake.error}</p> : null}

            {projects.length > FILTER_FROM ? (
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={`Filter ${projects.length} folders`} className="h-8 w-full rounded-md border bg-background pl-8 pr-2 text-xs outline-none focus:border-ring" />
              </div>
            ) : null}

            {!projects.length ? (
              <div className="rounded-lg border border-dashed p-6 text-center text-xs text-muted-foreground">
                No folders yet. Add a project folder to put its coding agents in the chamber.
              </div>
            ) : (
              <ul className="divide-y rounded-xl border">
                {shown.map((p) => {
                  const online = p.agents.some((a) => a.online);
                  const mine = wake.mine(p.agents);
                  return (
                    <li key={p.path || p.name} className="space-y-2 px-3 py-2.5">
                      <div className="flex items-center gap-3">
                        <span className={cn("size-2 shrink-0 rounded-full", online ? "bg-[#a8cbbb]" : "bg-muted-foreground/30")} title={online ? "An agent is running" : "No agent running"} />
                        <div className="min-w-0 flex-1">
                          <div className="flex min-w-0 items-baseline gap-2">
                            <span className="truncate text-sm font-medium">{p.name}</span>
                            {p.path ? <span className="truncate font-mono text-[11px] text-muted-foreground" title={p.path}>{shortPath(p.path)}</span> : null}
                          </div>
                          <p className="truncate text-[11px] text-muted-foreground">
                            {[...p.agents].sort((a, b) => (KIND[a.kind] ?? a.kind).localeCompare(KIND[b.kind] ?? b.kind)).map((a, i) => (
                              <span key={a.agent} title={a.agent}>
                                {i ? " · " : ""}
                                <span className={cn(a.online && "font-medium text-[#36634d] dark:text-[#a8cbbb]")}>{KIND[a.kind] ?? a.kind}</span>
                              </span>
                            ))}
                          </p>
                        </div>
                        {mine.length && showPerFolder ? <WakeChoice wake={wake} agents={mine} label={`Who can wake the agents in ${p.name}`} /> : null}
                        {p.path ? (
                          confirming === p.path ? (
                            <span className="flex shrink-0 items-center gap-1">
                              <Button size="sm" variant="ghost" onClick={() => removeProject(p)} className="h-7 px-2 text-xs text-destructive hover:bg-destructive/10">Remove</Button>
                              <Button size="sm" variant="ghost" onClick={() => setConfirming(null)} className="h-7 px-2 text-xs">Keep</Button>
                            </span>
                          ) : (
                            <button type="button" onClick={() => setConfirming(p.path)} aria-label={`Remove ${p.name} from the chamber`} title="Remove from the chamber" className="shrink-0 rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive">
                              <Trash2 className="size-3.5" />
                            </button>
                          )
                        ) : null}
                      </div>
                      {mine.length && !sameEverywhere ? <div className="pl-5"><WakePeople wake={wake} agents={mine} /></div> : null}
                    </li>
                  );
                })}
                {!shown.length ? <li className="px-3 py-4 text-center text-xs text-muted-foreground">No folder matches “{filter}”.</li> : null}
              </ul>
            )}
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              When a teammate's agent messages one of yours that isn't running, Chamber can start it to answer: only for the people you allow, at most 3 times an hour each.
            </p>
          </section>

          <WakeLog wake={wake} />
        </div>
      </DialogContent>
    </Dialog>
  );
}
