// The team backlog: features the teams propose, the owner decides. Nothing here runs by
// itself - "Start work" is the owner's explicit go on an APPROVED feature, creating a planner
// mission under the feature's project. Pending rows carry the proposing mission as provenance.
import { useCallback, useEffect, useState } from "react";
import { decideFeature, getBacklog, startFeature, type FeatureRow } from "../api";

const BADGE: Record<string, string> = {
  pending: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  approved: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  rejected: "bg-muted text-muted-foreground",
  delivered: "bg-sky-500/15 text-sky-600 dark:text-sky-400",
};

export function BacklogView() {
  const [features, setFeatures] = useState<FeatureRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await getBacklog();
      setFeatures(data.features);
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

  const order = (f: FeatureRow) =>
    ({ pending: 0, approved: 1, delivered: 2, rejected: 3 }[f.status] ?? 4);
  const sorted = [...features].sort((a, b) => order(a) - order(b) || b.proposed_at - a.proposed_at);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-medium text-foreground">Backlog</h1>
        <p className="text-sm text-muted-foreground">
          Features the teams propose while working. You approve or reject each one; only an
          approved feature can start work.
        </p>
      </div>
      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      {sorted.length === 0 ? (
        <p className="rounded-md border border-border bg-card/80 p-6 text-sm text-muted-foreground">
          No proposals yet. When a mission settles, the team can propose follow-up features and
          they land here for your decision.
        </p>
      ) : (
        <ul className="space-y-2">
          {sorted.map((f) => (
            <li
              key={f.id}
              className="rounded-md border border-border bg-card/80 p-4 backdrop-blur"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-foreground">{f.title}</span>
                    <span
                      className={`rounded-sm px-1.5 py-0.5 text-xs ${BADGE[f.status] ?? "bg-muted text-muted-foreground"}`}
                    >
                      {f.status}
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-muted-foreground">{f.brief}</p>
                  <p className="mt-1 text-xs text-muted-foreground/70">
                    {f.project ? `project ${f.project}` : "no project"}
                    {f.proposed_by ? ` · proposed by ${f.proposed_by}` : ""}
                    {f.mission_id ? ` · mission ${f.mission_id}` : ""}
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
                        className="rounded-sm bg-foreground/[0.06] px-2 py-1 text-xs hover:bg-foreground/[0.1]"
                      >
                        Approve
                      </button>
                      <button
                        type="button"
                        disabled={busy === f.id}
                        onClick={() => void act(f.id, () => decideFeature(f.id, "rejected"))}
                        className="rounded-sm bg-foreground/[0.03] px-2 py-1 text-xs hover:bg-foreground/[0.06]"
                      >
                        Reject
                      </button>
                    </>
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
                      className="rounded-sm bg-foreground/[0.06] px-2 py-1 text-xs hover:bg-foreground/[0.1]"
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
    </div>
  );
}
