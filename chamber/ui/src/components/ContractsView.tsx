import { FileCode2, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";

import { api, type Contract } from "@/lib/api";
import { cn } from "@/lib/utils";

// The contract registry at the board: for each API contract, who has caught up to the
// current version. Agents register, publish and ack; this only shows where things stand.

const STATE: Record<Contract["consumers"][number]["state"], { label: string; className: string }> = {
  adapted: { label: "Adapted", className: "bg-online/15 text-foreground" },
  seen: { label: "Seen, not adapted", className: "bg-waiting/15 text-foreground" },
  outstanding: { label: "Not yet", className: "bg-muted text-muted-foreground" },
  object: { label: "Objects", className: "bg-destructive/10 text-destructive" },
};

function ContractCard({ c }: { c: Contract }) {
  const caughtUp = c.consumers.filter((x) => x.state === "adapted").length;
  return (
    <div className="rounded-xl border p-4">
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-[13px] font-medium">{c.name}</span>
        <span className="rounded border px-1.5 font-mono text-[11px] text-muted-foreground">v{c.version}</span>
        <span className="min-w-0 flex-1 truncate text-muted-foreground">{c.title}</span>
        {c.consumers.length ? <span className="text-[12px] text-muted-foreground">{caughtUp} of {c.consumers.length} adapted</span> : null}
      </div>
      {c.summary ? <p className="mt-1">{c.summary}</p> : null}
      <p className="mt-1 text-[12px] text-muted-foreground">
        Produced by {c.producer} · updated {c.updated}{c.decision ? ` · decision ${c.decision}` : ""}
      </p>

      {c.objections.map((o) => (
        <p key={o.agent} className="mt-3 flex items-start gap-2 rounded-md bg-destructive/10 px-2.5 py-2 text-[13px]">
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-destructive" />
          <span><span className="font-medium">{o.agent}</span> objects to v{o.version}: {o.note}</span>
        </p>
      ))}

      {c.consumers.length ? (
        <div className="mt-3 flex flex-col">
          {c.consumers.map((x) => (
            <div key={x.agent} className="flex items-baseline gap-2 border-t py-1.5 text-[13px]">
              <span className="w-56 shrink-0 truncate text-muted-foreground">{x.agent}</span>
              <span className={cn("shrink-0 rounded px-1.5 py-px text-[11px]", STATE[x.state].className)}>{STATE[x.state].label}</span>
              <span className="min-w-0 flex-1 truncate" title={x.note}>
                {x.note || (x.state === "outstanding" && x.lastVersion ? `last caught up at v${x.lastVersion}` : "")}
              </span>
              {x.date ? <span className="shrink-0 text-[12px] text-muted-foreground">{x.date}</span> : null}
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-3 flex items-start gap-2 text-[13px] text-muted-foreground">
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-waiting" />
          No agent has subscribed, so a change reaches nobody. Whoever uses it subscribes from their agent.
        </p>
      )}
    </div>
  );
}

export function ContractsView() {
  const [contracts, setContracts] = useState<Contract[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const load = () =>
      api.contracts().then(
        (r) => (setContracts((prev) => (JSON.stringify(prev) === JSON.stringify(r.contracts) ? prev : r.contracts)), setError("")),
        (e) => setError(String(e.message))
      );
    void load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, []);
  // Contracts needing attention first: an objection, then anyone not yet adapted.
  const weight = (c: Contract) => (c.objections.length ? 0 : c.consumers.some((x) => x.state !== "adapted") ? 1 : 2);
  const sorted = [...(contracts ?? [])].sort((a, b) => weight(a) - weight(b));

  return (
    <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-8 py-6">
      <div className="flex w-full max-w-3xl flex-col gap-4">
        <div className="flex items-start gap-3 rounded-xl border bg-muted/40 p-4">
          <FileCode2 className="mt-0.5 size-4 text-dot" />
          <div className="flex-1">
            <p className="font-medium">Who has caught up to each contract</p>
            <p className="text-muted-foreground">
              When an agent changes an API it produces, every agent subscribed to it is told, mid-task or the next time it starts. Each one answers: seen, adapted (saying what it changed and ran), or objects. Seen is not adapted.
            </p>
          </div>
        </div>
        {error ? <p className="text-destructive">{error}</p> : null}
        {contracts === null && !error ? <p className="text-muted-foreground">Loading…</p> : null}
        {contracts && !contracts.length ? <p className="text-muted-foreground">No contracts yet. An agent registers one when it owns an API others depend on.</p> : null}
        {sorted.map((c) => <ContractCard key={c.name} c={c} />)}
      </div>
    </div>
  );
}
