// Settings: the team policy as understandable controls - who may run unattended, the test
// command, concurrency - with the raw file behind an advanced disclosure. Every save is
// validated by the server exactly like a hand-edited policy file.
import { Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Button } from "./ui/Button";
import { getPolicy, savePolicy } from "../api";
import { humanError } from "../lib";

type Rule = { adapter: string; role: string };
const ADAPTERS = ["root", "claude-code", "codex"];

export function SettingsView() {
  const [rules, setRules] = useState<Rule[]>([]);
  const [testCommand, setTestCommand] = useState("");
  const [journeyCommand, setJourneyCommand] = useState("");
  const [maxConcurrent, setMaxConcurrent] = useState(1);
  const [raw, setRaw] = useState<Record<string, unknown>>({});
  const [advanced, setAdvanced] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const policy = await getPolicy();
      const data = policy.raw;
      setRaw(data);
      const unattended = Array.isArray(data.allow_unattended) ? data.allow_unattended : [];
      setRules(
        unattended.map((r: Record<string, unknown>) => ({
          adapter: String(r.adapter ?? "root"),
          role: String(r.role ?? "*"),
        })),
      );
      setTestCommand(String(data.test_command ?? ""));
      setJourneyCommand(String(data.journey_command ?? ""));
      setMaxConcurrent(Number(data.max_concurrent ?? policy.max_concurrent ?? 1));
      setAdvanced(JSON.stringify(data, null, 2));
      setError(null);
    } catch (e) {
      setError(humanError(e, "policy"));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const save = async () => {
    setMessage(null);
    setError(null);
    const policy: Record<string, unknown> = { ...raw };
    policy.allow_unattended = rules.map((r) => ({ adapter: r.adapter, role: r.role || "*" }));
    if (testCommand.trim()) policy.test_command = testCommand.trim();
    else delete policy.test_command;
    if (journeyCommand.trim()) policy.journey_command = journeyCommand.trim();
    else delete policy.journey_command;
    policy.max_concurrent = Math.max(1, Math.min(4, Math.trunc(maxConcurrent) || 1));
    try {
      await savePolicy(policy);
      setMessage("Saved - the next run uses this policy.");
    } catch (e) {
      setError(humanError(e, "policy"));
    }
  };

  const saveAdvanced = async () => {
    setMessage(null);
    setError(null);
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(advanced || "{}");
    } catch {
      setError("That isn't valid JSON - check the syntax and try again.");
      return;
    }
    try {
      await savePolicy(parsed);
      setMessage("Saved - the next run uses this policy.");
      await refresh();
    } catch (e) {
      setError(humanError(e, "policy"));
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-medium">Policy</h2>
      </div>
      {error ? (
        <p className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
          {error}
        </p>
      ) : null}
      {message ? (
        <p className="rounded-md border border-success/30 bg-success/5 p-3 text-xs text-success">
          {message}
        </p>
      ) : null}

      <section className="shadow-card rounded-xl border border-border bg-card p-4">
        <h3 className="text-sm font-medium">May run without asking</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Anything else pauses for your one-time approval before it runs.
        </p>
        <div className="mt-3 space-y-2">
          {rules.map((rule, i) => (
            <div key={i} className="flex items-center gap-2">
              <select
                value={rule.adapter}
                onChange={(e) =>
                  setRules(rules.map((r, j) => (j === i ? { ...r, adapter: e.target.value } : r)))
                }
                className="rounded-md border border-border bg-background px-2 py-1 font-mono text-xs outline-none"
              >
                {ADAPTERS.map((a) => (
                  <option key={a}>{a}</option>
                ))}
              </select>
              <input
                value={rule.role}
                onChange={(e) =>
                  setRules(rules.map((r, j) => (j === i ? { ...r, role: e.target.value } : r)))
                }
                placeholder="role or *"
                className="w-32 rounded-md border border-border bg-background px-2 py-1 font-mono text-xs outline-none"
              />
              <button
                type="button"
                onClick={() => setRules(rules.filter((_, j) => j !== i))}
                className="text-muted-foreground hover:text-destructive"
                aria-label="Remove rule"
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={() => setRules([...rules, { adapter: "root", role: "developer" }])}
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <Plus className="size-3.5" /> Add rule
          </button>
        </div>
      </section>

      <section className="shadow-card rounded-xl border border-border bg-card p-4">
        <h3 className="text-sm font-medium">Verification</h3>
        <label className="mt-3 block text-xs text-muted-foreground">
          Test command · owner-set; the worker cannot supply it
          <input
            value={testCommand}
            onChange={(e) => setTestCommand(e.target.value)}
            placeholder="e.g. pytest -q"
            className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 font-mono text-xs outline-none"
          />
        </label>
        <label className="mt-3 block text-xs text-muted-foreground">
          Journey replay command · owner-set; the worker cannot supply it. Empty means finished
          work waits for verification.
          <input
            value={journeyCommand}
            onChange={(e) => setJourneyCommand(e.target.value)}
            placeholder="e.g. python scripts/smoke.py"
            className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 font-mono text-xs outline-none"
          />
        </label>
        <label className="mt-3 block text-xs text-muted-foreground">
          Parallel steps (1-4)
          <input
            type="number"
            min={1}
            max={4}
            value={maxConcurrent}
            onChange={(e) => setMaxConcurrent(Number(e.target.value))}
            className="mt-1 w-20 rounded-md border border-border bg-background px-2 py-1.5 font-mono text-xs outline-none"
          />
        </label>
      </section>

      <Button
        size="sm"
        loading={saving}
        onClick={() => {
          setSaving(true);
          void save().finally(() => setSaving(false));
        }}
      >
        Save policy
      </Button>

      <details className="shadow-card rounded-xl border border-border bg-card p-4">
        <summary className="cursor-pointer text-xs text-muted-foreground">
          Advanced: raw policy file
        </summary>
        <textarea
          value={advanced}
          onChange={(e) => setAdvanced(e.target.value)}
          rows={10}
          spellCheck={false}
          className="mt-3 w-full rounded-md border border-border bg-background p-3 font-mono text-xs outline-none"
        />
        <Button
          variant="outline"
          size="sm"
          className="mt-2"
          onClick={() => void saveAdvanced()}
        >
          Save raw JSON
        </Button>
      </details>
    </div>
  );
}
