import { ChevronDown, ClipboardList, FilePen, ShieldCheck, Zap } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { modeLabel } from "../lib/transcript.ts";
import type { PermissionMode } from "../lib/types.ts";

const MODES: { mode: PermissionMode; icon: ReactNode; description: string }[] = [
  { mode: "default", icon: <ShieldCheck size={15} />, description: "Read-only commands run; ask before edits and other commands." },
  { mode: "accept_edits", icon: <FilePen size={15} />, description: "Edits and the project's own checks run; ask before anything else." },
  { mode: "plan", icon: <ClipboardList size={15} />, description: "Investigate and propose a plan. No edits." },
  { mode: "yolo", icon: <Zap size={15} />, description: "Approve everything automatically. Use with care." },
];

export const MODE_ORDER = MODES.map((m) => m.mode);

export function ModeMenu({ mode, onChange, down = false }: { mode: PermissionMode; onChange: (mode: PermissionMode) => void; down?: boolean }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  const current = MODES.find((m) => m.mode === mode) ?? MODES[0];
  return (
    <div className={`menuAnchor${down ? " down" : ""}`} ref={root}>
      <button className={`chip${mode === "yolo" ? " warn" : ""}`} onClick={() => setOpen((v) => !v)} title="Permission mode (Shift+Tab cycles)">
        {current.icon}
        {modeLabel(mode)}
        <ChevronDown size={13} />
      </button>
      {open ? (
        <div className="popover">
          {MODES.map((m) => (
            <button
              key={m.mode}
              className={`menuOption${m.mode === mode ? " active" : ""}`}
              onClick={() => {
                onChange(m.mode);
                setOpen(false);
              }}
            >
              <strong>
                {m.icon}
                {modeLabel(m.mode)}
              </strong>
              <span className="desc">{m.description}</span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
