import { ArrowLeft, Play, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  getMission,
  redispatchStep,
  resumeMission,
  type MissionDetail,
} from "../api";
import { Button } from "./ui/Button";
import { MissionArtifactsPanel } from "./MissionArtifacts";
import { MissionRunTrace } from "./MissionRunTrace";
import { Badge } from "./ui/Badge";
import { formatTime, formatRelative } from "../lib";

const POLL_MS = 1500;

export function overallBadge(overall: string, live?: boolean, approved?: boolean) {
  if (live) return <Badge variant="info">running</Badge>;
  if (overall === "needs-owner" && approved) return <Badge variant="success">approved</Badge>;
  switch (overall) {
    case "done":
      return <Badge variant="success">done</Badge>;
    case "needs-owner":
      return <Badge className="border-amber-500/40 bg-amber-500/10 text-amber-700">needs owner</Badge>;
    case "interrupted":
      return <Badge variant="destructive">interrupted</Badge>;
    default:
      return <Badge variant="secondary">{overall}</Badge>;
  }
}

export function MissionDetailView({ id, onBack }: { id: string; onBack: () => void }) {
  const [detail, setDetail] = useState<MissionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setDetail(await getMission(id));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [id]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  const act = async (fn: () => Promise<unknown>) => {
    setActionError(null);
    setBusy(true);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (error) return <p className="p-6 text-sm text-destructive">{error}</p>;
  if (!detail) {
    return (
      <div className="animate-pulse space-y-4 p-6" aria-label="loading mission">
        <div className="h-4 w-40 rounded-sm bg-muted" />
        <div className="h-7 w-2/3 rounded-sm bg-muted" />
        <div className="h-20 rounded-md bg-muted" />
        <div className="h-20 rounded-md bg-muted" />
      </div>
    );
  }

  const live = Boolean(detail.live?.running);
  const resumable = !live && ["needs-owner", "interrupted", "failed"].includes(detail.status.status);
  // Backlog-started missions compose the goal as "Title: brief". Split that
  // composition into a kicker + heading so the title isn't repeated into one
  // long sentence. Direct goals without a title-like prefix render whole.
  const goal = detail.mission.goal;
  const splitAt = goal.indexOf(": ");
  const prefix = splitAt > 0 ? goal.slice(0, splitAt) : "";
  const hasTitle =
    splitAt > 0 && prefix.length <= 60 && prefix.split(" ").length <= 8 && !/[.!?]$/.test(prefix);
  const steps = detail.status.steps ?? [];
  const settled = steps.filter((s) => s.state === "settled" && s.status === "done").length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-4" /> Missions
        </button>
        {overallBadge(detail.status.status, live, detail.approved)}
        <span className="flex-1" />
        {resumable ? (
          <Button size="sm" loading={busy} onClick={() => act(() => resumeMission(id))}>
            <Play className="size-3" /> Resume
          </Button>
        ) : null}
        <Button variant="outline" size="sm" loading={busy} onClick={() => refresh()}>
          <RefreshCw className="size-3" /> Refresh
        </Button>
      </div>
      {/* product-first: the goal is the headline; activity reads as relative time */}
      <div>
        {hasTitle ? (
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {prefix}
          </p>
        ) : null}
        <h2 className="font-display text-2xl font-semibold tracking-tight text-foreground">
          {hasTitle ? goal.slice(splitAt + 2) : goal}
        </h2>
        <p className="mt-1 text-xs text-muted-foreground/70">
          {detail.journal.length > 0
            ? `updated ${formatRelative(detail.journal[detail.journal.length - 1].ts)}`
            : `${settled} of ${steps.length} steps settled`}
        </p>
      </div>
      {detail.live?.error ? (
        <p className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
          {detail.live.error}
        </p>
      ) : null}
      {actionError ? (
        <p className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
          {actionError}
        </p>
      ) : null}
      <MissionRunTrace
        detail={detail}
        onChanged={refresh}
        onRedispatch={(step) => act(() => redispatchStep(id, step))}
      />
      <MissionArtifactsPanel missionId={detail.mission.id} artifacts={detail.artifacts} />
      <details className="rounded-md border border-border bg-card p-4">
        <summary className="cursor-pointer text-sm font-medium text-foreground">
          Journal ({detail.journal.length} events)
        </summary>
        <div className="mt-3 max-h-72 space-y-1 overflow-auto font-mono text-xs text-muted-foreground">
          {[...detail.journal].reverse().map((entry, i) => (
            <div key={i} className="flex gap-2">
              <span className="shrink-0 text-muted-foreground/70">{formatTime(entry.ts)}</span>
              <span className="shrink-0 text-foreground/80">{entry.step}</span>
              <span>{entry.event}</span>
              {entry.status ? <span>({entry.status})</span> : null}
            </div>
          ))}
          {detail.journal.length === 0 ? <p>Nothing journaled yet.</p> : null}
        </div>
      </details>
    </div>
  );
}

