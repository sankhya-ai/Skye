import { Bot, Check, Copy, FileText, GitBranch, Hash, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Avatar } from "@/components/Avatar";
import { ChannelCanvas } from "@/components/ChannelCanvas";
import { Composer } from "@/components/Composer";
import { GateCard } from "@/components/GateCard";
import { PatchCard } from "@/components/PatchCard";
import { api, type AttachedFile } from "@/lib/api";
import { address, ago, defaultRecipient, personOf, who, type Talk } from "@/lib/board";
import { cn } from "@/lib/utils";

/** One talk, Slack-style: messages grouped by sender, a composer, Done. `talkId` null = a new talk. */
export function Conversation({ talkId, me, to: fixedTo, people, channels = [], onClose, onStarted, onChanged }: {
  talkId: string | null;
  me: string;
  to: string; // where a new talk goes ("" = ask)
  people: string[];
  channels?: { name: string; members: string[] }[];
  onClose: () => void;
  onStarted: (thread: string) => void;
  onChanged: () => void;
}) {
  const [talk, setTalk] = useState<Talk | null>(null);
  const [to, setTo] = useState(fixedTo);
  const [notice, setNotice] = useState("");
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const toContainerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const count = talk?.messages.length ?? 0;

  const filtered = people.filter((p) => !to || p.toLowerCase().includes(to.toLowerCase()));

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (toContainerRef.current && !toContainerRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  useEffect(() => {
    setTalk(null);
    if (!talkId) return;
    let live = true;
    const load = () =>
      api.talk(talkId).then(
        (t) => {
          if (!live) return;
          setTalk((prev) => (JSON.stringify(prev) === JSON.stringify(t) ? prev : t));
        },
        () => undefined
      );
    void load();
    const timer = setInterval(load, 2500);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [talkId]);

  // Scroll the message list only: scrollIntoView would also scroll the page's own layout.
  useEffect(() => {
    if (list.current) list.current.scrollTop = list.current.scrollHeight;
  }, [count]);

  const explain = (r: { held?: boolean; wake?: { woke: boolean; reason?: string } }) =>
    setNotice(r.wake?.woke ? "Starting that agent to answer…" : r.wake ? `${r.wake.reason} Your message is queued and it will see it first thing.` : "");

  const send = async (text: string, files: AttachedFile[] = []) => {
    if (talkId && talk) {
      explain(await api.say(talkId, defaultRecipient(talk, me), text, files));
      setTalk(await api.talk(talkId));
    } else {
      const target = address(to);
      if (!target) throw new Error("Who is it for? Type a name, e.g. nitin, or a #channel.");
      const fallbackTitle = text ? text.split("\n")[0].slice(0, 80) : files[0] ? `File: ${files[0].name}` : "New talk";
      const started = await api.startTalk(target, text, fallbackTitle, files);
      explain(started);
      onStarted(started.thread);
    }
    onChanged();
  };

  const handleReaction = async (messageId: string, emoji: string) => {
    try {
      const res = await api.toggleReaction(messageId, emoji);
      if (talk) {
        setTalk({
          ...talk,
          messages: talk.messages.map((msg) => (msg.id === messageId ? { ...msg, reactions: res.reactions } : msg)),
        });
      }
    } catch {}
  };

  return (
    <section className="flex min-h-0 w-[440px] shrink-0 flex-col border-l">
      <header className="flex h-12 shrink-0 items-center gap-2 border-b px-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <h2 className="truncate font-medium">{talkId ? talk?.title ?? "" : "New talk"}</h2>
            {talk?.branch || talk?.messages.find((m) => m.branch)?.branch ? (
              <span className="inline-flex items-center gap-1 rounded bg-accent/80 px-1.5 py-0.5 text-[10px] font-mono text-accent-foreground">
                <GitBranch className="size-2.5" />
                {talk?.branch || talk?.messages.find((m) => m.branch)?.branch}
              </span>
            ) : null}
          </div>
          {talk ? <p className="truncate text-[12px] text-muted-foreground">{talk.channel ?? talk.with.map((p) => who(p, me)).join(", ")}</p> : null}
        </div>

        <button aria-label="Close" onClick={onClose} className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground">
          <X className="size-4" />
        </button>
      </header>

      {talk?.channel ? <ChannelCanvas channelName={talk.channel} /> : null}

      <div ref={list} className="scroll-thin min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {!talkId ? (
          <div ref={toContainerRef} className="relative mb-3">
            <div className="flex items-center gap-2 rounded-lg border bg-muted/40 px-3 py-2 transition-colors focus-within:border-ring focus-within:bg-background">
              <span className="text-sm font-medium text-muted-foreground">To</span>
              <input
                ref={inputRef}
                autoFocus={!fixedTo}
                value={to}
                onChange={(e) => {
                  setTo(e.target.value);
                  setDropdownOpen(true);
                  setHighlightedIndex(0);
                }}
                onFocus={() => {
                  setDropdownOpen(true);
                  setHighlightedIndex(0);
                }}
                onKeyDown={(e) => {
                  if (!dropdownOpen || filtered.length === 0) {
                    if (e.key === "ArrowDown") {
                      setDropdownOpen(true);
                      e.preventDefault();
                    }
                    return;
                  }
                  if (e.key === "ArrowDown") {
                    e.preventDefault();
                    setHighlightedIndex((prev) => (prev + 1) % filtered.length);
                  } else if (e.key === "ArrowUp") {
                    e.preventDefault();
                    setHighlightedIndex((prev) => (prev - 1 + filtered.length) % filtered.length);
                  } else if (e.key === "Enter") {
                    e.preventDefault();
                    if (filtered[highlightedIndex]) {
                      setTo(filtered[highlightedIndex]);
                      setDropdownOpen(false);
                    }
                  } else if (e.key === "Escape") {
                    setDropdownOpen(false);
                  }
                }}
                placeholder="nitin, #general, or an agent"
                className="flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground/70"
              />
              {to ? (
                <button
                  type="button"
                  onClick={() => {
                    setTo("");
                    inputRef.current?.focus();
                  }}
                  className="size-4 text-muted-foreground hover:text-foreground"
                >
                  <X className="size-3.5" />
                </button>
              ) : null}
            </div>

            {dropdownOpen && filtered.length > 0 ? (
              <div className="absolute top-full left-0 right-0 z-50 mt-1 max-h-56 overflow-y-auto rounded-lg border border-border bg-popover text-popover-foreground shadow-lg p-1">
                {filtered.map((item, idx) => {
                  const isChannel = item.startsWith("#");
                  const isAgent = item.includes("/") && !item.endsWith("/board");
                  const isHighlighted = idx === highlightedIndex;

                  return (
                    <div
                      key={item}
                      role="option"
                      aria-selected={isHighlighted}
                      onMouseDown={(e) => {
                        e.preventDefault();
                        setTo(item);
                        setDropdownOpen(false);
                      }}
                      onMouseEnter={() => setHighlightedIndex(idx)}
                      className={cn(
                        "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm cursor-pointer transition-colors",
                        isHighlighted ? "bg-accent text-accent-foreground font-medium" : "text-foreground hover:bg-accent/70"
                      )}
                    >
                      {isChannel ? (
                        <div className="flex size-5 items-center justify-center rounded bg-muted">
                          <Hash className="size-3.5 text-muted-foreground" />
                        </div>
                      ) : isAgent ? (
                        <div className="flex size-5 items-center justify-center rounded bg-[#c5b4e3]/20">
                          <Bot className="size-3.5 text-[#604988] dark:text-[#c5b4e3]" />
                        </div>
                      ) : (
                        <Avatar name={item} size={20} />
                      )}

                      <span className="truncate flex-1">{item}</span>

                      <span className="text-[10px] text-muted-foreground font-mono uppercase tracking-wider">
                        {isChannel ? "channel" : isAgent ? "agent" : "person"}
                      </span>
                    </div>
                  );
                })}
              </div>
            ) : null}
          </div>
        ) : null}
        {talk?.messages.map((m, i) => {
          const prev = talk.messages[i - 1];
          const grouped = prev && prev.from === m.from && m.ts - prev.ts < 5 * 60_000;
          const name = who(m.from, me);
          const isAgent = m.from.includes("/") && !m.from.endsWith("/board");

          // Detect gate ID in message text or type
          const gateMatch = /approval gate:\s*([0-9a-f]{32})/i.exec(m.text) || /gate:\s*([0-9a-f]{32})/i.exec(m.text);
          const gateId = gateMatch ? gateMatch[1] : null;

          // Detect patch
          const hasPatch = Boolean(m.patch || m.type === "patch" || m.text.includes("diff --git"));
          const patchContent = m.patch || (m.text.includes("diff --git") ? m.text : "");

          return (
            <div key={m.id} className={cn("group relative flex gap-3", grouped ? "mt-0.5" : "mt-4 first:mt-0")}>
              <div className="w-7 shrink-0">{grouped ? null : <Avatar name={personOf(m.from, me)} size={28} />}</div>
              <div className="min-w-0 flex-1">
                {grouped ? null : (
                  <div className="flex items-baseline gap-2">
                    <span className="font-semibold first-letter:uppercase">{name}</span>
                    {isAgent ? (
                      <span className="inline-flex items-center gap-1 rounded bg-accent/70 px-1.5 py-0.2 text-[10px] text-accent-foreground font-mono">
                        <Bot className="size-2.5" /> Agent
                      </span>
                    ) : null}
                    {m.to && !m.to.startsWith("#") && m.to !== `${me}/board` ? <span className="text-[12px] text-muted-foreground">to {who(m.to, me)}</span> : null}
                    <span className="text-[12px] text-muted-foreground">{ago(m.ts)}</span>
                  </div>
                )}
                
                {gateId ? (
                  <GateCard gateId={gateId} description={m.text} requestedBy={m.from} me={me} onResolved={onChanged} />
                ) : hasPatch && patchContent ? (
                  <>
                    <MessageContent text={m.text.replace(/diff --git[\s\S]*/, "").trim() || "Sent a patch:"} />
                    <PatchCard patch={patchContent} branch={m.branch} title={talk?.title} />
                  </>
                ) : (
                  <MessageContent text={m.text} />
                )}

                {/* Reactions and Quick Emoji Picker (Below the message) */}
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  {/* Reaction Chips */}
                  {m.reactions && Object.keys(m.reactions).length > 0 ? (
                    Object.entries(m.reactions).map(([emoji, users]) => {
                      const hasMine = users.includes(me);
                      return (
                        <button
                          key={emoji}
                          onClick={() => void handleReaction(m.id, emoji)}
                          className={cn(
                            "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium transition-colors cursor-pointer",
                            hasMine
                              ? "border-primary/40 bg-primary/10 text-primary font-semibold"
                              : "border-border/60 bg-muted/40 text-muted-foreground hover:bg-accent hover:text-foreground"
                          )}
                          title={users.join(", ")}
                        >
                          <span>{emoji}</span>
                          <span className="text-[11px] font-mono">{users.length}</span>
                        </button>
                      );
                    })
                  ) : null}

                  {/* Quick Reactions Bar - appears cleanly below the message on hover */}
                  <div className="opacity-0 group-hover:opacity-100 transition-opacity inline-flex items-center gap-0.5 rounded-full border border-border/70 bg-card/90 px-1 py-0.5 shadow-2xs backdrop-blur-xs text-xs">
                    {["👍", "❤️", "👀", "🚀", "✅"].map((emoji) => (
                      <button
                        key={emoji}
                        onClick={() => void handleReaction(m.id, emoji)}
                        className="size-5.5 rounded-full hover:bg-accent flex items-center justify-center transition-transform hover:scale-125 cursor-pointer text-xs"
                        title={`React ${emoji}`}
                      >
                        {emoji}
                      </button>
                    ))}
                  </div>
                </div>

                {m.files?.length ? (
                  <div className="mt-2 flex flex-col gap-2">
                    {m.files.map((f) => {
                      const isImage = /\.(png|jpe?g|webp|gif|svg)$/i.test(f.name);
                      const href = f.path ? `/api/board/files?path=${encodeURIComponent(f.path)}` : null;

                      if (isImage && href) {
                        return (
                          <div key={f.name} className="mt-1">
                            <a href={href} target="_blank" rel="noreferrer" className="block max-w-sm overflow-hidden rounded-lg border hover:opacity-95 transition-opacity">
                              <img src={href} alt={f.name} className="max-h-60 max-w-full rounded-md object-contain bg-muted/20" />
                            </a>
                            <span className="text-[11px] text-muted-foreground mt-0.5 block">
                              {f.name} {f.size ? `(${Math.round(f.size / 1024)} KB)` : ""}
                            </span>
                          </div>
                        );
                      }

                      const chip = (
                        <span className="inline-flex items-center gap-1.5 rounded-md border bg-muted/60 px-2.5 py-1 text-[12px] hover:bg-muted transition-colors">
                          <FileText className="size-3.5 text-muted-foreground shrink-0" />
                          <span className="font-medium text-foreground">{f.name}</span>
                          {f.size ? <span className="text-muted-foreground/70 text-[10px]">({Math.round(f.size / 1024)} KB)</span> : null}
                        </span>
                      );
                      return href ? (
                        <a key={f.name} href={href} target="_blank" rel="noreferrer" className="inline-block">
                          {chip}
                        </a>
                      ) : (
                        <span key={f.name} className="inline-block">{chip}</span>
                      );
                    })}
                  </div>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>

      {notice ? <p className="border-t bg-muted/40 px-4 py-2 text-xs text-muted-foreground">{notice}</p> : null}
      <Composer
        placeholder={talkId ? `Reply${talk?.channel ? ` in ${talk.channel}` : talk ? ` to ${who(defaultRecipient(talk, me), me)}` : ""}…` : "What do you want to talk about?"}
        people={people.filter((p) => !p.includes("/") && !p.startsWith("#")).map((p) => p.replace(/^@/, ""))}
        onSend={send}
        channel={(() => {
          const name = talk?.channel || (to.startsWith("#") ? to : "");
          const found = channels.find((c) => c.name === name);
          return found ? { name, members: found.members, add: async (owner: string) => { await api.channel("add", name, owner); onChanged(); } } : undefined;
        })()}
      />
    </section>
  );
}

function MessageContent({ text }: { text: string }) {
  if (!text.includes("```")) {
    return <div className="whitespace-pre-wrap break-words leading-relaxed">{text}</div>;
  }
  const parts: { type: "text" | "code"; content: string; lang?: string }[] = [];
  const regex = /```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g;
  let last = 0;
  let match;
  while ((match = regex.exec(text)) !== null) {
    if (match.index > last) parts.push({ type: "text", content: text.slice(last, match.index) });
    parts.push({ type: "code", lang: match[1] || "code", content: match[2] });
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push({ type: "text", content: text.slice(last) });

  return (
    <div className="space-y-1.5">
      {parts.map((p, i) =>
        p.type === "text" ? (
          <div key={i} className="whitespace-pre-wrap break-words leading-relaxed">{p.content}</div>
        ) : (
          <CodeSnippet key={i} lang={p.lang || "code"} code={p.content} />
        )
      )}
    </div>
  );
}

function CodeSnippet({ lang, code }: { lang: string; code: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="rounded-md border bg-muted/60 overflow-hidden font-mono text-xs my-1">
      <div className="flex items-center justify-between px-2.5 py-1 bg-muted/90 border-b text-[10px] text-muted-foreground font-semibold">
        <span>{lang}</span>
        <button onClick={copy} className="inline-flex items-center gap-1 hover:text-foreground text-[10px]">
          {copied ? <Check className="size-2.5 text-emerald-500" /> : <Copy className="size-2.5" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="p-2.5 overflow-x-auto text-[11px] leading-relaxed scroll-thin">{code.trim()}</pre>
    </div>
  );
}
