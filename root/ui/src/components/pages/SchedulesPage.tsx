// Schedules: prompts root runs by itself, on time. Built from the same rows
// and groups as Settings so the app reads as one quiet surface. Each run is an
// ordinary session; nobody is there to approve, so what would ask is skipped.

import { Folder, Plus, Play, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { api } from "../../lib/api.ts";
import type { Schedule, ScheduleMode } from "../../lib/types.ts";
import { Group, Row, Switch } from "../settings/controls.tsx";

const MODES: { value: ScheduleMode; label: string }[] = [
  { value: "plan", label: "Read only" },
  { value: "default", label: "Read and run safe commands" },
  { value: "accept_edits", label: "Can edit files" },
];
const EXAMPLES = ["weekdays 09:00", "every 2h", "daily 02:00"];

function when(ts: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
}

function basename(path: string): string {
  return path.split("/").filter(Boolean).pop() ?? path;
}

export function SchedulesPage({
  workspaces,
  onOpenSession,
  onPickFolder,
}: {
  workspaces: string[];
  onOpenSession: (id: string) => void;
  /** The app's folder picker: the Mac's Open panel in the app, root's folder browser in a browser. */
  onPickFolder: (then: (path: string) => void) => void;
}) {
  const [items, setItems] = useState<Schedule[] | null>(null);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState({ name: "", workspace: workspaces[0] ?? "", prompt: "", when: "weekdays 09:00", mode: "plan" as ScheduleMode });

  const refresh = useCallback(() => api.schedules().then((d) => setItems(d.schedules), (e: Error) => setError(e.message)), []);
  useEffect(() => {
    void refresh();
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  }, [refresh]);

  const act = (work: Promise<unknown>) => {
    setError("");
    work.then(refresh, (e: Error) => setError(e.message));
  };

  const create = () =>
    act(
      api.createSchedule(draft).then(() => {
        setCreating(false);
        setDraft({ ...draft, name: "", prompt: "" });
      }),
    );

  return (
    <div className="pageShell scroll">
      <div className="settingsColumn">
        <div className="simplePageHead">
          <h1>Schedules</h1>
          {!creating ? (
            <button className="pill ink" onClick={() => setCreating(true)}>
              <Plus size={13} /> New schedule
            </button>
          ) : null}
        </div>
        <p className="settingsLede">Prompts root runs by itself, on time. Each run is a session you can open. Nobody is there to approve, so anything that would ask you is skipped.</p>
        {error ? <div className="settingsError">{error}</div> : null}

        {creating ? (
          <Group title="New schedule">
            <Row label="Name">
              <input className="input" placeholder="Nightly tests" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            </Row>
            <Row label="Folder" help={draft.workspace || "The project it works in."}>
              <button type="button" className="pill" onClick={() => onPickFolder((path) => setDraft((d) => ({ ...d, workspace: path })))}>
                <Folder size={13} /> {draft.workspace ? basename(draft.workspace) : "Choose…"}
              </button>
            </Row>
            <Row label="What to do" stacked>
              <textarea
                className="input"
                rows={3}
                placeholder="Run the tests. If anything fails, say which and why."
                value={draft.prompt}
                onChange={(e) => setDraft({ ...draft, prompt: e.target.value })}
              />
            </Row>
            <Row
              label="When"
              help={
                <>
                  e.g.{" "}
                  {EXAMPLES.map((example) => (
                    <button key={example} type="button" className="linkish" style={{ marginRight: 8 }} onClick={() => setDraft({ ...draft, when: example })}>
                      {example}
                    </button>
                  ))}
                </>
              }
            >
              <input className="input mono" value={draft.when} onChange={(e) => setDraft({ ...draft, when: e.target.value })} />
            </Row>
            <Row label="It may">
              <select className="input" value={draft.mode} onChange={(e) => setDraft({ ...draft, mode: e.target.value as ScheduleMode })}>
                {MODES.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            </Row>
            <div className="settingsRow" style={{ justifyContent: "flex-end", gap: 8 }}>
              <button className="pill" onClick={() => setCreating(false)}>
                Cancel
              </button>
              <button className="pill ink" disabled={!draft.prompt.trim() || !draft.workspace.trim()} onClick={create}>
                Create
              </button>
            </div>
          </Group>
        ) : null}

        <Group>
          {items && items.length === 0 ? <Row label="No schedules yet" help="Create one here, or ask root in a session: “every weekday at 9, summarize the open pull requests”." /> : null}
          {(items ?? []).map((item) => (
            <Row
              key={item.id}
              label={item.name}
              help={
                <>
                  {item.when} · {MODES.find((m) => m.value === item.mode)?.label} · {basename(item.workspace)}
                  <br />
                  {item.running ? "Running now" : item.enabled ? `Next ${when(item.next_run)}` : "Paused"}
                  {item.last_session ? (
                    <>
                      {" · "}
                      <button className="linkish" onClick={() => onOpenSession(item.last_session)}>
                        last run {item.last_status === "finished" ? "" : `(${item.last_status}) `}
                        {when(item.last_run)}
                      </button>
                    </>
                  ) : null}
                </>
              }
            >
              <div className="settingsInline">
                <button className="iconButton" title="Run now" disabled={item.running} onClick={() => act(api.runSchedule(item.id))}>
                  <Play size={14} />
                </button>
                <button className="iconButton" title="Remove" onClick={() => act(api.deleteSchedule(item.id))}>
                  <Trash2 size={14} />
                </button>
                <Switch checked={item.enabled} label={item.enabled ? "Pause" : "Resume"} onChange={(enabled) => act(api.updateSchedule(item.id, { enabled }))} />
              </div>
            </Row>
          ))}
        </Group>
      </div>
    </div>
  );
}
