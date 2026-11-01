// Home canvas adapted from block/berd's HomeScreen (Apache-2.0, see skye/ui/NOTICE):
// clock in the display face, a muted greeting, and one centered pill composer
// (berd's chat-composer token: translucent muted surface, 40px radius, backdrop blur).
import { ArrowUp } from "lucide-react";
import { useEffect, useState } from "react";
import { createMission, getBacklog, type FeatureRow, type MissionRow, type Project } from "../api";

function HomeClock({ now }: { now: Date }) {
  const parts = now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const [hm, period] = parts.split(" ");
  return (
    <div className="mb-1 flex items-baseline gap-1.5 pl-4">
      <span className="font-display text-6xl font-normal tracking-tight text-foreground">
        {hm}
      </span>
      {period ? (
        <span className="text-lg font-normal text-muted-foreground">{period}</span>
      ) : null}
    </div>
  );
}

function greeting(hour: number): string {
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

// Workspace glanceables, berd's widget-canvas idea backed by the real engine state:
// each project shows live work and what needs the owner; the backlog card counts
// proposals awaiting a decision. Clicking a card goes to the work, not a metric page.
function Glanceables({
  projects,
  missions,
  features,
}: {
  projects: Project[];
  missions: MissionRow[];
  features: FeatureRow[];
}) {
  const pending = features.filter((f) => f.status === "pending");
  const cards = projects.map((project) => {
    const rows = missions.filter((m) => m.project === project.id);
    const live = rows.filter((m) => m.live).length;
    const needsOwner = rows.filter((m) => m.needs_owner).length;
    return { project, live, needsOwner, total: rows.length };
  });
  if (cards.length === 0 && pending.length === 0) return null;
  return (
    <div className="mt-6 grid w-full max-w-[600px] grid-cols-2 gap-3">
      {cards.map(({ project, live, needsOwner, total }) => (
        <a
          key={project.id}
          href={`#/missions?project=${encodeURIComponent(project.id)}`}
          className="glass-panel shadow-card rounded-md p-4 transition-colors hover:bg-card"
        >
          <p className="text-sm font-medium text-foreground">{project.name}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {live > 0 ? `${live} running` : total > 0 ? `${total} mission${total === 1 ? "" : "s"}` : "no missions yet"}
            {needsOwner > 0 ? ` · ${needsOwner} needs you` : ""}
          </p>
          {live > 0 ? (
            <span className="mt-2 inline-block size-1.5 animate-pulse rounded-full bg-success" />
          ) : null}
        </a>
      ))}
      {pending.length > 0 ? (
        <a
          href="#/backlog"
          className="glass-panel shadow-card rounded-md p-4 transition-colors hover:bg-card"
        >
          <p className="text-sm font-medium text-foreground">Backlog</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {pending.length} proposal{pending.length === 1 ? "" : "s"} waiting for your decision
          </p>
        </a>
      ) : null}
    </div>
  );
}

export function HomeView({
  projects,
  missions,
  onCreated,
}: {
  projects: Project[];
  missions: MissionRow[];
  onCreated: (id: string) => void;
}) {
  const [now, setNow] = useState(() => new Date());
  const [goal, setGoal] = useState("");
  const [project, setProject] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [features, setFeatures] = useState<FeatureRow[]>([]);

  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    const load = () => getBacklog().then((d) => setFeatures(d.features)).catch(() => {});
    void load();
    const timer = setInterval(load, 5000);
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

  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center px-6 pb-4">
      <div className="flex w-full max-w-[600px] flex-col antialiased">
        <HomeClock now={now} />
        <p className="mb-6 pl-4 font-display text-xl font-normal text-muted-foreground">
          {greeting(now.getHours())} - what should the team work on?
        </p>
        {/* berd's chat composer: translucent muted pill over the canvas */}
        <div
          className="rounded-composer p-4 backdrop-blur-md transition-colors"
          style={{ background: "var(--surface-chat-composer)" }}
        >
          <textarea
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void submit();
              }
            }}
            placeholder="Describe the goal - a planner agent grows the workflow at runtime..."
            rows={3}
            className="w-full resize-none bg-transparent px-1 text-sm leading-relaxed text-foreground outline-none placeholder:text-muted-foreground"
          />
          <div className="flex items-center justify-between pt-2">
            {projects.length > 0 ? (
              <select
                value={project}
                onChange={(e) => setProject(e.target.value)}
                className="h-8 rounded-full border border-border bg-card px-3 text-xs text-muted-foreground outline-none"
              >
                <option value="">No project</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            ) : (
              <span className="px-1 text-xs text-muted-foreground">
                Planned dynamically - no fixed pipeline
              </span>
            )}
            {/* berd's primary button: near-black pill */}
            <button
              type="button"
              onClick={() => void submit()}
              disabled={!goal.trim() || busy}
              className="inline-flex h-9 items-center justify-center gap-1.5 rounded-full bg-primary px-4 text-sm text-primary-foreground transition disabled:opacity-30"
              aria-label="Start mission"
            >
              <ArrowUp className="size-4" />
            </button>
          </div>
        </div>
        {error ? <p className="mt-2 pl-4 text-xs text-destructive">{error}</p> : null}
        <Glanceables projects={projects} missions={missions} features={features} />
      </div>
    </div>
  );
}
