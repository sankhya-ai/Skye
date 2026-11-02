// The last step of every mission: the team worked on its own git branch, and the owner
// brings the result into their project with one click (a normal git merge into whatever
// branch their checkout is on). git refuses to overwrite the owner's own uncommitted edits;
// a conflicting merge is aborted and explained - the project is left exactly as it was.
import { GitMerge } from "lucide-react";
import { useEffect, useState } from "react";
import { getDelivery, mergeMission, type DeliveryRepo, type MergeResult } from "../api";
import { humanError } from "../lib";
import { Button } from "./ui/Button";

export function BringIntoProject({ missionId, settled }: { missionId: string; settled: boolean }) {
  const [repos, setRepos] = useState<Record<string, DeliveryRepo> | null>(null);
  const [result, setResult] = useState<MergeResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () =>
    getDelivery(missionId)
      .then((d) => { setRepos(d.repos); if (d.last_merge) setResult(d.last_merge); })
      .catch(() => setRepos(null));

  useEffect(() => { void load(); }, [missionId, settled]);

  if (!settled || !repos) return null;
  const entries = Object.entries(repos);
  if (entries.length === 0) return null;
  const pending = entries.filter(([, r]) => r.uncommitted_files > 0 || r.commits_ahead > 0);
  const merged = result?.ok && pending.length === 0;

  const merge = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await mergeMission(missionId));
      await load();
    } catch (e) {
      setError(humanError(e, "task"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="glass-panel shadow-card space-y-2 rounded-md p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-foreground">
            {merged ? "In your project" : pending.length > 0 ? "Ready to bring into your project" : "No changes to bring in"}
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {pending.length > 0
              ? <>The team worked on branch <span className="font-mono">{pending[0][1].branch}</span>
                  {pending.length > 1 ? ` (and ${pending.length - 1} more repo${pending.length === 2 ? "" : "s"})` : ""}.
                  {" "}Review the changed files below, then merge them into the branch your project is on.</>
              : merged
                ? "The team's work is merged into your project."
                : "The team finished without changing any files."}
          </p>
        </div>
        {pending.length > 0 ? (
          <Button size="sm" loading={busy} onClick={() => void merge()}>
            <GitMerge /> Bring into my project
          </Button>
        ) : null}
      </div>
      {result ? (
        <ul className="space-y-0.5 text-xs">
          {Object.entries(result.repos).map(([name, r]) => (
            <li key={name} className={r.ok ? "text-muted-foreground" : "text-destructive"}>
              {entries.length > 1 ? <span className="font-medium">{name}: </span> : null}{r.detail}
            </li>
          ))}
        </ul>
      ) : null}
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </div>
  );
}
