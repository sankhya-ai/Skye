import { AlertTriangle, GitCommit, Search, Sparkles, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, type SearchHit } from "@/lib/api";

interface SearchBarProps {
  onOpenTalk: (id: string) => void;
}

export function SearchBar({ onOpenTalk }: SearchBarProps) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!query.trim()) {
      setHits([]);
      setOpen(false);
      return;
    }

    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const res = await api.search(query.trim());
        setHits(res.hits || []);
        setOpen(true);
        setSelectedIndex(0);
      } catch {
        setHits([]);
      } finally {
        setLoading(false);
      }
    }, 150);

    return () => clearTimeout(timer);
  }, [query]);

  // Click outside to dismiss dropdown
  useEffect(() => {
    const onMouseDown = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, []);

  const handleSelect = (hit: SearchHit) => {
    setOpen(false);
    setQuery("");
    if (hit.talkId) onOpenTalk(hit.talkId);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!open || !hits.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((prev) => (prev + 1) % hits.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((prev) => (prev - 1 + hits.length) % hits.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (hits[selectedIndex]) handleSelect(hits[selectedIndex]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div ref={containerRef} className="relative w-72 max-w-sm">
      <div className="relative flex items-center">
        <Search className="pointer-events-none absolute left-2.5 size-3.5 text-muted-foreground" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => query.trim() && setOpen(true)}
          onKeyDown={handleKeyDown}
          placeholder="Search talks, incidents, memory..."
          className="h-8 w-full rounded-md border bg-muted/40 pl-8 pr-7 text-xs text-foreground placeholder:text-muted-foreground focus:border-foreground/30 focus:bg-background focus:outline-none"
        />
        {query ? (
          <button
            onClick={() => {
              setQuery("");
              setHits([]);
              setOpen(false);
            }}
            className="absolute right-2 text-muted-foreground hover:text-foreground"
          >
            <X className="size-3.5" />
          </button>
        ) : (
          <kbd className="pointer-events-none absolute right-2 select-none rounded border bg-muted px-1 text-[10px] text-muted-foreground">
            ⌘K
          </kbd>
        )}
      </div>

      {open && (
        <div className="absolute left-0 right-0 top-9 z-50 max-h-80 overflow-y-auto rounded-lg border bg-popover p-1 text-popover-foreground shadow-lg">
          {loading ? (
            <div className="px-3 py-2 text-xs text-muted-foreground">Searching team memory & talks...</div>
          ) : hits.length === 0 ? (
            <div className="px-3 py-2 text-xs text-muted-foreground">No matching incidents or talks.</div>
          ) : (
            hits.map((hit, idx) => {
              const isSelected = idx === selectedIndex;
              const isIncident = hit.type === "incident" || /error|bug|fix/i.test(hit.text);
              const isPatch = hit.type === "patch";

              return (
                <button
                  key={hit.id}
                  onClick={() => handleSelect(hit)}
                  className={`flex w-full flex-col items-start gap-1 rounded-md px-3 py-2 text-left text-xs transition-colors ${
                    isSelected ? "bg-accent text-accent-foreground" : "hover:bg-muted"
                  }`}
                >
                  <div className="flex w-full items-center justify-between gap-2">
                    <span className="flex items-center gap-1.5 font-medium truncate">
                      {isIncident ? (
                        <AlertTriangle className="size-3 text-[#c5b4e3]" />
                      ) : isPatch ? (
                        <GitCommit className="size-3 text-[#a8cbbb]" />
                      ) : (
                        <Sparkles className="size-3 text-[#8db8d5]" />
                      )}
                      {hit.title || hit.citation}
                    </span>
                    <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase font-mono text-muted-foreground">
                      {hit.channel || hit.type}
                    </span>
                  </div>
                  <p className="line-clamp-2 text-[11px] text-muted-foreground leading-normal">
                    {hit.text.replace(/\n+/g, " ")}
                  </p>
                </button>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
