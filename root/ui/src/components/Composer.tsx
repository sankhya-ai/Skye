import { ArrowUp, Paperclip, Square, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import type { Attachment, SlashCommand } from "../lib/types.ts";

const HOST_COMMANDS: SlashCommand[] = [
  { name: "verify", description: "Run automated tests & verification suite", aliases: ["test", "check"] },
  { name: "plan", description: "Toggle plan mode (think & propose steps without writing files)", aliases: [] },
  { name: "diff", description: "Inspect uncommitted code changes in the inspector", aliases: ["changes"] },
  { name: "yolo", description: "Toggle auto-approve for every action", aliases: [] },
  { name: "init", description: "Analyze codebase architecture and generate AGENTS.md", aliases: [] },
  { name: "compact", description: "Compact session context to save token budget", aliases: [] },
  { name: "clear", description: "Clear context and start a fresh session turn", aliases: ["reset"] },
];
const MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024;

interface Suggestion {
  label: string;
  detail: string;
  insert: string;
}

function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export function Composer({
  running,
  disabled,
  placeholder,
  slashCommands,
  files,
  onSend,
  onStop,
  draftKey,
  chips,
}: {
  chips?: ReactNode;
  running: boolean;
  disabled: boolean;
  placeholder: string;
  slashCommands: SlashCommand[];
  files: string[];
  onSend: (text: string, attachments: Attachment[]) => void;
  onStop: () => void;
  draftKey: string;
}) {
  const [text, setText] = useState(() => {
    try {
      return localStorage.getItem(`draft:${draftKey}`) ?? "";
    } catch {
      return "";
    }
  });
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [active, setActive] = useState(0);
  const [caret, setCaret] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);

  useEffect(() => {
    try {
      if (text) localStorage.setItem(`draft:${draftKey}`, text);
      else localStorage.removeItem(`draft:${draftKey}`);
    } catch {
      /* storage unavailable */
    }
  }, [text, draftKey]);

  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 260)}px`;
  }, [text]);

  useEffect(() => {
    area.current?.focus();
  }, [draftKey]);

  const suggestions: Suggestion[] = useMemo(() => {
    if (dismissed) return [];
    const before = text.slice(0, caret);
    const slash = /^\/([\w:-]*)$/.exec(before);
    if (slash) {
      const query = slash[1].toLowerCase();
      const byName = new Map<string, SlashCommand>();
      for (const command of [...slashCommands, ...HOST_COMMANDS]) byName.set(command.name, command);
      return [...byName.values()]
        .filter((c) => c.name.toLowerCase().includes(query))
        .slice(0, 12)
        .map((c) => ({ label: `/${c.name}`, detail: c.description, insert: `/${c.name} ` }));
    }
    const mention = /(?:^|\s)@([\w./-]*)$/.exec(before);
    if (mention) {
      const query = mention[1].toLowerCase();
      const ranked = files
        .filter((f) => f.toLowerCase().includes(query))
        .sort((a, b) => {
          const an = a.toLowerCase().split("/").pop()!.startsWith(query) ? 0 : 1;
          const bn = b.toLowerCase().split("/").pop()!.startsWith(query) ? 0 : 1;
          return an - bn || a.length - b.length;
        })
        .slice(0, 10);
      return ranked.map((f) => ({ label: f, detail: "", insert: `@${f} ` }));
    }
    return [];
  }, [text, caret, slashCommands, files, dismissed]);

  useEffect(() => setActive(0), [suggestions.length]);

  const accept = (suggestion: Suggestion) => {
    const before = text.slice(0, caret);
    const after = text.slice(caret);
    const start = before.startsWith("/") && !before.includes(" ") ? 0 : before.lastIndexOf("@");
    const next = before.slice(0, start) + suggestion.insert + after;
    setText(next);
    const position = start + suggestion.insert.length;
    requestAnimationFrame(() => {
      area.current?.setSelectionRange(position, position);
      setCaret(position);
    });
  };

  const submit = () => {
    const value = text.trim();
    if ((!value && !attachments.length) || disabled) return;
    onSend(value, attachments);
    setText("");
    setAttachments([]);
    setDismissed(false);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (suggestions.length) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setActive((a) => (a + 1) % suggestions.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setActive((a) => (a - 1 + suggestions.length) % suggestions.length);
        return;
      }
      if (event.key === "Tab" || (event.key === "Enter" && !event.shiftKey)) {
        event.preventDefault();
        accept(suggestions[active]);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setDismissed(true);
        return;
      }
    }
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  const addFiles = async (list: FileList | File[]) => {
    const added: Attachment[] = [];
    for (const file of Array.from(list)) {
      if (file.size > MAX_ATTACHMENT_BYTES) continue;
      const data = await readAsBase64(file);
      added.push({
        name: file.name || "pasted-image.png",
        mime: file.type || "application/octet-stream",
        data,
        preview: file.type.startsWith("image/") ? URL.createObjectURL(file) : undefined,
      });
    }
    setAttachments((current) => [...current, ...added]);
  };

  return (
    <div
      className="composer"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        if (e.dataTransfer.files.length) void addFiles(e.dataTransfer.files);
      }}
    >
      {suggestions.length ? (
        <div className="popover scroll" role="listbox">
          {suggestions.map((s, index) => (
            <button
              key={s.label}
              className={`popItem${index === active ? " active" : ""}`}
              onMouseDown={(e) => {
                e.preventDefault();
                accept(s);
              }}
            >
              <span className="name">{s.label}</span>
              <span className="desc">{s.detail}</span>
            </button>
          ))}
        </div>
      ) : null}
      {attachments.length ? (
        <div className="attachments">
          {attachments.map((a, index) => (
            <span key={index} className="attachment">
              {a.preview ? <img src={a.preview} alt="" /> : <Paperclip size={13} />}
              {a.name}
              <button className="iconButton" style={{ minHeight: 22, minWidth: 22, padding: 0 }} onClick={() => setAttachments((c) => c.filter((_, i) => i !== index))} aria-label={`Remove ${a.name}`}>
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      ) : null}
      <textarea
        ref={area}
        rows={1}
        value={text}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(e) => {
          setText(e.target.value);
          setCaret(e.target.selectionStart ?? e.target.value.length);
          setDismissed(false);
        }}
        onSelect={(e) => setCaret(e.currentTarget.selectionStart ?? 0)}
        onKeyDown={onKeyDown}
        onPaste={(e) => {
          const images = Array.from(e.clipboardData.files).filter((f) => f.type.startsWith("image/"));
          if (images.length) {
            e.preventDefault();
            void addFiles(images);
          }
        }}
        aria-label="Message root"
      />
      {running && !text.trim() && !attachments.length ? (
        <button className="sendButton" onClick={onStop} title="Stop (Esc)" aria-label="Stop">
          <Square size={13} fill="currentColor" />
        </button>
      ) : (
        <button
          className="sendButton"
          onClick={submit}
          disabled={disabled || (!text.trim() && !attachments.length)}
          title={running ? "Queue for after this turn (Enter)" : "Send (Enter)"}
          aria-label={running ? "Queue" : "Send"}
        >
          <ArrowUp size={18} />
        </button>
      )}
      <div className="composerBar">
        <button className="chip" onClick={() => picker.current?.click()} title="Attach files or images" disabled={disabled} aria-label="Attach">
          <Paperclip size={15} />
        </button>
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files) void addFiles(e.target.files);
            e.target.value = "";
          }}
        />
        {chips}
        <span className="spacer" />
        <span className="faint small hideNarrow">
          <span className="kbd">/</span> commands · <span className="kbd">@</span> files
        </span>
      </div>
    </div>
  );
}
