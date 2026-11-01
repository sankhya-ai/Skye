// The mission's team line: what the workers said to each other while working. Polled with
// a server cursor - entries are keyed by seq so refreshes never duplicate or drop one, and
// a failed read is "unavailable", never "empty".
import { useEffect, useRef, useState } from "react";
import { getDiscussion, type DiscussionEntry } from "../api";
import { formatRelative } from "../lib";

const KIND_LABEL: Record<string, string> = {
  note: "",
  broadcast: "to everyone",
  question: "asked",
  answer: "answered",
  "contract-change": "changed the plan",
  ack: "acknowledged",
};

function Entry({ entry }: { entry: DiscussionEntry }) {
  const label = KIND_LABEL[entry.kind] ?? entry.kind;
  return (
    <div className="rounded-sm px-2 py-1.5 text-xs">
      <span className="font-medium text-foreground">{entry.author}</span>{" "}
      {label ? <span className="text-muted-foreground">{label}</span> : null}{" "}
      <span className="text-muted-foreground/70">{formatRelative(entry.ts)}</span>
      {entry.kind === "ack" && entry.ack_of ? (
        <span className="text-muted-foreground"> the plan change</span>
      ) : null}
      {entry.body ? (
        <p className="mt-0.5 whitespace-pre-wrap text-muted-foreground">{entry.body}</p>
      ) : null}
      {entry.kind === "contract-change" &&
      entry.waiting_on &&
      entry.waiting_on.length > 0 ? (
        <p className="mt-0.5 text-amber-700">
          waiting on: {entry.waiting_on.join(", ")}
        </p>
      ) : null}
    </div>
  );
}

export function DiscussionPanel({ missionId }: { missionId: string }) {
  const [entries, setEntries] = useState<Map<number, DiscussionEntry>>(new Map());
  const [error, setError] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const cursor = useRef(0);

  useEffect(() => {
    // A mission change or server restart must not carry a stale cursor: start over.
    setEntries(new Map());
    setLoaded(false);
    setError(false);
    cursor.current = 0;
    let stop = false;
    const poll = async () => {
      try {
        const page = await getDiscussion(missionId, cursor.current);
        if (stop) return;
        cursor.current = page.cursor;
        if (page.entries.length > 0) {
          setEntries((prev) => {
            const next = new Map(prev);
            for (const entry of page.entries) next.set(entry.seq, entry);
            return next;
          });
        }
        setError(false);
        setLoaded(true);
      } catch {
        if (!stop) setError(true);
      }
    };
    void poll();
    const timer = setInterval(() => void poll(), 2000);
    return () => {
      stop = true;
      clearInterval(timer);
    };
  }, [missionId]);

  const ordered = [...entries.values()].sort((a, b) => a.seq - b.seq);
  return (
    <details className="rounded-md border border-border bg-card p-4" open={ordered.length > 0}>
      <summary className="cursor-pointer text-sm font-medium text-foreground">
        Team discussion{ordered.length > 0 ? ` (${ordered.length})` : ""}
      </summary>
      <div className="mt-3 max-h-64 space-y-0.5 overflow-auto">
        {ordered.map((entry) => (
          <Entry key={entry.seq} entry={entry} />
        ))}
        {ordered.length === 0 && error && !loaded ? (
          <p className="text-xs text-muted-foreground">
            Discussion unavailable - retrying in the background.
          </p>
        ) : null}
        {ordered.length === 0 && loaded && !error ? (
          <p className="text-xs text-muted-foreground">No discussion recorded yet.</p>
        ) : null}
        {ordered.length > 0 && error ? (
          <p className="text-xs text-muted-foreground/70">
            Updates paused - retrying in the background.
          </p>
        ) : null}
      </div>
    </details>
  );
}
