import { Check, ChevronDown, Folder, FolderPlus } from "lucide-react";
import { useEffect, useRef, useState } from "react";

function basename(path: string): string {
  return path.split("/").filter(Boolean).pop() ?? path;
}

/** The composer's folder chip: the folders already used, and "Add a folder…" for the picker. */
export function WorkspaceMenu({
  workspace,
  recent,
  onChange,
  onPickFolder,
  down = false,
}: {
  workspace: string;
  recent: string[];
  onChange: (path: string) => void;
  onPickFolder: (then: (path: string) => void) => void;
  down?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const escape = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  const folders = workspace && !recent.includes(workspace) ? [workspace, ...recent] : recent;
  const add = () => {
    setOpen(false);
    onPickFolder(onChange);
  };
  return (
    <div className={`menuAnchor${down ? " down" : ""}`} ref={root}>
      <button className="chip" onClick={() => (folders.length ? setOpen((v) => !v) : add())} title={workspace || "Choose a folder"}>
        <Folder size={14} /> {workspace ? basename(workspace) : "Choose a folder"} <ChevronDown size={13} />
      </button>
      {open ? (
        <div className="popover">
          {folders.map((path) => (
            <button
              key={path}
              className={`menuOption${path === workspace ? " active" : ""}`}
              onClick={() => {
                onChange(path);
                setOpen(false);
              }}
            >
              <strong>
                <Folder size={15} />
                {basename(path)}
                {path === workspace ? <Check size={13} /> : null}
              </strong>
              <span className="desc path" title={path}>
                {path}
              </span>
            </button>
          ))}
          <div className="menuDivider" />
          <button className="menuOption" onClick={add}>
            <strong>
              <FolderPlus size={15} />
              Add a folder…
            </strong>
          </button>
        </div>
      ) : null}
    </div>
  );
}
