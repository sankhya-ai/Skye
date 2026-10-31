import { X } from "lucide-react";

import type { TodoItem } from "../../lib/transcript.ts";
import type { Session } from "../../lib/types.ts";
import { TodoItems } from "../turn/Steps.tsx";
import { ChangesPanel } from "./ChangesPanel.tsx";
import { FilesPanel } from "./FilesPanel.tsx";

export type InspectorTab = "changes" | "files" | "plan";

const TABS: { id: InspectorTab; label: string }[] = [
  { id: "changes", label: "Changes" },
  { id: "files", label: "Files" },
  { id: "plan", label: "Plan" },
];

export function Inspector({
  tab,
  onTab,
  onClose,
  workspace,
  session,
  todos,
  refreshKey,
  files,
  onMention,
}: {
  tab: InspectorTab;
  onTab: (tab: InspectorTab) => void;
  onClose: () => void;
  workspace: string;
  session: Session;
  todos: TodoItem[];
  refreshKey: number;
  files: string[];
  onMention: (path: string) => void;
}) {
  return (
    <aside className="card inspector">
      <div className="inspectorHead">
        <div className="tabs" role="tablist">
          {TABS.map((t) => (
            <button key={t.id} role="tab" aria-selected={tab === t.id} className={`tab${tab === t.id ? " active" : ""}`} onClick={() => onTab(t.id)}>
              {t.label}
            </button>
          ))}
        </div>
        <button className="iconButton" onClick={onClose} title="Close panel">
          <X size={15} />
        </button>
      </div>
      <div className="tabBody scroll">
        {tab === "changes" ? <ChangesPanel workspace={workspace} refreshKey={refreshKey} /> : null}
        {tab === "files" ? <FilesPanel workspace={workspace} files={files} onMention={onMention} /> : null}
        {tab === "plan" ? (
          todos.length ? (
            <TodoItems items={todos} live={session.running} />
          ) : (
            <p className="help">The agent writes its plan here with SetTodoList when a task has several steps.</p>
          )
        ) : null}
      </div>
    </aside>
  );
}
