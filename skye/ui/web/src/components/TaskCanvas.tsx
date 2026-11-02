// The task as a flow: your request on the left, one card per agent in dependency order,
// the result on the right, with connectors between them. Patterned on flow editors
// (n8n, Sim): fixed-width cards with an icon header and label/value rows, an animated
// pulse on the edge feeding the agent that is working, success-tinted edges behind
// finished work. A card is a door: clicking it opens that agent's own panel.
import {
  Bot,
  Check,
  ClipboardList,
  Clock,
  Code2,
  FlaskConical,
  Flag,
  LayoutTemplate,
  MessageSquare,
  Palette,
  Search,
  Server,
  ShieldCheck,
  SkipForward,
  CircleAlert,
  X,
} from "lucide-react";
import type { ComponentType } from "react";
import { cn } from "../lib";
import {
  CARD_H,
  CARD_W,
  REQUEST_ID,
  RESULT_ID,
  formatDuration,
  roleName,
  type Layout,
  type StepView,
} from "../task/model";

const ROLE_ICON: Record<string, ComponentType<{ className?: string }>> = {
  pm: ClipboardList,
  planner: ClipboardList,
  developer: Code2,
  "senior-dev": Code2,
  "backend-dev": Server,
  "frontend-dev": LayoutTemplate,
  designer: Palette,
  artist: Palette,
  reviewer: ShieldCheck,
  tester: FlaskConical,
  devops: Server,
  explorer: Search,
};

const ROLE_COLORS = ["#6f5cf6", "#0ea5e9", "#f97316", "#10b981", "#ec4899", "#eab308", "#14b8a6", "#8b5cf6"];
const FIXED_COLOR: Record<string, string> = {
  pm: "#6f5cf6",
  developer: "#0ea5e9",
  "frontend-dev": "#f97316",
  "backend-dev": "#14b8a6",
  reviewer: "#10b981",
  designer: "#ec4899",
  tester: "#eab308",
};

export function roleColor(role: string): string {
  if (FIXED_COLOR[role]) return FIXED_COLOR[role];
  let h = 0;
  for (const ch of role) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return ROLE_COLORS[h % ROLE_COLORS.length];
}

export function RoleIcon({ role, size = "md" }: { role: string; size?: "sm" | "md" }) {
  const Icon = ROLE_ICON[role] ?? Bot;
  return (
    <span
      className={cn("inline-flex shrink-0 items-center justify-center rounded-md text-white",
        size === "sm" ? "size-5" : "size-6")}
      style={{ backgroundColor: roleColor(role) }}
    >
      <Icon className={size === "sm" ? "size-3" : "size-3.5"} />
    </span>
  );
}

export function StateMark({ state }: { state: string }) {
  switch (state) {
    case "running":
      return <span className="size-4 animate-spin rounded-full border-2 border-info/25 border-t-info" aria-label="running" />;
    case "done":
      return <span className="flex size-4 items-center justify-center rounded-full bg-success text-white" aria-label="done"><Check className="size-2.5" strokeWidth={3} /></span>;
    case "failed":
    case "interrupted":
    case "resume-rejected":
      return <span className="flex size-4 items-center justify-center rounded-full bg-destructive text-white" aria-label={state}><X className="size-2.5" strokeWidth={3} /></span>;
    case "needs-owner":
    case "cancelled":
      return <CircleAlert className="size-4 text-amber-500" aria-label={state} />;
    case "skipped":
      return <SkipForward className="size-4 text-muted-foreground" aria-label="skipped" />;
    default:
      return <Clock className="size-4 text-muted-foreground/60" aria-label="pending" />;
  }
}

export const STATE_LABEL: Record<string, string> = {
  pending: "Waiting",
  running: "Working",
  done: "Done",
  failed: "Failed",
  "needs-owner": "Needs you",
  interrupted: "Interrupted",
  cancelled: "Stopped",
  skipped: "Skipped",
  "resume-rejected": "Session lost",
};

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex h-5 items-center justify-between gap-3 text-[11px]">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className={cn("truncate text-foreground/85", mono && "font-mono text-[10.5px]")} title={value}>
        {value}
      </span>
    </div>
  );
}

