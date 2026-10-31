// A delegated task as a card of its own: who is on it, what they are doing now,
// their latest calls streaming in — and, once done, one line to open the full
// trace, instead of the subagent's whole transcript poured into the turn.

import { Bot, ChevronRight } from "lucide-react";
import { useState, type ReactNode } from "react";

import { liveStatus } from "../../lib/activity.ts";
import { stepDuration } from "../../lib/meta.ts";
import { parseArgs, type ToolBlock } from "../../lib/transcript.ts";
import { ThoughtRow, ToolRow } from "./Steps.tsx";

const TAIL = 3;

export function SubagentCard({ step, workspace, running, renderChildren }: { step: ToolBlock; workspace: string; running: boolean; renderChildren: (step: ToolBlock) => ReactNode }) {
  const [open, setOpen] = useState(false);
  const args = parseArgs(step.args) ?? {};
  const name = String(args.subagent_name ?? "subagent");
  const task = String(args.description ?? "");
  const children = step.children?.blocks ?? [];
  const calls = children.filter((b): b is ToolBlock => b.kind === "tool");
  const active = running && !step.result;
  const failed = Boolean(step.result?.is_error);
  const duration = stepDuration(step);
  const status = active
    ? liveStatus(children, workspace)
    : failed
      ? step.result?.message || "Failed"
      : !step.result
        ? "Interrupted"
        : [`${calls.length} tool call${calls.length === 1 ? "" : "s"}`, duration].filter(Boolean).join(" · ");
  const lastThought = children[children.length - 1];
  const tail = active && !open ? calls.slice(-TAIL) : [];
  return (
    <div className={`agentCard${active ? " active" : ""}${failed ? " failed" : ""}${open ? " open" : ""}`}>
      <button className="agentHead" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <span className="agentBadge">
          <Bot size={13} />
          {name}
        </span>
        <span className="agentTask" title={task}>
          {task}
        </span>
        <ChevronRight size={13} className="chev" />
      </button>
      <div className={`agentStatus${active ? " shimmer" : ""}${failed ? " err" : ""}`}>{status}</div>
      {tail.length ? (
        <div className="agentTail">
          {calls.length > tail.length ? (
            <button className="moreRows" onClick={() => setOpen(true)}>
              {calls.length - tail.length} earlier
            </button>
          ) : null}
          {tail.map((call) => (
            <ToolRow key={call.key} step={call} workspace={workspace} running={active} />
          ))}
          {lastThought?.kind === "think" ? <ThoughtRow text={lastThought.text} live /> : null}
        </div>
      ) : null}
      {open ? (
        <div className="agentBody">
          {args.prompt ? (
            <details className="assignment">
              <summary>Assignment</summary>
              <div className="thoughts">{String(args.prompt)}</div>
            </details>
          ) : null}
          {renderChildren(step)}
        </div>
      ) : null}
    </div>
  );
}
