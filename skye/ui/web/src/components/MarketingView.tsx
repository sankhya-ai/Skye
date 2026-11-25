// Marketing: one business profile in, a week of reel and post briefs out. Briefs land in
// the Backlog as pending items; nothing is published and nothing runs until the owner decides.
import { useCallback, useEffect, useState } from "react";
import { getMarketing, planMarketingWeek, type FeatureRow, type MarketingProfile } from "../api";
import { humanError } from "../lib";
import { Button } from "./ui/Button";

const FIELD =
  "w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none transition-colors focus:border-foreground/40";

const LABEL: Record<string, string> = {
  pending: "Needs decision",
  approved: "Approved",
  rejected: "Rejected",
  delivered: "Delivered",
};

export function MarketingView() {
  const [form, setForm] = useState({ name: "", what_you_sell: "", audience: "", tone: "", refs: "" });
  const [items, setItems] = useState<FeatureRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await getMarketing();
      const p = data.profile;
      setForm((f) => (f.name || f.what_you_sell ? f : {
        name: p.name ?? "",
        what_you_sell: p.what_you_sell ?? "",
        audience: p.audience ?? "",
        tone: p.tone ?? "",
        refs: (p.style_references ?? []).join("\n"),
      }));
      setItems(data.items);
    } catch (err) {
      setError(humanError(err, "load"));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const set = (key: keyof typeof form) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  const plan = async () => {
    setBusy(true);
    setError(null);
    const profile: MarketingProfile = {
      name: form.name,
      what_you_sell: form.what_you_sell,
      audience: form.audience,
      tone: form.tone,
      style_references: form.refs.split("\n").map((s) => s.trim()).filter(Boolean),
    };
    try {
      await planMarketingWeek(profile);
      await load();
    } catch (err) {
      setError(humanError(err, "plan"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-medium">Marketing</h2>
      <div className="space-y-3 rounded-xl border border-border bg-card/80 p-4 backdrop-blur">
        <label className="block space-y-1 text-xs text-muted-foreground">
          Business
          <input className={FIELD} value={form.name} onChange={set("name")} />
        </label>
        <label className="block space-y-1 text-xs text-muted-foreground">
          Sells
          <input className={FIELD} value={form.what_you_sell} onChange={set("what_you_sell")} />
        </label>
        <div className="grid grid-cols-2 gap-3 max-sm:grid-cols-1">
          <label className="block space-y-1 text-xs text-muted-foreground">
            Audience
            <input className={FIELD} value={form.audience} onChange={set("audience")} />
          </label>
          <label className="block space-y-1 text-xs text-muted-foreground">
            Tone
            <input className={FIELD} value={form.tone} onChange={set("tone")} />
          </label>
        </div>
        <label className="block space-y-1 text-xs text-muted-foreground">
          Style
          <textarea className={`${FIELD} min-h-16`} value={form.refs} onChange={set("refs")} />
        </label>
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
        <div className="flex justify-end">
          <Button loading={busy} onClick={() => void plan()}>
            Plan week
          </Button>
        </div>
      </div>
      {items.length ? (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium">This week</h3>
            <a href="#/backlog" className="text-xs underline">
              Open in backlog
            </a>
          </div>
          <ul className="divide-y divide-border rounded-xl border border-border bg-card/80 backdrop-blur">
            {[...items].sort((a, b) => a.title.localeCompare(b.title, undefined, { numeric: true })).map((f) => (
              <li key={f.id} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
                <span className="truncate">{f.title}</span>
                <span className="shrink-0 rounded-xs bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                  {LABEL[f.status] ?? f.status}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
