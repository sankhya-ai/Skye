// The team backlog: features and fixes the teams propose, the owner decides. "Start work"
// is the owner's explicit go on an APPROVED item, creating a planner mission under its
// project. Provenance is shown in product terms (which project, from work or from an error
// signal) - raw ids stay in the API, not on the surface.
import { useCallback, useEffect, useState } from "react";
import {
  decideFeature,
  verifyFeature,
  getBacklog,
  getIssues,
  getProjects,
  startFeature,
  type FeatureRow,
  type IssueRow,
  type Project,
} from "../api";

const BADGE: Record<string, string> = {
  pending: "bg-warning/25 text-foreground",
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

export function BacklogView() {
  const [features, setFeatures] = useState<FeatureRow[]>([]);
  const [issues, setIssues] = useState<IssueRow[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [backlogData, issueData, projectData] = await Promise.all([
        getBacklog(),
        getIssues(),
        getProjects(),
      ]);
      setFeatures(backlogData.features);
      setIssues(issueData.issues);
      setProjects(projectData.projects);
      setError(null);
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
      setError(err instanceof Error ? err.message : String(err));
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

  const order = (f: FeatureRow) =>
    ({ pending: 0, approved: 1, "awaiting-verification": 2, delivered: 3, rejected: 4 }[f.status] ?? 5);
  const sorted = [...features].sort((a, b) => order(a) - order(b) || b.proposed_at - a.proposed_at);
  const activeIssues = issues.filter((i) => {
    const feature = features.find((f) => f.id === i.feature_id);
    return !feature || feature.status === "pending";
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-2xl font-semibold tracking-tight text-foreground">
          Backlog
        </h1>
        <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
          What the teams want to build or fix next. Approving lets the team plan and start;
          each execution step still follows your approval policy.
        </p>
      </div>
      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      {sorted.length === 0 ? (
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
                      className={`rounded-xs px-1.5 py-0.5 text-xs ${BADGE[f.status] ?? "bg-muted text-muted-foreground"}`}
                    >
                      {f.status}
                    </span>
                  </div>
                  <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{f.brief}</p>
                  <p className="mt-1.5 text-xs text-muted-foreground/70">
                    {projectName(f.project)} · {provenance(f)}
                    {f.mission_id && f.status !== "delivered" ? " · work started" : ""}
                    {f.status === "delivered" && f.verification?.journey_passed
                      ? " · user journey replayed clean"
                      : ""}
                    {f.decision_note ? ` · “${f.decision_note}”` : ""}
                  </p>
                </div>
                <div className="flex shrink-0 gap-2">
                  {f.status === "pending" ? (
                    <>
                      <button
                        type="button"
                        disabled={busy === f.id}
                        onClick={() => void act(f.id, () => decideFeature(f.id, "approved"))}
                        className="h-8 rounded-full bg-primary px-3 text-xs text-primary-foreground transition disabled:opacity-30"
                      >
                        Approve
                      </button>
                      <button
                        type="button"
                        disabled={busy === f.id}
                        onClick={() => void act(f.id, () => decideFeature(f.id, "rejected"))}
                        className="h-8 rounded-full border border-border bg-card px-3 text-xs text-foreground transition hover:bg-muted disabled:opacity-30"
                      >
                        Reject
                      </button>
                    </>
                  ) : null}
                  {f.status === "awaiting-verification" ? (
                    <button
                      type="button"
                      disabled={busy === f.id}
                      onClick={() => void act(f.id, () => verifyFeature(f.id))}
                      className="h-8 rounded-full bg-primary px-3 text-xs text-primary-foreground transition disabled:opacity-30"
                    >
                      Replay journey
                    </button>
                  ) : null}
                  {f.status === "approved" && !f.mission_id ? (
                    <button
                      type="button"
                      disabled={busy === f.id}
                      onClick={() =>
                        void act(f.id, async () => {
                          const res = await startFeature(f.id, true);
                          window.location.hash = `/mission/${encodeURIComponent(res.id)}`;
                        })
                      }
                      className="h-8 rounded-full bg-primary px-3 text-xs text-primary-foreground transition disabled:opacity-30"
                    >
                      Start work
                    </button>
                  ) : null}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
      {activeIssues.length > 0 ? (
        <section>
          <h2 className="text-sm font-medium text-foreground">Signals</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Errors the team has seen. Each became a proposal above - approving it starts the
            repair.
          </p>
          <ul className="mt-2 space-y-1.5">
            {activeIssues.map((i) => (
              <li
                key={i.id}
                className="rounded-sm border border-border bg-card px-3 py-2 text-xs"
              >
                <span className="font-medium text-foreground">{i.summary}</span>
                <span className="ml-2 text-muted-foreground">
                  {KIND_LABEL[i.kind] ?? i.kind}
                  {i.occurrences > 1 ? ` · ${i.occurrences}x` : ""} · {projectName(i.project)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
