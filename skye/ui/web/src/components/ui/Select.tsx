// Skye's dropdown. The native <select> menu is drawn by the OS (a macOS system menu that
// ignores the page's design), so this renders its own listbox in the app's visual language.
// Accessible by keyboard: Enter/Space/ArrowDown open, arrows move, Enter selects, Escape
// closes, typing jumps to the first matching option. The menu is portalled with fixed
// positioning so no card's overflow can clip it, and it flips above the trigger when there
// is no room below. It follows its trigger while the page scrolls.
import { Check, ChevronDown } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { cn } from "../../lib";

export type SelectOption = {
  value: string;
  label: string;
  hint?: string;
  /** An action row (e.g. "Add project folder…"), set apart below the choices. */
  action?: boolean;
  disabled?: boolean;
};

export function Select({
  value,
  options,
  onChange,
  ariaLabel,
  placeholder = "Choose…",
  disabled = false,
  size = "sm",
  mono = false,
  className,
  menuMinWidth = 220,
}: {
  value: string;
  options: SelectOption[];
  onChange: (value: string) => void;
  ariaLabel: string;
  placeholder?: string;
  disabled?: boolean;
  size?: "xs" | "sm";
  mono?: boolean;
  className?: string;
  menuMinWidth?: number;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [pos, setPos] = useState<{ left: number; top: number; width: number; up: boolean } | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const typed = useRef({ text: "", at: 0 });
  const listId = useId();
  const selected = options.find((o) => o.value === value && !o.action);
  const enabled = options.map((o, i) => (o.disabled ? -1 : i)).filter((i) => i >= 0);

  const place = useCallback(() => {
    const r = trigger.current?.getBoundingClientRect();
    if (!r) return;
    const estimate = Math.min(320, options.length * 34 + 12);
    const up = window.innerHeight - r.bottom < estimate + 12 && r.top > window.innerHeight - r.bottom;
    const width = Math.min(Math.max(r.width, menuMinWidth), Math.max(0, window.innerWidth - 16));
    const left = Math.min(Math.max(8, r.left), window.innerWidth - width - 8);
    setPos({ left, top: up ? r.top - 6 : r.bottom + 6, width, up });
  }, [options.length, menuMinWidth]);

  const openMenu = () => {
    if (disabled) return;
    const current = options.findIndex((o) => o.value === value && !o.action);
    setActive(current >= 0 ? current : enabled[0] ?? 0);
    place();
    setOpen(true);
  };
  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) trigger.current?.focus();
  };
  const choose = (i: number) => {
    const option = options[i];
    if (!option || option.disabled) return;
    close();
    if (option.value !== value || option.action) onChange(option.value);
  };

  useLayoutEffect(() => {
    if (!open) return;
    place();
    // Follow the trigger while the page scrolls (trackpad momentum often lands just after
    // opening); close only once the trigger has scrolled out of sight.
    const onScroll = (e: Event) => {
      if (menu.current && e.target instanceof Node && menu.current.contains(e.target)) return;
      const r = trigger.current?.getBoundingClientRect();
      if (!r || r.bottom < 0 || r.top > window.innerHeight) close(false);
      else place();
    };
    window.addEventListener("resize", place);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [open, place]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!menu.current?.contains(t) && !trigger.current?.contains(t)) close(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  useEffect(() => {
    if (open) menu.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  const move = (delta: number) => {
    const at = enabled.indexOf(active);
    const next = enabled[(at + delta + enabled.length) % enabled.length];
    if (next !== undefined) setActive(next);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open) {
      if (["Enter", " ", "ArrowDown", "ArrowUp"].includes(e.key)) {
        e.preventDefault();
        openMenu();
      }
      return;
    }
    if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "Tab") close(false);
    else if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
    else if (e.key === "Home") { e.preventDefault(); setActive(enabled[0]); }
    else if (e.key === "End") { e.preventDefault(); setActive(enabled[enabled.length - 1]); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(active); }
    else if (e.key.length === 1) {
      const now = Date.now();
      typed.current = { text: (now - typed.current.at < 600 ? typed.current.text : "") + e.key.toLowerCase(), at: now };
      const hit = options.findIndex((o) => !o.disabled && o.label.toLowerCase().startsWith(typed.current.text));
      if (hit >= 0) setActive(hit);
    }
  };

  const height = size === "xs" ? "h-7" : "h-8";
  const firstAction = options.findIndex((o) => o.action);

  return (
    <>
      <button
        ref={trigger}
        type="button"
        role="combobox"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-activedescendant={open ? `${listId}-${active}` : undefined}
        disabled={disabled}
        onClick={() => (open ? close() : openMenu())}
        onKeyDown={onKeyDown}
        className={cn(
          "inline-flex min-w-0 items-center justify-between gap-2 rounded-full border border-border bg-card pl-3 pr-2.5 text-xs text-foreground transition-colors",
          "hover:bg-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-60",
          open && "border-foreground/25",
          height,
          mono && "font-mono",
          className,
        )}
      >
        <span className={cn("truncate", !selected && "text-muted-foreground")}>
          {selected ? selected.label : placeholder}
        </span>
        <ChevronDown
          aria-hidden
          className={cn("size-3.5 shrink-0 text-muted-foreground transition-transform duration-150", open && "rotate-180")}
        />
      </button>
      {open && pos
        ? createPortal(
            <div
              ref={menu}
              id={listId}
              role="listbox"
              aria-label={ariaLabel}
              tabIndex={-1}
              style={{
                position: "fixed",
                left: pos.left,
                width: pos.width,
                ...(pos.up ? { bottom: window.innerHeight - pos.top } : { top: pos.top }),
              }}
              className="z-50 max-h-80 overflow-y-auto rounded-sm border border-border bg-card p-1 shadow-[var(--shadow-popover)]"
            >
              {options.map((o, i) => {
                const isSelected = o.value === value && !o.action;
                return (
                  <div key={`${o.value}-${i}`}>
                    {i === firstAction && i > 0 ? <div className="mx-1 my-1 border-t border-border" /> : null}
                    <div
                      id={`${listId}-${i}`}
                      data-index={i}
                      role="option"
                      aria-selected={isSelected}
                      aria-disabled={o.disabled || undefined}
                      onMouseEnter={() => !o.disabled && setActive(i)}
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => choose(i)}
                      className={cn(
                        "flex cursor-pointer items-start gap-2 rounded-xs px-2.5 py-1.5 text-xs",
                        i === active && !o.disabled && "bg-accent",
                        o.disabled && "cursor-not-allowed opacity-50",
                        o.action ? "text-muted-foreground" : "text-foreground",
                      )}
                    >
                      <span className="min-w-0 flex-1">
                        <span className={cn("block truncate", mono && !o.action && "font-mono")}>{o.label}</span>
                        {o.hint ? <span className="mt-0.5 block text-[11px] leading-4 text-muted-foreground">{o.hint}</span> : null}
                      </span>
                      {isSelected ? <Check aria-hidden className="mt-0.5 size-3.5 shrink-0 text-foreground" /> : <span className="size-3.5 shrink-0" />}
                    </div>
                  </div>
                );
              })}
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
