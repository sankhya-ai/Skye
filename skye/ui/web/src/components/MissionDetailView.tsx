import { ArrowLeft, Play, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  getMission,
  redispatchStep,
  resumeMission,
  type MissionDetail,
} from "../api";
import { MissionRunTrace } from "./MissionRunTrace";
import { Badge } from "./ui/Badge";
import { formatTime } from "../lib";

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
    try {
      await fn();
      await refresh();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
  };

  if (error) return <p className="p-6 text-sm text-destructive">{error}</p>;
  if (!detail) return <p className="p-6 text-sm text-muted-foreground">Loading...</p>;

  const live = Boolean(detail.live?.running);
  const resumable = !live && ["needs-owner", "interrupted", "failed"].includes(detail.status.status);

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
        <h2 className="font-mono text-lg">{detail.mission.id}</h2>
        {overallBadge(detail.status.status, live, detail.approved)}
        <span className="flex-1" />
        {resumable ? (
          <button
            type="button"
            className="inline-flex items-center gap-1 rounded-md border border-border bg-card px-3 py-1.5 text-xs font-medium transition hover:bg-muted"
            onClick={() => act(() => resumeMission(id))}
          >
            <Play className="size-3" /> Resume mission
          </button>
        ) : null}
        <button
          type="button"
          className="inline-flex items-center gap-1 rounded-md border border-border bg-card px-3 py-1.5 text-xs font-medium transition hover:bg-muted"
          onClick={() => refresh()}
        >
          <RefreshCw className="size-3" /> Refresh
        </button>
      </div>
      <p className="text-sm text-muted-foreground">{detail.mission.goal}</p>
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
      <section className="rounded-xl border border-border bg-card p-4">
        <h3 className="mb-2 text-sm font-medium">Journal</h3>
        <div className="max-h-72 space-y-1 overflow-auto font-mono text-xs text-muted-foreground">
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
      </section>
    </div>
  );
}

