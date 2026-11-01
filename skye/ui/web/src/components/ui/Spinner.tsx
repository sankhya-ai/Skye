// Quiet loading indicator in berd's register: a small spinning arc, never a novelty
// animation. Sizes pair with the Button scale.
import { cn } from "../../lib";

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="loading"
      className={cn(
        "inline-block size-3.5 shrink-0 animate-spin rounded-full border-[1.5px] border-current border-t-transparent opacity-80",
        className,
      )}
    />
  );
}
