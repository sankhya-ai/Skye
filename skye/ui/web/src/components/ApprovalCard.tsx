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
  fingerprint,
  onApproved,
}: {
  missionId: string;
  step: string;
  contract: Record<string, unknown>;
  fingerprint?: string;
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
        Step <span className="font-mono">{step}</span> is waiting on your approval.
      </p>
      {contract.goal ? (
        <p className="mb-1 text-xs font-medium text-foreground">{String(contract.goal)}</p>
      ) : null}
      <p className="mb-2 text-xs text-muted-foreground">
        {String(contract.adapter ?? "?")} · {String(contract.role ?? "?")}
        {contract.budget && typeof contract.budget === "object"
          ? ` · up to ${String((contract.budget as Record<string, unknown>).max_steps ?? "?")} steps`
          : ""}
      </p>
      <details className="mb-2">
        <summary className="cursor-pointer text-xs text-muted-foreground/80 hover:text-foreground">
          What you're approving
        </summary>
        <dl className="mt-1 space-y-0.5 font-mono text-[11px] text-muted-foreground">
          {fingerprint ? <div>fingerprint {fingerprint}</div> : null}
          {contract.repo ? <div>repo {String(contract.repo)}</div> : null}
          <div>
            checks{" "}
            {Array.isArray(contract.gates) && contract.gates.length > 0
              ? contract.gates.map((g) => String(g)).join(", ")
              : "none"}
          </div>
        </dl>
        {contract.brief ? (
          <pre className="mt-1 max-h-32 overflow-auto rounded-sm bg-muted/60 p-2 font-mono text-[10px] leading-relaxed text-muted-foreground whitespace-pre-wrap">
            {String(contract.brief)}
          </pre>
        ) : null}
      </details>
      <p className="mb-2 text-xs text-muted-foreground">
        Allows one attempt. Resume to start. Policy unchanged.
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
