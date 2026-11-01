import { ArrowLeft, Play, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  getMission,
  redispatchStep,
  ackRecovery,
  resumeMission,
  type MissionDetail,
  getProjects,
  type Project,
} from "../api";
import { Button } from "./ui/Button";
import { MissionArtifactsPanel } from "./MissionArtifacts";
import { MissionFlow } from "./MissionFlow";
import { DiscussionPanel } from "./DiscussionPanel";
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
      return <Badge variant="warning">needs you</Badge>;
    case "interrupted":
      return <Badge variant="destructive">interrupted</Badge>;
    case "failed":
    case "gate-failed":
      return <Badge variant="destructive">failed</Badge>;
    case "not-started":
      return <Badge variant="secondary">not started</Badge>;
    default:
      // An unrecognized status is unknown, not a raw enum.
      return <Badge variant="outline">unknown</Badge>;
  }
}

// Journal event names are operator record, not product copy - map the known ones.
const EVENT_LABEL: Record<string, string> = {
  dispatched: "started",
  settled: "finished",
  "needs-owner": "waiting on owner",
  redispatch: "re-run by owner",
  reattached: "reattached after restart",
  skipped: "skipped",
  "step-accepted": "step accepted",
  "proposal-rejected": "proposal rejected",
  "feature-proposed": "backlog proposal",
  "review-round": "review round",
  "claim-held": "waiting - another step holds these files",
  "blocked-claims": "blocked on claimed files",
  "agent-accepted": "agent proposal accepted",
  "agent-rejected": "agent proposal rejected",
  "discussion-folded": "discussion folded into brief",
  "worker-job": "worker job recorded",
  "features-rejected": "proposals rejected",
  "gate-failed": "gate failed",
  "request-changes": "changes requested",
};

