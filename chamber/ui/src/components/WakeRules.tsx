import { Eye, Loader2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, type WakeEntry, type WakeRule } from "@/lib/api";
import { people, type BoardData } from "@/lib/board";
import { cn } from "@/lib/utils";

const DEFAULT: WakeRule = { who: "nobody", can: "answer" };
const select = "rounded-md border bg-background px-2 py-1 text-xs outline-none focus:border-ring";

/** One line per thing a woken agent did, from its CLI's JSON events (Claude Code stream-json, Codex --json). */
function describe(event: any): { kind: "say" | "tool" | "done" | "error"; text: string } | null {
  if (event?.type === "assistant") {
    const parts = event.message?.content ?? (event.text ? [{ type: "text", text: event.text }] : []);
    const said = parts.filter((p: any) => p.type === "text").map((p: any) => p.text).join("\n").trim();
    const tools = parts.filter((p: any) => p.type === "tool_use").map((p: any) => `${p.name}${p.input?.file_path ? ` ${p.input.file_path}` : p.input?.pattern ? ` ${p.input.pattern}` : ""}`);
    if (tools.length) return { kind: "tool", text: tools.join(", ") };
    return said ? { kind: "say", text: said } : null;
  }
  if (event?.type === "result") return { kind: event.is_error ? "error" : "done", text: event.result || (event.is_error ? "Stopped with an error." : "Done.") };
  if (event?.type === "item.completed" && event.item) {
    const item = event.item;
    if (item.type === "agent_message") return { kind: "say", text: item.text };
    if (item.type === "command_execution") return { kind: "tool", text: item.command };
    if (item.type === "file_change") return { kind: "tool", text: `edited ${(item.changes || []).map((c: any) => c.path).join(", ")}` };
    if (item.type === "mcp_tool_call") return { kind: "tool", text: `${item.server}.${item.tool}` };
  }
  if (event?.type === "stderr" && event.text?.trim()) return { kind: "error", text: event.text.trim() };
  return null;
}

