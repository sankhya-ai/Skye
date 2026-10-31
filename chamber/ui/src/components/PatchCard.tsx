import { Check, ChevronDown, ChevronRight, Copy, GitBranch, GitCommit, Play } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";

interface PatchCardProps {
  patch: string;
  branch?: string | null;
  title?: string;
}

export function PatchCard({ patch, branch, title }: PatchCardProps) {
  const [open, setOpen] = useState(true);
  const [verifying, setVerifying] = useState(false);
  const [verification, setVerification] = useState<{ appliesCleanly: boolean; stat: string; error?: string | null } | null>(null);
  const [copied, setCopied] = useState(false);
  const [applying, setApplying] = useState(false);
  const [applied, setApplied] = useState(false);
  const [applyError, setApplyError] = useState("");

  const lines = patch.split("\n");
  const additions = lines.filter((l) => l.startsWith("+") && !l.startsWith("+++")).length;
  const deletions = lines.filter((l) => l.startsWith("-") && !l.startsWith("---")).length;

  const handleVerify = async () => {
    setVerifying(true);
    setApplyError("");
    try {
      const res = await api.reviewPatch(patch);
      setVerification(res);
    } catch (err: any) {
      setVerification({ appliesCleanly: false, stat: "", error: err?.message || "Verification failed" });
    } finally {
      setVerifying(false);
    }
  };

  const handleApply = async () => {
    setApplying(true);
    setApplyError("");
    try {
      const res = await api.applyPatch(patch);
      if (res.success) {
        setApplied(true);
      } else {
        setApplyError(res.output || "Failed to apply");
      }
    } catch (err: any) {
      setApplyError(err?.message || "Failed to apply patch");
    } finally {
      setApplying(false);
    }
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(patch);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="my-2 overflow-hidden rounded-lg border bg-muted/30 text-xs shadow-sm">
      <div className="flex items-center justify-between border-b bg-muted/60 px-3 py-2">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setOpen((v) => !v)}
            className="flex items-center gap-1.5 font-medium text-foreground hover:text-foreground/80"
          >
            {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
            <GitCommit className="size-3.5 text-muted-foreground" />
            <span>{title || "Unified Git Patch"}</span>
          </button>
          {branch ? (
            <span className="inline-flex items-center gap-1 rounded bg-accent px-1.5 py-0.5 text-[11px] text-accent-foreground font-mono">
              <GitBranch className="size-3" />
              {branch}
            </span>
          ) : null}
          <span className="text-[11px] font-mono text-emerald-500 font-semibold">+{additions}</span>
          <span className="text-[11px] font-mono text-rose-500 font-semibold">-{deletions}</span>
        </div>

        <div className="flex items-center gap-1.5">
          {verification?.appliesCleanly && !applied ? (
            <Button
              size="sm"
              variant="default"
              disabled={applying}
              onClick={handleApply}
              className="h-6 px-2 text-[11px] gap-1 font-medium"
            >
              <Check className="size-3" />
              {applying ? "Applying..." : "Apply to Worktree"}
            </Button>
          ) : null}
          {applied ? (
            <span className="text-[11px] font-semibold text-[#36634d] dark:text-[#a8cbbb] flex items-center gap-1 px-1">
              <Check className="size-3" /> Applied
            </span>
          ) : null}
          <Button
            size="sm"
            variant="outline"
            disabled={verifying}
            onClick={handleVerify}
            className="h-6 px-2 text-[11px] gap-1"
          >
            <Play className="size-3" />
            {verifying ? "Checking..." : "Verify Patch"}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={handleCopy}
            className="h-6 px-1.5 text-muted-foreground hover:text-foreground"
            title="Copy Raw Diff"
          >
            {copied ? <Check className="size-3 text-[#36634d] dark:text-[#a8cbbb]" /> : <Copy className="size-3" />}
          </Button>
        </div>
      </div>

      {verification ? (
        <div
          className={`border-b px-3 py-1.5 text-[11px] flex items-center justify-between ${
            verification.appliesCleanly
              ? "bg-[#a8cbbb]/15 text-[#36634d] dark:text-[#a8cbbb]"
              : "bg-destructive/10 text-destructive"
          }`}
        >
          <span>
            {verification.appliesCleanly
              ? "✔ Patch applies cleanly to local working tree."
              : `✖ Conflict: ${verification.error || "Patch does not apply cleanly."}`}
          </span>
          {verification.stat ? <span className="font-mono text-[10px]">{verification.stat.trim()}</span> : null}
        </div>
      ) : null}

      {applyError ? (
        <div className="border-b px-3 py-1.5 text-[11px] bg-destructive/10 text-destructive font-mono">
          {applyError}
        </div>
      ) : null}

      {open ? (
        <div className="max-h-64 overflow-y-auto bg-background/50 font-mono text-[11px] leading-tight">
          {lines.map((line, idx) => {
            const isAdd = line.startsWith("+") && !line.startsWith("+++");
            const isDel = line.startsWith("-") && !line.startsWith("---");
            const isHunk = line.startsWith("@@");
            const isFile = line.startsWith("diff --git") || line.startsWith("---") || line.startsWith("+++");

            return (
              <div
                key={idx}
                className={`flex px-2 py-0.5 ${
                  isAdd
                    ? "bg-[#a8cbbb]/20 text-[#36634d] dark:text-[#a8cbbb]"
                    : isDel
                    ? "bg-muted/70 text-muted-foreground"
                    : isHunk
                    ? "bg-[#8db8d5]/15 text-[#356f96] dark:text-[#8db8d5] font-semibold"
                    : isFile
                    ? "text-muted-foreground font-semibold"
                    : "text-foreground/80"
                }`}
              >
                <span className="w-8 shrink-0 select-none text-muted-foreground/60">{idx + 1}</span>
                <span className="select-text whitespace-pre-wrap break-all">{line || " "}</span>
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
