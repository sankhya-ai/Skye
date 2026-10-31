import { CheckCircle2, Circle, CircleDot, GitBranch } from "lucide-react";
import { useEffect, useState } from "react";

import { Avatar } from "@/components/Avatar";
import { ago, personOf, STATUSES, who, type Card, type Status } from "@/lib/board";
import { cn } from "@/lib/utils";

const ICON: Record<Status, React.ReactNode> = {
  waiting: <CircleDot className="size-3.5 text-waiting shrink-0" />,
  talking: <Circle className="size-3.5 text-blue-500 animate-pulse shrink-0" />,
  done: <CheckCircle2 className="size-3.5 text-emerald-500 shrink-0" />,
};

function People({ card, me }: { card: Card; me: string }) {
  const names = [...new Set(card.with.map((p) => personOf(p, me)))];
  return (
    <span className="flex -space-x-1">
      {names.slice(0, 3).map((n) => (
        <Avatar key={n} name={n} size={18} className="ring-2 ring-background rounded-full" />
      ))}
    </span>
  );
}

function cleanPreview(title: string, rawText: string): string {
  let text = (rawText || "").trim();
  const lowerTitle = title.toLowerCase().trim();
  if (text.toLowerCase().startsWith(lowerTitle)) {
    text = text.slice(lowerTitle.length).replace(/^[\s—:\-]+/, "").trim();
  }
  return text.slice(0, 120);
}

function Row({ card, me, open, onOpen }: { card: Card; me: string; open: boolean; onOpen: () => void }) {
  const preview = cleanPreview(card.title, card.last.text);
  return (
    <div
      role="button"
      onClick={onOpen}
      className={cn("group flex h-11 w-full items-center gap-3 border-b border-border/60 px-4 text-left hover:bg-accent/60 cursor-pointer transition-colors", open && "bg-accent")}
    >
      {ICON[card.status]}
      <div className="flex min-w-0 flex-1 items-baseline gap-2">
        <span className={cn("truncate text-[13px]", card.unread ? "font-semibold text-foreground" : "text-foreground/90")}>{card.title}</span>
        {preview ? <span className="truncate text-[12px] text-muted-foreground/70">{preview}</span> : null}
      </div>
      {card.branch ? (
        <span className="inline-flex shrink-0 items-center gap-1 rounded bg-accent/80 px-1.5 py-0.5 text-[10px] font-mono text-accent-foreground">
          <GitBranch className="size-2.5" /> {card.branch}
        </span>
      ) : null}
      {card.channel ? <span className="text-[12px] text-muted-foreground shrink-0">{card.channel}</span> : null}
      <People card={card} me={me} />
      <span className="w-8 text-right text-muted-foreground tabular-nums text-xs">{card.count}</span>
      <span className="w-10 text-right text-[11px] text-muted-foreground">{ago(card.updated)}</span>
    </div>
  );
}

function Tile({ card, me, open, onOpen }: { card: Card; me: string; open: boolean; onOpen: () => void }) {
  const preview = cleanPreview(card.title, card.last.text);
  return (
    <div
      role="button"
      onClick={onOpen}
      className={cn("group flex w-full flex-col gap-2 rounded-lg border bg-card p-3 text-left shadow-xs hover:border-muted-foreground/40 cursor-pointer transition-all", open && "border-ring ring-1 ring-ring/30")}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="flex items-start gap-2 min-w-0 flex-1">
          <span className="mt-0.5 shrink-0">{ICON[card.status]}</span>
          <span className={cn("line-clamp-2 text-[13px] leading-snug", card.unread ? "font-semibold text-foreground" : "font-medium text-foreground/90")}>
            {card.title}
          </span>
        </span>
      </div>

      {preview ? <p className="line-clamp-2 text-[12px] text-muted-foreground">{preview}</p> : null}

      <div className="mt-auto flex items-center justify-between text-muted-foreground text-[11px] pt-1 border-t border-border/40">
        <div className="flex items-center gap-1.5">
          <People card={card} me={me} />
          {card.channel ? <span className="font-mono text-[10px]">{card.channel}</span> : null}
          {card.branch ? (
            <span className="inline-flex items-center gap-0.5 font-mono text-[10px] text-accent-foreground">
              <GitBranch className="size-2.5" /> {card.branch}
            </span>
          ) : null}
        </div>
        <span>{card.count} · {ago(card.updated)}</span>
      </div>
    </div>
  );
}

