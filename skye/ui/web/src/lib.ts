export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
export function formatTime(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "never";
  // journal timestamps are epoch seconds and may arrive stringified - a bare numeric
  // string must never render raw.
  const asNumber = typeof value === "number" ? value : Number(value);
  const d = Number.isFinite(asNumber) && asNumber > 1e9
    ? new Date(asNumber * 1000)
    : new Date(value);
  return Number.isNaN(d.getTime()) ? "unknown time" : d.toLocaleString();
}

export function formatRelative(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "never run";
  const asNumber = typeof value === "number" ? value : Number(value);
  const d = Number.isFinite(asNumber) && asNumber > 1e9
    ? new Date(asNumber * 1000)
    : new Date(value);
  if (Number.isNaN(d.getTime())) return "unknown time";
  const seconds = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (seconds < 45) return "just now";
  const minutes = seconds / 60;
  if (minutes < 60) return `${Math.round(minutes)} min ago`;
  const hours = minutes / 60;
  if (hours < 24) return `${Math.round(hours)} h ago`;
  const days = hours / 24;
  if (days < 7) return `${Math.round(days)} d ago`;
  return d.toLocaleDateString();
}

// Raw fetch/network messages ("Failed to fetch", "NetworkError ...") are implementation
// noise. Users get what happened, that nothing changed, and that retry is safe.
export function humanError(err: unknown, action: string): string {
  const msg = err instanceof Error ? err.message : String(err);
  if (/failed to fetch|networkerror|load failed|connection/i.test(msg)) {
    return `Couldn't reach the server. ${action} didn't happen - try again.`;
  }
  return msg;
}
