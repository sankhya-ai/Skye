import { FilePlus2, FileDiff, FileMinus2 } from "lucide-react";
import type { MissionArtifacts as Artifacts } from "../api";
import { Badge } from "./ui/Badge";

// Files a mission actually changed, diffed from the start/end repo manifests the engine
// records (never parsed from worker output). Shown product-first: Added/Modified/Deleted.
function Row({ icon, path }: { icon: React.ReactNode; path: string }) {
  return (
    <div className="flex items-center gap-2 py-1">
      {icon}
      <span className="truncate font-mono text-xs text-foreground/90">{path}</span>
    </div>
  );
}

function Group({ label, paths, overflow, icon }: {
  label: string;
  paths: string[];
  overflow?: number;
  icon: React.ReactNode;
}) {
  if (paths.length === 0 && !overflow) return null;
  return (
    <div>
      <p className="mb-1 mt-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label} ({paths.length + (overflow ?? 0)})
      </p>
      {paths.map((path) => (
        <Row key={path} path={path} icon={icon} />
      ))}
      {overflow ? (
        <p className="py-1 text-xs text-muted-foreground">and {overflow} more</p>
      ) : null}
    </div>
  );
}

export function MissionArtifactsPanel({ artifacts }: { artifacts?: Artifacts }) {
  if (!artifacts?.recorded) {
    return (
      <div className="rounded-md border border-border bg-card p-4">
        <h3 className="text-sm font-medium text-foreground">Changed files</h3>
        <p className="mt-2 text-xs text-muted-foreground">
          No file-change record for this mission. Missions record the repo file manifest at
          start and completion; older missions predate that record.
        </p>
      </div>
    );
  }
  if (!artifacts.complete) {
    return (
      <div className="rounded-md border border-border bg-card p-4">
        <h3 className="text-sm font-medium text-foreground">Changed files</h3>
        <p className="mt-2 text-xs text-muted-foreground">
          The baseline manifest is recorded; the final diff lands when the mission settles done.
        </p>
      </div>
    );
  }
  const added = artifacts.added ?? [];
  const modified = artifacts.modified ?? [];
  const deleted = artifacts.deleted ?? [];
  const empty = added.length + modified.length + deleted.length === 0
    && !artifacts.added_overflow && !artifacts.modified_overflow && !artifacts.deleted_overflow;
  return (
    <div className="rounded-md border border-border bg-card p-4">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-medium text-foreground">Changed files</h3>
        {artifacts.start_truncated || artifacts.end_truncated ? (
          <Badge variant="secondary">large repo - diff partial</Badge>
        ) : null}
      </div>
      {empty ? (
        <p className="mt-2 text-xs text-muted-foreground">
          The repo tree is byte-identical before and after this mission - no file changed.
        </p>
      ) : (
        <div>
          <Group label="Added" paths={added} overflow={artifacts.added_overflow}
                 icon={<FilePlus2 className="size-3.5 shrink-0 text-success" />} />
          <Group label="Modified" paths={modified} overflow={artifacts.modified_overflow}
                 icon={<FileDiff className="size-3.5 shrink-0 text-info" />} />
          <Group label="Deleted" paths={deleted} overflow={artifacts.deleted_overflow}
                 icon={<FileMinus2 className="size-3.5 shrink-0 text-destructive" />} />
        </div>
      )}
    </div>
  );
}