export function MissionDetailView({ id, onBack }: { id: string; onBack: () => void }) {
  const [detail, setDetail] = useState<MissionDetail | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  useEffect(() => { getProjects().then((d) => setProjects(d.projects)).catch(() => {}); }, []);
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

  // A failed refresh must not wipe a mission we already rendered: keep the last known
  // state (the app banner reports the outage) and show the error only when nothing loaded.
  if (error && !detail) return <p className="p-6 text-sm text-destructive">{error}</p>;
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
  // Lost-tree recovery, from the AUTHORITATIVE workspace record in the payload (not journal
  // scans or client-local state): pending exactly while a lost-tree rebuild waits on the
  // operator's explicit ack - a reload after acking shows resolved, never a stale pending.
  const workspace = detail.workspace;
  const recoveryPending = Boolean(workspace?.recovery_pending);
  const recoveryAcked = Boolean(workspace?.recovery_acknowledged);
  // An acknowledged recovery offers Resume only while the mission is still waiting to run;
  // once it has settled, a Resume button and "resume when ready" copy would be stale.
  const missionDone = detail.status.status === "done";
  const resumable = !live && (["needs-owner", "interrupted", "failed"].includes(detail.status.status)
                              || (recoveryAcked && !missionDone));
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
          <Button size="sm" loading={busy} disabled={recoveryPending}
                  onClick={() => act(() => resumeMission(id))}>
            <Play className="size-3" /> Resume
          </Button>
        ) : null}
        <Button variant="outline" size="sm" loading={busy} onClick={() => refresh()}>
          <RefreshCw className="size-3" /> Refresh
        </Button>
      </div>
      {recoveryPending || recoveryAcked ? (
        <div className="rounded-md border border-warning/40 bg-warning/5 p-4 text-xs">
          <p className="font-medium text-foreground">
            {recoveryPending
              ? "Workspace recovery needs your review"
              : missionDone
                ? "Workspace was recovered during this mission"
                : "Workspace recovery acknowledged"}
          </p>
          {workspace?.repos ? (
            <>
              <p className="mt-1 text-muted-foreground">
                A worktree was lost and rebuilt from its branch. Committed work survived;
                uncommitted worker changes are unrecoverable.
                {missionDone ? " The mission has already finished." : " Review the rebuilt trees before continuing."}
              </p>
              <ul className="mt-1 space-y-0.5 font-mono text-[11px] text-muted-foreground/70">
                {Object.entries(workspace.repos).map(([name, r]) => (
                  <li key={name}>
                    {name}: {r.branch || "?"} (base {String(r.base_rev || "?").slice(0, 12)})
                    {r.uncommitted_lost ? " - uncommitted changes lost" : ""}
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <>
              <p className="mt-1 text-muted-foreground">
                The mission's workspace was rebuilt from branch{" "}
                <span className="font-mono">{workspace?.branch || "?"}</span> after its
                worktree was lost. Committed work survived; uncommitted worker changes are
                unrecoverable.
                {missionDone ? " The mission has already finished." : " Review the rebuilt tree before continuing."}
              </p>
              <p className="mt-1 font-mono text-[11px] text-muted-foreground/70">
                base {String(workspace?.base_rev || "?").slice(0, 12)}
              </p>
            </>
          )}
          {recoveryAcked ? (
            <p className="mt-2 text-muted-foreground">
              Recovery acknowledged{workspace?.recovery_acknowledged?.by
                ? ` by ${workspace.recovery_acknowledged.by}` : ""}
              {missionDone ? "." : " - resume when ready."}
            </p>
          ) : (
            <Button
              size="sm"
              className="mt-2"
              loading={busy}
              onClick={() => act(() => ackRecovery(id))}
            >
              I reviewed the rebuilt workspace - acknowledge
            </Button>
          )}
        </div>
      ) : null}
      {detail.mission.repos_source ? (
        // Where the repo map came from - provenance the owner should never have to guess.
        // The stored string names the project by id; the surface names it by its name.
        <p className="text-xs text-muted-foreground/80">
          {(() => {
            const source = detail.mission.repos_source as string;
            const pid = detail.mission.project;
            const name = pid ? projects.find((p) => p.id === pid)?.name : undefined;
            if (source.startsWith("discovered in project") && name) {
              return <>Repositories discovered automatically in project {name}&apos;s folder.</>;
            }
            return <>Repositories {source}.</>;
          })()}
        </p>
      ) : null}
      {workspace?.mode === "multi-worktree" && workspace.repos ? (
        <div className="text-xs text-muted-foreground">
          <p>
            Runs in separate Git worktrees across{" "}
            {Object.keys(workspace.repos).length} repositories:
          </p>
          <ul className="mt-0.5 space-y-0.5">
            {Object.entries(workspace.repos).map(([name, r]) => (
              <li key={name}>
                <span className="font-medium text-foreground">{name}</span>
                {detail.mission.repos?.[name] ? (
                  <span className="font-mono"> {detail.mission.repos[name]}</span>
                ) : null}
                {" - "}
                {r.mode === "worktree" ? (
                  <>branch <span className="font-mono">{r.branch || "?"}</span></>
                ) : (
                  <>shared tree{r.reason ? ` (${r.reason})` : ""}</>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : workspace ? (
        <p className="text-xs text-muted-foreground">
          {workspace.mode === "worktree" ? (
            <>
              Runs in a separate Git worktree on branch{" "}
              <span className="font-mono">{workspace.branch || "?"}</span>.
            </>
          ) : (
            <>
              Runs on the shared tree - no separate worktree
              {workspace.reason ? ` (${workspace.reason})` : ""}.
            </>
          )}
        </p>
      ) : null}
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
            : `${settled} of ${steps.length} steps done`}
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
      <MissionFlow missionId={detail.mission.id} />
      <MissionRunTrace
        detail={detail}
        onChanged={refresh}
        onRedispatch={(step) => act(() => redispatchStep(id, step))}
      />
      <DiscussionPanel missionId={detail.mission.id} />
      <MissionArtifactsPanel missionId={detail.mission.id} artifacts={detail.artifacts} />
      <details className="rounded-md border border-border bg-card p-4">
        <summary className="cursor-pointer text-sm font-medium text-foreground">
          Activity ({detail.journal.length} events)
        </summary>
        <div className="mt-3 max-h-72 space-y-1 overflow-auto font-mono text-xs text-muted-foreground">
          {[...detail.journal].reverse().map((entry, i) => (
            <div key={i} className="flex gap-2">
              <span className="shrink-0 text-muted-foreground/70">{formatTime(entry.ts)}</span>
              <span className="shrink-0 text-foreground/80">{entry.step}</span>
              <span>{EVENT_LABEL[entry.event] ?? entry.event}</span>
              {entry.status ? <span>({entry.status})</span> : null}
            </div>
          ))}
          {detail.journal.length === 0 ? <p>No recorded activity yet.</p> : null}
        </div>
      </details>
    </div>
  );
}

