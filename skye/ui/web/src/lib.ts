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
