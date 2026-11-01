// Shared button, following block/berd's button contract (Apache-2.0, see skye/ui/NOTICE):
// always rounded-full (buttons read as "press me", unlike rounded-xs badges), variant map
// instead of feature-level styling, a real loading state (spinner + hold label) so an
// in-flight action never looks dead or clickable twice.
import * as React from "react";
import { cn } from "../../lib";
import { Spinner } from "./Spinner";

const VARIANTS: Record<string, string> = {
  primary: "bg-primary text-primary-foreground hover:bg-primary/90",
  outline: "border border-input bg-background hover:bg-accent hover:text-accent-foreground",
  ghost: "hover:bg-accent hover:text-accent-foreground",
  destructive: "bg-destructive text-white hover:bg-destructive/90",
  link: "text-primary underline-offset-4 hover:underline",
};

const SIZES: Record<string, string> = {
  xs: "h-7 px-2.5 text-xs [&_svg]:size-3",
  sm: "h-8 px-3 text-xs [&_svg]:size-3",
  default: "h-9 px-4 text-sm [&_svg]:size-3.5",
  icon: "h-9 w-9 [&_svg]:size-4",
};

export function Button({
  className,
  variant = "primary",
  size = "default",
  loading = false,
  disabled,
  children,
  ...props
}: React.ComponentProps<"button"> & {
  variant?: keyof typeof VARIANTS;
  size?: keyof typeof SIZES;
  loading?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        "inline-flex cursor-pointer items-center justify-center gap-2 rounded-full text-left whitespace-nowrap transition-colors duration-150 disabled:pointer-events-none disabled:opacity-40",
        VARIANTS[variant] ?? VARIANTS.primary,
        SIZES[size] ?? SIZES.default,
        className,
      )}
      {...props}
    >
      {loading ? <Spinner /> : null}
      {children}
    </button>
  );
}
