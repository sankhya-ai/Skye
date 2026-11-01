// Claude Code account + usage limits, always visible in the sidebar so the owner does not
// have to keep checking the website. Sources (both free, no tokens spent): the CLI's own
// `claude auth status`, and the rate-limit frames Claude Code reports during Skye's runs.
// Between runs the numbers are "as of" the last run - the card says so.
import { ExternalLink, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { getClaudeUsage, type ClaudeUsage, type UsageWindow } from "../api";

const WINDOW_LABEL: Record<string, string> = {
  five_hour: "5-hour limit",
  seven_day: "Weekly limit",
  seven_day_opus: "Weekly · Opus",
  seven_day_sonnet: "Weekly · Sonnet",
  overage: "Extra usage",
};

function until(epochSeconds: number): string {
  const ms = epochSeconds * 1000 - Date.now();
  if (ms <= 0) return "now";
  const mins = Math.round(ms / 60000);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 48) return `${hours}h ${mins % 60}m`;
  return `${Math.round(hours / 24)}d`;
}

function ago(epochSeconds?: number): string {
  if (!epochSeconds) return "";
  const mins = Math.round((Date.now() - epochSeconds * 1000) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  return hours < 48 ? `${hours}h ago` : `${Math.round(hours / 24)}d ago`;
}

function statusText(w: UsageWindow): { text: string; tone: string } {
  if (w.expired) return { text: "reset since last run", tone: "text-muted-foreground" };
  if (w.status === "rejected") return { text: "limit reached", tone: "text-destructive" };
  if (w.status === "allowed_warning") return { text: "close to the limit", tone: "text-warning" };
  return { text: "available", tone: "text-success" };
}

function WindowRow({ w }: { w: UsageWindow }) {
  const status = statusText(w);
  const pct = typeof w.utilization === "number" && !w.expired
    ? Math.max(0, Math.min(100, Math.round(w.utilization * (w.utilization <= 1 ? 100 : 1))))
    : null;
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs text-foreground/80">{WINDOW_LABEL[w.window] ?? w.window}</span>
        <span className={`text-[11px] ${status.tone}`}>
          {pct !== null ? `${100 - pct}% left` : status.text}
        </span>
      </div>
      {pct !== null ? (
        <div className="h-1 overflow-hidden rounded-full bg-foreground/10" aria-hidden>
          <div
            className={`h-full rounded-full ${pct >= 90 ? "bg-destructive" : pct >= 70 ? "bg-warning" : "bg-success"}`}
            style={{ width: `${pct}%` }}
          />
        </div>
      ) : null}
      {w.resetsAt && !w.expired ? (
        <p className="text-[11px] text-muted-foreground">resets in {until(w.resetsAt)}</p>
      ) : null}
    </div>
  );
}

export function ClaudeUsageCard() {
  const [data, setData] = useState<ClaudeUsage | null>(null);
  const [failed, setFailed] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const load = (refresh = false) =>
    getClaudeUsage(refresh)
      .then((d) => { setData(d); setFailed(false); })
      .catch(() => setFailed(true));

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 20000);
    return () => clearInterval(timer);
  }, []);

  if (failed && !data) return null;
  if (!data) return null;
  const { account, windows, skye_totals: totals } = data;
  const lastSeen = windows.reduce((m, w) => Math.max(m, w.seen_at ?? 0), 0);

  return (
    <section
      aria-label="Claude Code usage"
      className="mx-1.5 mb-1.5 space-y-2 rounded-sm border border-border bg-card/70 p-2.5"
    >
      <div className="flex items-center justify-between gap-2">
        <p className="flex items-center gap-1.5 text-xs font-medium text-foreground">
          <span
            className={`inline-block size-1.5 rounded-full ${account.logged_in ? "bg-success" : "bg-destructive"}`}
            aria-hidden
          />
          Claude Code{account.plan ? ` · ${account.plan.charAt(0).toUpperCase()}${account.plan.slice(1)}` : ""}
        </p>
        <button
          type="button"
          onClick={() => { setRefreshing(true); void load(true).finally(() => setRefreshing(false)); }}
          className="rounded-sm p-0.5 text-muted-foreground hover:text-foreground"
          aria-label="Refresh Claude Code status"
          title="Re-check login (free - no tokens used)"
        >
          <RefreshCw className={`size-3 ${refreshing ? "animate-spin" : ""}`} />
        </button>
      </div>
      {!account.installed ? (
        <p className="text-[11px] leading-4 text-destructive">
          Not installed. Get it at claude.com/claude-code, run <code>claude</code> once and log in.
        </p>
      ) : !account.logged_in ? (
        <p className="text-[11px] leading-4 text-destructive">
          Not logged in. Run <code>claude</code> in a terminal and log in.
        </p>
      ) : windows.length > 0 ? (
        <div className="space-y-2">
          {windows.map((w) => <WindowRow key={w.window} w={w} />)}
          <p className="text-[11px] text-muted-foreground">as of the last run, {ago(lastSeen)}</p>
        </div>
      ) : (
        <p className="text-[11px] leading-4 text-muted-foreground">
          Limits appear here after the team's first run.
        </p>
      )}
      {totals.runs ? (
        <p className="text-[11px] leading-4 text-muted-foreground">
          Skye used {totals.runs} session{totals.runs === 1 ? "" : "s"}, {totals.turns ?? 0} turns
          {typeof totals.cost_usd === "number" && totals.cost_usd > 0
            ? ` (≈$${totals.cost_usd.toFixed(2)} at API prices)` : ""}
        </p>
      ) : null}
      <a
        href={data.usage_page}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
      >
        Full usage on claude.ai <ExternalLink className="size-3" />
      </a>
    </section>
  );
}
