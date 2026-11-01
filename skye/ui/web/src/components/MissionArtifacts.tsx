import { Eye, EyeOff, FileDiff, FileMinus2, FilePlus2, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { getArtifactFileUrl, type MissionArtifacts as Artifacts } from "../api";
import { Badge } from "./ui/Badge";

// Files a mission actually changed, diffed from the start/end repo manifests the engine
// records (never parsed from worker output). Image artifacts preview inline, loaded from
// the confined artifact endpoint (only recorded changed paths are served).

const PREVIEWABLE = new Set([".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp"]);

function previewable(path: string): boolean {
  const dot = path.lastIndexOf(".");
  return dot > 0 && PREVIEWABLE.has(path.slice(dot).toLowerCase());
}

function Row({ missionId, path, icon }: { missionId: string; path: string; icon: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const canPreview = previewable(path);

  useEffect(() => {
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [url]);

  const toggle = async () => {
    if (!canPreview) return;
    if (open) {
      // Closing revokes the blob URL: the object never outlives its preview, and the UI
      // exposes no open-in-tab or download path, so script-capable types (SVG) can never
      // be navigated to directly (the case scripts/svg_security_probe.py proved unsafe).
      setOpen(false);
      if (url) {
        URL.revokeObjectURL(url);
        setUrl(null);
      }
      return;
    }
    setLoading(true);
    try {
      if (!url) setUrl(await getArtifactFileUrl(missionId, path));
      setOpen(true);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <div className="flex items-center gap-2 py-1">
        {icon}
        <span className="truncate font-mono text-xs text-foreground/90">{path}</span>
        {canPreview ? (
          <button
            type="button"
            onClick={toggle}
            className="ml-auto inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground transition hover:bg-muted hover:text-foreground"
          >
            {loading ? (
              <Loader2 className="size-3 animate-spin" />
            ) : open ? (
              <EyeOff className="size-3" />
            ) : (
              <Eye className="size-3" />
            )}
            {open ? "Hide" : "Preview"}
          </button>
        ) : null}
      </div>
      {open && url ? (
        <div className="mb-2 ml-5 rounded-md border border-border bg-background p-2">
          {// <img> rendering never executes SVG scripts (verified distinction in
          // scripts/svg_security_probe.py: scripts only run on direct document navigation,
          // which this UI never offers). <img> also scales the artwork to fit, where a
          // fixed-height iframe cropped anything wider than the panel.
          }
          <img src={url} alt={path} className="h-auto max-h-96 w-full rounded-sm object-contain object-left" />
        </div>
      ) : null}
    </div>
  );
}

function Group({ missionId, label, paths, overflow, icon }: {
  missionId: string;
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
        <Row key={path} missionId={missionId} path={path} icon={icon} />
      ))}
      {overflow ? (
        <p className="py-1 text-xs text-muted-foreground">and {overflow} more</p>
      ) : null}
    </div>
  );
}

export function MissionArtifactsPanel({ missionId, artifacts }: {
  missionId: string;
  artifacts?: Artifacts;
}) {
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
          No changes yet.
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
          <Group missionId={missionId} label="Added" paths={added} overflow={artifacts.added_overflow}
                 icon={<FilePlus2 className="size-3.5 shrink-0 text-success" />} />
          <Group missionId={missionId} label="Modified" paths={modified} overflow={artifacts.modified_overflow}
                 icon={<FileDiff className="size-3.5 shrink-0 text-info" />} />
          <Group missionId={missionId} label="Deleted" paths={deleted} overflow={artifacts.deleted_overflow}
                 icon={<FileMinus2 className="size-3.5 shrink-0 text-destructive" />} />
        </div>
      )}
    </div>
  );
}
