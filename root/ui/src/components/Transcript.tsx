// A session as Chotu's coding mode showed it: the owner's message in a box,
// then root's turn — its words, quiet trace lines for the tools it used, any
// approval it is waiting on — and a footer with the time and a Copy button.

import { Brain, ClipboardList, Paperclip, RotateCcw, Sparkles } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { groupBlocks, liveStatus, type RenderItem } from "../lib/activity.ts";
import { producedFiles, type Block, type ToolBlock, type TranscriptState } from "../lib/transcript.ts";
import type { Flow, ProducedFile } from "../lib/types.ts";
import { Markdown } from "./Markdown.tsx";
import { Activity } from "./turn/Activity.tsx";
import { ApprovalCard, type ApproveFn } from "./turn/ApprovalCard.tsx";
import { JobRequestCard } from "./turn/JobRequestCard.tsx";
import { FlowCard } from "./turn/FlowCard.tsx";
import { ThoughtRow } from "./turn/Steps.tsx";
import { Working } from "./turn/Working.tsx";

type UserBlock = Extract<Block, { kind: "user" }>;
type EndBlock = Extract<Block, { kind: "turn_end" }>;
interface Turn {
  key: string;
  user: UserBlock | null;
  blocks: Block[];
  end: EndBlock | null;
}

function turnsOf(blocks: Block[]): Turn[] {
  const turns: Turn[] = [];
  let current: Turn = { key: "start", user: null, blocks: [], end: null };
  for (const block of blocks) {
    if (block.kind === "user") {
      if (current.user || current.blocks.length) turns.push(current);
      current = { key: block.key, user: block, blocks: [], end: null };
    } else if (block.kind === "turn_end") {
      current.end = block;
    } else {
      current.blocks.push(block);
    }
  }
  if (current.user || current.blocks.length) turns.push(current);
  return turns;
}

function when(ts: number): string {
  const date = new Date(ts * 1000);
  const today = new Date().toDateString() === date.toDateString();
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return today ? time : `${date.toLocaleDateString([], { weekday: "long" })} ${time}`;
}

function tokens(n: number): string {
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1_000 ? `${(n / 1_000).toFixed(1)}k` : String(n);
}

/** Where a produced file opens (or downloads). */
export type FileUrlFn = (file: ProducedFile, download: boolean) => string;

function size(bytes: number): string {
  return bytes >= 1_048_576 ? `${(bytes / 1_048_576).toFixed(1)} MB` : bytes >= 1024 ? `${Math.round(bytes / 1024)} KB` : `${bytes} B`;
}

function ProducedFiles({ files, url }: { files: ProducedFile[]; url: FileUrlFn }) {
  if (!files.length) return null;
  return (
    <div className="producedFiles">
      {files.map((file) => (
        <span key={`${file.scope}:${file.path}`} className="producedFile" title={file.scope === "scratch" ? "In the session's scratch folder" : "In the workspace"}>
          <span className="name">{file.path.split("/").pop()}</span>
          <span className="faint">{size(file.size)}</span>
          <a href={url(file, false)} target="_blank" rel="noreferrer">
            Open
          </a>
          <a href={url(file, true)}>Download</a>
        </span>
      ))}
    </div>
  );
}

/** Restore the workspace to a checkpoint (``undefined`` when restoring is not offered). */
export type RestoreFn = (checkpoint: string) => void;

