import { ChevronDown, ChevronRight, Edit3, FileCode, Pin, Save, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";

interface ChannelCanvasProps {
  channelName: string;
  onOpenTalk?: (id: string) => void;
}

export function ChannelCanvas({ channelName }: ChannelCanvasProps) {
  const [open, setOpen] = useState(false);
  const [content, setContent] = useState<string | null>(null);
  const [baseVersion, setBaseVersion] = useState(0);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState("");

  const cleanName = channelName.replace(/^#/, "");
  const contractPath = `contracts/${cleanName}.md`;

  const load = () => {
    setLoading(true);
    api.memoryFile(contractPath)
      .then((file) => {
        if (file && file.text) {
          setContent(file.text);
          setDraft(file.text);
          setBaseVersion(file.version);
        } else {
          api.memoryFile("MEMORY.md").then((m) => {
            if (m && m.text) {
              setContent(m.text);
              setDraft(m.text);
              setBaseVersion(m.version);
            }
          }).catch(() => {});
        }
      })
      .catch(() => {
        api.memoryFile("MEMORY.md").then((m) => {
          if (m && m.text) {
            setContent(m.text);
            setDraft(m.text);
            setBaseVersion(m.version);
          }
        }).catch(() => {});
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    setEditing(false);
  }, [channelName]);

  const handleSave = async () => {
    setSaving(true);
    setStatus("");
    try {
      const res = await api.saveMemoryFile(contractPath, draft, `Update ${cleanName} canvas`, baseVersion);
      if (res.ok) {
        setContent(draft);
        setEditing(false);
        setStatus("Saved");
        setTimeout(() => setStatus(""), 2000);
      } else {
        setStatus("Conflict: modified by another agent");
      }
    } catch (err: any) {
      setStatus(err?.message || "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="border-b bg-card/60 text-xs">
      <div className="flex items-center justify-between px-4 py-1.5">
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1.5 font-medium text-muted-foreground hover:text-foreground"
        >
          <Pin className="size-3.5 text-[#8db8d5]" />
          <span>Canvas</span>
          <span className="font-mono text-[11px] text-muted-foreground/70">({cleanName})</span>
          {open ? <ChevronDown className="size-3 text-muted-foreground" /> : <ChevronRight className="size-3 text-muted-foreground" />}
        </button>

        <div className="flex items-center gap-2">
          {status ? <span className="text-[11px] text-muted-foreground">{status}</span> : null}
          {open && !editing ? (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setDraft(content || `# ${cleanName} Context & Contracts\n\n- Pinned decisions and schemas here\n`);
                setEditing(true);
              }}
              className="h-6 gap-1 px-2 text-[11px] text-muted-foreground hover:text-foreground"
            >
              <Edit3 className="size-3" /> Edit
            </Button>
          ) : null}
          {editing ? (
            <div className="flex items-center gap-1">
              <Button
                size="sm"
                variant="outline"
                disabled={saving}
                onClick={handleSave}
                className="h-6 gap-1 px-2 text-[11px] text-emerald-600 dark:text-emerald-400"
              >
                <Save className="size-3" /> {saving ? "Saving..." : "Save"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setEditing(false)}
                className="h-6 px-1.5 text-[11px] text-muted-foreground"
              >
                <X className="size-3" />
              </Button>
            </div>
          ) : null}
        </div>
      </div>

      {open ? (
        <div className="border-t bg-muted/20 px-4 py-3">
          {loading ? (
            <p className="text-muted-foreground">Loading channel canvas...</p>
          ) : editing ? (
            <div className="flex flex-col gap-2">
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={6}
                placeholder="Markdown specs, endpoints, rules, and notes pinned for humans and agents..."
                className="w-full rounded border bg-background p-2.5 font-mono text-[11px] leading-relaxed outline-none focus:border-ring"
              />
              <span className="text-[10px] text-muted-foreground">
                Sealed end-to-end. Updates are saved to <code>{contractPath}</code> and visible to all coding agents.
              </span>
            </div>
          ) : content ? (
            <div className="max-h-56 overflow-y-auto whitespace-pre-wrap rounded border bg-background/80 p-3 font-mono text-[11px] leading-relaxed">
              {content}
            </div>
          ) : (
            <div className="rounded border border-dashed p-3 text-center text-muted-foreground">
              <FileCode className="mx-auto mb-1 size-5 text-muted-foreground/60" />
              <p>No pinned canvas for {channelName} yet.</p>
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  setDraft(`# ${cleanName} Context & Runbook\n\n- [ ] Active sprint goals\n- [ ] Microservice API schemas\n`);
                  setEditing(true);
                }}
                className="mt-2 h-6 text-[11px]"
              >
                Create Channel Canvas
              </Button>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
