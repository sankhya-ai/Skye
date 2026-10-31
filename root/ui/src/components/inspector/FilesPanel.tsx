import { ChevronDown, ChevronRight, File, Folder, Search } from "lucide-react";
import { useMemo, useState } from "react";

import { api } from "../../lib/api.ts";

interface TreeNode {
  name: string;
  path: string;
  children: Map<string, TreeNode>;
  file: boolean;
}

function buildTree(files: string[]): TreeNode {
  const root: TreeNode = { name: "", path: "", children: new Map(), file: false };
  for (const file of files) {
    let node = root;
    const parts = file.split("/");
    parts.forEach((part, index) => {
      const path = parts.slice(0, index + 1).join("/");
      let child = node.children.get(part);
      if (!child) {
        child = { name: part, path, children: new Map(), file: index === parts.length - 1 };
        node.children.set(part, child);
      }
      node = child;
    });
  }
  return root;
}

export function FilesPanel({ workspace, files, onMention }: { workspace: string; files: string[]; onMention: (path: string) => void }) {
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [viewing, setViewing] = useState<{ path: string; content: string; binary: boolean; truncated: boolean } | null>(null);
  const tree = useMemo(() => buildTree(files), [files]);
  const matches = useMemo(() => (filter ? files.filter((f) => f.toLowerCase().includes(filter.toLowerCase())).slice(0, 200) : []), [files, filter]);

  const open = (path: string) => {
    api.file(workspace, path).then(setViewing, () => setViewing(null));
  };

  const render = (node: TreeNode, depth: number): React.ReactNode[] =>
    [...node.children.values()]
      .sort((a, b) => Number(a.file) - Number(b.file) || a.name.localeCompare(b.name))
      .flatMap((child) => {
        const isOpen = expanded.has(child.path);
        const row = (
          <button
            key={child.path}
            className={`treeRow${viewing?.path === child.path ? " active" : ""}`}
            style={{ paddingLeft: 6 + depth * 14 }}
            onClick={() => {
              if (child.file) open(child.path);
              else
                setExpanded((current) => {
                  const next = new Set(current);
                  if (next.has(child.path)) next.delete(child.path);
                  else next.add(child.path);
                  return next;
                });
            }}
          >
            {child.file ? <File size={13} className="faint" /> : isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            {!child.file ? <Folder size={13} className="faint" /> : null}
            {child.name}
          </button>
        );
        return child.file || !isOpen ? [row] : [row, ...render(child, depth + 1)];
      });

  return (
    <>
      <div className="panelTools">
        <Search size={14} className="faint" />
        <input className="input" placeholder="Filter files" value={filter} onChange={(e) => setFilter(e.target.value)} />
      </div>
      <div className="fileTree">
        {filter
          ? matches.map((path) => (
              <button key={path} className={`treeRow${viewing?.path === path ? " active" : ""}`} onClick={() => open(path)}>
                <File size={13} className="faint" /> {path}
              </button>
            ))
          : render(tree, 0)}
      </div>
      {viewing ? (
        <div className="fileView">
          <div className="panelTools">
            <span className="mono small grow" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
              {viewing.path}
            </span>
            <button className="pill small" onClick={() => onMention(viewing.path)} title="Mention this file in the composer">
              @ Mention
            </button>
          </div>
          {viewing.binary ? <p className="muted small">Binary file.</p> : <pre>{viewing.content}{viewing.truncated ? "\n… (truncated)" : ""}</pre>}
        </div>
      ) : null}
    </>
  );
}

