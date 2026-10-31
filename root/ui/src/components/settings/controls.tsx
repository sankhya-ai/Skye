// The pieces every settings section is built from: a titled group of rows, a
// row (label and help on the left, its control on the right), and controls
// that save as the owner changes them — no Save button anywhere.

import { useEffect, useState, type ReactNode } from "react";

export function Section({ title, lede, children }: { title: string; lede?: ReactNode; children: ReactNode }) {
  return (
    <div className="settingsSection">
      <h1>{title}</h1>
      {lede ? <p className="settingsLede">{lede}</p> : null}
      {children}
    </div>
  );
}

export function Group({ title, action, children }: { title?: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="settingsGroupWrap">
      {title || action ? (
        <div className="settingsGroupHead">
          {title ? <h2>{title}</h2> : <span />}
          {action}
        </div>
      ) : null}
      <div className="settingsGroup">{children}</div>
    </section>
  );
}

export function Row({ label, help, children, stacked }: { label: ReactNode; help?: ReactNode; children?: ReactNode; stacked?: boolean }) {
  return (
    <div className={`settingsRow${stacked ? " stacked" : ""}`}>
      <div className="settingsRowLabel">
        <div className="settingsRowTitle">{label}</div>
        {help ? <div className="settingsRowHelp">{help}</div> : null}
      </div>
      {children !== undefined ? <div className="settingsRowControl">{children}</div> : null}
    </div>
  );
}

export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (value: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={`switch${checked ? " on" : ""}`}
      onClick={() => onChange(!checked)}
    >
      <span className="knob" />
    </button>
  );
}

export function Segmented<T extends string>({ value, options, onChange }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="segmented">
      {options.map((o) => (
        <button key={o.value} type="button" className={o.value === value ? "active" : ""} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** A text field that saves on Enter or when it loses focus, if it changed. */
export function TextField({
  value,
  onSave,
  placeholder,
  mono,
  type = "text",
  width,
}: {
  value: string;
  onSave: (value: string) => void;
  placeholder?: string;
  mono?: boolean;
  type?: "text" | "number" | "password";
  width?: number;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const commit = () => {
    if (draft !== value) onSave(draft);
  };
  return (
    <input
      className={`input${mono ? " mono" : ""}`}
      style={width ? { width } : undefined}
      type={type}
      value={draft}
      placeholder={placeholder}
      autoComplete="off"
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") setDraft(value);
      }}
    />
  );
}

/** A secret: never shown, replaced by typing a new one. */
export function SecretField({ isSet, hint, onSave, onClear, placeholder, locked }: { isSet: boolean; hint?: string; onSave: (value: string) => void; onClear?: () => void; placeholder: string; locked?: string }) {
  const [draft, setDraft] = useState("");
  if (locked) return <span className="settingsValue">{locked}</span>;
  return (
    <div className="settingsInline">
      <input
        className="input"
        type="password"
        autoComplete="off"
        placeholder={isSet ? `Saved${hint ? ` (${hint})` : ""}` : placeholder}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && draft.trim()) {
            onSave(draft.trim());
            setDraft("");
          }
        }}
      />
      {draft.trim() ? (
        <button
          type="button"
          className="pill ink"
          onClick={() => {
            onSave(draft.trim());
            setDraft("");
          }}
        >
          Save
        </button>
      ) : isSet && onClear ? (
        <button type="button" className="pill" onClick={onClear}>
          Remove
        </button>
      ) : null}
    </div>
  );
}
