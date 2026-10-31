import { Check, ChevronRight, Scale, TriangleAlert, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { api, type Decision } from "@/lib/api";
import { cn } from "@/lib/utils";

// The decision ledger at the board: agents propose and review, the person here rules.

/** "dev/codex.api · object — breaks v1 clients · 2026-10-29" → its parts. */
function review(line: string) {
  const [by = "", said = "", date = ""] = line.split(" · ");
  const [stance, ...why] = said.split(" — ");
  return { by, stance, why: why.join(" — "), date };
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2 text-[13px]">
      <span className="w-20 shrink-0 text-muted-foreground">{label}</span>
      <span className="min-w-0 flex-1 break-words">{children}</span>
    </div>
  );
}

function Proposal({ d, onRuled }: { d: Decision; onRuled: () => void }) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const rule = (outcome: "approve" | "reject") => {
    setBusy(true);
    setError("");
    api.decide(d.id, outcome, reason).then(onRuled, (e) => setError(String(e.message))).finally(() => setBusy(false));
  };
  const reviews = (d.reviews ?? []).map(review);
  return (
    <div className="rounded-xl border p-4">
      <div className="flex items-baseline gap-2">
        <span className="flex-1 font-medium">{d.title}</span>
        <span className="font-mono text-[11px] text-muted-foreground">{d.id}</span>
      </div>
      <p className="mb-3 text-[12px] text-muted-foreground">Proposed by {d["proposed-by"] ?? "?"}{d.added ? ` · ${d.added}` : ""}</p>
      <div className="flex flex-col gap-1">
        {d.rationale ? <Field label="Why">{d.rationale}</Field> : null}
        {d.affects ? <Field label="Affects"><span className="font-mono text-[12px]">{d.affects}</span></Field> : null}
        {d.supersedes ? <Field label="Replaces">{d.supersedes}, which stops applying if you approve this</Field> : null}
      </div>
      {d.conflicts && d.conflicts !== "none" ? (
        <p className="mt-3 flex items-start gap-2 rounded-md bg-muted/60 px-2.5 py-2 text-[13px]">
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-waiting" />
          Touches the same ground as {d.conflicts}. Check they don't contradict before approving.
        </p>
      ) : null}
      {reviews.length ? (
        <div className="mt-3 flex flex-col gap-1">
          {reviews.map((r) => (
            <p key={r.by} className="text-[13px]">
              <span className={cn("mr-1.5 rounded px-1.5 py-px text-[11px]", r.stance === "object" ? "bg-destructive/10 text-destructive" : "bg-online/15 text-foreground")}>{r.stance === "object" ? "objects" : "agrees"}</span>
              <span className="text-muted-foreground">{r.by}</span>
              {r.why ? <span>: {r.why}</span> : null}
            </p>
          ))}
        </div>
      ) : (
        <p className="mt-3 text-[13px] text-muted-foreground">No agent has reviewed it yet.</p>
      )}
      {rejecting ? (
        <form className="mt-4 flex gap-2" onSubmit={(e) => (e.preventDefault(), rule("reject"))}>
          <input autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why not? (optional, agents see it)" className="h-8 flex-1 rounded-md border bg-background px-2 outline-none focus:border-ring" />
          <Button size="sm" variant="destructive" type="submit" disabled={busy}>Reject</Button>
          <Button size="sm" variant="ghost" type="button" onClick={() => setRejecting(false)}>Cancel</Button>
        </form>
      ) : (
        <div className="mt-4 flex gap-2">
          <Button size="sm" onClick={() => rule("approve")} disabled={busy}><Check /> Approve</Button>
          <Button size="sm" variant="outline" onClick={() => setRejecting(true)} disabled={busy}><X /> Reject</Button>
        </div>
      )}
      {error ? <p className="mt-2 text-[13px] text-destructive">{error}</p> : null}
    </div>
  );
}

function Ruled({ d }: { d: Decision }) {
  return (
    <div className="flex flex-col gap-0.5 border-b py-2.5 last:border-0">
      <div className="flex items-baseline gap-2">
        <span className={cn("min-w-0 flex-1", d.status !== "approved" && "text-muted-foreground line-through decoration-muted-foreground/50")}>{d.title}</span>
        <span className="font-mono text-[11px] text-muted-foreground">{d.id}</span>
      </div>
      <p className="text-[12px] text-muted-foreground">
        {d.status === "superseded" ? `Replaced by ${d["superseded-by"]}` : `${d.status === "approved" ? "Approved" : "Rejected"} by ${(d["decided-by"] ?? "?").replace(/ \(human\)$/, "")}${d["decided-on"] ? ` · ${d["decided-on"]}` : ""}${d.reason ? ` · ${d.reason}` : ""}`}
        {d.affects ? ` · ${d.affects}` : ""}
      </p>
    </div>
  );
}

export function DecisionsView() {
  const [entries, setEntries] = useState<Decision[] | null>(null);
  const [error, setError] = useState("");
  const [past, setPast] = useState(false);
  const load = () =>
    api.decisions().then(
      (r) => (setEntries((prev) => (JSON.stringify(prev) === JSON.stringify(r.entries) ? prev : r.entries)), setError("")),
      (e) => setError(String(e.message))
    );
  useEffect(() => {
    void load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, []);

  const open = (entries ?? []).filter((d) => d.status === "proposed").reverse();
  const approved = (entries ?? []).filter((d) => d.status === "approved").reverse();
  const earlier = (entries ?? []).filter((d) => d.status === "rejected" || d.status === "superseded").reverse();

  return (
    <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-8 py-6">
      <div className="flex w-full max-w-3xl flex-col gap-6">
        <div className="flex items-start gap-3 rounded-xl border bg-muted/40 p-4">
          <Scale className="mt-0.5 size-4 text-dot" />
          <div className="flex-1">
            <p className="font-medium">Agents propose, you decide</p>
            <p className="text-muted-foreground">
              When an agent settles something the team should stick to, it proposes it here and other agents review it. What you approve, every agent starts its next session with. Approving a replacement retires the old decision.
            </p>
          </div>
        </div>
        {error ? <p className="text-destructive">{error}</p> : null}
        {entries === null && !error ? <p className="text-muted-foreground">Loading…</p> : null}

        {open.length ? (
          <section className="flex flex-col gap-3">
            <h3 className="text-[13px] font-medium text-muted-foreground">Waiting for you · {open.length}</h3>
            {open.map((d) => <Proposal key={d.id} d={d} onRuled={load} />)}
          </section>
        ) : entries ? (
          <p className="text-muted-foreground">Nothing waiting for a ruling.</p>
        ) : null}

        {approved.length ? (
          <section>
            <h3 className="mb-1 text-[13px] font-medium text-muted-foreground">On the record · {approved.length}</h3>
            <div className="rounded-xl border px-4">{approved.map((d) => <Ruled key={d.id} d={d} />)}</div>
          </section>
        ) : null}

        {earlier.length ? (
          <section>
            <button onClick={() => setPast((p) => !p)} className="mb-1 flex items-center gap-1 text-[13px] font-medium text-muted-foreground hover:text-foreground">
              <ChevronRight className={cn("size-3 transition-transform", past && "rotate-90")} /> Rejected and replaced · {earlier.length}
            </button>
            {past ? <div className="rounded-xl border px-4">{earlier.map((d) => <Ruled key={d.id} d={d} />)}</div> : null}
          </section>
        ) : null}
      </div>
    </div>
  );
}
