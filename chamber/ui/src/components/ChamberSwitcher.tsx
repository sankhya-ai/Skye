import { Check, ChevronDown, Pencil, Plus } from "lucide-react";
import { useState } from "react";

import { api } from "@/lib/api";
import { hue, type BoardData, type ChamberSummary } from "@/lib/board";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

/** A chamber's mark, like a Slack workspace icon: its initials on a colour of its own. */
export function ChamberIcon({ name, size = 28, className }: { name: string; size?: number; className?: string }) {
  const words = name.trim().split(/[\s._-]+/).filter(Boolean);
  const initials = ((words.length > 1 ? words[0][0] + words[1][0] : name.slice(0, 2)) || "?").toUpperCase();
  const h = hue(name.toLowerCase());
  return (
    <span
      className={cn("inline-flex shrink-0 items-center justify-center rounded-lg font-semibold", className)}
      style={{ width: size, height: size, fontSize: Math.round(size * 0.4), background: `hsl(${h} 42% 86%)`, color: `hsl(${h} 45% 28%)` }}
    >
      {initials}
    </span>
  );
}

export const relayHost = (relay: string) => {
  try {
    return new URL(relay).host;
  } catch {
    return relay;
  }
};

/** The chamber's name atop the sidebar; it opens the list of every chamber you're in, and adds one. */
export function ChamberSwitcher({ data, onChanged }: { data: BoardData | null; onChanged: () => void }) {
  const [renaming, setRenaming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const chambers = data?.chambers ?? [];
  const current = chambers.find((c) => c.active);
  const name = data?.chamber?.name || current?.name || "chamber";
  const others = chambers.filter((c) => !c.active);

  const go = async (work: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await work();
    } finally {
      setBusy(false);
      onChanged();
    }
  };

  const saveName = () => {
    const next = renaming?.trim();
    setRenaming(null);
    if (next && next !== name) void go(() => api.renameChamber(next));
  };

  return (
    <DropdownMenu onOpenChange={(open) => !open && setRenaming(null)}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          disabled={busy}
          className="flex min-w-0 items-center gap-2 rounded-md px-1 py-0.5 text-left outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ChamberIcon name={name} size={24} />
          <span className="truncate text-[15px] font-semibold tracking-tight">{name}</span>
          <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-72 p-1.5">
        <div className="flex items-center gap-2.5 rounded-md px-2 py-2">
          <ChamberIcon name={name} size={36} />
          <div className="min-w-0 flex-1">
            {renaming !== null ? (
              <input
                autoFocus
                value={renaming}
                onChange={(e) => setRenaming(e.target.value)}
                onKeyDown={(e) => {
                  e.stopPropagation();
                  if (e.key === "Enter") saveName();
                  if (e.key === "Escape") setRenaming(null);
                }}
                onBlur={saveName}
                maxLength={60}
                className="w-full rounded border bg-background px-1.5 py-0.5 text-sm font-semibold outline-none focus:border-ring"
              />
            ) : (
              <div className="flex items-center gap-1.5">
                <span className="truncate text-sm font-semibold">{name}</span>
                <button type="button" aria-label="Rename this chamber" title="Rename" onClick={() => setRenaming(name)} className="text-muted-foreground hover:text-foreground">
                  <Pencil className="size-3" />
                </button>
              </div>
            )}
            {current ? (
              <p className="truncate text-xs text-muted-foreground">
                {relayHost(current.relay)} · {current.owner}
                {current.admin ? " (admin)" : ""}
              </p>
            ) : null}
          </div>
          <Check className="size-4 shrink-0 text-muted-foreground" />
        </div>
        {others.length ? <DropdownMenuSeparator /> : null}
        {others.map((c: ChamberSummary) => (
          <DropdownMenuItem key={c.id} onSelect={() => void go(() => api.switchChamber(c.id))} className="gap-2.5 px-2 py-2">
            <ChamberIcon name={c.name} size={32} />
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{c.name}</p>
              <p className="truncate text-xs text-muted-foreground">{relayHost(c.relay)}</p>
            </div>
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => void go(() => api.addChamber())} className="gap-2.5 px-2 py-2">
          <span className="inline-flex size-8 items-center justify-center rounded-lg border bg-muted/40">
            <Plus className="size-4" />
          </span>
          <span className="text-sm">Add a chamber</span>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
