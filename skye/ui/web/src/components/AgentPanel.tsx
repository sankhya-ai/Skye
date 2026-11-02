// One agent's side of the task, opened from its workflow card: what it was asked, what it
// said while working (streamed from the journal), the tools it used, its final answer -
// and a composer to keep talking to it. A message to a running agent is queued on its
// live input for its next turn; to a finished agent it starts a new run that continues
// the same recorded session, so the agent answers with its full context.
import { ArrowUp, ChevronDown, ChevronRight, Copy, RotateCcw, UserCheck, Wrench, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { MissionDetail } from "../api";
import { cn, formatTime } from "../lib";
import { formatDuration, roleName, type ChatItem, type StepView } from "../task/model";
import { ApprovalCard } from "./ApprovalCard";
import { MarkdownView } from "./MarkdownView";
import { RoleIcon, STATE_LABEL, StateMark } from "./TaskCanvas";
import { Button } from "./ui/Button";

function ToolGroup({ calls }: { calls: { name: string; detail?: string }[] }) {
  const [open, setOpen] = useState(false);
  const names = [...new Set(calls.map((c) => c.name))];
  return (
    <div className="pl-8">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex max-w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-[11px] text-muted-foreground hover:bg-muted/60"
      >
        {open ? <ChevronDown className="size-3 shrink-0" /> : <ChevronRight className="size-3 shrink-0" />}
        <Wrench className="size-3 shrink-0" />
        <span className="truncate">
          Used {calls.length} tool{calls.length === 1 ? "" : "s"} · {names.slice(0, 4).join(", ")}
          {names.length > 4 ? ` +${names.length - 4}` : ""}
        </span>
      </button>
      {open ? (
        <ol className="mt-0.5 space-y-0.5 border-l border-border pl-3">
          {calls.map((c, i) => (
            <li key={i} className="flex gap-2 font-mono text-[10.5px] leading-relaxed">
              <span className="shrink-0 text-foreground/80">{c.name}</span>
              {c.detail ? <span className="truncate text-muted-foreground" title={c.detail}>{c.detail}</span> : null}
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}

function AgentBubble({ role, children }: { role: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2.5">
      <div className="pt-0.5"><RoleIcon role={role} size="sm" /></div>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

function Answer({ text, partial }: { text: string; partial?: boolean }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="rounded-xl border border-border bg-background">
      <div className="flex items-center justify-between border-b border-border px-3 py-1.5">
        <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Answer</span>
        <button
          type="button"
          className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
          onClick={() => {
            void navigator.clipboard?.writeText(text).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1200);
            });
          }}
        >
          <Copy className="size-3" /> {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <div className="px-3 py-2.5">
        {partial ? (
          <p className="mb-2 text-[11px] text-muted-foreground">
            This run was recorded before full answers were kept, so only its ending survives.
          </p>
        ) : null}
        <MarkdownView source={text} />
      </div>
    </div>
  );
}

export function ChatItemView({ item, step, proposed, onOpenStep }: {
  item: ChatItem;
  step: StepView;
  proposed: StepView[];
  onOpenStep: (id: string) => void;
}) {
  switch (item.type) {
    case "run":
      return (
        <div className="flex items-center gap-2 py-1 text-[10.5px] text-muted-foreground/80">
          <span className="h-px flex-1 bg-border" />
          {item.label} · {formatTime(item.ts)}
          <span className="h-px flex-1 bg-border" />
        </div>
      );
    case "user":
      return (
        <div className="flex justify-end">
          <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-foreground px-3 py-2 text-sm text-background">
            {item.text}
          </p>
        </div>
      );
    case "agent":
      return (
        <AgentBubble role={step.role}>
          <MarkdownView source={item.text} />
        </AgentBubble>
      );
    case "tools":
      return <ToolGroup calls={item.calls} />;
    case "plan":
      return (
        <AgentBubble role={step.role}>
          <div className="rounded-xl border border-border bg-background p-3">
            <p className="text-sm text-foreground">
              Planned the workflow: {proposed.length} agent{proposed.length === 1 ? "" : "s"}
            </p>
            <ul className="mt-2 space-y-1.5">
              {proposed.map((p) => (
                <li key={p.id}>
                  <button
                    type="button"
                    onClick={() => onOpenStep(p.id)}
                    className="flex w-full items-start gap-2 rounded-lg border border-border px-2 py-1.5 text-left hover:bg-muted/50"
                  >
                    <RoleIcon role={p.role} size="sm" />
                    <span className="min-w-0">
                      <span className="block text-xs font-medium text-foreground">{roleName(p.role)}</span>
                      <span className="line-clamp-2 text-[11px] text-muted-foreground">{p.goal}</span>
                    </span>
                  </button>
                </li>
              ))}
              {proposed.length === 0 ? (
                <li className="text-xs text-muted-foreground">No further steps were needed.</li>
              ) : null}
            </ul>
          </div>
        </AgentBubble>
      );
    case "answer":
      return (
        <AgentBubble role={step.role}>
          <Answer text={item.text} partial={item.partial} />
        </AgentBubble>
      );
    case "status":
      return (
        <p
          className={cn(
            "rounded-lg border px-3 py-2 text-xs",
            item.tone === "error" && "border-destructive/30 bg-destructive/5 text-destructive",
            item.tone === "warn" && "border-amber-400/50 bg-amber-50 text-amber-800",
            item.tone === "info" && "border-border bg-muted/40 text-muted-foreground",
          )}
        >
          {item.text}
        </p>
      );
  }
}

export function AgentPanel({
  step,
  steps,
  detail,
  busy,
  note,
  lead,
  onClose,
  onOpenStep,
  onFollowUp,
  onRedispatch,
  onRedispatchFresh,
  onHandoff,
  onChanged,
}: {
  step: StepView;
  steps: StepView[];
  detail: MissionDetail;
  busy: boolean;
  note: string | null;
  /** The step a failure here would hand to: the team lead, or the planner that proposed it. */
  lead: StepView | null;
  onClose: () => void;
  onOpenStep: (id: string) => void;
  onFollowUp: (step: string, text: string) => Promise<boolean>;
  onRedispatch: (step: string) => void;
  onRedispatchFresh: (step: string) => void;
  onHandoff: (step: string) => void;
  onChanged: () => void;
}) {
  const [tab, setTab] = useState<"chat" | "runs" | "brief">("chat");
  const [draft, setDraft] = useState("");
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const live = Boolean(detail.live?.running);
  const running = step.state === "running" && live;
  const proposed = steps.filter((s) => s.proposedBy === step.id);
  const pending = detail.pending_contracts.find((c) => c.step === step.id);
  const fingerprint = [...detail.journal].reverse()
    .find((e) => e.event === "needs-owner" && e.step === step.id && typeof e.fingerprint === "string")
    ?.fingerprint as string | undefined;

  useEffect(() => { setTab("chat"); pinned.current = true; }, [step.id]);
  useEffect(() => {
    const el = scroller.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [step.items.length, step.id, tab, running]);

  const canSend = running || (!live && step.followupable);
  const placeholder = running
    ? `Message the ${roleName(step.role).toLowerCase()} while it works...`
    : canSend
      ? `Ask the ${roleName(step.role).toLowerCase()} a follow-up...`
      : live
        ? "You can chat with this agent once the task's current run settles"
        : "This agent has no session to continue yet";
  const send = async () => {
    const text = draft.trim();
    if (!text || !canSend) return;
    pinned.current = true;
    if (await onFollowUp(step.id, text)) setDraft("");
  };
  const retry = !live && ["interrupted", "cancelled", "resume-rejected"].includes(step.state);
  const elapsed = step.startedAt
    ? formatDuration((step.endedAt ?? (running ? Date.now() / 1000 : step.startedAt)) - step.startedAt)
    : "";

  return (
    <div className="flex h-full min-h-0 flex-col" data-agent-panel={step.id}>
      <div className="flex items-center gap-2.5 border-b border-border px-4 py-3">
        <RoleIcon role={step.role} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-foreground">{roleName(step.role)}</p>
          <p className="truncate text-[11px] text-muted-foreground">
            {[step.model, step.adapter, step.id].filter(Boolean).join(" · ")}
          </p>
        </div>
        <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <StateMark state={step.state} />
          {STATE_LABEL[step.state] ?? step.state}
          {elapsed ? ` · ${elapsed}` : ""}
        </span>
        <button type="button" onClick={onClose} aria-label="Close agent panel"
                className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground">
          <X className="size-4" />
        </button>
      </div>
      <div className="flex gap-1 border-b border-border px-3">
        {(["chat", "runs", "brief"] as const).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={cn(
              "-mb-px border-b-2 px-2 py-2 text-xs capitalize",
              tab === t ? "border-foreground text-foreground" : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {t === "chat" ? "Conversation" : t === "runs" ? "Runs" : "Brief"}
          </button>
        ))}
      </div>

      {tab === "runs" ? (
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {step.runs.length === 0 ? (
            <p className="text-xs text-muted-foreground">No runs yet - this agent starts when the steps before it finish.</p>
          ) : (
            <ol className="space-y-2">
              {[...step.runs].reverse().map((run, i) => (
                <li key={i} className="rounded-lg border border-border px-3 py-2">
                  <div className="flex items-center gap-2 text-xs">
                    <StateMark state={run.state} />
                    <span className="font-medium text-foreground">{STATE_LABEL[run.state] ?? run.state}</span>
                    <span className="text-muted-foreground">{run.label}</span>
                    <span className="flex-1" />
                    <span className="text-[11px] text-muted-foreground">
                      {formatTime(run.startedAt)}
                      {run.endedAt ? ` · ${formatDuration(run.endedAt - run.startedAt)}` : ""}
                    </span>
                  </div>
                  {run.reason && run.state !== "done" ? (
                    <p className={cn("mt-1 text-[11px] leading-snug",
                      ["failed", "interrupted", "resume-rejected"].includes(run.state)
                        ? "text-destructive" : "text-muted-foreground")}>
                      {run.reason}
                    </p>
                  ) : null}
                </li>
              ))}
            </ol>
          )}
        </div>
      ) : tab === "brief" ? (
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Goal</p>
          <p className="mt-1 whitespace-pre-wrap text-sm text-foreground">{step.goal}</p>
          {step.brief && step.brief !== step.goal ? (
            <>
              <p className="mt-4 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Full brief sent to the agent
              </p>
              <pre className="mt-1 whitespace-pre-wrap rounded-lg bg-muted/50 p-3 font-sans text-xs leading-relaxed text-foreground/85">
                {step.brief}
              </pre>
            </>
          ) : null}
          <p className="mt-4 text-[11px] text-muted-foreground">
            {step.origin === "proposed"
              ? `Added to the workflow by the ${roleName(steps.find((s) => s.id === step.proposedBy)?.role ?? "planner").toLowerCase()}.`
              : "Part of the original task."}
          </p>
        </div>
      ) : (
        <div
          ref={scroller}
          onScroll={(e) => {
            const el = e.currentTarget;
            pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
          }}
          className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4"
        >
          <div className="flex justify-end">
            <div className="max-w-[85%] rounded-2xl rounded-br-md bg-muted px-3 py-2">
              <p className="text-[10.5px] text-muted-foreground">
                {step.role === "pm" ? "You asked" : "Skye asked this agent"}
              </p>
              <p className="mt-0.5 line-clamp-6 whitespace-pre-wrap text-sm text-foreground">{step.goal}</p>
            </div>
          </div>
          {step.items.map((item, i) => (
            <ChatItemView key={i} item={item} step={step} proposed={proposed} onOpenStep={onOpenStep} />
          ))}
          {step.state === "pending" ? (
            <p className="text-center text-xs text-muted-foreground">
              This agent starts when the steps before it finish.
            </p>
          ) : null}
          {pending ? (
            <ApprovalCard missionId={detail.mission.id} step={step.id} contract={pending}
                          fingerprint={fingerprint} onApproved={onChanged} />
          ) : null}
          {!live && step.state === "failed" ? (
            step.handedTo ? (() => {
              const leadStep = steps.find((s) => s.id === step.handedTo);
              const handoffTs = [...detail.journal].reverse()
                .find((e) => e.event === "recovery-handoff" && e.step === step.id)?.ts ?? 0;
              const answered = detail.journal.some(
                (e) => e.event === "recovery-resolved" && e.step === step.id && e.ts > handoffTs);
              // The lead settled once without answering THIS handoff: the engine will not
              // re-open it on its own (each handoff buys one paid answer).
              const unanswered = !answered && detail.journal.some(
                (e) => e.event === "recovery-unaddressed" && e.step === step.id && e.ts > handoffTs);
              const working = Boolean(leadStep && leadStep.state === "running");
              const leadName = step.handedToRole ? roleName(step.handedToRole).toLowerCase() : "lead";
              return (
                <div className="rounded-lg border border-amber-400/50 bg-amber-50 p-3">
                  <p className="text-xs text-amber-900">
                    Handed to the {leadName} - {answered
                      ? "it answered."
                      : working
                        ? "it is working on the recovery now."
                        : unanswered
                          ? "it finished without a recovery or a justified waiver. Hand it over again to have it retry."
                          : "it has not answered yet - resume the task so it can pick the failure up."}
                    {" "}The failure stays on the record; this agent does not run again on its own.
                  </p>
                  {unanswered && !working ? (
                    <div className="mt-2">
                      <Button variant="danger-outline" size="sm" disabled={busy} onClick={() => onHandoff(step.id)}>
                        <UserCheck className="size-3" /> Hand it to the {leadName} again
                      </Button>
                    </div>
                  ) : null}
                  {answered ? (
                    <button type="button" onClick={() => onOpenStep(step.handedTo!)}
                            className="mt-1.5 text-xs text-amber-900 underline underline-offset-2 hover:text-foreground">
                      Open the lead's answer
                    </button>
                  ) : null}
                </div>
              );
            })() : (
              <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3">
                <p className="text-xs font-medium text-destructive">{step.reason || "This run failed."}</p>
                {lead ? (
                  <>
                    <p className="mt-1.5 text-xs text-muted-foreground">
                      Hand it to the {roleName(lead.role).toLowerCase()}: it sees the failure in its
                      own session and decides the recovery - a revised or replacement step, or the
                      goal stands without it. Nothing else re-runs.
                    </p>
                    <div className="mt-2">
                      <Button variant="danger-outline" size="sm" disabled={busy} onClick={() => onHandoff(step.id)}>
                        <UserCheck className="size-3" /> Hand it to the {roleName(lead.role).toLowerCase()}
                      </Button>
                    </div>
                  </>
                ) : (
                  <p className="mt-1.5 text-xs text-muted-foreground">
                    This task has no lead to take the failure - reset the task to start over.
                  </p>
                )}
              </div>
            )
          ) : null}
          {retry ? (
            <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3">
              <p className="mb-2 text-xs text-muted-foreground">
                Run it again to continue this agent's recorded session, or start fresh without
                it. Neither resets the task or undoes file changes.
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <Button variant="danger-outline" size="sm" disabled={busy} onClick={() => onRedispatch(step.id)}>
                  <RotateCcw className="size-3" /> Run again
                </Button>
                {step.hasSession ? (
                  <button type="button" disabled={busy} onClick={() => onRedispatchFresh(step.id)}
                          className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground">
                    Start fresh instead
                  </button>
                ) : null}
              </div>
            </div>
          ) : null}
          {running ? (
            <div className="flex items-center gap-2 pl-8 text-xs text-info">
              <span className="flex gap-0.5">
                <span className="size-1.5 animate-bounce rounded-full bg-info [animation-delay:-0.3s]" />
                <span className="size-1.5 animate-bounce rounded-full bg-info [animation-delay:-0.15s]" />
                <span className="size-1.5 animate-bounce rounded-full bg-info" />
              </span>
              <span className="truncate">{step.latest || "Working"}</span>
            </div>
          ) : null}
        </div>
      )}

      <div className="border-t border-border bg-muted/40 p-3">
        {note ? <p className="mb-2 text-[11px] text-muted-foreground">{note}</p> : null}
        <div className={cn("flex items-end gap-2 rounded-xl border border-[#d6d6d6] bg-card px-3 py-2.5 shadow-sm transition-[border-color,box-shadow] focus-within:border-foreground/40 focus-within:shadow-[0_0_0_3px_rgba(0,0,0,0.06)]",
                           !canSend && "opacity-60")}>
          <textarea
            value={draft}
            disabled={!canSend || busy}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            rows={Math.min(6, Math.max(1, draft.split("\n").length))}
            placeholder={placeholder}
            className="min-h-6 flex-1 resize-none bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          <button
            type="button"
            aria-label="Send"
            disabled={!canSend || busy || !draft.trim()}
            onClick={() => void send()}
            className="inline-flex size-7 shrink-0 items-center justify-center rounded-lg bg-foreground text-background disabled:opacity-30"
          >
            <ArrowUp className="size-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
