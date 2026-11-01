// Adapted from the shadcn/ui Badge as vendored in block/buzz
// (desktop/src/shared/ui/badge.tsx, Apache-2.0). Variant map inlined to avoid
// the class-variance-authority dependency.
import * as React from "react";
import { cn } from "../../lib";

const VARIANTS: Record<string, string> = {
  default: "border-transparent bg-muted text-foreground",
  secondary: "border-transparent bg-muted text-muted-foreground",
  destructive: "border-transparent bg-destructive/15 text-destructive",
  outline: "text-muted-foreground",
  success: "border-transparent bg-success/15 text-success",
  info: "border-transparent bg-info/15 text-info",
};

export function Badge({
  className,
  variant = "default",
  ...props
}: React.ComponentProps<"span"> & { variant?: keyof typeof VARIANTS }) {
  return (
    <span
      className={cn(
        "inline-flex w-fit shrink-0 items-center justify-center gap-1 rounded-xs border px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        VARIANTS[variant] ?? VARIANTS.default,
        className,
      )}
      {...props}
    />
  );
}