function Footer({ end, text, onRestore }: { end: EndBlock; text: string; onRestore?: RestoreFn }) {
  const [copied, setCopied] = useState(false);
  const failed = end.status === "error" || end.status === "budget_exhausted";
  const status =
    end.status === "cancelled" ? "Stopped" : end.status === "max_steps_reached" ? "Stopped at the step limit — say “continue”" : failed ? "Failed" : "";
  return (
    <>
      {failed && end.error ? <div className="turnError">{end.error}</div> : null}
      <div className="messageActions">
        <span>{when(end.ts)}</span>
        {status ? <span className={failed ? "err" : ""}>{status}</span> : null}
        {end.duration != null ? <span>{end.duration < 60 ? `${end.duration.toFixed(1)}s` : `${Math.floor(end.duration / 60)}m ${Math.round(end.duration % 60)}s`}</span> : null}
        {end.files.length ? <span title={end.files.join("\n")}>{end.files.length} file{end.files.length === 1 ? "" : "s"} changed</span> : null}
        {end.verified ? (
          <span className="verified" title={`Verified with ${end.checks ?? 1} automated check(s)`}>✓ verified</span>
        ) : end.files.length > 0 ? (
          <span className="unverified" title="Changes completed without running automated checks">unverified</span>
        ) : null}
        {end.usage.input || end.usage.output ? (
          <span>
            {tokens(end.usage.input)} in · {tokens(end.usage.output)} out{end.usage.cost ? ` · $${end.usage.cost.toFixed(4)}` : ""}
          </span>
        ) : null}
        {end.checkpoint && onRestore ? (
          <button onClick={() => onRestore(end.checkpoint!)} title="Put every file back the way it was before this turn (and after it, nothing later). You can undo this.">
            Restore files
          </button>
        ) : null}
        {text ? (
          <button
            onClick={() => {
              void navigator.clipboard?.writeText(text).then(() => {
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              });
            }}
          >
            {copied ? "Copied" : "Copy"}
          </button>
        ) : null}
      </div>
    </>
  );
}

export function Items({
  items,
  workspace,
  running,
  onApprove,
  onFlow,
  onRestore,
}: {
  items: RenderItem[];
  workspace: string;
  running: boolean;
  onApprove: ApproveFn;
  onFlow?: (flow: Flow) => void;
  onRestore?: RestoreFn;
}) {
  const children = (step: ToolBlock): ReactNode =>
    step.children ? <Items items={groupBlocks(step.children.blocks, { running: running && !step.result, workspace })} workspace={workspace} running={running && !step.result} onApprove={onApprove} /> : null;
  return (
    <>
      {items.map((item, index) => {
        const live = running && index === items.length - 1;
        switch (item.kind) {
          case "text":
            return (
              <div key={item.key} className="assistantText">
                <Markdown text={item.text} />
              </div>
            );
          case "think":
            return item.text.trim() ? <ThoughtRow key={item.key} text={item.text} live={live} /> : null;
          case "activity":
            return <Activity key={item.key} item={item} workspace={workspace} running={running} live={live} onApprove={onApprove} renderChildren={children} />;
          case "approval":
            return item.approval.status === "pending" ? <ApprovalCard key={item.key} approval={item.approval} workspace={workspace} onApprove={onApprove} /> : null;
          case "jobrequest":
            return <JobRequestCard key={item.key} job={item.job} />;
          case "notice":
            return (
              <div key={item.key} className={`notice ${item.tone}`}>
                {item.text}
              </div>
            );
          case "flow":
            return <FlowCard key={item.key} flow={item.flow} onChange={onFlow} />;
          case "rollback": {
            const changed = item.restored.length + item.removed.length;
            return (
              <div key={item.key} className="learnedLine">
                <RotateCcw size={13} />
                <span>
                  {changed
                    ? `Restored the files to before “${item.label}”: ${item.restored.length} put back, ${item.removed.length} removed.`
                    : `Nothing had changed since “${item.label}”.`}{" "}
                  {changed && item.undo && onRestore ? (
                    <button onClick={() => onRestore(item.undo)}>
                      Undo restore
                    </button>
                  ) : null}
                </span>
              </div>
            );
          }
          case "learned":
            return (
              <div key={item.key} className="learnedLine">
                <Sparkles size={13} />
                {item.ok ? (
                  <span>
                    {item.promoted ? `Learned and kept ${item.promoted} lesson${item.promoted === 1 ? "" : "s"}` : "Found something worth learning"}
                    {item.skills.length ? ` — skill ${item.skills.map((s) => `“${s}”`).join(", ")}` : ""}
                    {item.memories ? `${item.skills.length ? " and" : " —"} ${item.memories} memor${item.memories === 1 ? "y" : "ies"}` : ""}.{" "}
                    {!item.verified && item.mode === "auto" ? "Not kept on its own: the change was never run. " : null}
                    {item.mode === "review" || item.promoted < item.skills.length + item.memories ? <a href="#/settings/agent">Review</a> : null}
                  </span>
                ) : (
                  <span>The learning review did not run: {item.error}</span>
                )}
              </div>
            );
          default:
            return null;
        }
      })}
    </>
  );
}

