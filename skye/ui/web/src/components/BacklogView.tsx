// The team backlog: features and fixes the teams propose, the owner decides. "Start work"
// is the owner's explicit go on an APPROVED item, creating a planner mission under its
// project. Provenance is shown in product terms (which project, from work or from an error
// signal) - raw ids stay in the API, not on the surface.
import { useCallback, useEffect, useState } from "react";
import {
  decideFeature,
  reworkFeature,
  verifyFeature,
  getBacklog,
  getTickets,
  getIssues,
  getMissions,
  getProjects,
  startFeature,
  type FeatureRow,
  type ThreadEntry,
  type IssueRow,
  type MissionRow,
  type Project,
} from "../api";
import { ScrollArea } from "./ScrollArea";
import { humanError } from "../lib";
import { Button } from "./ui/Button";
import { Dialog } from "./ui/Dialog";

// User-facing status; the technical enum stays in the API.
const statusLabel = (f: FeatureRow): string => {
  switch (f.status) {
    case "pending":
      return "Needs decision";
    case "reopened":
      return "Reopened - needs your decision";
    case "approved":
      return "Approved";
    case "awaiting-verification":
      // A real failed replay writes journey_passed false explicitly; null means the
      // work finished and no replay has run yet.
      return f.verification?.journey_passed === false
        ? "Verification failed"
        : "Needs review";
    case "delivered":
      return "Delivered";
    case "rejected":
      return "Rejected";
    default:
      return f.status;
  }
};

const badgeClass = (f: FeatureRow): string => {
  if (f.status === "awaiting-verification" && f.verification?.journey_passed === false) {
    return "bg-warning/25 text-foreground";
  }
  return BADGE[f.status] ?? "bg-muted text-muted-foreground";
};

const BADGE: Record<string, string> = {
  pending: "bg-warning/25 text-foreground",
  reopened: "bg-warning/25 text-foreground",
  approved: "bg-success/20 text-foreground",
  "awaiting-verification": "bg-info/15 text-foreground",
  rejected: "bg-muted text-muted-foreground",
  delivered: "bg-info/20 text-foreground",
};

const KIND_LABEL: Record<string, string> = {
  "production-log": "production logs",
  "frontend-error": "a frontend error",
  "user-report": "a user report",
  "test-failure": "a failing check",
};

// Thread kinds read as product words, never raw record tags. Folded team-line entries
// carry their discussion kind as a "[kind] " body prefix (the recorded data) - it is
// parsed here for display so the surface shows a readable label instead of the tag.
const THREAD_KIND_LABEL: Record<string, string> = {
  evidence: "evidence",
  verdict: "verdict",
  status: "status",
  discussion: "note",
};
const FOLDED_KIND_LABEL: Record<string, string> = {
  note: "note",
  "contract-change": "plan change",
  ack: "ack",
};

function threadLabel(entry: ThreadEntry): { chip: string | null; body: string } {
  let body = entry.body;
  let chip = THREAD_KIND_LABEL[entry.kind] ?? null;
  const folded = entry.kind === "discussion" ? body.match(/^\[([a-z-]+)\]\s*/) : null;
  if (folded) {
    chip = FOLDED_KIND_LABEL[folded[1]] ?? folded[1];
    body = body.slice(folded[0].length);
  }
  return { chip, body };
}

