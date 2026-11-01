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
import { cn, formatRelative, formatTime } from "../lib";
import { ApprovalCard } from "./ApprovalCard";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";

type StepState = {
  state: string;
  status?: string;
  reason?: string;
  output?: string;
  at?: number;
  reviewRound?: number;
  reviewFeedback?: string;
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
      case "cancelled":
      case "resume-rejected":
      case "interrupted":
        next.state = entry.event;
        next.reason = entry.reason;
        next.at = entry.ts;
        break;
      case "skipped":
        next.state = "skipped";
        next.reason = entry.reason;
        next.at = entry.ts;
        break;
      case "review-round":
        // The reviewer sent the step back with feedback; the step re-runs with it. A
        // journaled redo edge, bounded by max_review_rounds - never a silent retry.
        next.reviewRound = typeof entry.round === "number" ? entry.round : next.reviewRound;
        next.reviewFeedback = typeof entry.feedback === "string" ? entry.feedback : next.reviewFeedback;
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
      return <Badge variant="warning">needs you</Badge>;
    case "cancelled":
      return <Badge variant="warning">stopped</Badge>;
    case "resume-rejected":
      return <Badge variant="warning">session resume rejected</Badge>;
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
  onRedispatchFresh,
  onFollowUp,
  busy,
}: {
  busy: boolean;
  detail: MissionDetail;
  onChanged: () => void;
  onRedispatch: (step: string) => void;
  onRedispatchFresh: (step: string) => void;
  onFollowUp: (step: string, text: string) => Promise<boolean>;
}) {
  const [followUps, setFollowUps] = useState<Record<string, string>>({});
  const [followUpOpen, setFollowUpOpen] = useState<Record<string, boolean>>({});
  // Session vocabulary from the authoritative status read (redacted server-side), keyed
  // by step id: continuity, recorded-session presence, follow-up eligibility, and the
  // latest streamed progress line.
  const statusByStep = new Map((detail.status.steps ?? []).map((s) => [s.id, s]));
  const states = statesFromJournal(detail.journal);
  const overall = detail.status.status;
  const liveRunning = Boolean(detail.live?.running);
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
  const fingerprintByStep = new Map<string, string>();
  for (const entry of detail.journal) {
    if (entry.event === "needs-owner" && entry.step && typeof entry.fingerprint === "string") {
      fingerprintByStep.set(entry.step, entry.fingerprint);
    }
  }

  return (
    <div className="space-y-3" data-testid="mission-run-trace">
      {steps.map((step) => {
        const st = states.get(step.id) ?? { state: "pending" };
        const session = statusByStep.get(step.id);
        // A dispatch recorded before the mission stopped can never settle: show the
        // mission's outcome on the step instead of a spinner that implies live work.
        // The journal keeps the raw dispatch record; this is display-only.
        const staleDispatch =
          st.state === "dispatched" && !liveRunning && (overall === "interrupted" || overall === "failed");
        const display = staleDispatch ? overall : st.state;
        const retryStep = !liveRunning && (staleDispatch || ["cancelled", "resume-rejected"].includes(st.state));
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
              <StepIcon state={display} />
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
                  {session?.continuity === "resumed" ? (
                    <Badge variant="info" title="continued the vendor session recorded by this step's earlier run">
                      session resumed
                    </Badge>
                  ) : session?.continuity === "reattached" ? (
                    <Badge variant="info" title="reattached to the still-running job">
                      reattached
                    </Badge>
                  ) : null}
                  {stateBadge(display, detail.approved)}
                  {st.at ? (
                    <span className="text-xs text-muted-foreground">{formatRelative(st.at)}</span>
                  ) : null}
                </div>
                {staleDispatch ? (
                  <p className="mt-1 text-xs text-muted-foreground">
                    This step was running when the mission stopped. Its changes may be
                    partially applied.
                  </p>
                ) : null}
                {st.reviewRound ? (
                  <p className="mt-1 text-xs text-muted-foreground">
                    Review round {st.reviewRound}
                    {st.reviewFeedback ? `: ${st.reviewFeedback}` : ""}
                  </p>
                ) : null}
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
                  <p className="mt-2 text-xs text-muted-foreground">
                    {st.state === "needs-owner"
                      ? // The dispatcher appends the contract fingerprint + CLI hint for
                        // terminal users; the card below carries the action, the journal
                        // keeps the full text.
                        st.reason.split(" Contract fingerprint")[0].replace(/^./, (c) => c.toUpperCase())
                      : st.reason}
                  </p>
                ) : null}
                {retryStep ? (
                  <div className="mt-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3">
                    <p className="mb-2 text-xs text-muted-foreground">
                      Running it again re-runs exactly this step, continuing its recorded
                      session when one exists. "Start fresh instead" starts only this step
                      without its recorded session. Neither option resets the mission or
                      undoes file changes.
                    </p>
                    <div className="flex flex-wrap items-center gap-2">
                      <Button
                        variant="danger-outline"
                        size="sm"
                        disabled={busy}
                        onClick={() => onRedispatch(step.id)}
                      >
                        <Send className="size-3" /> Run this step again
                      </Button>
                      {session?.has_session ? (
                        <button
                          type="button"
                          className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
                          disabled={busy}
                          onClick={() => onRedispatchFresh(step.id)}
                        >
                          Start fresh instead (abandons the recorded session)
                        </button>
                      ) : null}
                    </div>
                  </div>
                ) : null}
                {st.state === "dispatched" && liveRunning && session?.progress ? (
                  // The latest line the worker streamed, delivered via the journal on the
                  // poll cadence - recent progress, never a claim of live attachment.
                  <p className="mt-1 truncate text-xs text-muted-foreground" data-progress={step.id}>
                    Latest progress: {session.progress}
                  </p>
                ) : null}
                {session?.followupable || (liveRunning && st.state === "dispatched") ? (
                  <div className="mt-2" data-followup={step.id}>
                    {liveRunning && st.state !== "dispatched" ? (
                      <p className="text-xs text-muted-foreground/80">
                        Follow-ups open once the mission settles.
                      </p>
                    ) : liveRunning ? (
                      followUpOpen[step.id] ? (
                        <div className="rounded-lg border border-border bg-muted/30 p-3">
                          <p className="mb-2 text-xs text-muted-foreground">
                            Submit a follow-up to this running step. Submission does not
                            confirm that the provider received or used it. Unsupported
                            protocols return an error without starting a fresh session.
                          </p>
                          <div className="flex items-center gap-2">
                            <input
                              type="text"
                              value={followUps[step.id] ?? ""}
                              onChange={(e) =>
                                setFollowUps((f) => ({ ...f, [step.id]: e.target.value }))}
                              placeholder="Follow up on the running step..."
                              className="h-8 min-w-0 flex-1 rounded-md border border-border bg-card px-2 text-xs outline-none"
                            />
                            <Button
                              variant="outline"
                              size="sm"
                              disabled={busy || !(followUps[step.id] ?? "").trim()}
                              onClick={() => {
                                const text = (followUps[step.id] ?? "").trim();
                                if (!text) return;
                                void onFollowUp(step.id, text).then((sent) => {
                                  if (!sent) return;
                                  setFollowUps((f) => ({ ...f, [step.id]: "" }));
                                  setFollowUpOpen((o) => ({ ...o, [step.id]: false }));
                                });
                              }}
                            >
                              <Send className="size-3" /> Send to running step
                            </Button>
                          </div>
                        </div>
                      ) : (
                        <button
                          type="button"
                          className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
                          onClick={() => setFollowUpOpen((o) => ({ ...o, [step.id]: true }))}
                        >
                          Follow up on the running step
                        </button>
                      )
                    ) : followUpOpen[step.id] ? (
                      <div className="rounded-lg border border-border bg-muted/30 p-3">
                        <p className="mb-2 text-xs text-muted-foreground">
                          Starts a new run that continues this step&apos;s recorded session -
                          not a live message to a running worker.
                        </p>
                        <div className="flex items-center gap-2">
                          <input
                            type="text"
                            value={followUps[step.id] ?? ""}
                            onChange={(e) =>
                              setFollowUps((f) => ({ ...f, [step.id]: e.target.value }))}
                            placeholder="Follow up on this step..."
                            className="h-8 min-w-0 flex-1 rounded-md border border-border bg-card px-2 text-xs outline-none"
                          />
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={busy || !(followUps[step.id] ?? "").trim()}
                            onClick={() => {
                              const text = (followUps[step.id] ?? "").trim();
                              if (!text) return;
                              void onFollowUp(step.id, text).then((sent) => {
                                  if (!sent) return;
                                setFollowUps((f) => ({ ...f, [step.id]: "" }));
                                setFollowUpOpen((o) => ({ ...o, [step.id]: false }));
                              });
                            }}
                          >
                            <Send className="size-3" /> Send follow-up
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <button
                        type="button"
                        className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
                        onClick={() => setFollowUpOpen((o) => ({ ...o, [step.id]: true }))}
                      >
                        Follow up (continues this step&apos;s session)
                      </button>
                    )}
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
                      fingerprint={fingerprintByStep.get(step.id)}
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
