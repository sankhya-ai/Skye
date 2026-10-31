// The macOS app's side of the page (macos/Sources/Root/Bridge.swift), when root
// runs inside it: a native folder picker, notifications, the Dock badge.
// In a browser there is no bridge and callers fall back to the web UI.

export interface NativeBridge {
  version: string;
  pickFolder(): Promise<string | null>;
  notify(title: string, body: string, sessionId?: string): void;
  setBadge(count: number): void;
  revealInFinder(path: string): void;
}

declare global {
  interface Window {
    rootNative?: NativeBridge;
    /** Asked by the app before it quits: how many sessions are still working. */
    __rootBusy?: () => number;
  }
}

export const native: NativeBridge | null = typeof window !== "undefined" ? (window.rootNative ?? null) : null;

/** What changed between two polls of the session list that the owner should hear about. */
export function sessionNews(
  before: Map<string, { running: boolean; pending: number }>,
  sessions: { id: string; title: string; running: boolean; pending_approvals?: number }[],
): { finished: { id: string; title: string }[]; asking: { id: string; title: string }[]; pending: number } {
  const finished = [];
  const asking = [];
  let pending = 0;
  for (const s of sessions) {
    const was = before.get(s.id);
    const waiting = s.pending_approvals ?? 0;
    pending += waiting;
    if (was?.running && !s.running) finished.push({ id: s.id, title: s.title });
    if (waiting > (was?.pending ?? 0)) asking.push({ id: s.id, title: s.title });
  }
  return { finished, asking, pending };
}
