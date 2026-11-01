// Adapted from block/buzz desktop/src/features/workflows/ui/WorkflowRunTrace.tsx
// (Apache-2.0): per-step trace cards with a status icon, monospace step id,
// status badge, reason and output blocks. Skye additions: journal-driven live
// states (dispatched/interrupted/redispatch), dynamic steps, and a functional
// approval card for needs-owner pauses.
import {
  Check,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Clock,
  Loader2,
  Send,
  SkipForward,
  X,
} from "lucide-react";
import { useState } from "react";
import type { JournalEntry, MissionDetail } from "../api";
import { cn, formatTime } from "../lib";
import { ApprovalCard } from "./ApprovalCard";
import { Badge } from "./ui/Badge";

type StepState = {
  state: string;
  status?: string;
  reason?: string;
  output?: string;
  at?: number;
};

function statesFromJournal(journal: JournalEntry[]): Map<string, StepState> {
  const map = new Map<string, StepState>();
  for (const entry of journal) {
    if (!entry.step) continue;
    const prev = map.get(entry.step) ?? { state: "pending" };
    const next: StepState = { ...prev };
    switch (entry.event) {
      case "dispatched":
        next.state = "dispatched";
        next.reason = undefined;  // a new attempt supersedes the previous one's reason
        next.status = undefined;
        next.at = entry.ts;
        break;
      case "reattached":
        next.state = "dispatched";
        next.reason = undefined;  // a new attempt supersedes the previous one's reason
        next.status = undefined;
        next.at = entry.ts;
        break;
      case "redispatch":
        next.state = "dispatched";
        next.reason = undefined;  // a new attempt supersedes the previous one's reason
        next.status = undefined;
        next.at = entry.ts;
        break;
      case "settled":
        next.state = entry.status === "done" ? "done" : "failed";
        next.status = entry.status;
        next.reason = entry.reason;
        next.output = entry.output;
        next.at = entry.ts;
        break;
      case "needs-owner":
        next.state = "needs-owner";
        next.reason = entry.reason;
        next.at = entry.ts;
        break;
      case "interrupted":
        next.state = "interrupted";
        next.reason = entry.reason;
        next.at = entry.ts;
        break;
      case "skipped":
        next.state = "skipped";
        next.reason = entry.reason;
        next.at = entry.ts;
        break;
      default:
        break;
    }
    map.set(entry.step, next);
  }
  return map;
}

function StepIcon({ state }: { state: string }) {
  const cls = "mt-0.5 size-4 shrink-0";
  switch (state) {
    case "done":
      return <Check className={cn(cls, "text-success")} />;
    case "failed":
      return <X className={cn(cls, "text-destructive")} />;
    case "skipped":
      return <SkipForward className={cn(cls, "text-muted-foreground")} />;
    case "dispatched":
      return <Loader2 className={cn(cls, "animate-spin text-info")} />;
    case "needs-owner":
      return <CircleAlert className={cn(cls, "text-amber-600")} />;
    case "interrupted":
      return <CircleAlert className={cn(cls, "text-destructive")} />;
    default:
      return <Clock className={cn(cls, "text-muted-foreground")} />;
  }
}

function stateBadge(state: string, approved?: boolean) {
  if (state === "needs-owner" && approved) {
    return <Badge variant="success">approved</Badge>;
  }
  switch (state) {
    case "done":
      return <Badge variant="success">done</Badge>;
    case "failed":
      return <Badge variant="destructive">failed</Badge>;
    case "dispatched":
      return <Badge variant="info">running</Badge>;
    case "needs-owner":
      return <Badge className="border-amber-500/30 bg-amber-500/10 text-amber-700">needs owner</Badge>;
    case "interrupted":
      return <Badge variant="destructive">interrupted</Badge>;
    case "skipped":
      return <Badge variant="secondary">skipped</Badge>;
    default:
      return <Badge variant="outline">pending</Badge>;
  }
}

export function MissionRunTrace({
  detail,
  onChanged,
  onRedispatch,
}: {
  detail: MissionDetail;
  onChanged: () => void;
  onRedispatch: (step: string) => void;
}) {
  const states = statesFromJournal(detail.journal);
  const [openOutputs, setOpenOutputs] = useState<Record<string, boolean>>({});
  const authored = detail.mission.steps.map((s) => ({
    id: String(s.id),
    role: String(s.role ?? ""),
    adapter: String(s.adapter ?? ""),
    dynamic: false,
  }));
  const dynamic = detail.dynamic_steps.map((s) => ({
    id: String(s.id),
    role: String(s.role ?? ""),
    adapter: String(s.adapter ?? ""),
    dynamic: true,
  }));
  const steps = [...authored, ...dynamic];
  const pendingByStep = new Map(detail.pending_contracts.map((c) => [c.step, c]));

  return (
    <div className="space-y-3" data-testid="mission-run-trace">
      {steps.map((step) => {
        const st = states.get(step.id) ?? { state: "pending" };
        const pending = pendingByStep.get(step.id);
        const open = openOutputs[step.id] ?? false;
        return (
          <div
            key={step.id}
            className="rounded-xl border border-border bg-card/40 p-4"
            data-testid={`trace-step-${step.id}`}
            data-state={st.state}
          >
            <div className="flex items-start gap-3">
              <StepIcon state={st.state} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm">{step.id}</span>
                  {step.dynamic ? (
                    <Badge variant="info" title="proposed by a worker at run time">
                      dynamic
                    </Badge>
                  ) : null}
                  <span className="text-xs text-muted-foreground">
                    {step.role} · {step.adapter}
                  </span>
                  {stateBadge(st.state, detail.approved)}
                  {st.at ? (
                    <span className="text-xs text-muted-foreground">{formatTime(st.at)}</span>
                  ) : null}
                </div>
                {st.state === "needs-owner" && detail.approved ? (
                  <div
                    className="mt-2 rounded-lg border border-success/30 bg-success/5 p-3"
                    data-testid="approved-cue"
                  >
                    <p className="text-xs text-success">
                      Approved for one attempt. Resume the mission to continue.
                    </p>
                  </div>
                ) : st.reason ? (
                  <p className="mt-2 text-xs text-muted-foreground">{st.reason}</p>
                ) : null}
                {st.state === "interrupted" ? (
                  <div className="mt-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3">
                    <p className="mb-2 text-xs text-muted-foreground">
                      The run stopped mid-step and cannot reattach. Repeating work with unknown
                      partial effects is your call: redispatch re-runs exactly this step, or reset
                      the mission to start clean.
                    </p>
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-1 text-xs font-medium text-red-200 transition hover:bg-destructive/20"
                      onClick={() => onRedispatch(step.id)}
                    >
                      <Send className="size-3" /> Redispatch this step
                    </button>
                  </div>
                ) : null}
                {st.output ? (
                  <div className="mt-2">
                    <button
                      type="button"
                      className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                      onClick={() => setOpenOutputs((o) => ({ ...o, [step.id]: !open }))}
                    >
                      {open ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
                      output
                    </button>
                    {open ? (
                      <pre className="mt-1 max-h-64 overflow-auto rounded-md bg-muted/50 p-3 text-xs whitespace-pre-wrap">
                        {st.output}
                      </pre>
                    ) : null}
                  </div>
                ) : null}
                {pending ? (
                  <div className="mt-2">
                    <ApprovalCard
                      missionId={detail.mission.id}
                      step={step.id}
                      contract={pending}
                      onApproved={onChanged}
                    />
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
