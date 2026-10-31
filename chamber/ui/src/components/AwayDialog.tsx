import { Bot, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

interface AwayDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  me: string;
  onChanged?: () => void;
}

const PRESETS = [
  "In a meeting",
  "At lunch",
  "Deep work",
  "On vacation",
];

export function AwayDialog({ open, onOpenChange, me, onChanged }: AwayDialogProps) {
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState("in a meeting");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      api.awayStatus().then((s) => {
        setRunning(s.running);
        if (s.status) setStatus(s.status);
      }).catch(() => {});
    }
  }, [open]);

  const handleToggle = async (nextState: boolean) => {
    setLoading(true);
    setError("");
    try {
      const res = await api.setAway(nextState, status.trim() || "away from desk");
      setRunning(res.running);
      if (res.status) setStatus(res.status);
      onChanged?.();
      onOpenChange(false);
    } catch (err: any) {
      setError(err?.message || "Failed to update away status");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <Bot className="size-5 text-foreground" />
            <DialogTitle>Away Mode Responder</DialogTitle>
          </div>
          <DialogDescription>
            Let your personal agent (<code>@{me}/away</code>) answer teammates and coding agents from local repo memory while you are away.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4 py-2">
          {/* Current Status Banner */}
          <div className="rounded-xl border bg-muted/20 p-3.5 space-y-1">
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  "size-2.5 rounded-full",
                  running ? "bg-[#8db8d5] animate-pulse" : "bg-[#a8cbbb]"
                )}
              />
              <span className="text-xs font-semibold text-foreground">
                {running ? "Away Mode is Currently Active" : "You are Currently Active & Available"}
              </span>
            </div>
            <p className="text-[11px] text-muted-foreground pl-4.5">
              {running
                ? `@${me}/away is auto-answering questions from your local codebase.`
                : "You receive all notifications and mentions normally."}
            </p>
          </div>

          {/* Status Note Input */}
          <div className="flex flex-col gap-2">
            <label className="text-xs font-medium text-foreground">
              {running ? "Current Away Note" : "Away Note (shown to teammates)"}
            </label>
            <input
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              placeholder="e.g. In a meeting, Deep work until 4pm..."
              className="h-8.5 rounded-md border bg-background px-3 text-xs text-foreground outline-none focus:border-ring transition-colors"
            />

            {/* Quick Presets */}
            <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
              <span className="text-[10px] text-muted-foreground mr-1">Presets:</span>
              {PRESETS.map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setStatus(p)}
                  className={cn(
                    "rounded-md border px-2 py-0.5 text-[11px] transition-colors",
                    status === p
                      ? "bg-primary text-primary-foreground font-medium border-primary"
                      : "bg-muted/40 hover:bg-muted text-muted-foreground hover:text-foreground border-border/70"
                  )}
                >
                  {p}
                </button>
              ))}
            </div>
          </div>

          {error ? <p className="text-xs text-destructive">{error}</p> : null}

          {/* Privacy Footnote */}
          <div className="rounded-lg border bg-muted/20 p-2.5 text-xs text-muted-foreground flex items-center gap-2">
            <Sparkles className="size-3.5 text-muted-foreground shrink-0" />
            <span>Responses run locally on your machine with zero key leaks.</span>
          </div>
        </div>

        <DialogFooter className="gap-2 sm:justify-between">
          <Button
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
            className="h-8 text-xs"
          >
            Cancel
          </Button>

          <div className="flex items-center gap-2">
            {running ? (
              <>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={loading}
                  onClick={() => handleToggle(true)}
                  className="h-8 text-xs"
                >
                  {loading ? "Updating..." : "Update Note"}
                </Button>
                <Button
                  size="sm"
                  variant="default"
                  disabled={loading}
                  onClick={() => handleToggle(false)}
                  className="h-8 text-xs font-medium"
                >
                  {loading ? "Turning off..." : "Set as Active (I'm Back)"}
                </Button>
              </>
            ) : (
              <Button
                size="sm"
                variant="default"
                disabled={loading}
                onClick={() => handleToggle(true)}
                className="h-8 text-xs font-medium"
              >
                {loading ? "Activating..." : "Set as Away"}
              </Button>
            )}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
