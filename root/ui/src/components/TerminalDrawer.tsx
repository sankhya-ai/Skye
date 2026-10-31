// The terminal as a drawer under the conversation, as in VS Code and Codex:
// the main column's full width, a height the owner drags (and root remembers),
// toggled with ⌘J. Mounted on first open and kept while hidden.

import { X } from "lucide-react";
import { lazy, Suspense, useRef, useState, type PointerEvent } from "react";
import { shortcut } from "../lib/keys.ts";

// xterm.js is loaded the first time the terminal opens, not with the page.
const TerminalPanel = lazy(() => import("./TerminalPanel.tsx"));

const MIN_HEIGHT = 140;
/** What the conversation keeps when the drawer is dragged up: its header and the composer. */
const KEEP_FOR_CHAT = 300;

function stored(): number {
  try {
    return Number(localStorage.getItem("root:terminal-height")) || 300;
  } catch {
    return 300;
  }
}

export function TerminalDrawer({ sessionId, open, onClose }: { sessionId: string; open: boolean; onClose: () => void }) {
  const [opened, setOpened] = useState(open);
  const [height, setHeight] = useState(stored);
  const drag = useRef<{ y: number; height: number } | null>(null);
  const root = useRef<HTMLDivElement>(null);
  if (open && !opened) setOpened(true);
  if (!opened) return null;

  const limit = (value: number) => {
    const main = root.current?.parentElement?.clientHeight ?? window.innerHeight;
    return Math.round(Math.max(MIN_HEIGHT, Math.min(value, main - KEEP_FOR_CHAT)));
  };
  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { y: event.clientY, height };
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current) setHeight(limit(drag.current.height + drag.current.y - event.clientY));
  };
  const onPointerUp = () => {
    drag.current = null;
    try {
      localStorage.setItem("root:terminal-height", String(height));
    } catch {
      /* storage unavailable */
    }
  };

  return (
    <div ref={root} className="terminalDrawer" hidden={!open} style={{ height: limit(height) }}>
      <div
        className="terminalResize"
        role="separator"
        aria-orientation="horizontal"
        title="Drag to resize"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onDoubleClick={() => setHeight(limit(height < 420 ? 9999 : 300))}
      />
      <div className="terminalHead">
        <span className="small">Terminal</span>
        <span className="faint small hideNarrow">
          <span className="kbd">{shortcut("J")}</span> to hide
        </span>
        <span className="spacer" />
        <button className="iconButton" onClick={onClose} title={`Hide the terminal (${shortcut("J")}) — it keeps running`} aria-label="Hide terminal">
          <X size={14} />
        </button>
      </div>
      <Suspense fallback={<p className="help terminalLoading">Opening a terminal…</p>}>
        <TerminalPanel sessionId={sessionId} hidden={!open} />
      </Suspense>
    </div>
  );
}
