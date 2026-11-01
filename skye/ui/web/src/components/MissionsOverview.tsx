// Adapted from block/berd src/features/automations/ui/AutomationsOverview.tsx and
// AutomationOverviewRow.tsx (Apache-2.0): card rows with a status activity dot,
// title, summary line and last-activity label, clicking through to detail.
import type { MissionRow } from "../api";
import { cn, formatTime } from "../lib";

function activityDotClass(row: MissionRow) {
  if (row.live) return "bg-info animate-pulse";
  if (row.approved) return "bg-success";
  if (row.needs_owner) return "bg-amber-400";
  if (row.failed > 0 || row.overall === "interrupted") return "bg-destructive";
  if (row.overall === "done") return "bg-success";
  if (row.overall === "running" || row.overall === "pending") return "bg-info";
  return "bg-muted-foreground";
}

function statusLabel(row: MissionRow) {
  if (row.live) return "running now";
  if (row.approved) return "approved - resume to continue";
  if (row.needs_owner) return "waiting on you";
  switch (row.overall) {
    case "done":
      return "done";
    case "interrupted":
      return "interrupted";
    case "not-started":
      return "not started";
    default:
      return row.overall;
  }
}

function MissionRowButton({ row, onOpen }: { row: MissionRow; onOpen: () => void }) {
  return (
    <button
      type="button"
      className="group grid min-h-[86px] w-full gap-3 rounded-md bg-card px-6 py-5 text-left transition-[background-color,box-shadow,border-color] duration-200 hover:shadow-md hover:ring-1 hover:ring-inset hover:ring-border focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring md:grid-cols-[minmax(0,1fr)_auto] md:items-center"
      onClick={onOpen}
      aria-label={row.id}
      data-testid={`mission-row-${row.id}`}
    >
      <span className="min-w-0">
        <span className="flex min-w-0 items-center gap-2">
          <span className="truncate text-base font-normal text-foreground">{row.id}</span>
        </span>
        {row.goal ? (
          <span className="mt-2 block truncate text-sm text-muted-foreground">{row.goal}</span>
        ) : null}
        <span className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1.5">
            <span
              className={cn("size-[7px] shrink-0 rounded-full", activityDotClass(row))}
              role="img"
              aria-label={statusLabel(row)}
            />
            {statusLabel(row)}
          </span>
          <span aria-hidden="true">·</span>
          <span>
            {row.done}/{row.steps} steps done
            {row.failed > 0 ? `, ${row.failed} failed` : ""}
          </span>
        </span>
      </span>
      <span className="hidden max-w-56 truncate text-right text-sm text-muted-foreground md:block">
        {row.last_activity ? formatTime(row.last_activity) : "never run"}
      </span>
    </button>
  );
}

export function MissionsOverview({
  missions,
  onOpen,
}: {
  missions: MissionRow[];
  onOpen: (id: string) => void;
}) {
  if (missions.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
        No missions yet. Run one with <code className="font-mono">skye mission run mission.json</code>.
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
