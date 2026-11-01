// Settings: the team policy file, edited as validated JSON (the server rejects invalid policy).
import { useCallback, useEffect, useState } from "react";
import { getPolicy, savePolicy } from "../api";

export function SettingsView() {
  const [text, setText] = useState("");
  const [path, setPath] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const policy = await getPolicy();
      setText(JSON.stringify(policy.raw, null, 2));
      setPath(policy.path);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const save = async () => {
    setMessage(null);
    setError(null);
    try {
      await savePolicy(JSON.parse(text || "{}"));
      setMessage("Saved - the next dispatch runs under this policy.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-medium">Policy</h2>
        <p className="text-sm text-muted-foreground">
          Who may run unattended, the test command, concurrency. <code>{path}</code>
        </p>
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
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={16}
        spellCheck={false}
        className="shadow-card w-full rounded-xl border border-border bg-card p-4 font-mono text-xs outline-none"
      />
      <button
        type="button"
        onClick={() => void save()}
        className="rounded-md bg-foreground px-3 py-1.5 text-xs font-medium text-background"
      >
        Save policy
      </button>
    </div>
  );
}
