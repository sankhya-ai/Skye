// A scrollable region that never clips silently: while unread content sits below the fold
// it says so, and the hint clears at the end. With `pinned`, the view follows new content
// (a live discussion) until the reader scrolls up themselves - then it stays put.
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

export function ScrollArea({
  className,
  children,
  pinned = false,
  hint = "more below - scroll",
}: {
  className?: string;
  children: ReactNode;
  pinned?: boolean;
  hint?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [moreBelow, setMoreBelow] = useState(false);
  const atBottom = useRef(true);
  const check = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const gap = el.scrollHeight - el.scrollTop - el.clientHeight;
    atBottom.current = gap <= 4;
    setMoreBelow(gap > 4);
  }, []);
  useEffect(() => {
    const el = ref.current;
    if (el && pinned && atBottom.current) el.scrollTop = el.scrollHeight;
    check();
  }, [children, pinned, check]);
  return (
    <div>
      <div ref={ref} onScroll={check} className={className}>
        {children}
      </div>
      {moreBelow ? (
        <p className="mt-0.5 text-[11px] text-muted-foreground/60">{hint}</p>
      ) : null}
    </div>
  );
}
