// A run of tool calls as one line ("Read 8 files, searched code"). While it is
// the live edge of the turn it shows its last few calls streaming in; when the
// turn moves on it folds to the line, opening to every call on demand. A step
// waiting for the owner shows its card right here.

import { ChevronRight, ListTodo, Loader2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import type { Activity as ActivityItem, ThinkBlock } from "../../lib/activity.ts";
import { parseArgs, type ToolBlock } from "../../lib/transcript.ts";
import { ApprovalCard, type ApproveFn } from "./ApprovalCard.tsx";
import { StepIcon } from "./icons.tsx";
import { ThoughtRow, TodoItems, ToolRow } from "./Steps.tsx";
import { SubagentCard } from "./SubagentCard.tsx";

const TAIL = 4;

function Rows({ rows, workspace, running, live }: { rows: (ToolBlock | ThinkBlock)[]; workspace: string; running: boolean; live: boolean }) {
  return (
    <>
      {rows.map((row, index) =>
        row.kind === "think" ? (
          <ThoughtRow key={row.key} text={row.text} live={live && index === rows.length - 1} />
        ) : (
          <ToolRow key={row.key} step={row} workspace={workspace} running={running} />
        ),
      )}
    </>
  );
}

function PlanCard({ item, live }: { item: ActivityItem; live: boolean }) {
  const step = item.steps[item.steps.length - 1];
  const shown = step.result?.display?.find((d) => d.type === "todo")?.items;
  const args = parseArgs(step.args)?.todos;
  const items: { title: string; status: string }[] = shown ?? (Array.isArray(args) ? args : []);
  const done = items.filter((i) => String(i.status).toLowerCase() === "done").length;
  return (
    <div className="planCard">
      <div className="planHead">
        <ListTodo size={14} />
        <span>Plan</span>
        {items.length ? (
          <span className="planCount">
            {done}/{items.length}
          </span>
        ) : null}
        {items.length ? (
          <span className="planMeter">
            <span style={{ width: `${(done / items.length) * 100}%` }} />
          </span>
        ) : null}
      </div>
      <TodoItems items={items} live={live} />
    </div>
  );
}

export function Activity({
  item,
  workspace,
  running,
  live,
  onApprove,
  renderChildren,
}: {
  item: ActivityItem;
  workspace: string;
  running: boolean;
  /** This line is the live edge of a running turn. */
  live: boolean;
  onApprove: ApproveFn;
  renderChildren: (step: ToolBlock) => ReactNode;
}) {
  const [open, setOpen] = useState(item.tone === "error");
  useEffect(() => {
    if (item.tone === "error") setOpen(true);
  }, [item.tone]);
  const pending = item.steps.filter((s) => s.approval?.status === "pending");
  const approvals = pending.map((step) => <ApprovalCard key={step.approval!.request.id} approval={step.approval!} workspace={workspace} onApprove={onApprove} />);
  const first = item.steps[0];
  const thoughts = item.rows.filter((r): r is ThinkBlock => r.kind === "think");
  const trailing = item.rows[item.rows.length - 1];

  if (item.groupKind === "plan" && first.result && !first.result.is_error) return <PlanCard item={item} live={running} />;

  if (item.steps.length === 1) {
    return (
      <div className="activity single">
        {item.icon === "agent" ? (
          <SubagentCard step={first} workspace={workspace} running={running} renderChildren={renderChildren} />
        ) : (
          <ToolRow step={first} workspace={workspace} running={running} thoughts={live && trailing.kind === "think" ? thoughts.slice(0, -1) : thoughts} />
        )}
        {live && trailing.kind === "think" ? <ThoughtRow text={trailing.text} live /> : null}
        {approvals}
      </div>
    );
  }

  const working = item.tone === "working" || live;
  const tail = live && !open ? item.rows.slice(-TAIL) : [];
  const hidden = item.rows.length - tail.length;
  return (
    <div className={`activity group ${item.tone}${open ? " open" : ""}`}>
      <button className="activityLine" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <span className="rowIcon">{item.tone === "working" ? <Loader2 size={14} className="spinner" /> : <StepIcon kind={item.icon} size={14} />}</span>
        <span className={`label${working ? " shimmer" : ""}`}>{item.label}</span>
        {/\d/.test(item.label) ? null : <span className="count">{item.steps.length}</span>}
        <ChevronRight size={13} className="chev" />
      </button>
      {open || tail.length ? (
        <div className="activityRows">
          {tail.length && hidden > 0 ? (
            <button className="moreRows" onClick={() => setOpen(true)}>
              {hidden} earlier
            </button>
          ) : null}
          <Rows rows={open ? item.rows : tail} workspace={workspace} running={running} live={live} />
        </div>
      ) : null}
      {approvals}
    </div>
  );
}
