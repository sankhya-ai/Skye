// The Result card's panel: what the task produced. Three views -
//  Preview: the changed project running live (its own dev server, in the task's worktree)
//           in a frame beside the workflow, desktop or phone width;
//  Answer:  the final reply, for tasks whose result is an explanation, report or article;
//  Changes: the changed files, with "bring into project".
// The preview only starts on the owner's click: Skye never runs project code by itself.
import {
  ExternalLink,
  Flag,
  Loader2,
  Monitor,
  Play,
  RotateCw,
  Smartphone,
  Square,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  getPreview,
  startPreview,
  stopPreview,
  type MissionDetail,
  type PreviewState,
} from "../api";
import { cn } from "../lib";
import { roleName, type StepView } from "../task/model";
import { ChatItemView } from "./AgentPanel";
import { BringIntoProject } from "./BringIntoProject";
import { MissionArtifactsPanel } from "./MissionArtifacts";
import { RoleIcon, StateMark } from "./TaskCanvas";
import { Button } from "./ui/Button";

type Tab = "preview" | "answer" | "changes";

function Preview({ missionId, running }: { missionId: string; running: boolean }) {
  const [state, setState] = useState<PreviewState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [device, setDevice] = useState<"desktop" | "phone">("desktop");
  const [nonce, setNonce] = useState(0);
  const frame = useRef<HTMLIFrameElement>(null);

  const load = useCallback(async () => {
    try {
      setState(await getPreview(missionId));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [missionId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (state?.status !== "starting") return;
    const t = setInterval(() => void load(), 1200);
    return () => clearInterval(t);
  }, [state?.status, load]);

  const start = async () => {
    setError(null);
    try { setState(await startPreview(missionId)); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  const stop = async () => {
    try { setState(await stopPreview(missionId)); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };

  if (!state && error) {
    return <p className="p-4 text-xs text-destructive">{error}</p>;
  }
  if (!state) {
    return <p className="flex items-center gap-2 p-4 text-xs text-muted-foreground"><Loader2 className="size-3 animate-spin" /> Checking what this project runs...</p>;
  }
  if (state.kind === "none") {
    return (
      <div className="p-6 text-center">
        <p className="text-sm text-foreground">Nothing to run for this result</p>
        <p className="mt-1 text-xs text-muted-foreground">
          The project has no dev script or web page to preview. The Answer tab has what the agents produced.
        </p>
      </div>
    );
  }
  if (state.status === "ready" && state.url) {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex items-center gap-2 border-b border-border px-3 py-2">
          <div className="flex min-w-0 flex-1 items-center gap-2 rounded-md border border-border bg-muted/40 px-2 py-1">
            <span className="size-2 shrink-0 rounded-full bg-success" />
            <span className="truncate font-mono text-[11px] text-foreground/80">{state.url}</span>
          </div>
          <div className="flex overflow-hidden rounded-md border border-border">
            <button type="button" aria-label="Desktop width" onClick={() => setDevice("desktop")}
                    className={cn("p-1.5", device === "desktop" ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted")}>
              <Monitor className="size-3.5" />
            </button>
            <button type="button" aria-label="Phone width" onClick={() => setDevice("phone")}
                    className={cn("p-1.5", device === "phone" ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted")}>
              <Smartphone className="size-3.5" />
            </button>
          </div>
          <button type="button" aria-label="Reload" onClick={() => setNonce((n) => n + 1)}
                  className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"><RotateCw className="size-3.5" /></button>
          <a href={state.url} target="_blank" rel="noreferrer" aria-label="Open in a new tab"
             className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"><ExternalLink className="size-3.5" /></a>
          <button type="button" aria-label="Stop preview" onClick={() => void stop()}
                  className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-destructive"><Square className="size-3.5" /></button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto bg-muted/50">
          <iframe
            ref={frame}
            key={nonce}
            title="Result preview"
            src={state.url}
            className={cn("block h-full border-0 bg-white", device === "phone" ? "mx-auto my-3 h-[calc(100%-1.5rem)] w-[390px] rounded-xl shadow-lg ring-1 ring-border" : "w-full")}
          />
        </div>
      </div>
    );
  }
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
      {state.status === "starting" ? (
        <>
          <Loader2 className="size-5 animate-spin text-info" />
          <p className="text-sm text-foreground">Starting the preview: {state.phase || "starting"}...</p>
          <p className="text-xs text-muted-foreground">{state.how}</p>
        </>
      ) : (
        <>
          <p className="text-sm text-foreground">See the result running</p>
          {state.isolated ? (
            <p className="max-w-sm text-xs text-muted-foreground">
              Runs <span className="font-mono">{state.how}</span> in this task&apos;s own copy of the
              project, so you see exactly what the agents changed. Your project folder is untouched.
            </p>
          ) : (
            <p className="max-w-sm text-xs text-muted-foreground">
              Runs <span className="font-mono">{state.how}</span> in your project folder itself -
              this task worked there directly, without a separate copy. Installing dependencies or
              building writes into that folder.
            </p>
          )}
          {state.status === "failed" ? (
            <p className="max-w-sm rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              {state.error || "The preview failed to start."}
            </p>
          ) : null}
          {running ? <p className="text-[11px] text-muted-foreground">The agents are still working; the preview shows their files as they are now.</p> : null}
          <Button size="sm" onClick={() => void start()}>
            <Play className="size-3" /> {state.status === "failed" ? "Try again" : "Run preview"}
          </Button>
        </>
      )}
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
      {state.log && state.log.length && state.status !== "stopped" ? (
        <pre className="max-h-40 w-full max-w-lg overflow-auto rounded-md bg-muted/60 p-2 text-left font-mono text-[10.5px] text-muted-foreground">
          {state.log.join("\n")}
        </pre>
      ) : null}
    </div>
  );
}

export function ResultPanel({ detail, steps, live, changedFiles, onClose, onOpenStep, onWide }: {
  detail: MissionDetail;
  steps: StepView[];
  live: boolean;
  changedFiles: number | null;
  onClose: () => void;
  onOpenStep: (id: string) => void;
  onWide: (wide: boolean) => void;
}) {
  const answering = [...steps].reverse().find((s) => s.state === "done" && s.answer && s.role !== "pm")
    ?? [...steps].reverse().find((s) => s.state === "done" && s.answer);
  const answerItem = answering?.items.slice().reverse().find((i) => i.type === "answer");
  // A task that changed files is most likely something to look at; one that changed
  // nothing is an answer to read.
  const [tab, setTab] = useState<Tab>(changedFiles ? "preview" : "answer");
  useEffect(() => { onWide(tab === "preview"); }, [tab, onWide]);
  useEffect(() => () => onWide(false), [onWide]);
  const done = detail.status.status === "done";

  return (
    <div className="flex h-full min-h-0 flex-col" data-result-panel>
      <div className="flex items-center gap-2.5 border-b border-border px-4 py-3">
        <span className={cn("inline-flex size-6 items-center justify-center rounded-md text-white",
          done ? "bg-success" : "bg-muted-foreground/50")}>
          <Flag className="size-3.5" />
        </span>
        <p className="flex-1 text-sm font-medium text-foreground">Result</p>
        <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <StateMark state={live ? "running" : done ? "done" : "pending"} />
          {live ? "Agents working" : done ? "Done" : detail.status.status}
          {changedFiles !== null ? ` · ${changedFiles} file${changedFiles === 1 ? "" : "s"} changed` : ""}
        </span>
        <button type="button" onClick={onClose} aria-label="Close result"
                className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground">
          <X className="size-4" />
        </button>
      </div>
      <div className="flex gap-1 border-b border-border px-3">
        {(["preview", "answer", "changes"] as const).map((t) => (
          <button key={t} type="button" onClick={() => setTab(t)}
                  className={cn("-mb-px border-b-2 px-2 py-2 text-xs capitalize",
                    tab === t ? "border-foreground text-foreground" : "border-transparent text-muted-foreground hover:text-foreground")}>
            {t}
          </button>
        ))}
      </div>
      <div className={cn("min-h-0 flex-1", tab === "preview" ? "" : "overflow-y-auto")}>
        {tab === "preview" ? (
          <Preview missionId={detail.mission.id} running={live} />
        ) : tab === "answer" ? (
          <div className="space-y-3 px-4 py-4">
            {answering && answerItem ? (
              <>
                <button type="button" onClick={() => onOpenStep(answering.id)}
                        className="flex items-center gap-1.5 text-[11px] text-muted-foreground hover:text-foreground">
                  <RoleIcon role={answering.role} size="sm" />
                  {roleName(answering.role)} answered · open its conversation
                </button>
                <ChatItemView item={answerItem} step={answering} proposed={[]} onOpenStep={onOpenStep} />
              </>
            ) : (
              <p className="text-xs text-muted-foreground">
                {live ? "The answer appears here when the agents finish." : "No agent left a final answer."}
              </p>
            )}
          </div>
        ) : (
          <div className="space-y-4 px-4 py-4">
            <BringIntoProject
              missionId={detail.mission.id}
              settled={!live && ["done", "delivered", "awaiting-verification"].includes(detail.status.status)}
            />
            <MissionArtifactsPanel missionId={detail.mission.id} artifacts={detail.artifacts} running={live} />
          </div>
        )}
      </div>
    </div>
  );
}
