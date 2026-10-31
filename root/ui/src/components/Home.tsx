// Tura's home: a greeting and one composer. Sending starts a session in the
// chosen folder with that message.

import { useState } from "react";

import type { Attachment, PermissionMode } from "../lib/types.ts";
import { Composer } from "./Composer.tsx";
import { ModeMenu } from "./ModeMenu.tsx";
import { WorkspaceMenu } from "./WorkspaceMenu.tsx";

function greeting(): string {
  const hour = new Date().getHours();
  return hour < 5 ? "Working late?" : hour < 12 ? "Good morning." : hour < 18 ? "Good afternoon." : "Good evening.";
}

function basename(path: string): string {
  return path.split("/").filter(Boolean).pop() ?? path;
}

export function Home({
  recent,
  error,
  onPickFolder,
  onStart,
}: {
  recent: string[];
  error: string;
  onPickFolder: (then: (path: string) => void) => void;
  onStart: (workspace: string, text: string, mode: PermissionMode) => Promise<void>;
}) {
  const [workspace, setWorkspace] = useState(recent[0] ?? "");
  const [mode, setMode] = useState<PermissionMode>("default");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");

  const start = async (text: string, _attachments: Attachment[]) => {
    if (!workspace) {
      onPickFolder((path) => {
        setWorkspace(path);
        void go(path, text);
      });
      return;
    }
    await go(workspace, text);
  };

  const go = async (path: string, text: string) => {
    setBusy(true);
    setFailure("");
    try {
      await onStart(path, text, mode);
    } catch (e) {
      setFailure(String((e as Error).message));
      setBusy(false);
    }
  };

  return (
    <main className="card body">
      <div className="transcript scroll">
        <div className="home">
          <div className="eyebrow">{greeting()}</div>
          <h1 className="greeting">What should we get done?</h1>
          <div className="column homeComposer">
            <Composer
              running={false}
              disabled={busy}
              placeholder={workspace ? `Ask root to work in ${basename(workspace)}…` : "Ask root… (pick a folder first)"}
              slashCommands={[]}
              files={[]}
              onSend={start}
              onStop={() => undefined}
              draftKey="home"
              chips={
                <>
                  <WorkspaceMenu workspace={workspace} recent={recent} onChange={setWorkspace} onPickFolder={onPickFolder} down />
                  <ModeMenu mode={mode} onChange={setMode} down />
                </>
              }
            />
            {failure || error ? <p className="small" style={{ color: "var(--err)" }}>{failure || error}</p> : null}
          </div>
        </div>
      </div>
    </main>
  );
}