/** Gmail/Superhuman inbox: clear sender, title, separate snippet, time, and quick actions. */
function Inbox({ cards, me, open, onOpen, selectedIndex, keyboardActive }: { cards: Card[]; me: string; open: string | null; onOpen: (id: string) => void; selectedIndex: number; keyboardActive: boolean }) {
  return (
    <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
      {cards.map((c, index) => {
        const from = c.last.from;
        const isMine = from.startsWith(`${me}/`) || from === `@${me}`;
        const otherPersons = c.with.filter((p) => !p.startsWith(`${me}/`) && p !== `@${me}` && !p.startsWith("#"));
        const target = !isMine ? from : (otherPersons[0] || from);
        const displayName = !isMine
          ? who(from, me, { thirdPerson: true })
          : (otherPersons.length > 0 ? otherPersons.map((p) => who(p, me)).join(", ") : who(from, me, { thirdPerson: true }));

        const rawPreview = cleanPreview(c.title, c.last.text);
        const prefix = isMine ? (from.endsWith("/away") ? "Away responder: " : "You: ") : "";
        const preview = rawPreview ? `${prefix}${rawPreview}` : "";
        const isSelected = keyboardActive && selectedIndex === index;
        const isOpen = open === c.id;

        return (
          <div
            key={c.id}
            role="button"
            onClick={() => onOpen(c.id)}
            className={cn(
              "group relative flex h-14 w-full items-center gap-3 border-b border-border/60 px-4 text-left cursor-pointer transition-colors",
              (isOpen || isSelected) ? "bg-accent/70 font-medium" : "bg-card/30 hover:bg-accent/40"
            )}
          >
            {isOpen ? <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-primary" /> : null}
            <span className={cn("size-2 shrink-0 rounded-full transition-all", c.unread ? "bg-dot shadow-xs" : "bg-transparent")} />
            <Avatar name={personOf(target, me)} agent={target} size={28} />
            
            <div className="w-40 shrink-0 truncate">
              <span className={cn("text-[13px] first-letter:uppercase truncate block", c.unread ? "font-semibold text-foreground" : "font-medium text-foreground/90")}>
                {displayName}
              </span>
            </div>

            <div className="min-w-0 flex-1 flex flex-col justify-center gap-0.5">
              <div className="flex items-center gap-2">
                <span className={cn("truncate text-[13px]", c.unread ? "font-semibold text-foreground" : "font-normal text-foreground/85")}>
                  {c.title}
                </span>
                {c.branch ? (
                  <span className="inline-flex shrink-0 items-center gap-1 rounded bg-accent/80 px-1.5 py-0.2 text-[10px] font-mono text-accent-foreground">
                    <GitBranch className="size-2.5" /> {c.branch}
                  </span>
                ) : null}
              </div>
              {preview ? (
                <span className="truncate text-[12px] text-muted-foreground/80">
                  {preview}
                </span>
              ) : null}
            </div>

            {c.channel ? <span className="shrink-0 text-muted-foreground text-xs font-mono">{c.channel}</span> : null}
            <span className={cn("w-12 shrink-0 text-right text-[11px]", c.unread ? "font-semibold text-foreground" : "text-muted-foreground/70")}>{ago(c.updated)}</span>
          </div>
        );
      })}
    </div>
  );
}

/** Gmail's inbox, Linear's list (grouped by status), or a board (a column per status). */
export function TalkList({
  cards,
  me,
  layout,
  open,
  onOpen,
  onDone,
  empty,
}: {
  cards: Card[];
  me: string;
  layout: "inbox" | "list" | "board";
  open: string | null;
  onOpen: (id: string) => void;
  onDone?: (id: string) => void;
  empty: string;
}) {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [keyboardActive, setKeyboardActive] = useState(false);

  // Keyboard triage: j / k navigation, e for done
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = /input|textarea/i.test((e.target as HTMLElement)?.tagName ?? "");
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (!cards.length) return;

      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        setKeyboardActive(true);
        setSelectedIndex((i) => Math.min(cards.length - 1, i + 1));
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        setKeyboardActive(true);
        setSelectedIndex((i) => Math.max(0, i - 1));
      } else if (e.key === "Enter" || e.key === "o") {
        e.preventDefault();
        if (cards[selectedIndex]) onOpen(cards[selectedIndex].id);
      } else if (e.key === "e") {
        e.preventDefault();
        if (cards[selectedIndex] && onDone) {
          onDone(cards[selectedIndex].id);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cards, selectedIndex, onOpen, onDone]);

  if (!cards.length) return <div className="flex flex-1 items-center justify-center p-8 text-muted-foreground">{empty}</div>;
  if (layout === "inbox") {
    return <Inbox cards={cards} me={me} open={open} onOpen={onOpen} selectedIndex={selectedIndex} keyboardActive={keyboardActive} />;
  }

  if (layout === "board") {
    return (
      <div className="grid min-h-0 flex-1 grid-cols-3 gap-3 overflow-hidden p-3">
        {STATUSES.map(({ status, label }) => {
          const column = cards.filter((c) => c.status === status);
          return (
            <section key={status} className="flex min-h-0 flex-col rounded-lg bg-muted/40 border border-border/40">
              <h3 className="flex items-center gap-2 px-3 py-2 font-medium text-xs border-b border-border/40">
                {ICON[status]} {label} <span className="text-muted-foreground text-[11px] font-mono ml-auto">{column.length}</span>
              </h3>
              <div className="scroll-thin flex flex-col gap-2 overflow-y-auto p-2">
                {column.map((c) => (
                  <Tile
                    key={c.id}
                    card={c}
                    me={me}
                    open={open === c.id}
                    onOpen={() => onOpen(c.id)}
                  />
                ))}
                {column.length === 0 ? (
                  <div className="py-8 text-center text-xs text-muted-foreground/60 italic">No talks</div>
                ) : null}
              </div>
            </section>
          );
        })}
      </div>
    );
  }

  return (
    <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
      {STATUSES.map(({ status, label }) => {
        const group = cards.filter((c) => c.status === status);
        if (!group.length) return null;
        return (
          <section key={status}>
            <h3 className="sticky top-0 z-10 flex h-8 items-center gap-2 border-b bg-muted/80 px-4 text-[12px] font-medium backdrop-blur">
              {ICON[status]} {label} <span className="text-muted-foreground">{group.length}</span>
            </h3>
            {group.map((c) => (
              <Row
                key={c.id}
                card={c}
                me={me}
                open={open === c.id}
                onOpen={() => onOpen(c.id)}
              />
            ))}
          </section>
        );
      })}
    </div>
  );
}
