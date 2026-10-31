// A coding flow as it runs: candidates, their checks, and the owner's controls.
// "Review" shows the exact patch; "Apply" is bound to that patch's hash.

import { Check, CircleDashed, GitMerge, Loader2, X } from "lucide-react";
import { useMemo, useState } from "react";

import { api } from "../../lib/api.ts";
import { parseUnifiedDiff } from "../../lib/diff.ts";
import type { Flow, FlowReview } from "../../lib/types.ts";
import { ConfirmDialog } from "../ConfirmDialog.tsx";
import { DiffHeader, DiffRows } from "../DiffView.tsx";

const STATUS: Record<Flow["status"], string> = {
  preparing: "Prepared",
  working: "Working",
  needs_review: "Needs your choice",
  ready_to_apply: "Ready to review",
  applied: "Applied",
  rolled_back: "Rolled back",
  failed: "No verified patch",
  cancelled: "Cancelled",
};

function CandidateChip({ candidate }: { candidate: Flow["candidates"][number] }) {
  const icon =
    candidate.status === "verified" ? <Check size={12} /> : candidate.status === "working" ? <Loader2 size={12} className="spinner" /> : candidate.status.includes("fail") ? <X size={12} /> : <CircleDashed size={12} />;
  return (
    <span className={`approvalChip${candidate.status === "verified" ? " trusted" : ""}`} title={candidate.verification.map((v) => `${v.command} → ${v.exit_code}`).join("\n")}>
      {icon} {candidate.id.replace("_", " ")} · {candidate.status.replace(/_/g, " ")}
      {candidate.attempts > 1 ? ` · ${candidate.attempts} attempts` : ""}
    </span>
  );
}

export function FlowCard({ flow, onChange }: { flow: Flow; onChange?: (flow: Flow) => void }) {
  const [review, setReview] = useState<FlowReview | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [confirmAction, setConfirmAction] = useState<{
    title: string;
    message: string;
    confirmText?: string;
    action: () => Promise<Flow | FlowReview | void>;
  } | null>(null);
  const files = useMemo(() => (review ? parseUnifiedDiff(review.diff.patch) : []), [review]);

  const act = async (label: string, run: () => Promise<Flow | FlowReview | void>) => {
    setBusy(label);
    setError("");
    try {
      const result = await run();
      if (result && "status" in result) onChange?.(result);
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy("");
    }
  };

  const c = flow.controls;
  return (
    <div className="flowCard">
      <div className="flowHead">
        <GitMerge size={15} />
        <strong>Coding flow {flow.id}</strong>
        <span className={`tag${flow.status === "ready_to_apply" || flow.status === "needs_review" ? " accent" : ""}`}>{STATUS[flow.status]}</span>
        {flow.status === "working" ? <Loader2 size={13} className="spinner faint" /> : null}
      </div>
      <p className="flowTask">{flow.task}</p>
      <div className="approvalMeta">
        {flow.candidates.map((candidate) => (
          <CandidateChip key={candidate.id} candidate={candidate} />
        ))}
      </div>
      {flow.summary ? <p className="stepMessage">{flow.summary}</p> : null}
      {flow.judgment ? <p className="stepMessage">Judge picked {flow.judgment.winner.replace("_", " ")}: {flow.judgment.summary}</p> : null}
      {flow.apply ? <p className="stepMessage">Merged as <code>{flow.apply.applied_commit.slice(0, 12)}</code>.</p> : null}
      {review ? (
        <div className="flowReview">
          {files.map((file) => (
            <div key={file.path} className="diff">
              <DiffHeader path={file.path} added={file.added} removed={file.removed} />
              <DiffRows rows={file.rows} />
            </div>
          ))}
        </div>
      ) : null}
      {error ? <p className="stepMessage err">{error}</p> : null}
      <div className="approvalActions">
        {c.can_review && !review ? (
          <button className="pill ink" disabled={!!busy} onClick={() => act("review", async () => setReview(await api.flowReview(flow.id)))}>
            Review patch
          </button>
        ) : null}
        {c.can_apply && review ? (
          <button className="pill ink" disabled={!!busy} onClick={() => act("apply", () => api.flowApply(flow.id, review.diff.sha256))}>
            {busy === "apply" ? "Applying…" : "Apply to checkout"}
          </button>
        ) : null}
        {c.can_select
          ? flow.candidates
              .filter((candidate) => candidate.status === "verified")
              .map((candidate) => (
                <button key={candidate.id} className="pill" disabled={!!busy} onClick={() => act("select", () => api.flowSelect(flow.id, candidate.id))}>
                  Use {candidate.id.replace("_", " ")}
                </button>
              ))
          : null}
        {c.can_start ? <button className="pill" disabled={!!busy} onClick={() => act("start", () => api.flowAction(flow.id, "start"))}>{flow.status === "preparing" ? "Start" : "Resume"}</button> : null}
        {c.can_stop ? <button className="pill" disabled={!!busy} onClick={() => act("stop", () => api.flowAction(flow.id, "stop"))}>Stop</button> : null}
        {c.can_rollback && flow.apply ? (
          <button
            className="pill danger"
            disabled={!!busy}
            onClick={() =>
              setConfirmAction({
                title: "Roll back flow",
                message: "Revert the merge this flow applied? The checkout will be returned to the pre-merge state.",
                confirmText: "Roll back",
                action: () => api.flowRollback(flow.id, flow.apply!.applied_commit),
              })
            }
          >
            Roll back
          </button>
        ) : null}
        {c.can_cancel ? (
          <button
            className="pill danger"
            disabled={!!busy}
            onClick={() =>
              setConfirmAction({
                title: "Discard candidates",
                message: "Discard this flow's candidates and stop generation?",
                confirmText: "Discard",
                action: () => api.flowAction(flow.id, "cancel"),
              })
            }
          >
            Discard
          </button>
        ) : null}
      </div>
      {confirmAction ? (
        <ConfirmDialog
          title={confirmAction.title}
          message={confirmAction.message}
          confirmText={confirmAction.confirmText ?? "Confirm"}
          onClose={() => setConfirmAction(null)}
          onConfirm={async () => {
            const { action } = confirmAction;
            setConfirmAction(null);
            await act("confirm", action);
          }}
        />
      ) : null}
    </div>
  );
}
