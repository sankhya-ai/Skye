import { Eye, EyeOff, FileDiff, FileMinus2, FilePlus2, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { getArtifactFileUrl, getArtifactText, type MissionArtifacts as Artifacts, type RepoArtifacts } from "../api";
import { Badge } from "./ui/Badge";
import { MarkdownView } from "./MarkdownView";

// Files a mission actually changed, diffed from the start/end repo manifests the engine
// records (never parsed from worker output). Image artifacts preview inline, loaded from
// the confined artifact endpoint (only recorded changed paths are served).

const PREVIEWABLE = new Set([".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp"]);
const TEXT_PREVIEWABLE = new Set([".md", ".markdown", ".txt"]);

function previewKind(path: string): "image" | "text" | null {
  const dot = path.lastIndexOf(".");
  if (dot <= 0) return null;
  const ext = path.slice(dot).toLowerCase();
  if (PREVIEWABLE.has(ext)) return "image";
  if (TEXT_PREVIEWABLE.has(ext)) return "text";
  return null;
}

function Row({ missionId, path, repo, icon }: { missionId: string; path: string; repo?: string; icon: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const kind = previewKind(path);
  const canPreview = kind !== null;

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
      setText(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      if (kind === "image") {
        if (!url) setUrl(await getArtifactFileUrl(missionId, path, repo));
      } else if (text === null) {
        setText(await getArtifactText(missionId, path, repo));
      }
      setOpen(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
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
      {error ? (
        <p className="mb-2 ml-5 text-xs text-destructive">{error}</p>
      ) : null}
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
      {open && kind === "text" && text !== null ? (
        <div className="mb-2 ml-5 max-h-96 overflow-y-auto rounded-md border border-border bg-background p-3">
          {path.toLowerCase().endsWith(".txt") ? (
            <pre className="whitespace-pre-wrap font-mono text-xs text-foreground/90">{text}</pre>
          ) : (
            <MarkdownView source={text} />
          )}
        </div>
      ) : null}
    </div>
  );
}

function Group({ missionId, label, paths, overflow, repo, icon }: {
  missionId: string;
  label: string;
  paths: string[];
  overflow?: number;
  repo?: string;
  icon: React.ReactNode;
}) {
  if (paths.length === 0 && !overflow) return null;
  return (
    <div>
      <p className="mb-1 mt-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label} ({paths.length + (overflow ?? 0)})
      </p>
      {paths.map((path) => (
        <Row key={path} missionId={missionId} path={path} repo={repo} icon={icon} />
      ))}
      {overflow ? (
        <p className="py-1 text-xs text-muted-foreground">and {overflow} more</p>
      ) : null}
    </div>
  );
}

function DiffGroups({ missionId, repo, entry }: {
  missionId: string;
  repo?: string;
  entry: RepoArtifacts;
}) {
  const added = entry.added ?? [];
  const modified = entry.modified ?? [];
  const deleted = entry.deleted ?? [];
  const empty = added.length + modified.length + deleted.length === 0
    && !entry.added_overflow && !entry.modified_overflow && !entry.deleted_overflow;
  if (empty) {
    return (
      <p className="mt-2 text-xs text-muted-foreground">
        The repo tree is byte-identical before and after this mission - no file changed.
      </p>
    );
  }
  return (
    <div>
      <Group missionId={missionId} label="Added" paths={added} overflow={entry.added_overflow} repo={repo}
             icon={<FilePlus2 className="size-3.5 shrink-0 text-success" />} />
      <Group missionId={missionId} label="Modified" paths={modified} overflow={entry.modified_overflow} repo={repo}
             icon={<FileDiff className="size-3.5 shrink-0 text-info" />} />
      <Group missionId={missionId} label="Deleted" paths={deleted} overflow={entry.deleted_overflow} repo={repo}
             icon={<FileMinus2 className="size-3.5 shrink-0 text-destructive" />} />
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
  const repos = artifacts.repos ? Object.entries(artifacts.repos) : [];
  if (repos.length > 0) {
    // Multi-repo mission: group the recorded diff per repository so the same relative
    // path in two repos is reviewed under its own repo, never merged into one list.
    return (
      <div className="rounded-md border border-border bg-card p-4">
        <h3 className="text-sm font-medium text-foreground">Changed files</h3>
        {repos.map(([name, entry]) => (
          <div key={name} data-repo={name} className="mt-3 rounded-md border border-border/60 bg-background/40 p-3">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-foreground">{name}</span>
              {entry.start_truncated || entry.end_truncated ? (
                <Badge variant="secondary">large repo - diff partial</Badge>
              ) : null}
            </div>
            {entry.complete ? (
              <DiffGroups missionId={missionId} repo={name} entry={entry} />
            ) : (
              <p className="mt-2 text-xs text-muted-foreground">
                No recorded changes yet - the final file record is written when the mission finishes.
              </p>
            )}
          </div>
        ))}
      </div>
    );
  }
  if (!artifacts.complete) {
    // The end-of-mission file manifest is only recorded when the mission completes;
    // interrupted or in-flight work may have changed files we cannot see yet.
    return (
      <div className="rounded-md border border-border bg-card p-4">
        <h3 className="text-sm font-medium text-foreground">Changed files</h3>
        <p className="mt-2 text-xs text-muted-foreground">
          No recorded changes - the final file record is written when the mission finishes.
        </p>
      </div>
    );
  }
  return (
    <div className="rounded-md border border-border bg-card p-4">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-medium text-foreground">Changed files</h3>
        {artifacts.start_truncated || artifacts.end_truncated ? (
          <Badge variant="secondary">large repo - diff partial</Badge>
        ) : null}
      </div>
      <DiffGroups missionId={missionId} entry={artifacts} />
    </div>
  );
}
