// Home canvas adapted from block/berd's HomeScreen (Apache-2.0, see skye/ui/NOTICE):
// large clock, greeting, and one centered composer on the dot-grid canvas.
import { ArrowUp } from "lucide-react";
import { useEffect, useState } from "react";
import { createMission, type Project } from "../api";

function greeting(hour: number): string {
  if (hour < 5) return "Up late";
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

export function HomeView({
  projects,
  onCreated,
}: {
  projects: Project[];
  onCreated: (id: string) => void;
}) {
  const [now, setNow] = useState(() => new Date());
  const [goal, setGoal] = useState("");
  const [project, setProject] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  const submit = async () => {
    const text = goal.trim();
    if (!text || busy) return;
    setBusy(true);
    setError(null);
    try {
      // A planner mission: the engine grows the real workflow from the goal at runtime.
      const created = await createMission({
        goal: text,
        plan: true,
        run: true,
        ...(project ? { project } : {}),
      });
      onCreated(created.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  const time = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return (
    <div className="flex min-h-[calc(100vh-4rem)] flex-col items-center justify-center gap-6 px-6">
      <div className="text-center">
        <p className="text-6xl font-semibold tracking-tight tabular-nums">{time}</p>
        <p className="mt-2 text-xl text-muted-foreground">
          {greeting(now.getHours())} - what should the team work on?
        </p>
      </div>
      <div className="w-full max-w-[600px]">
        <div className="shadow-card rounded-2xl border border-border bg-card p-3">
          <textarea
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void submit();
            }}
            placeholder="Describe the goal - a planner agent will break it into a dynamic workflow..."
            rows={3}
            className="w-full resize-none bg-transparent px-2 py-1 text-sm outline-none placeholder:text-muted-foreground"
          />
          <div className="flex items-center justify-between px-1 pt-1">
            {projects.length > 0 ? (
              <select
                value={project}
                onChange={(e) => setProject(e.target.value)}
                className="rounded-sm border border-border bg-background px-1.5 py-0.5 text-xs text-muted-foreground outline-none"
              >
                <option value="">No project</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            ) : (
              <span className="text-xs text-muted-foreground">
                Planned dynamically - no fixed pipeline
              </span>
            )}
            <button
              type="button"
              onClick={() => void submit()}
              disabled={!goal.trim() || busy}
              className="inline-flex size-8 items-center justify-center rounded-full bg-foreground text-background transition disabled:opacity-30"
              aria-label="Start mission"
            >
              <ArrowUp className="size-4" />
            </button>
          </div>
        </div>
        {error ? <p className="mt-2 text-xs text-destructive">{error}</p> : null}
      </div>
    </div>
  );
}