export function Transcript({
  state,
  workspace,
  onApprove,
  empty,
  hasMore,
  onLoadEarlier,
  planMode,
  onApprovePlan,
  onRestore,
  fileUrl,
}: {
  state: TranscriptState;
  workspace: string;
  onApprove: ApproveFn;
  empty: ReactNode;
  hasMore: boolean;
  onLoadEarlier: () => void;
  planMode: boolean;
  onApprovePlan: () => void;
  onRestore?: RestoreFn;
  fileUrl?: FileUrlFn;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const onScroll = () => {
      stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  });

  const turns = turnsOf(state.blocks);
  const waiting = state.pending.size > 0;

  return (
    <div className="transcript scroll" ref={scroller}>
      <div className="column">
        {hasMore ? (
          <div className="notice">
            <button className="pill small" onClick={onLoadEarlier}>
              Load earlier messages
            </button>
          </div>
        ) : null}
        {turns.length === 0 ? empty : null}
        {turns.map((turn, index) => {
          const last = index === turns.length - 1;
          const running = last && state.running;
          const items = groupBlocks(turn.blocks, { running, workspace });
          const answer = turn.blocks.filter((b): b is Extract<Block, { kind: "text" }> => b.kind === "text").map((b) => b.text).join("\n\n");
          return (
            <section key={turn.key} className="turn">
              {turn.user ? (
                <div className="message user">
                  <div className="userBox">
                    <div className="messageLabel">You</div>
                    {turn.user.text}
                    {turn.user.attachments.length ? (
                      <div className="memoryNote">
                        <Paperclip size={12} /> {turn.user.attachments.map((a) => a.name).join(", ")}
                      </div>
                    ) : null}
                    {turn.user.memory.length ? (
                      <details className="memoryNote">
                        <summary>
                          <Brain size={12} /> Dhee attached {turn.user.memory.length} memor{turn.user.memory.length === 1 ? "y" : "ies"}
                        </summary>
                        <ul>
                          {turn.user.memory.map((m) => (
                            <li key={m.id || m.memory}>{m.memory}</li>
                          ))}
                        </ul>
                      </details>
                    ) : null}
                  </div>
                </div>
              ) : null}
              {items.length || running || turn.end ? (
                <div className="message assistant">
                  {turn.user ? <div className="messageLabel">root</div> : null}
                  <Items items={items} workspace={workspace} running={running} onApprove={onApprove} onRestore={running ? undefined : onRestore} />
                  {running && !waiting && turn.user ? <Working since={turn.user.ts * 1000} label={state.compacting ? "Compacting context…" : `${liveStatus(turn.blocks, workspace)}…`} /> : null}
                  {fileUrl ? <ProducedFiles files={producedFiles(turn.blocks)} url={fileUrl} /> : null}
                  {turn.end ? <Footer end={turn.end} text={answer} onRestore={running ? undefined : onRestore} /> : null}
                </div>
              ) : null}
              {last && planMode && !state.running && turn.end?.status === "finished" && turn.end.mode === "plan" ? (
                <div className="planBar">
                  <ClipboardList size={16} />
                  <span className="grow">Plan mode: nothing has been changed. Approve the plan to let root implement it.</span>
                  <button className="pill ink" onClick={onApprovePlan}>
                    Approve plan
                  </button>
                </div>
              ) : null}
            </section>
          );
        })}
      </div>
    </div>
  );
}
