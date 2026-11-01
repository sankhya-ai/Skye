// Shared modal: tokenized overlay + panel, berd's modal shadow and scale/fade entrance,
// Escape and backdrop click close. For blocking decisions only, per berd's modal rule.
import * as React from "react";
import { cn } from "../../lib";

export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
  className,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  className?: string;
}) {
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-6"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        className="absolute inset-0 animate-[overlay-in_150ms_ease-out] bg-foreground/20"
        onClick={onClose}
      />
      <div
        className={cn(
          "relative w-full max-w-md animate-[dialog-in_180ms_cubic-bezier(0.16,1,0.3,1)] rounded-md border border-border bg-card p-6 text-card-foreground shadow-modal",
          className,
        )}
      >
        <h2 className="font-display text-lg font-semibold tracking-tight">{title}</h2>
        <div className="mt-2 text-sm leading-relaxed text-muted-foreground">{children}</div>
        {footer ? <div className="mt-6 flex justify-end gap-2">{footer}</div> : null}
      </div>
    </div>
  );
}
