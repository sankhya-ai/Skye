import { CheckCircle2, Download, ExternalLink, Loader2, LogIn } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { api, type AgentTool } from "@/lib/api";

/** Claude Code and Codex on this computer: install them and sign in without a terminal. */
export function AgentTools() {
  const [tools, setTools] = useState<AgentTool[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => api.agentTools().then((r) => setTools(r.tools), () => undefined), []);
  useEffect(() => {
    void load();
  }, [load]);
  // While something installs or waits for a sign-in, keep looking.
  const busy = tools?.some((t) => t.job?.running);
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(load, 2000);
    return () => clearInterval(timer);
  }, [busy, load]);

  const start = async (tool: AgentTool, action: "install" | "login") => {
    setError("");
    try {
      setTools((await api.startAgentTool(tool.id, action)).tools);
    } catch (e: any) {
      setError(e?.message || "That didn't start.");
    }
  };

  if (!tools) return null;
  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Coding agents on this computer</h3>
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
      <div className="divide-y rounded-xl border">
        {tools.map((t) => {
          const job = t.job;
          const ready = t.installed && t.signedIn;
          return (
            <div key={t.id} className="space-y-1.5 p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <span className="text-xs font-medium">{t.label}</span>
                  <span className="ml-2 text-[11px] text-muted-foreground">
                    {!t.installed ? "Not installed" : !t.signedIn ? "Installed, not signed in" : t.version?.replace(/\s*\(.*\)$/, "") || "Ready"}
                  </span>
                </div>
                {job?.running ? (
                  <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                    <Loader2 className="size-3.5 animate-spin" /> {job.action === "install" ? "Installing…" : "Finish signing in in your browser…"}
                  </span>
                ) : ready ? (
                  <span className="flex items-center gap-1 text-[11px] text-[#36634d] dark:text-[#a8cbbb]"><CheckCircle2 className="size-3.5" /> Ready</span>
                ) : !t.installed ? (
                  <Button size="sm" onClick={() => start(t, "install")} className="h-7 gap-1.5 text-xs"><Download className="size-3.5" /> Install</Button>
                ) : (
                  <Button size="sm" onClick={() => start(t, "login")} className="h-7 gap-1.5 text-xs"><LogIn className="size-3.5" /> Sign in</Button>
                )}
              </div>
              {job?.running && job.action === "login" && job.link ? (
                <button type="button" onClick={() => void api.openExternal(job.link!)} className="inline-flex items-center gap-1 text-[11px] text-primary hover:underline">
                  <ExternalLink className="size-3" /> Browser didn't open? Open the sign-in page
                </button>
              ) : null}
              {job && !job.running && job.ok === false ? (
                <p className="whitespace-pre-wrap break-words font-mono text-[10px] text-destructive">{job.output.trim().split("\n").slice(-3).join("\n") || "It stopped with an error."}</p>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
