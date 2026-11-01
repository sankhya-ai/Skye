// Mission rows. NOTE: an earlier version of this list was adapted from berd's Automations
// UI - the one surface berd's DESIGN.md explicitly excludes as design precedent. This rewrite
// follows the canonical rules instead: quiet flat rows, state color only for state, badges
// for status (rounded-xs tags), no raw identifiers in the primary view, relative time.
import type { MissionRow } from "../api";
import { cn, formatRelative } from "../lib";
import { Badge } from "./ui/Badge";

function statusVariant(row: MissionRow): { variant: string; label: string; live: boolean } {
  if (row.live) return { variant: "info", label: "running now", live: true };
  if (row.needs_owner) return { variant: "outline", label: "waiting on you", live: false };
  if (row.approved) return { variant: "success", label: "approved - resume to continue", live: false };
  if (row.failed > 0 || row.overall === "interrupted")
    return { variant: "destructive", label: row.overall === "interrupted" ? "interrupted" : "failed", live: false };
  if (row.overall === "done") return { variant: "success", label: "done", live: false };
  if (row.overall === "running" || row.overall === "pending")
    return { variant: "info", label: row.overall, live: true };
  return { variant: "secondary", label: row.overall, live: false };
}

function MissionRowButton({ row, onOpen }: { row: MissionRow; onOpen: () => void }) {
  const status = statusVariant(row);
  return (
    <button
      type="button"
      className="group grid min-h-[72px] w-full gap-2 rounded-md bg-card px-6 py-4 text-left transition-[background-color,box-shadow] duration-150 hover:shadow-mini focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring md:grid-cols-[minmax(0,1fr)_auto_auto] md:items-center"
      onClick={onOpen}
      aria-label={row.goal || row.id}
      data-testid={`mission-row-${row.id}`}
    >
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium text-foreground">
          {row.goal || row.id}
        </span>
        <span className="mt-1 block text-xs text-muted-foreground">
          {row.done}/{row.steps} steps done
          {row.failed > 0 ? `, ${row.failed} failed` : ""}
        </span>
      </span>
      <Badge variant={status.variant as never} className="md:justify-self-end">
        {status.live ? (
          <span className="size-1.5 animate-pulse rounded-full bg-current" aria-hidden="true" />
        ) : null}
        {status.label}
      </Badge>
      <span className="text-xs text-muted-foreground md:justify-self-end">
        {formatRelative(row.last_activity)}
      </span>
    </button>
  );
}

export function MissionsOverview({
  missions,
  loading,
  unavailable,
  onOpen,
}: {
  missions: MissionRow[];
  loading?: boolean;
  unavailable?: boolean;
  onOpen: (id: string) => void;
}) {
  if (loading && missions.length === 0) {
    return (
      <div className="animate-pulse space-y-2" aria-label="loading missions">
        <div className="h-16 rounded-md bg-muted" />
        <div className="h-16 rounded-md bg-muted" />
      </div>
    );
  }
  if (missions.length === 0) {
    // Unknown is not empty: without one successful load, say so instead of
    // claiming there is no work.
    if (unavailable) {
      return (
        <div className="rounded-md border border-dashed border-border bg-card/50 p-10 text-center">
          <p className="text-sm font-medium text-foreground">Missions unavailable</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Can't load missions right now - retrying in the background.
          </p>
        </div>
      );
    }
    return (
      <div className="rounded-md border border-dashed border-border bg-card/50 p-10 text-center">
        <p className="text-sm font-medium text-foreground">No missions yet</p>
        <p className="mt-1 text-sm text-muted-foreground">Approved work runs here.</p>
      </div>
    );
  }
  return (
    <section aria-label="Missions" className="space-y-2">
      {missions.map((row) => (
        <MissionRowButton key={row.id} row={row} onOpen={() => onOpen(row.id)} />
      ))}
    </section>
  );
}
