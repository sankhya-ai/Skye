import { ArrowUp, FileText, Paperclip, UploadCloud, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { type AttachedFile } from "@/lib/api";
import { cn } from "@/lib/utils";

function formatSize(bytes?: number): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface StagedAttachment extends AttachedFile {
  previewUrl?: string;
  isImage?: boolean;
}

function DropZoneOverlay() {
  return (
    <div className="pointer-events-none absolute inset-x-4 inset-y-0 z-20 flex items-center justify-center rounded-xl border-2 border-dashed border-primary bg-primary/10 backdrop-blur-[1px] animate-in fade-in duration-150">
      <span className="flex items-center gap-2 rounded-full bg-foreground px-4 py-2 text-xs font-semibold text-background shadow-md">
        <UploadCloud aria-hidden="true" className="size-4" />
        <span>Drop files to attach</span>
      </span>
    </div>
  );
}

/** Slack & Buzz-style composer: Enter sends, Shift+Enter is a new line, "@" mentions, file attachments & paste. */
export function Composer({
  placeholder,
  people,
  onSend,
  disabled,
  channel,
}: {
  placeholder: string;
  people: string[];
  onSend: (text: string, files?: AttachedFile[]) => Promise<void>;
  disabled?: boolean;
  /** In a channel: who is in it, and how to add someone, so a mention of an outsider isn't lost. */
  channel?: { name: string; members: string[]; add: (owner: string) => Promise<void> };
}) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<StagedAttachment[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pick, setPick] = useState(0);
  const [dragging, setDragging] = useState(false);
  // People @mentioned who aren't in this channel: they'd never see it. Asked before sending.
  const [outsiders, setOutsiders] = useState<string[]>([]);
  useEffect(() => setOutsiders([]), [text]);
  const area = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // The "@word" being typed at the cursor, if any.
  const caret = area.current?.selectionStart ?? text.length;
  const typing = /(?:^|\s)@([a-z0-9._-]*)$/i.exec(text.slice(0, caret));
  const matches = typing ? people.filter((p) => p.startsWith(typing[1].toLowerCase())).slice(0, 6) : [];

  const complete = (name: string) => {
    if (!typing) return;
    const start = caret - typing[1].length;
    const next = `${text.slice(0, start)}${name} ${text.slice(caret)}`;
    setText(next);
    requestAnimationFrame(() => {
      area.current?.focus();
      area.current?.setSelectionRange(start + name.length + 1, start + name.length + 1);
    });
  };

  const processFiles = async (fileList: FileList | File[]) => {
    const files = Array.from(fileList);
    if (!files.length) return;

    setError("");
    const newAttachments: StagedAttachment[] = [];

    for (const file of files) {
      if (file.size > 25 * 1024 * 1024) {
        setError(`"${file.name}" exceeds 25 MB limit.`);
        continue;
      }

      const isImage = file.type.startsWith("image/");
      const previewUrl = isImage ? URL.createObjectURL(file) : undefined;

      try {
        const base64 = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => {
            const res = String(reader.result || "");
            resolve(res.includes(",") ? res.split(",")[1] : res);
          };
          reader.onerror = reject;
          reader.readAsDataURL(file);
        });

        newAttachments.push({
          name: file.name,
          data: base64,
          size: file.size,
          previewUrl,
          isImage,
        });
      } catch {
        setError(`Failed to read "${file.name}"`);
      }
    }

    setAttachments((prev) => [...prev, ...newAttachments]);
  };

  const removeAttachment = (index: number) => {
    setAttachments((prev) => {
      const removed = prev[index];
      if (removed?.previewUrl) URL.revokeObjectURL(removed.previewUrl);
      return prev.filter((_, i) => i !== index);
    });
  };

  useEffect(() => {
    return () => {
      for (const a of attachments) {
        if (a.previewUrl) URL.revokeObjectURL(a.previewUrl);
      }
    };
  }, []);

  const handlePaste = (e: React.ClipboardEvent) => {
    if (e.clipboardData.files && e.clipboardData.files.length > 0) {
      e.preventDefault();
      void processFiles(e.clipboardData.files);
    }
  };

  const send = async (add: boolean | null = null) => {
    const body = text.trim();
    if ((!body && !attachments.length) || busy || disabled) return;
    if (channel && add === null) {
      const mentioned = [...body.matchAll(/(?:^|\s)@([a-z0-9._-]+)/gi)].map((m) => m[1].toLowerCase());
      const missing = [...new Set(mentioned)].filter((n) => people.includes(n) && !channel.members.includes(n));
      if (missing.length) return setOutsiders(missing);
    }
    setOutsiders([]);
    setBusy(true);
    setError("");
    try {
      if (add && channel) for (const name of outsiders) await channel.add(name);
      const outgoingFiles = attachments.map(({ name, data, size }) => ({ name, data, size }));
      await onSend(body || (outgoingFiles[0] ? `Attached: ${outgoingFiles.map((f) => f.name).join(", ")}` : ""), outgoingFiles);
      setText("");
      for (const a of attachments) {
        if (a.previewUrl) URL.revokeObjectURL(a.previewUrl);
      }
      setAttachments([]);
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="relative px-4 pb-4"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (e.dataTransfer.files) void processFiles(e.dataTransfer.files);
      }}
    >
      {dragging ? <DropZoneOverlay /> : null}

      {matches.length ? (
        <div className="absolute bottom-full left-4 mb-1 w-56 overflow-hidden rounded-lg border bg-popover p-1 shadow-md z-30">
          {matches.map((p, i) => (
            <button
              key={p}
              onMouseDown={(e) => (e.preventDefault(), complete(p))}
              className={cn("flex w-full items-center rounded-md px-2 py-1 text-left text-sm", i === pick % matches.length && "bg-accent")}
            >
              @{p}
            </button>
          ))}
        </div>
      ) : null}

      <div
        className={cn(
          "flex flex-col rounded-xl border bg-background px-3 py-2 shadow-xs transition-colors focus-within:border-ring",
          dragging && "border-primary bg-accent/20"
        )}
      >
        {/* Attachment preview tray */}
        {attachments.length ? (
          <div className="mb-2 flex flex-wrap gap-2 pt-1 border-b pb-2">
            {attachments.map((file, i) => (
              <div
                key={`${file.name}-${i}`}
                className="group relative flex items-center gap-1.5 rounded-lg border bg-muted/60 py-1 pl-1.5 pr-2 text-xs shadow-2xs hover:bg-muted transition-colors"
              >
                {file.isImage && file.previewUrl ? (
                  <img src={file.previewUrl} alt={file.name} className="size-6 rounded object-cover border" />
                ) : (
                  <FileText className="size-4 text-muted-foreground" />
                )}
                <span className="max-w-[140px] truncate font-medium text-foreground">{file.name}</span>
                <span className="text-[10px] text-muted-foreground">({formatSize(file.size)})</span>
                <button
                  type="button"
                  onClick={() => removeAttachment(i)}
                  title={`Remove ${file.name}`}
                  className="ml-1 flex size-4 items-center justify-center rounded-full bg-muted-foreground/15 text-muted-foreground hover:bg-destructive hover:text-destructive-foreground transition-colors"
                >
                  <X className="size-2.5" />
                </button>
              </div>
            ))}
          </div>
        ) : null}

        <div className="flex items-end gap-2">
          {/* File attachment button */}
          <input
            type="file"
            multiple
            ref={fileInputRef}
            onChange={(e) => {
              if (e.target.files) void processFiles(e.target.files);
              e.target.value = "";
            }}
            className="hidden"
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            title="Attach files (images, code, documents up to 25 MB)"
            className="inline-flex size-7 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <Paperclip className="size-4" />
          </button>

          <textarea
            ref={area}
            rows={1}
            value={text}
            disabled={disabled}
            placeholder={attachments.length ? "Add a message or press enter to send…" : placeholder}
            onChange={(e) => (setText(e.target.value), setPick(0))}
            onPaste={handlePaste}
            onKeyDown={(e) => {
              if (matches.length && (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey))) {
                e.preventDefault();
                complete(matches[pick % matches.length]);
              } else if (matches.length && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
                e.preventDefault();
                setPick((v) => v + (e.key === "ArrowDown" ? 1 : matches.length - 1));
              } else if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            className="max-h-48 min-h-6 flex-1 resize-none bg-transparent leading-6 outline-none [field-sizing:content] placeholder:text-muted-foreground"
          />

          <button
            onClick={() => void send()}
            disabled={(!text.trim() && !attachments.length) || busy || disabled}
            aria-label="Send"
            className="inline-flex size-7 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground disabled:opacity-30 transition-opacity"
          >
            <ArrowUp className="size-4" />
          </button>
        </div>
      </div>
      {outsiders.length && channel ? (
        <div className="mt-1.5 flex flex-wrap items-center gap-2 rounded-md border bg-muted/40 px-2.5 py-1.5 text-[12px]">
          <span>
            {outsiders.map((n) => `@${n}`).join(", ")} {outsiders.length === 1 ? "isn't" : "aren't"} in {channel.name}, so {outsiders.length === 1 ? "they" : "they"} won't see this.
          </span>
          <button type="button" onClick={() => void send(true)} className="rounded bg-primary px-2 py-0.5 font-medium text-primary-foreground">Add and send</button>
          <button type="button" onClick={() => void send(false)} className="text-muted-foreground hover:text-foreground">Send anyway</button>
        </div>
      ) : null}
      {error ? <p className="mt-1 text-[12px] text-destructive">{error}</p> : null}
    </div>
  );
}
