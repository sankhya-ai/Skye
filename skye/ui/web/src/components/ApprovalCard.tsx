// Adapted from block/buzz desktop/src/features/workflows/ui/WorkflowApprovalCard.tsx
// (Apache-2.0). Buzz's card is read-only ("actions are not yet available in
// Desktop"); here the card is functional: approving records the one-time,
// fingerprint-bound approval exactly like `skye approve` does.
import { useState } from "react";
import { approveStep } from "../api";
import { humanError } from "../lib";
import { Button } from "./ui/Button";

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
        Approving runs this contract once. Policy unchanged.
      </p>
      {error ? <p className="mb-2 text-xs text-destructive">{error}</p> : null}
      <Button
        variant="warning"
        size="sm"
        loading={busy}
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await approveStep(missionId, step);
            onApproved();
          } catch (e) {
            setError(humanError(e, "approval"));
          } finally {
            setBusy(false);
          }
        }}
      >
        Approve this step once
      </Button>
    </div>
  );
}
