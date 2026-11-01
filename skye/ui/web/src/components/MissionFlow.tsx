import { GitBranch, Loader2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { getMissionGraph, type GraphNode, type MissionGraph } from "../api";
import { cn, formatRelative } from "../lib";
import { Badge } from "./ui/Badge";

// The mission's workflow drawn from the real graph route: nodes are actual authored +
// planner-proposed steps, edges are their declared `needs` dependencies, the log is the
// server's redacted timeline projection. Nothing is synthesized for display - a step with
// no journal record is "pending", and free-text worker output never renders here.

const NODE_W = 176;
const NODE_H = 64;
const COL_GAP = 56;
const ROW_GAP = 14;
const PAD = 12;

function stateBadge(state: string) {
  switch (state) {
    case "done":
      return <Badge variant="success">done</Badge>;
    case "failed":
    case "gate-failed":
      return <Badge variant="destructive">failed</Badge>;
    case "running":
      return <Badge variant="info">running</Badge>;
    case "needs-owner":
      return <Badge variant="warning">needs you</Badge>;
    case "interrupted":
      return <Badge variant="destructive">interrupted</Badge>;
    case "skipped":
      return <Badge variant="secondary">skipped</Badge>;
    case "review":
      return <Badge variant="info">in review</Badge>;
    default:
      return <Badge variant="secondary">pending</Badge>;
  }
}

function stateRing(state: string): string {
  switch (state) {
    case "done":
      return "border-success/50";
    case "failed":
    case "gate-failed":
    case "interrupted":
      return "border-destructive/50";
    case "running":
      return "border-info/60";
    case "needs-owner":
      return "border-warning/60";
    default:
      return "border-border";
  }
}

// Dependency layers: a node's column is one past the deepest step it needs. Roots (no
// needs) sit in column 0. The mission schema already rejects needs pointing at later or
// unknown steps, so layering here cannot cycle.
function layers(nodes: GraphNode[], edges: { from: string; to: string }[]): Map<string, number> {
  const depth = new Map<string, number>();
  const ids = new Set(nodes.map((n) => n.id));
  const deps = new Map<string, string[]>();
  for (const e of edges) {
    if (ids.has(e.from) && ids.has(e.to)) {
      deps.set(e.to, [...(deps.get(e.to) ?? []), e.from]);
    }
  }
  const visit = (id: string, seen: Set<string>): number => {
    if (depth.has(id)) return depth.get(id)!;
    if (seen.has(id)) return 0; // unreachable given schema validation; stay total
    seen.add(id);
    const d = (deps.get(id) ?? []).reduce((acc, dep) => Math.max(acc, visit(dep, seen) + 1), 0);
    depth.set(id, d);
    return d;
  };
  for (const n of nodes) visit(n.id, new Set());
  return depth;
}

export function MissionFlow({ missionId }: { missionId: string }) {
  const [graph, setGraph] = useState<MissionGraph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [logOpen, setLogOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      setGraph(await getMissionGraph(missionId));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [missionId]);

  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [load]);

  if (error && !graph) {
    return (
      <div className="rounded-md border border-border bg-card p-4">
        <h3 className="text-sm font-medium text-foreground">Workflow</h3>
        <p className="mt-2 text-xs text-muted-foreground">Workflow unavailable - {error}</p>
      </div>
    );
  }
  if (!graph) {
    return (
      <div className="rounded-md border border-border bg-card p-4">
        <h3 className="text-sm font-medium text-foreground">Workflow</h3>
        <p className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="size-3 animate-spin" /> Loading the execution graph...
        </p>
      </div>
    );
  }

  const depth = layers(graph.nodes, graph.edges);
  const pos = new Map<string, { x: number; y: number }>();
  const perCol = new Map<number, number>();
  for (const n of graph.nodes) {
    const col = depth.get(n.id) ?? 0;
    const row = perCol.get(col) ?? 0;
    perCol.set(col, row + 1);
    pos.set(n.id, { x: PAD + col * (NODE_W + COL_GAP), y: PAD + row * (NODE_H + ROW_GAP) });
  }
  const cols = Math.max(1, ...[...depth.values()].map((d) => d + 1));
  const rows = Math.max(1, ...perCol.values());
  const width = PAD * 2 + cols * NODE_W + (cols - 1) * COL_GAP;
  const height = PAD * 2 + rows * NODE_H + (rows - 1) * ROW_GAP;
  const selectedNode = graph.nodes.find((n) => n.id === selected) ?? null;

  return (
    <div data-flow-card className="rounded-md border border-border bg-card p-4">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-medium text-foreground">Workflow</h3>
        <span className="text-xs text-muted-foreground">
          {graph.nodes.length} step{graph.nodes.length === 1 ? "" : "s"}
          {graph.edges.length > 0 ? `, ${graph.edges.length} dependenc${graph.edges.length === 1 ? "y" : "ies"}` : ""}
        </span>
      </div>
      <div className="mt-3 overflow-x-auto">
        <div className="relative" style={{ width, height, minWidth: "100%" }}>
          <svg className="absolute inset-0" width={width} height={height} aria-hidden="true">
            {graph.edges.map((e) => {
              const a = pos.get(e.from);
              const b = pos.get(e.to);
              if (!a || !b) return null;
              const x1 = a.x + NODE_W;
              const y1 = a.y + NODE_H / 2;
              const x2 = b.x;
              const y2 = b.y + NODE_H / 2;
              const mid = (x1 + x2) / 2;
              return (
                <path
                  key={`${e.from}->${e.to}`}
                  data-flow-edge={`${e.from}->${e.to}`}
                  d={`M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`}
                  fill="none"
                  className="stroke-muted-foreground/40"
                  strokeWidth={1.5}
                />
              );
            })}
          </svg>
          {graph.nodes.map((n) => {
            const p = pos.get(n.id)!;
            return (
              <button
                key={n.id}
                type="button"
                data-flow-node={n.id}
                onClick={() => setSelected(selected === n.id ? null : n.id)}
                className={cn(
                  "absolute rounded-md border bg-background p-2 text-left transition hover:bg-muted/60",
                  stateRing(n.state),
                  selected === n.id && "ring-1 ring-foreground/40",
                )}
                style={{ left: p.x, top: p.y, width: NODE_W, height: NODE_H }}
              >
                <span className="flex items-center justify-between gap-1">
                  <span className="truncate font-mono text-[11px] text-foreground">{n.id}</span>
                  {stateBadge(n.state)}
                </span>
                <span className="mt-1 flex items-center gap-1 text-[10px] text-muted-foreground">
                  <span className="truncate">
                    {n.role} · {n.adapter}
                  </span>
                  {n.repo ? (
                    <span className="ml-auto inline-flex shrink-0 items-center gap-0.5 rounded-full border border-border px-1.5">
                      <GitBranch className="size-2.5" />
                      {n.repo}
                    </span>
                  ) : null}
                </span>
              </button>
            );
          })}
        </div>
      </div>
      {selectedNode ? (
        <div className="mt-3 rounded-md border border-border/60 bg-background/50 p-3 text-xs">
          <p className="font-medium text-foreground">{selectedNode.goal || selectedNode.id}</p>
          <p className="mt-1 text-muted-foreground">
            {selectedNode.origin === "proposed"
              ? `Proposed by ${selectedNode.proposed_by || "the planner"}`
              : "Part of the original mission"}
            {selectedNode.repo ? ` · runs in ${selectedNode.repo}` : ""}
          </p>
          {selectedNode.repo_reason ? (
            <p className="mt-1 text-muted-foreground">Why this repo: {selectedNode.repo_reason}</p>
          ) : null}
          {selectedNode.reason ? (
            <p className="mt-1 text-muted-foreground">{selectedNode.reason}</p>
          ) : null}
        </div>
      ) : null}
      <button
        type="button"
        onClick={() => setLogOpen(!logOpen)}
        className="mt-3 text-xs text-muted-foreground underline-offset-2 hover:underline"
      >
        {logOpen ? "Hide" : "Show"} execution log ({graph.timeline.length} events)
      </button>
      {logOpen ? (
        <ol className="mt-2 max-h-72 space-y-1 overflow-y-auto rounded-md border border-border/60 bg-background/50 p-2">
          {graph.timeline.map((e, i) => (
            <li key={i} className="flex items-baseline gap-2 text-[11px]">
              <span className="shrink-0 text-muted-foreground/70">
                {typeof e.ts === "number" ? formatRelative(e.ts) : ""}
              </span>
              {e.step ? <span className="shrink-0 font-mono text-foreground/80">{e.step}</span> : null}
              <span className="text-muted-foreground">
                {e.event}
                {e.status ? ` · ${e.status}` : ""}
                {e.reason ? ` - ${e.reason}` : ""}
              </span>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}
