import { ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../../lib/api.ts";
import { parseUnifiedDiff } from "../../lib/diff.ts";
import { DiffRows } from "../DiffView.tsx";

const STATUS_LABEL: Record<string, string> = { M: "modified", A: "added", D: "deleted", R: "renamed", "??": "new", AM: "added", MM: "modified" };

export function ChangesPanel({ workspace, refreshKey }: { workspace: string; refreshKey: number }) {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.changes>> | null>(null);
  const [error, setError] = useState("");
  const [openFiles, setOpenFiles] = useState<Set<string>>(new Set());
  const load = useCallback(() => {
    api.changes(workspace).then(setData, (e) => setError(String(e.message)));
  }, [workspace]);
  useEffect(load, [load, refreshKey]);
  const diffs = useMemo(() => new Map(parseUnifiedDiff(data?.diff ?? "").map((d) => [d.path, d])), [data]);
  if (error) return <p className="small" style={{ color: "var(--err)" }}>{error}</p>;
  if (!data) return <p className="faint small">Loading…</p>;
  if (!data.git) return <p className="muted small">This workspace is not a git repository, so there is no diff to show. The transcript shows every edit the agent made.</p>;
  const toggle = (path: string) =>
    setOpenFiles((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  return (
    <>
      <div className="panelTools">
        <span className="sectionLabel grow">
          {data.branch} · {data.files.length} changed
        </span>
        <button className="iconButton" onClick={load} title="Refresh">
          <RefreshCw size={14} />
        </button>
      </div>
      {data.files.length === 0 ? <p className="muted small">Working tree is clean.</p> : null}
      {data.files.map((file) => {
        const diff = diffs.get(file.path);
        const open = openFiles.has(file.path);
        return (
          <div key={file.path} className="diff" style={{ marginBottom: 8 }}>
            <button className="changeRow" onClick={() => diff && toggle(file.path)} disabled={!diff}>
              {diff ? open ? <ChevronDown size={13} /> : <ChevronRight size={13} /> : <span style={{ width: 13 }} />}
              <span className="path">{file.path}</span>
              <span className="faint small">{STATUS_LABEL[file.status] ?? file.status}</span>
              {diff ? (
                <>
                  <span className="plus">+{diff.added}</span>
                  <span className="minus">−{diff.removed}</span>
                </>
              ) : (
                <span className="faint small">binary or empty</span>
              )}
            </button>
            {open && diff ? <DiffRows rows={diff.rows} /> : null}
          </div>
        );
      })}
    </>
  );
}

