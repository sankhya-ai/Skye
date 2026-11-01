export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
export function formatTime(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "never";
  const d = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString();
}
