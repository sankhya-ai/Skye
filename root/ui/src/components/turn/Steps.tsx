// One tool call as one line — icon, verb, subject, and a short note ("598 lines",
// "+12 −3", "exit 1") — that opens to what it did: a terminal, the file, the diff.

import { Check, ChevronRight, CircleDot, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import type { ThinkBlock } from "../../lib/activity.ts";
import { stepKind } from "../../lib/activity.ts";
import { stepDuration, stepMeta, tidyThought } from "../../lib/meta.ts";
import { describeTool, relativePath } from "../../lib/tools.ts";
import { outputText, parseArgs, type ToolBlock } from "../../lib/transcript.ts";
import type { DisplayBlock } from "../../lib/types.ts";
import { TextDiff } from "../DiffView.tsx";
import { StepIcon } from "./icons.tsx";

const MAX_LINES = 400;

/** A plan. The item in progress spins only while the turn is live: a finished
 * turn's unfinished item is where it stopped, not something still happening. */
export function TodoItems({ items, live = false }: { items: { title: string; status: string }[]; live?: boolean }) {
  return (
    <ul className="todoList">
      {items.map((item, index) => {
        const status = String(item.status).toLowerCase().replace(" ", "_");
        return (
          <li key={index} className={status}>
            <span className="box">
              {status === "done" ? <Check size={14} /> : status === "in_progress" ? live ? <Loader2 size={14} className="spinner" /> : <CircleDot size={14} /> : <span className="faint">○</span>}
            </span>
            <span>{item.title}</span>
          </li>
        );
      })}
    </ul>
  );
}

export function Displays({ blocks, workspace }: { blocks: DisplayBlock[]; workspace: string }) {
  return (
    <>
      {blocks.map((block, index) => {
        if (block.type === "diff") {
          return <TextDiff key={index} path={relativePath(String(block.path ?? ""), workspace)} oldText={String(block.old_text ?? "")} newText={String(block.new_text ?? "")} />;
        }
        if (block.type === "shell") {
          return (
            <pre key={index} className="output">
              <span className="faint">$ </span>
              {block.command}
            </pre>
          );
        }
        if (block.type === "todo" && Array.isArray(block.items)) return <TodoItems key={index} items={block.items} />;
        return null;
      })}
    </>
  );
}

/** Long output, cut to what a browser renders without stalling. */
function Output({ text, code }: { text: string; code?: boolean }) {
  const lines = text.replace(/\n+$/, "").split("\n");
  const shown = lines.length > MAX_LINES ? lines.slice(0, MAX_LINES).join("\n") : lines.join("\n");
  return (
    <pre className={`output${code ? " code" : ""}`}>
      {shown}
      {lines.length > MAX_LINES ? <span className="faint">{`\n… ${lines.length - MAX_LINES} more lines`}</span> : null}
    </pre>
  );
}

function approvalNote(step: ToolBlock): string {
  const approval = step.approval;
  if (!approval || approval.status === "pending") return "";
  if (approval.status === "reject") return "Rejected";
  if (approval.auto) return `Allowed automatically${approval.reason ? ` — ${approval.reason}` : ""}`;
  return approval.status === "approve_for_session" ? "Allowed for this session" : "Approved by you";
}

function Body({ step, workspace, thoughts }: { step: ToolBlock; workspace: string; thoughts: ThinkBlock[] }) {
  const result = step.result;
  const output = outputText(result);
  const kind = stepKind(step.name);
  const diffs = result?.display?.filter((d) => d.type === "diff" || d.type === "todo") ?? [];
  const note = approvalNote(step);
  const command = kind === "command" ? String(parseArgs(step.args)?.command ?? "") : "";
  return (
    <div className="toolBody">
      {thoughts.map((t) => (
        <ThoughtRow key={t.key} text={t.text} live={false} />
      ))}
      {kind === "command" ? (
        <div className={`terminal${result?.is_error ? " failed" : ""}`}>
          <div className="termCmd">
            <span className="prompt">$</span>
            {command}
          </div>
          {output ? <Output text={output} /> : result ? <div className="termEmpty">{result.is_error ? result.message : "no output"}</div> : null}
        </div>
      ) : diffs.length ? (
        <Displays blocks={diffs} workspace={workspace} />
      ) : output ? (
        <Output text={output} code={kind === "read"} />
      ) : null}
      {result?.message && (result.is_error ? kind !== "command" : kind !== "command" && kind !== "read") ? <p className={`stepMessage${result.is_error ? " err" : ""}`}>{result.message}</p> : null}
      {note ? <p className="stepMessage">{note}</p> : null}
    </div>
  );
}

export function ToolRow({ step, workspace, running, thoughts = [] }: { step: ToolBlock; workspace: string; running: boolean; thoughts?: ThinkBlock[] }) {
  const view = describeTool(step.name, step.args, workspace);
  const kind = stepKind(step.name);
  const active = running && !step.result && step.approval?.status !== "reject";
  const meta = stepMeta(step);
  const interrupted = !running && !step.result && step.approval?.status !== "reject";
  const hasDiff = Boolean(step.result?.display?.some((d) => d.type === "diff"));
  const failed = Boolean(step.result?.is_error);
  const [open, setOpen] = useState(failed || hasDiff);
  // A call that finishes while on screen opens to its diff or its failure.
  useEffect(() => {
    if (failed || hasDiff) setOpen(true);
  }, [failed, hasDiff]);
  const expandable = Boolean(step.result || thoughts.length || step.approval);
  const duration = kind === "command" ? stepDuration(step) : "";
  const tone = step.result?.is_error ? "error" : step.approval?.status === "reject" || interrupted ? "muted" : active ? "working" : "done";
  return (
    <div className={`toolRow ${tone}${open ? " open" : ""}`}>
      <button className="toolLine" aria-expanded={open} disabled={!expandable} onClick={() => setOpen((v) => !v)}>
        <span className="rowIcon">{active ? <Loader2 size={13} className="spinner" /> : <StepIcon kind={kind} size={13} />}</span>
        <span className={`verb${active ? " shimmer" : ""}`}>{view.verb}</span>
        {view.subject ? (
          <code className="subject" title={view.subject}>
            {view.subject}
          </code>
        ) : null}
        <span className="rowMeta">
          {meta.added || meta.removed ? (
            <>
              <span className="plus">+{meta.added}</span>
              <span className="minus">−{meta.removed}</span>
            </>
          ) : null}
          {meta.text ? <span className={meta.error ? "err" : ""}>{meta.text}</span> : null}
          {interrupted ? <span>interrupted</span> : null}
          {duration ? <span>{duration}</span> : null}
        </span>
        {expandable ? <ChevronRight size={13} className="chev" /> : null}
      </button>
      {open ? <Body step={step} workspace={workspace} thoughts={thoughts} /> : null}
    </div>
  );
}

/** A model's thinking: a live tail while it streams, one quiet line after. */
export function ThoughtRow({ text, live }: { text: string; live: boolean }) {
  const clean = tidyThought(text);
  if (live) {
    const tail = clean.length > 220 ? `…${clean.slice(-220)}` : clean;
    return (
      <div className="thoughtRow live">
        <span className="thoughtLabel shimmer">Thinking</span>
        {tail ? <div className="thoughtTail">{tail}</div> : null}
      </div>
    );
  }
  const preview = clean.split("\n")[0];
  return (
    <details className="thoughtRow">
      <summary>
        <ChevronRight size={12} className="chev" />
        <span className="thoughtLabel">Thought</span>
        <span className="thoughtPreview">{preview}</span>
      </summary>
      <div className="thoughts">{clean}</div>
    </details>
  );
}
