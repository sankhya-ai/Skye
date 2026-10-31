// Shortcut labels as the platform writes them: ⌘J on a Mac, Ctrl+J elsewhere.
// The handlers accept either modifier; only what the page shows differs.

const mac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

export function shortcut(key: string): string {
  return mac ? `⌘${key}` : `Ctrl+${key}`;
}