function threadTime(ts: number): string {
  const d = new Date(ts * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

// Chronological history, oldest first; the ScrollArea says while unread history exists.
function ThreadList({ thread }: { thread: ThreadEntry[] }) {
  return (
    <ScrollArea
      className="mt-1 max-h-44 space-y-2 overflow-y-auto pr-1 text-xs"
      hint="more history below - scroll"
    >
      {thread.map((entry, i) => {
        const { chip, body } = threadLabel(entry);
        return (
          <div key={i} className="border-l-2 border-border/70 pl-2">
            <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground/70">
              <span className="font-medium text-foreground/80">{entry.actor}</span>
              {chip ? (
                <span className="rounded-xs border border-border px-1 py-px text-[10px] leading-tight text-muted-foreground">
                  {chip}
                </span>
              ) : null}
              <span className="ml-auto tabular-nums">{threadTime(entry.ts)}</span>
            </p>
            <p className="mt-0.5 leading-relaxed text-foreground/80">{body}</p>
          </div>
        );
      })}
    </ScrollArea>
  );
}

export function BacklogView() {
  const [features, setFeatures] = useState<FeatureRow[]>([]);
  const [issues, setIssues] = useState<IssueRow[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [missions, setMissions] = useState<MissionRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [pickupDue, setPickupDue] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [rejectError, setRejectError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [ticketData, issueData, projectData, missionData] = await Promise.all([
        getTickets(),
        getIssues(),
        getProjects(),
        getMissions(),
      ]);
      setFeatures(ticketData.tickets);
      setPickupDue(ticketData.pickup_due);
      setIssues(issueData.issues);
      setProjects(projectData.projects);
      setMissions(missionData.missions);
      setError(null);
      setLoaded(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => clearInterval(timer);
  }, [refresh]);

  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    try {
      await fn();
      await refresh();
    } catch (err) {
      setError(humanError(err, "action"));
    } finally {
      setBusy(null);
    }
  };

  const projectName = (id: string) =>
    projects.find((p) => p.id === id)?.name ?? (id || "no project");
  const issueFor = (f: FeatureRow) => issues.find((i) => i.feature_id === f.id);
  const provenance = (f: FeatureRow): string => {
    const issue = issueFor(f);
    if (issue) {
      return `from ${KIND_LABEL[issue.kind] ?? "a signal"}${issue.occurrences > 1 ? ` · seen ${issue.occurrences}x` : ""}`;
    }
    return f.proposed_by ? "proposed while working" : "added directly";
  };

  // The linked mission's actual state, not a bare "work started": a paused mission
  // waiting on the owner reads differently from one mid-run.
  const missionState = (missionId: string): string => {
    const m = missions.find((row) => row.id === missionId);
    if (!m) return "work started";
    if (m.overall === "done") return ""; // the status badge carries the review state
    if (m.needs_owner) return "paused - one step needs your OK";
    if (m.live) return "running";
    if (m.overall === "interrupted") return "interrupted";
    if (m.overall === "failed") return "failed";
    return "work started";
  };

  const order = (f: FeatureRow) =>
    ({ pending: 0, reopened: 0, approved: 1, "awaiting-verification": 2, delivered: 3, rejected: 4 }[f.status] ?? 5);
  const sorted = [...features].sort((a, b) => order(a) - order(b) || b.proposed_at - a.proposed_at);
  // A signal stays OPEN until its linked repair is verified delivered - work started or
  // even finished is not a fix until the journey replay passes.
  const issueState = (i: IssueRow): { label: string; resolved: boolean } => {
    const feature = features.find((f) => f.id === i.feature_id);
    if (!feature || feature.status === "pending")
      return { label: "awaiting decision", resolved: false };
    if (feature.status === "delivered")
      return { label: "resolved - journey replay passed", resolved: true };
    if (feature.status === "rejected")
      return { label: "repair proposal rejected - still open", resolved: false };
    return { label: "repair in progress", resolved: false };
  };
  const sortedIssues = [...issues].sort(
    (a, b2) => Number(issueState(a).resolved) - Number(issueState(b2).resolved),
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-2xl font-semibold tracking-tight text-foreground">
          Backlog
        </h1>
      </div>
      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      {pickupDue.length > 0 ? (
        <p className="glass-panel rounded-md p-3 text-xs text-muted-foreground">
          {pickupDue.length} reopened {pickupDue.length === 1 ? "ticket is" : "tickets are"}{" "}
          approved and waiting to start.
        </p>
      ) : null}
      {!loaded && !error ? (
        <p className="glass-panel rounded-md p-6 text-sm text-muted-foreground">
          Loading tickets...
        </p>
      ) : sorted.length === 0 && error && !loaded ? (
        <p className="glass-panel rounded-md p-6 text-sm text-muted-foreground">
          Tickets unavailable - retrying in the background.
        </p>
      ) : sorted.length === 0 ? (
        <p className="glass-panel rounded-md p-6 text-sm text-muted-foreground">
          Nothing waiting. When a team finishes work or an error comes in, proposals land
          here for your decision.
        </p>
      ) : (
        <ul className="space-y-2">
          {sorted.map((f) => (
            <li key={f.id} className="glass-panel rounded-md p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-foreground">{f.title}</span>
                    <span
                      className={`rounded-xs px-1.5 py-0.5 text-xs ${badgeClass(f)}`}
                    >
                      {statusLabel(f)}
                    </span>
                  </div>
                  <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
                    {f.brief}
                  </p>
                  {f.thread && f.thread.length > 0 ? (
                    <details className="mt-1.5">
                      <summary className="cursor-pointer text-xs text-muted-foreground/70 hover:text-foreground">
                        Thread ({f.thread.length})
                      </summary>
                      <ThreadList thread={f.thread} />
                    </details>
                  ) : null}
                  {f.verification?.journey_changed ? (
                    <p className="mt-1 text-xs text-warning">
                      The journey check changed between replays - review before trusting this
                      result. Keep the check outside the team's repo for hard integrity.
                    </p>
                  ) : null}
                  {f.verification?.evidence || f.verification?.repo_revision ? (
                    <details className="mt-1.5">
                      <summary className="cursor-pointer text-xs text-muted-foreground/70 hover:text-foreground">
                        Replay evidence
                      </summary>
                      {f.verification?.evidence ? (
                        <pre className="mt-1 max-h-24 overflow-auto rounded-sm bg-muted/60 px-2 py-1 font-mono text-[10px] leading-relaxed text-muted-foreground">
                          {f.verification.evidence}
                        </pre>
                      ) : null}
                      {f.verification?.repo_revision ? (
                        <p className="mt-1 font-mono text-[11px] text-muted-foreground">
                          replayed against {f.verification.repo_revision}
                        </p>
                      ) : null}
                    </details>
                  ) : null}
                  {f.labels && f.labels.length > 0 ? (
                    <p className="mt-1.5 flex flex-wrap gap-1">
                      {f.labels.map((label) => (
                        <span
                          key={label}
                          className="rounded-xs border border-border px-1.5 py-0.5 text-xs text-muted-foreground"
                        >
                          {label}
                        </span>
                      ))}
                    </p>
                  ) : null}
                  <p className="mt-1.5 text-xs text-muted-foreground/70">
                    {projectName(f.project)} · {provenance(f)}
                    {f.reopen_count > 0
                      ? ` · reopened ${f.reopen_count}x`
                      : ""}
                    {f.missions && f.missions.length > 1
                      ? ` · ${f.missions.length} runs`
                      : ""}
                    {f.mission_id && f.status !== "delivered" && f.status !== "awaiting-verification"
                      ? ` · ${missionState(f.mission_id)}`
                      : ""}
                    {f.status === "awaiting-verification"
                      ? f.verification?.journey_passed === false
                        ? " · replay failed - send back to start the repair"
                        : " · work finished - replay the journey to check it"
                      : ""}
                    {f.status === "delivered" && f.verification?.journey_passed
                      ? " · journey replay passed"
                      : ""}
                    {f.decision_note ? ` · “${f.decision_note}”` : ""}
                  </p>
                </div>
                <div className="flex shrink-0 gap-2">
                  {f.status === "pending" || f.status === "reopened" ? (
                    <>
                      <Button
                        size="sm"
                        loading={busy === f.id}
                        onClick={() => void act(f.id, () => decideFeature(f.id, "approved"))}
                      >
                        Approve
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          setRejectError(null);
                          setRejecting(f.id);
                        }}
                      >
                        Reject
                      </Button>
                    </>
                  ) : null}
                  {f.status === "awaiting-verification" ? (
                    f.verification?.journey_passed === false ? (
                      <>
                        <Button
                          size="sm"
                          loading={busy === f.id}
                          onClick={() =>
                            void act(f.id, () =>
                              reworkFeature(f.id, "journey replay failed - sent back for repair"),
                            )
                          }
                        >
                          Send back for repair
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          loading={busy === f.id}
                          onClick={() => void act(f.id, () => verifyFeature(f.id))}
                        >
                          Replay again
                        </Button>
                      </>
                    ) : (
                      <Button
                        size="sm"
                        loading={busy === f.id}
                        onClick={() => void act(f.id, () => verifyFeature(f.id))}
                      >
                        Replay journey
                      </Button>
                    )
                  ) : null}
                  {f.status === "approved" && !f.mission_id ? (
                    <Button
                      size="sm"
                      loading={busy === f.id}
                      onClick={() =>
                        void act(f.id, async () => {
                          const res = await startFeature(f.id, true);
                          window.location.hash = `/mission/${encodeURIComponent(res.id)}`;
                        })
                      }
                    >
                      Start work
                    </Button>
                  ) : null}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
      {sortedIssues.length > 0 ? (
        <section>
          <h2 className="text-sm font-medium text-foreground">Signals</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Resolved only after verified delivery.
          </p>
          <ul className="mt-2 space-y-1.5">
            {sortedIssues.map((i) => {
              const state = issueState(i);
              return (
                <li
                  key={i.id}
                  className={`rounded-sm border border-border bg-card px-3 py-2 text-xs ${
                    state.resolved ? "opacity-70" : ""
                  }`}
                >
                  <span className="font-medium text-foreground">{i.summary}</span>
                  <span className="ml-2 text-muted-foreground">
                    {KIND_LABEL[i.kind] ?? i.kind}
                    {i.occurrences > 1 ? ` · ${i.occurrences}x` : ""} · {projectName(i.project)}
                  </span>
                  <span
                    className={`ml-2 rounded-full px-2 py-0.5 ${
                      state.resolved
                        ? "bg-success/20 text-foreground"
                        : "bg-warning/20 text-foreground"
                    }`}
                  >
                    {state.label}
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}
      <Dialog
        open={rejecting !== null}
        onClose={() => {
          if (busy) return; // never dismiss mid-action
          setRejecting(null);
        }}
        title="Reject this proposal?"
        footer={
          <>
            <Button
              variant="ghost"
              size="sm"
              disabled={busy !== null}
              onClick={() => setRejecting(null)}
            >
              Keep it
            </Button>
            <Button
              variant="destructive"
              size="sm"
              loading={busy === rejecting && rejecting !== null}
              onClick={() => {
                const id = rejecting;
                if (!id || busy) return; // double-submit guard
                void (async () => {
                  setBusy(id);
                  try {
                    await decideFeature(id, "rejected");
                    await refresh();
                    setRejecting(null);
                    setRejectError(null);
                  } catch (err) {
                    // Reconcile before showing anything: the request may have landed.
                    let landed = false;
                    try {
                      const d = await getBacklog();
                      landed = d.features.some((x) => x.id === id && x.status === "rejected");
                    } catch {
                      // still unreachable - the error copy below already says to check
                    }
                    if (landed) {
                      setRejecting(null);
                      setRejectError(null);
                    } else {
                      setRejectError(humanError(err, "rejection"));
                    }
                    await refresh();
                  } finally {
                    setBusy(null);
                  }
                })();
              }}
            >
              Reject
            </Button>
          </>
        }
      >
        The team drops it. You can ask again any time.
        {rejectError ? (
          <p className="mt-2 text-sm text-destructive">{rejectError}</p>
        ) : null}
      </Dialog>
    </div>
  );
}
