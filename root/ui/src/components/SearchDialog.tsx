// Search, as Tura has it: a dialog over the app. Recent sessions before you
// type; then sessions whose title matches, and sessions where anything said
// matches (session search). Enter opens the first, Escape closes.

import { Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { api } from "../lib/api.ts";
import type { SearchHit, Session } from "../lib/types.ts";

function basename(path: string): string {
  return path.split("/").filter(Boolean).pop() ?? path;
}

function when(ts: number): string {
  const date = new Date(ts * 1000);
  return new Date().toDateString() === date.toDateString()
    ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString([], { day: "numeric", month: "short" });
}

interface Result {
  id: string;
  title: string;
  detail: string;
}

export function SearchDialog({ sessions, onOpen, onClose }: { sessions: Session[]; onOpen: (id: string) => void; onClose: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);

  useEffect(() => {
    input.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setHits([]);
      return;
    }
    const timer = setTimeout(() => api.search(q).then((d) => setHits(d.results), () => setHits([])), 180);
    return () => clearTimeout(timer);
  }, [query]);

  const results: Result[] = useMemo(() => {
    const q = query.trim().toLowerCase();
    const live = sessions.filter((s) => s.kind === "session");
    if (!q) return live.slice(0, 9).map((s) => ({ id: s.id, title: s.title, detail: `${basename(s.workspace)} · ${when(s.updated_at)}` }));
    const byTitle = live.filter((s) => s.title.toLowerCase().includes(q)).map((s) => ({ id: s.id, title: s.title, detail: basename(s.workspace) }));
    const seen = new Set(byTitle.map((r) => r.id));
    const byContent = hits
      .filter((h) => !seen.has(h.session_id) && (seen.add(h.session_id) || true))
      .map((h) => ({ id: h.session_id, title: h.title, detail: (h.reply || h.prompt).replace(/[[\]]/g, "") }));
    return [...byTitle, ...byContent].slice(0, 12);
  }, [query, sessions, hits]);

  const open = (id: string) => {
    onOpen(id);
    onClose();
  };

  return createPortal(
    <div className="searchOverlay" role="presentation" onMouseDown={onClose}>
      <section className="searchDialog" role="dialog" aria-modal="true" aria-label="Search sessions" onMouseDown={(e) => e.stopPropagation()}>
        <div className="searchInputRow">
          <Search size={18} />
          <input
            ref={input}
            value={query}
            placeholder="Search sessions"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && results[0]) open(results[0].id);
            }}
          />
          <button type="button" onClick={onClose} aria-label="Close search">
            <X size={16} />
          </button>
        </div>
        <div className="searchResults">
          <span>{query.trim() ? "Results" : "Recent"}</span>
          {results.map((result) => (
            <button type="button" key={result.id} className="searchResult" onClick={() => open(result.id)}>
              <i aria-hidden="true" />
              <strong>{result.title}</strong>
              <small>{result.detail}</small>
            </button>
          ))}
          {results.length === 0 ? <p className="searchEmpty">{query.trim() ? "No session matches." : "No sessions yet."}</p> : null}
        </div>
      </section>
    </div>,
    document.body,
  );
}
