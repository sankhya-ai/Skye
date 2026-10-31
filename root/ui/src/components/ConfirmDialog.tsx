import { AlertTriangle, X } from "lucide-react";
import { useState } from "react";

import { useEscape } from "../lib/useEscape.ts";

export function ConfirmDialog({
  title,
  message,
  confirmText = "Delete",
  cancelText = "Cancel",
  danger = true,
  onClose,
  onConfirm,
}: {
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
  onClose: () => void;
  onConfirm: () => Promise<void> | void;
}) {
  useEscape(onClose);
  const [busy, setBusy] = useState(false);

  const handleConfirm = async () => {
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="backdrop" onMouseDown={onClose}>
      <div
        className="dialog confirmDialog"
        onMouseDown={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={title}
      >
        <div className="dialogHead">
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            {danger ? <AlertTriangle size={17} style={{ color: "var(--err)" }} /> : null}
            <h2>{title}</h2>
          </div>
          <button
            type="button"
            className="iconButton"
            onClick={onClose}
            title="Close"
            aria-label="Close"
          >
            <X size={15} />
          </button>
        </div>
        <div className="dialogBody">
          <p style={{ margin: "4px 0 8px", fontSize: "13.5px", lineHeight: "1.5", color: "var(--ink-muted)" }}>
            {message}
          </p>
        </div>
        <div className="dialogFoot">
          <button
            type="button"
            className="pill"
            onClick={onClose}
            disabled={busy}
          >
            {cancelText}
          </button>
          <button
            type="button"
            className={`pill ink ${danger ? "danger" : ""}`}
            onClick={() => void handleConfirm()}
            disabled={busy}
          >
            {busy ? "Deleting…" : confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}