/** What a woken agent is doing, or did: refreshed while it runs. */
function RunView({ entry, onClose }: { entry: WakeEntry; onClose: () => void }) {
  const [lines, setLines] = useState<{ kind: string; text: string }[] | null>(null);
  const running = !entry.ended;
  useEffect(() => {
    let stop = false;
    const load = () => api.wakeRun(entry.id).then((r) => !stop && setLines(r.events.map(describe).filter(Boolean) as any), () => !stop && setLines([]));
    load();
    const timer = running ? setInterval(load, 2000) : undefined;
    return () => {
      stop = true;
      clearInterval(timer);
    };
  }, [entry.id, running]);
  return (
    <div className="rounded-lg border bg-muted/20 p-3 text-xs">
      <div className="mb-2 flex items-center justify-between">
        <span className="font-medium">
          {entry.agent.split("/")[1]} for {entry.from} {running ? <Loader2 className="ml-1 inline size-3 animate-spin" /> : null}
        </span>
        <button type="button" onClick={onClose} className="text-muted-foreground hover:text-foreground"><X className="size-3.5" /></button>
      </div>
      {lines === null ? <p className="text-muted-foreground">Loading…</p> : !lines.length ? <p className="text-muted-foreground">{running ? "Starting…" : "Nothing recorded."}</p> : (
        <ol className="max-h-64 space-y-1.5 overflow-y-auto">
          {lines.map((line, i) => (
            <li key={i} className={cn("whitespace-pre-wrap break-words", line.kind === "tool" && "font-mono text-[11px] text-muted-foreground", line.kind === "error" && "text-destructive", line.kind === "done" && "border-t pt-1.5 font-medium")}>
              {line.kind === "tool" ? `→ ${line.text}` : line.text}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

/**
 * One choice for who may wake an agent that isn't running and what it may do then, e.g. "Team can
 * ask questions". A folder's agents share it, so ten folders are ten choices, not thirty.
 */
type Choice = "nobody" | "team-answer" | "team-work" | "people-answer" | "people-work" | "mixed";

const CHOICES: { value: Exclude<Choice, "mixed">; label: string }[] = [
  { value: "nobody", label: "Only me" },
  { value: "team-answer", label: "Team: answer questions" },
  { value: "team-work", label: "Team: do work" },
  { value: "people-answer", label: "Chosen people: answer" },
  { value: "people-work", label: "Chosen people: do work" },
];

const choiceOf = (rule: WakeRule): Choice => (rule.who === "nobody" ? "nobody" : `${Array.isArray(rule.who) ? "people" : "team"}-${rule.can}` as Choice);
const pickedOf = (rule: WakeRule) => (Array.isArray(rule.who) ? rule.who : []);

function ruleFor(choice: Exclude<Choice, "mixed">, picked: string[]): WakeRule {
  if (choice === "nobody") return { who: "nobody", can: "answer" };
  const [who, can] = choice.split("-") as ["team" | "people", "answer" | "work"];
  return { who: who === "team" ? "team" : picked, can };
}

/** Your wake rules, your teammates and what woke your agents lately; saving applies to many agents at once. */
export function useWake(data: BoardData | null) {
  const [rules, setRules] = useState<Record<string, WakeRule>>({});
  const [log, setLog] = useState<WakeEntry[]>([]);
  const [error, setError] = useState("");
  const [members, setMembers] = useState<string[]>([]);
  useEffect(() => {
    api.members().then((r) => setMembers(r.members.map((m) => m.owner)), () => undefined);
  }, []);
  const load = useCallback(() => api.wakeRules().then((r) => { setRules(r.agents); setLog(r.log); }, () => undefined), []);
  useEffect(() => {
    void load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [load]);

  const me = data?.me ?? "";
  // Everyone in the chamber (its members) and anyone seen in talks or channels.
  const teammates = [...new Set([...members, ...people(data).map((p) => p.owner)])].filter((o) => o && o !== me).sort();
  const mine = (agents: { agent: string }[]) => agents.map((a) => a.agent).filter((a) => a.startsWith(`${me}/`));

  /** The choice shared by these agents ("mixed" when they differ), and the people it names. */
  const shared = (agents: string[]) => {
    const all = agents.map((a) => rules[a] ?? DEFAULT);
    const choices = new Set(all.map(choiceOf));
    const picked = [...new Set(all.flatMap(pickedOf))];
    return { choice: (choices.size === 1 ? [...choices][0] : "mixed") as Choice, picked };
  };

  const save = async (agents: string[], rule: WakeRule) => {
    setError("");
    setRules((r) => ({ ...r, ...Object.fromEntries(agents.map((a) => [a, rule])) }));
    try {
      for (const agent of agents) setRules((await api.setWakeRule(agent, rule)).agents);
    } catch (e: any) {
      setError(e?.message || "Couldn't save that.");
      void load();
    }
  };

  return { rules, log, error, teammates, mine, shared, save };
}

/** Who may wake these agents, and what they may do: one dropdown, plus who exactly when it's chosen people. */
export function WakeChoice({ wake, agents, label, mixed = "Differs by agent" }: { wake: ReturnType<typeof useWake>; agents: string[]; label: string; mixed?: string }) {
  const { choice, picked } = wake.shared(agents);
  return (
    <select
      aria-label={label}
      className={select}
      value={choice}
      onChange={(e) => e.target.value !== "mixed" && wake.save(agents, ruleFor(e.target.value as Exclude<Choice, "mixed">, picked))}
    >
      {choice === "mixed" ? <option value="mixed">{mixed}</option> : null}
      {CHOICES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
    </select>
  );
}

/** The teammates a "chosen people" rule names, as toggles. */
export function WakePeople({ wake, agents }: { wake: ReturnType<typeof useWake>; agents: string[] }) {
  const { choice, picked } = wake.shared(agents);
  if (!choice.startsWith("people")) return null;
  const can = choice.endsWith("work") ? "work" : "answer";
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[11px] text-muted-foreground">Who:</span>
      {wake.teammates.length ? wake.teammates.map((name) => {
        const on = picked.includes(name);
        return (
          <button key={name} type="button" onClick={() => wake.save(agents, { who: on ? picked.filter((n) => n !== name) : [...picked, name], can })} className={cn("rounded-full border px-2 py-0.5 text-[11px]", on ? "border-primary bg-primary/10 text-primary" : "text-muted-foreground hover:text-foreground")}>
            @{name}
          </button>
        );
      }) : <span className="text-[11px] text-muted-foreground">No teammates yet. Invite someone first.</span>}
    </div>
  );
}

/** What woke your agents lately (the newest few, more on request), and what each did. */
export function WakeLog({ wake }: { wake: ReturnType<typeof useWake> }) {
  const [watching, setWatching] = useState<WakeEntry | null>(null);
  const [all, setAll] = useState(false);
  const { log } = wake;
  if (!log.length) return null;
  const shown = all ? log.slice(0, 50) : log.slice(0, 5);
  return (
    <div className="space-y-1.5">
      <h4 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Recently woken</h4>
      {watching ? <RunView entry={log.find((e) => e.id === watching.id) ?? watching} onClose={() => setWatching(null)} /> : null}
      <ul className="divide-y rounded-lg border text-xs">
        {shown.map((e) => (
          <li key={e.id} className="flex items-center justify-between gap-2 px-3 py-2">
            <div className="min-w-0">
              <span className={cn("mr-1.5 inline-block size-1.5 rounded-full", e.result === "refused" ? "bg-destructive/70" : e.ended ? "bg-muted-foreground/50" : "bg-[#a8cbbb] animate-pulse")} />
              <span className="font-medium">{e.from}</span> → {e.agent.split("/")[1]}
              <span className="ml-1.5 text-muted-foreground">{new Date(e.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
              {e.result === "refused" ? <p className="truncate text-[11px] text-muted-foreground">{e.reason}</p> : null}
            </div>
            {e.result === "woke" ? (
              <button type="button" onClick={() => setWatching(e)} className="flex shrink-0 items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground">
                <Eye className="size-3" /> {e.ended ? "See what it did" : "Watch"}
              </button>
            ) : null}
          </li>
        ))}
      </ul>
      {log.length > 5 ? (
        <button type="button" onClick={() => setAll(!all)} className="text-[11px] text-muted-foreground hover:text-foreground">
          {all ? "Show fewer" : `Show all ${Math.min(log.length, 50)}`}
        </button>
      ) : null}
    </div>
  );
}
