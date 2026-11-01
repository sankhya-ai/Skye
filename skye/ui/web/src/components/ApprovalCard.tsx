// Adapted from block/buzz desktop/src/features/workflows/ui/WorkflowApprovalCard.tsx
// (Apache-2.0). Buzz's card is read-only ("actions are not yet available in
// Desktop"); here the card is functional: approving records the one-time,
// fingerprint-bound approval exactly like `skye approve` does.
import { useState } from "react";
import { approveStep } from "../api";

export function ApprovalCard({
  missionId,
  step,
  contract,
  onApproved,
}: {
  missionId: string;
  step: string;
  contract: Record<string, unknown>;
  onApproved: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <div
      className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3"
      data-testid="approval-card"
    >
      <p className="mb-2 text-sm font-medium">Approval required</p>
      <p className="mb-1 text-xs text-muted-foreground">
        Step <span className="font-mono">{step}</span> is paused for the owner.
      </p>
      <p className="mb-2 text-xs text-muted-foreground">
        Adapter: {String(contract.adapter ?? "?")} · Role: {String(contract.role ?? "?")}
      </p>
      <p className="mb-2 text-xs text-muted-foreground">
        Approving runs exactly this contract once, then the mission resumes. It never widens the
        standing policy and never approves a second run.
      </p>
      {error ? <p className="mb-2 text-xs text-destructive">{error}</p> : null}
      <button
        type="button"
        disabled={busy}
        className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-1 text-xs font-medium text-amber-200 transition hover:bg-amber-500/20 disabled:opacity-50"
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await approveStep(missionId, step);
            onApproved();
          } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Approving..." : "Approve this step once"}
      </button>
    </div>
  );
}
