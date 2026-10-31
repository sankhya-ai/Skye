import { ArrowUp, Folder, FolderGit2, FolderPlus, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api } from "../lib/api.ts";
import { useEscape } from "../lib/useEscape.ts";

export function NewSessionDialog({
  recent,
  hosted,
  title = "New session",
  action = "Start session here",
  onClose,
  onCreate,
}: {
  recent: string[];
  hosted: boolean;
  title?: string;
  action?: string;
  onClose: () => void;
  onCreate: (workspace: string) => Promise<void>;
}) {
  useEscape(onClose);
  const [newName, setNewName] = useState("");
  const [gitUrl, setGitUrl] = useState("");
  const [path, setPath] = useState(recent[0] ?? "");
  const [listing, setListing] = useState<Awaited<ReturnType<typeof api.dirs>> | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // Once the owner types a path, a listing that arrives later must not replace it.
  const typed = useRef(false);

  const browse = (target?: string, initial = false) => {
    api.dirs(target).then(
      (d) => {
        setListing(d);
        if (!(initial && typed.current)) setPath(d.path);
        setError("");
      },
      (e) => setError(String(e.message)),
    );
  };

  useEffect(() => browse(recent[0] || undefined, true), []); // eslint-disable-line react-hooks/exhaustive-deps

  const create = async (target: string) => {
    if (!target.trim()) return;
    setBusy(true);
    setError("");
    try {
      await onCreate(target.trim());
    } catch (e) {
      setError(String((e as Error).message));
      setBusy(false);
    }
  };

  return (
    <div className="backdrop" onMouseDown={onClose}>
      <div className="dialog" onMouseDown={(e) => e.stopPropagation()} role="dialog" aria-label={title}>
        <div className="dialogHead">
          <h2>{title}</h2>
          <button className="iconButton" onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </div>
        <div className="dialogBody scroll">
          <div className="field">
            <label htmlFor="ws">Workspace folder</label>
            <form
              className="panelTools"
              style={{ marginBottom: 0 }}
              onSubmit={(e) => {
                e.preventDefault();
                browse(path);
              }}
            >
              <input
                id="ws"
                className="input mono"
                value={path}
                onChange={(e) => {
                  typed.current = true;
                  setPath(e.target.value);
                }}
                placeholder="/path/to/project"
              />
              <button type="submit" className="pill small">
                Go
              </button>
            </form>
            <span className="help">The agent works inside this folder. Its AGENTS.md or CLAUDE.md is loaded as project instructions.</span>
          </div>
          {hosted ? (
            <form
              className="field"
              onSubmit={(e) => {
                e.preventDefault();
                setBusy(true);
                api.createWorkspace(newName.trim(), gitUrl.trim()).then(
                  (created) => create(created.path),
                  (err) => {
                    setError(String(err.message));
                    setBusy(false);
                  },
                );
              }}
            >
              <label htmlFor="newws">New workspace</label>
              <div className="panelTools" style={{ marginBottom: 0 }}>
                <input id="newws" className="input" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="name" />
                <input className="input mono" value={gitUrl} onChange={(e) => setGitUrl(e.target.value)} placeholder="https://github.com/… (optional)" />
                <button type="submit" className="pill small" disabled={busy || (!newName.trim() && !gitUrl.trim())}>
                  <FolderPlus size={13} /> {gitUrl.trim() ? "Clone" : "Create"}
                </button>
              </div>
            </form>
          ) : null}
          {recent.length ? (
            <>
              <div className="sectionLabel" style={{ marginBottom: 6 }}>
                Recent
              </div>
              <div className="dirList" style={{ marginBottom: 14 }}>
                {recent.map((r) => (
                  <button key={r} className="dirRow" onClick={() => create(r)} disabled={busy}>
                    <FolderGit2 size={14} className="faint" /> <span className="mono small">{r}</span>
                  </button>
                ))}
              </div>
            </>
          ) : null}
          {listing ? (
            <>
              <div className="sectionLabel" style={{ marginBottom: 6 }}>
                Browse
              </div>
              <div className="dirList scroll">
                {listing.parent ? (
                  <button className="dirRow" onClick={() => browse(listing.parent!)}>
                    <ArrowUp size={14} className="faint" /> ..
                  </button>
                ) : null}
                {listing.entries.map((entry) => (
                  <button key={entry.path} className="dirRow" onClick={() => browse(entry.path)} onDoubleClick={() => create(entry.path)}>
                    {entry.git ? <FolderGit2 size={14} /> : <Folder size={14} className="faint" />} {entry.name}
                  </button>
                ))}
                {listing.entries.length === 0 ? <div className="dirRow faint">No subfolders</div> : null}
              </div>
            </>
          ) : null}
          {error ? <p className="small" style={{ color: "var(--err)" }}>{error}</p> : null}
        </div>
        <div className="dialogFoot">
          <button className="pill" onClick={onClose}>
            Cancel
          </button>
          <button className="pill ink" onClick={() => create(path)} disabled={busy || !path.trim()}>
            {busy ? "Starting…" : action}
          </button>
        </div>
      </div>
    </div>
  );
}