function Handle({ side }: { side: "left" | "right" }) {
  return (
    <span
      className={cn(
        "absolute top-1/2 size-2.5 -translate-y-1/2 rounded-full border-2 border-card bg-muted-foreground/40",
        side === "left" ? "-left-[5px]" : "-right-[5px]",
      )}
    />
  );
}

function stepDuration(step: StepView, now: number): string {
  if (!step.startedAt) return "";
  const end = step.endedAt ?? (step.state === "running" ? now : undefined);
  return end ? formatDuration(end - step.startedAt) : "";
}

export function TaskCanvas({
  steps,
  layout,
  goal,
  projectName,
  taskState,
  changedFiles,
  selected,
  onSelect,
}: {
  steps: StepView[];
  layout: Layout;
  goal: string;
  projectName?: string;
  taskState: string;
  changedFiles: number | null;
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  const now = Date.now() / 1000;
  const byId = new Map(steps.map((s) => [s.id, s]));
  const doneCount = steps.filter((s) => s.state === "done").length;

  const edgeState = (from: string, to: string): "idle" | "active" | "done" => {
    const target = to === RESULT_ID ? null : byId.get(to);
    const source = from === REQUEST_ID ? null : byId.get(from);
    const sourceDone = source ? source.state === "done" : true;
    if (target?.state === "running" && sourceDone) return "active";
    if (to === RESULT_ID) return taskState === "done" && source?.state === "done" ? "done" : "idle";
    if (target && target.state !== "pending" && sourceDone) return "done";
    return "idle";
  };

  return (
    <div
      className="relative mx-auto"
      style={{ width: layout.width, height: layout.height }}
      onClick={(e) => { if (e.target === e.currentTarget) onSelect(null); }}
    >
      <svg className="pointer-events-none absolute inset-0" width={layout.width} height={layout.height} aria-hidden="true">
        {layout.edges.map((e) => {
          const a = layout.pos.get(e.from);
          const b = layout.pos.get(e.to);
          if (!a || !b) return null;
          const x1 = a.x + CARD_W + 5;
          const y1 = a.y + CARD_H / 2;
          const x2 = b.x - 5;
          const y2 = b.y + CARD_H / 2;
          const bend = Math.max(40, (x2 - x1) / 2);
          const d = `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`;
          const st = edgeState(e.from, e.to);
          return (
            <g key={`${e.from}->${e.to}`} data-flow-edge={`${e.from}->${e.to}`}>
              <path
                d={d}
                fill="none"
                stroke={st === "done" ? "var(--success)" : st === "active" ? "var(--info)" : "#cfcfcf"}
                strokeOpacity={st === "idle" ? 1 : 0.75}
                strokeWidth={st === "idle" ? 1.5 : 2}
                strokeDasharray={e.kind === "proposed" && st === "idle" ? "5 5" : undefined}
              />
              {st === "active" ? (
                <path d={d} fill="none" stroke="var(--info)" strokeWidth={3} strokeLinecap="round"
                      pathLength={100} strokeDasharray="14 86">
                  <animate attributeName="stroke-dashoffset" from="100" to="0" dur="1.4s" repeatCount="indefinite" />
                </path>
              ) : null}
            </g>
          );
        })}
      </svg>

      {/* Your request */}
      {(() => {
        const p = layout.pos.get(REQUEST_ID)!;
        return (
          <button
            type="button"
            data-flow-node="request"
            onClick={() => onSelect(null)}
            className={cn(
              "absolute flex flex-col overflow-hidden rounded-xl border bg-card text-left shadow-sm transition hover:shadow-md",
              selected === null ? "border-foreground/30 ring-2 ring-foreground/10" : "border-border",
            )}
            style={{ left: p.x, top: p.y, width: CARD_W, height: CARD_H }}
          >
            <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border px-3">
              <span className="inline-flex size-6 items-center justify-center rounded-md bg-foreground text-background">
                <MessageSquare className="size-3.5" />
              </span>
              <span className="text-sm font-medium text-foreground">Your request</span>
            </div>
            <p className="line-clamp-4 px-3 pt-2 text-xs leading-relaxed text-foreground/80">{goal}</p>
            {projectName ? (
              <div className="mt-auto px-3 pb-2"><Row label="Project" value={projectName} /></div>
            ) : null}
            <Handle side="right" />
          </button>
        );
      })()}

      {steps.map((s) => {
        const p = layout.pos.get(s.id);
        if (!p) return null;
        const dur = stepDuration(s, now);
        const isSel = selected === s.id;
        return (
          <button
            key={s.id}
            type="button"
            data-flow-node={s.id}
            onClick={() => onSelect(isSel ? null : s.id)}
            className={cn(
              "group absolute flex flex-col rounded-xl border bg-card text-left shadow-sm transition hover:shadow-md",
              s.state === "running" && "border-info/50 shadow-[0_0_0_4px_color-mix(in_srgb,var(--info)_14%,transparent)]",
              s.state === "done" && "border-success/45",
              ["failed", "interrupted", "resume-rejected"].includes(s.state) && "border-destructive/50",
              ["needs-owner", "cancelled"].includes(s.state) && "border-amber-400/70",
              s.state === "pending" && "border-border",
              isSel && "ring-2 ring-foreground/25",
            )}
            style={{ left: p.x, top: p.y, width: CARD_W, height: CARD_H }}
          >
            <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border px-3">
              <RoleIcon role={s.role} />
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">{roleName(s.role)}</span>
              <StateMark state={s.state} />
            </div>
            <div className="space-y-0.5 px-3 pt-1.5">
              <Row label="Model" value={[s.model || "default", s.adapter].filter(Boolean).join(" · ")} />
              <Row label="Step" value={s.id} mono />
              <Row
                label={s.state === "pending" ? "Status" : "Activity"}
                value={s.state === "pending"
                  ? "Waiting"
                  : [STATE_LABEL[s.state] ?? s.state, s.toolCount ? `${s.toolCount} tool calls` : "", dur].filter(Boolean).join(" · ")}
              />
            </div>
            <p
              className={cn(
                "mx-3 mt-1.5 line-clamp-2 border-t border-dashed border-border pt-1.5 text-[11px] leading-snug",
                s.state === "running" ? "text-info" : "text-muted-foreground",
              )}
            >
              {s.state === "pending"
                ? s.goal
                : s.state === "done"
                  ? s.latest || "Finished"
                  : s.reason && s.state !== "running"
                    ? s.reason
                    : s.latest || "Starting..."}
            </p>
            <Handle side="left" />
            <Handle side="right" />
          </button>
        );
      })}

      {/* Result */}
      {(() => {
        const p = layout.pos.get(RESULT_ID)!;
        const done = taskState === "done";
        const failed = ["failed", "interrupted", "gate-failed"].includes(taskState);
        return (
          <button
            type="button"
            data-flow-node="result"
            onClick={() => onSelect(null)}
            className={cn(
              "absolute flex flex-col overflow-hidden rounded-xl border bg-card text-left shadow-sm transition hover:shadow-md",
              done ? "border-success/45" : failed ? "border-destructive/50" : "border-border",
            )}
            style={{ left: p.x, top: p.y, width: CARD_W, height: CARD_H }}
          >
            <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border px-3">
              <span className={cn("inline-flex size-6 items-center justify-center rounded-md text-white",
                done ? "bg-success" : failed ? "bg-destructive" : "bg-muted-foreground/50")}>
                <Flag className="size-3.5" />
              </span>
              <span className="flex-1 text-sm font-medium text-foreground">Result</span>
              <StateMark state={done ? "done" : failed ? "failed" : taskState === "needs-owner" ? "needs-owner" : steps.some((s) => s.state === "running") ? "running" : "pending"} />
            </div>
            <div className="space-y-0.5 px-3 pt-1.5">
              <Row label="Agents" value={`${doneCount} of ${steps.length} done`} />
              <Row label="Changed files" value={changedFiles === null ? "recorded at the end" : String(changedFiles)} />
            </div>
            <p className="mx-3 mt-1.5 line-clamp-2 border-t border-dashed border-border pt-1.5 text-[11px] leading-snug text-muted-foreground">
              {done
                ? "Open to read the answer and bring changes into your project."
                : failed
                  ? "The task stopped. Open the failed agent to see why."
                  : taskState === "needs-owner"
                    ? "An agent is waiting for your decision."
                    : "Fills in when the agents finish."}
            </p>
            <Handle side="left" />
          </button>
        );
      })()}
    </div>
  );
}
