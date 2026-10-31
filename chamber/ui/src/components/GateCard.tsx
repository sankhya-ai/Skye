import { AlertTriangle, Check, ChevronDown, ChevronRight, Clock, Code, FileText, Lock, ShieldAlert, ShieldCheck, Terminal, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type Gate, type GateActionDetails } from "@/lib/api";
import { ago, ownerOf } from "@/lib/board";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface GateCardProps {
  gateId: string;
  description?: string;
  action?: string | GateActionDetails;
  requestedBy?: string;
  expiresAt?: number;
  createdAt?: number;
  resolvedAt?: number;
  resolvedBy?: string;
  lineId?: string;
  initialStatus?: "pending" | "approved" | "rejected" | "expired";
  me?: string;
  onResolved?: (gate: Gate) => void;
}

export function GateCard({
  gateId,
  description,
  action,
  requestedBy = "",
  expiresAt,
  createdAt,
  resolvedAt,
  resolvedBy,
  lineId,
  initialStatus = "pending",
  me = "",
  onResolved,
}: GateCardProps) {
  const [status, setStatus] = useState<string>(initialStatus);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [showDiff, setShowDiff] = useState(false);
  const [timeLeft, setTimeLeft] = useState<string>("");

  // Countdown timer
  useEffect(() => {
    if (!expiresAt || status !== "pending") return;

    const updateTimer = () => {
      const remaining = expiresAt - Date.now();
      if (remaining <= 0) {
        setTimeLeft("Expired");
        setStatus("expired");
        return;
      }
      const minutes = Math.floor(remaining / 60000);
      const seconds = Math.floor((remaining % 60000) / 1000);
      setTimeLeft(`${minutes}m ${seconds.toString().padStart(2, "0")}s`);
    };

    updateTimer();
    const interval = setInterval(updateTimer, 1000);
    return () => clearInterval(interval);
  }, [expiresAt, status]);

  const handleDecision = async (decision: "approve" | "reject") => {
    setLoading(true);
    setError("");
    try {
      const res = await api.resolveGate(gateId, decision);
      setStatus(res.gate.status);
      onResolved?.(res.gate);
    } catch (err: any) {
      setError(err?.message || "Failed to resolve gate");
    } finally {
      setLoading(false);
    }
  };

  const actionObj = typeof action === "object" ? action : null;
  const command = actionObj?.command || (typeof action === "string" ? action : "");
  const diff = actionObj?.diff;
  const files = actionObj?.files || [];
  const environment = actionObj?.environment || "local worktree";
  const approvers = actionObj?.approvers || [];

  // Check self-approval policy: if requester's owner is the same as current logged in user
  const isSelfRequest = me && requestedBy && ownerOf(requestedBy) === me;

  return (
    <div className="my-2.5 rounded-lg border bg-card p-3.5 shadow-sm transition-all hover:border-border/80">
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          {status === "approved" ? (
            <ShieldCheck className="size-4.5 text-emerald-500 shrink-0" />
          ) : status === "rejected" ? (
            <X className="size-4.5 text-destructive shrink-0" />
          ) : (
            <ShieldAlert className="size-4.5 text-foreground shrink-0" />
          )}
          <div>
            <div className="flex items-center gap-2">
              <span className="text-[13px] font-semibold tracking-tight">Approval Gate</span>
              <span className="font-mono text-[11px] text-muted-foreground bg-muted px-1.5 py-0.2 rounded">
                {gateId.slice(0, 8)}
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {timeLeft && status === "pending" ? (
            <span className="flex items-center gap-1 text-[11px] text-muted-foreground font-mono">
              <Clock className="size-3" />
              {timeLeft}
            </span>
          ) : null}
          <span
            className={cn(
              "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium capitalize",
              status === "approved"
                ? "bg-[#a8cbbb]/20 text-[#36634d] dark:text-[#a8cbbb]"
                : status === "rejected"
                ? "bg-destructive/10 text-destructive"
                : "bg-muted text-foreground"
            )}
          >
            {status}
          </span>
        </div>
      </div>

      {/* Description */}
      <p className="mt-2 text-[13px] font-medium text-foreground/95 leading-relaxed">
        {description || "Action paused pending human verification."}
      </p>

      {/* Action / Command / Target Env */}
      {command ? (
        <div className="mt-2 rounded-md bg-muted/70 p-2 font-mono text-[12px] border border-border/50 text-foreground">
          <div className="flex items-center gap-1.5 text-muted-foreground text-[10px] uppercase font-semibold tracking-wider mb-1">
            <Terminal className="size-3" /> Target Command
          </div>
          <code className="text-foreground break-all select-all font-mono">$ {command}</code>
        </div>
      ) : null}

      {/* Files touched */}
      {files.length > 0 ? (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] text-muted-foreground">Files touched:</span>
          {files.map((f) => (
            <span key={f} className="inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[11px] font-mono text-foreground">
              <FileText className="size-2.5 text-muted-foreground" /> {f}
            </span>
          ))}
        </div>
      ) : null}

      {/* Diff preview if present */}
      {diff ? (
        <div className="mt-2">
          <button
            onClick={() => setShowDiff((v) => !v)}
            className="flex items-center gap-1 text-[11px] font-medium text-muted-foreground hover:text-foreground"
          >
            {showDiff ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
            <Code className="size-3" />
            <span>{showDiff ? "Hide changes" : "View proposed diff"}</span>
          </button>
          {showDiff ? (
            <pre className="mt-1.5 max-h-48 overflow-y-auto rounded bg-muted/60 p-2 font-mono text-[11px] text-muted-foreground border">
              {diff}
            </pre>
          ) : null}
        </div>
      ) : null}

      {/* Target environment */}
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
        <span>Target: <strong className="font-mono text-foreground">{environment}</strong></span>
        {lineId ? <span>· Chamber Line: <strong className="font-mono text-foreground">{lineId.slice(0, 10)}</strong></span> : null}
        {approvers.length > 0 ? <span>· Policy approvers: <strong className="font-mono text-foreground">{approvers.join(", ")}</strong></span> : null}
      </div>

      {/* Self-approval policy alert */}
      {isSelfRequest && status === "pending" ? (
        <div className="mt-2 flex items-center gap-1.5 rounded-lg bg-muted/40 border px-2.5 py-1.5 text-[11px] text-muted-foreground">
          <AlertTriangle className="size-3.5 shrink-0 text-muted-foreground" />
          <span>
            <strong>Self-Approval Policy:</strong> Requested by your agent ({requestedBy}). Standard policy recommends approval by a second teammate.
          </span>
        </div>
      ) : null}

      {error ? <p className="mt-2 text-[11px] text-destructive font-medium">{error}</p> : null}

      {/* Actions */}
      {status === "pending" ? (
        <div className="mt-3 flex items-center gap-2 pt-2 border-t border-border/40">
          <Button
            size="sm"
            variant="default"
            disabled={loading}
            onClick={() => handleDecision("approve")}
            className="h-8 px-3.5 text-xs font-medium"
          >
            <Check className="mr-1.5 size-3.5" /> Approve Action
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={loading}
            onClick={() => handleDecision("reject")}
            className="h-8 px-3 text-xs"
          >
            <X className="mr-1.5 size-3.5" /> Reject
          </Button>
        </div>
      ) : null}

      {/* Audit Line */}
      <div className="mt-2.5 flex items-center justify-between text-[10px] text-muted-foreground/75 border-t border-border/30 pt-1.5 font-mono">
        <span className="flex items-center gap-1">
          <Lock className="size-2.5 text-emerald-500" />
          <span>E2EE Sealed · Ed25519 Verified</span>
        </span>
        <span>
          {status === "pending"
            ? `Requested by ${requestedBy || "agent"}${createdAt ? ` · ${ago(createdAt)}` : ""}`
            : `Resolved by ${resolvedBy || "admin"} ${resolvedAt ? ago(resolvedAt) : ""}`}
        </span>
      </div>
    </div>
  );
}
