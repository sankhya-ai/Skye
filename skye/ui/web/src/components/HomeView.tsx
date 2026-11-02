// Home canvas adapted from block/berd's HomeScreen (Apache-2.0, see skye/ui/NOTICE):
// clock in the display face, a muted greeting, and one centered pill composer
// (berd's chat-composer token: translucent muted surface, 40px radius, backdrop blur).
import { ArrowUp } from "lucide-react";
import { humanError } from "../lib";
import { Spinner } from "./ui/Spinner";
import { Select } from "./ui/Select";
import { useEffect, useState } from "react";
import {
  createMission,
  createProject,
  getClaudeUsage,
  initProjectGit,
  pickFolder,
  type ClaudeAccount,
  getBacklog,
  getMissionSetup,
  type FeatureRow,
  type MissionRow,
  type MissionSetup,
  type Project,
  getProjectRepos,
  type ProjectRepoDiscovery,
} from "../api";

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
            {live > 0 ? `${live} running` : total > 0 ? `${total} task${total === 1 ? "" : "s"}` : "no tasks yet"}
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
  onProjectsChanged,
}: {
  projects: Project[];
  missions: MissionRow[];
  onCreated: (id: string) => void;
  onProjectsChanged: () => void;
}) {
  // Team: a light planning session sizes the team (often just one developer).
  // Solo: one developer session does the goal directly - no planning session at all.
  const [mode, setMode] = useState<"team" | "solo">(() => {
    try { return (localStorage.getItem("skye.mode") as "team" | "solo") || "team"; } catch { return "team"; }
  });
  const [account, setAccount] = useState<ClaudeAccount | null>(null);
  const [gitBusy, setGitBusy] = useState(false);
  const [gitNote, setGitNote] = useState<string | null>(null);
  const [discoveryTick, setDiscoveryTick] = useState(0);
  const [now, setNow] = useState(() => new Date());
  const [goal, setGoal] = useState("");
  const [project, setProject] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [features, setFeatures] = useState<FeatureRow[]>([]);
  const [setup, setSetup] = useState<MissionSetup | null>(null);
  const [discovery, setDiscovery] = useState<ProjectRepoDiscovery["discovery"] | null>(null);
  const [repoRows, setRepoRows] = useState<Array<{ name: string; path: string }>>([]);
  const [defaultRepo, setDefaultRepo] = useState("");

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

  useEffect(() => {
    getMissionSetup().then(setSetup).catch(() => {});
    getClaudeUsage().then((d) => setAccount(d.account)).catch(() => {});
  }, []);

  useEffect(() => {
    try { localStorage.setItem("skye.mode", mode); } catch { /* per-viewer convenience only */ }
  }, [mode]);

  // A mission always works on a project: pick the most recently added one by default.
  useEffect(() => {
    if (!project && projects.length > 0) setProject(projects[projects.length - 1].id);
  }, [projects, project]);

  const addProjectFolder = async () => {
    setError(null);
    try {
      const picked = await pickFolder();
      let path = picked.path;
      if (!picked.supported) {
        path = window.prompt("Absolute path of your project folder") ?? "";
      }
      if (!path) return;
      const saved = await createProject({ name: path.split("/").filter(Boolean).pop() ?? "project", repo: path });
      onProjectsChanged();
      setProject(saved.project.id);
    } catch (e) {
      setError(humanError(e, "project"));
    }
  };

  const makeGitRepo = async () => {
    if (!project) return;
    setGitBusy(true);
    try {
      const r = await initProjectGit(project);
      setGitNote(r.detail);
      setDiscoveryTick((t) => t + 1);
    } catch (e) {
      setGitNote(humanError(e, "project"));
    } finally {
      setGitBusy(false);
    }
  };

  // Read-only scope preview for a selected project: exactly which repositories a mission
  // would start with, before anything is created. The preview grants no start authority -
  // the mission is still created only when the owner submits.
  useEffect(() => {
    setDiscovery(null);
    if (!project) return;
    let live = true;
    getProjectRepos(project)
      .then((d) => { if (live) setDiscovery(d.discovery); })
      .catch(() => { if (live) setDiscovery(null); });
    return () => { live = false; };
  }, [project, discoveryTick]);

  // The composer never guesses a path: every repository is typed by the owner and validated
  // by the server's mission loader (duplicate realpaths, aliases and same-repo subdirectories
  // are rejected there, exactly like a task file).
  const REPO_NAME = /^[A-Za-z0-9._-]+$/;

  const submit = async () => {
    const text = goal.trim();
    if (!text || busy) return;
    const declared = repoRows.filter((r) => r.name.trim() || r.path.trim());
    for (const row of declared) {
      if (!REPO_NAME.test(row.name.trim())) {
        setError(`Repository name "${row.name || "?"}" must be plain letters, digits, . _ or -`);
        return;
      }
      if (!row.path.trim().startsWith("/")) {
        setError(`Repository "${row.name}" needs an absolute path`);
        return;
      }
    }
    if (new Set(declared.map((r) => r.name.trim())).size !== declared.length) {
      setError("Repository names must be unique");
      return;
    }
    if (!project && declared.length === 0) {
      setError("Choose the project folder the team should work on");
      return;
    }
    const plannerRepo = defaultRepo
      || declared.find((r) => REPO_NAME.test(r.name.trim()))?.path.trim()
      || "";
    setBusy(true);
    setError(null);
    try {
      // A planner mission: the engine grows the real workflow from the goal at runtime.
      const created = await createMission({
        goal: text,
        ...(mode === "solo" ? { solo: true } : { plan: true }),
        run: true,
        ...(project ? { project } : {}),
        ...(declared.length > 0
          ? {
              repos: Object.fromEntries(declared.map((r) => [r.name.trim(), r.path.trim()])),
              repo: plannerRepo,
            }
          : {}),
      });
      onCreated(created.id);
    } catch (e) {
      setError(humanError(e, "task"));
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
        {account && (!account.installed || !account.logged_in) ? (
          <div role="alert" className="mb-3 rounded-md border border-destructive/40 bg-destructive/10 px-4 py-2.5 text-xs leading-5 text-destructive">
            {!account.installed
              ? <>Claude Code isn&apos;t installed on this machine. Install it from claude.com/claude-code, run <code>claude</code> once in a terminal and log in, then reload this page.</>
              : <>Claude Code is installed but not logged in. Run <code>claude</code> in a terminal, log in, then reload this page.</>}
          </div>
        ) : null}
        {/* The composer is the page's one input: a solid white card that reads as a text
            field against the gray canvas, with a clear focus ring while typing. */}
        <div
          className="cursor-text rounded-composer border border-[#dcdcdc] bg-card p-4 shadow-[0_1px_2px_rgba(0,0,0,0.04),0_6px_24px_-8px_rgba(0,0,0,0.12)] transition-[border-color,box-shadow] hover:border-[#c8c8c8] focus-within:border-foreground/40 focus-within:shadow-[0_0_0_4px_rgba(0,0,0,0.06),0_6px_24px_-8px_rgba(0,0,0,0.14)]"
          onClick={(e) => {
            if (e.target === e.currentTarget) e.currentTarget.querySelector("textarea")?.focus();
          }}
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
            placeholder="Describe the goal..."
            rows={3}
            autoFocus
            className="w-full resize-none bg-transparent px-1 text-[15px] leading-relaxed text-foreground outline-none placeholder:text-muted-foreground"
          />
          <div className="flex items-center justify-between pt-2">
            <div className="flex items-center gap-1.5">
              {projects.length > 0 ? (
                <Select
                  value={project}
                  ariaLabel="Project"
                  className="max-w-[12rem]"
                  menuMinWidth={260}
                  onChange={(v) => (v === "__add" ? void addProjectFolder() : setProject(v))}
                  options={[
                    ...projects.map((p) => ({ value: p.id, label: p.name, hint: p.repo })),
                    { value: "__add", label: "+ Add project folder…", action: true },
                  ]}
                />
              ) : (
                <button
                  type="button"
                  onClick={() => void addProjectFolder()}
                  className="h-8 rounded-full border border-dashed border-border bg-card px-3 text-xs text-foreground/80 hover:text-foreground"
                >
                  + Choose project folder
                </button>
              )}
              <div role="radiogroup" aria-label="How to staff this goal" className="flex h-8 items-center rounded-full border border-border bg-card p-0.5 text-xs">
                {(["team", "solo"] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    role="radio"
                    aria-checked={mode === m}
                    onClick={() => setMode(m)}
                    title={m === "team"
                      ? "A quick planning session decides the smallest team (often one developer)"
                      : "One developer session does it directly - fewest tokens"}
                    className={`h-full rounded-full px-2.5 transition-colors ${mode === m ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`}
                  >
                    {m === "team" ? "Team" : "Solo"}
                  </button>
                ))}
              </div>
            </div>
            {/* berd's primary button: near-black pill */}
            <button
              type="button"
              onClick={() => void submit()}
              disabled={!goal.trim() || busy}
              aria-busy={busy || undefined}
              className="inline-flex h-9 items-center justify-center gap-1.5 rounded-full bg-primary px-4 text-sm text-primary-foreground transition disabled:opacity-30"
              aria-label="Start task"
            >
              {busy ? <Spinner className="size-4" /> : <ArrowUp className="size-4" />}
            </button>
          </div>
        </div>
        {discovery ? (
          <div data-discovery-preview className="mt-2 pl-4 text-xs text-muted-foreground">
            {discovery.error ? (
              <p>Can&apos;t inspect the project folder: {discovery.error}</p>
            ) : discovery.root_is_repo && discovery.has_commits === false ? (
              <p>
                This git repository has no commits yet, so the team can&apos;t branch from it.{" "}
                <button type="button" disabled={gitBusy} onClick={() => void makeGitRepo()} className="underline hover:text-foreground">
                  Make a first commit
                </button>
              </p>
            ) : discovery.root_is_repo ? (
              <p>The team works on its own copy (a git branch) - your files change only when you bring the result in.</p>
            ) : Object.keys(discovery.repos).length > 0 ? (
              <p>
                {Object.keys(discovery.repos).length === 1
                  ? "1 repository found in this project: "
                  : `${Object.keys(discovery.repos).length} repositories found in this project: `}
                {Object.keys(discovery.repos).join(", ")}. Skye assigns each step to one
                itself and shows why.
              </p>
            ) : (
              <p>
                This folder isn&apos;t a git repository yet - the team needs one to work on its own branch.{" "}
                <button type="button" disabled={gitBusy} onClick={() => void makeGitRepo()} className="underline hover:text-foreground">
                  Make it a git repository
                </button>
              </p>
            )}
            {gitNote ? <p className="mt-1">{gitNote}</p> : null}
            {!discovery.error && (discovery.skipped > 0
              || discovery.outside_scope.length > 0
              || discovery.inaccessible.length > 0 || discovery.invalid.length > 0
              || discovery.overflow.length > 0) ? (
              <p className="mt-1 text-muted-foreground/80">
                Left out:
                {discovery.skipped > 0
                  ? ` ${discovery.skipped} folder${discovery.skipped === 1 ? "" : "s"} without git`
                  : ""}
                {discovery.overflow.length > 0
                  ? ` ${discovery.overflow.length} over the limit (${discovery.overflow.join(", ")})`
                  : ""}
                {discovery.outside_scope.length > 0
                  ? ` outside the folder (${discovery.outside_scope.join(", ")})`
                  : ""}
                {discovery.inaccessible.length > 0
                  ? ` unreadable (${discovery.inaccessible.join(", ")})`
                  : ""}
                {discovery.invalid.length > 0
                  ? ` not a git repo (${discovery.invalid.join(", ")})`
                  : ""}
                .
              </p>
            ) : null}
          </div>
        ) : null}
        <details className="mt-2 pl-4 text-xs">
          <summary className="cursor-pointer text-muted-foreground/70 hover:text-foreground">
            Multiple repositories (monorepo or separate repos)
          </summary>
          <div className="mt-2 space-y-1.5">
            {repoRows.map((row, i) => (
              <div key={i} className="flex items-center gap-1.5">
                <input
                  value={row.name}
                  onChange={(e) =>
                    setRepoRows(repoRows.map((r, j) => (j === i ? { ...r, name: e.target.value } : r)))
                  }
                  placeholder="label (e.g. web)"
                  className="w-32 rounded-md border border-border bg-background px-2 py-1 text-xs outline-none"
                />
                <input
                  value={row.path}
                  onChange={(e) =>
                    setRepoRows(repoRows.map((r, j) => (j === i ? { ...r, path: e.target.value } : r)))
                  }
                  placeholder="/absolute/path/to/repo"
                  className="flex-1 rounded-md border border-border bg-background px-2 py-1 font-mono text-xs outline-none"
                />
                <button
                  type="button"
                  onClick={() => setRepoRows(repoRows.filter((_, j) => j !== i))}
                  className="text-muted-foreground hover:text-destructive"
                  aria-label={`Remove repository ${row.name || i + 1}`}
                >
                  x
                </button>
              </div>
            ))}
            <button
              type="button"
              onClick={() => setRepoRows([...repoRows, { name: "", path: "" }])}
              className="text-muted-foreground hover:text-foreground"
            >
              + Add repository
            </button>
            {repoRows.length > 0 ? (
              <div className="flex items-center gap-2">
                <label className="text-muted-foreground">Planning runs in</label>
                <Select
                  value={defaultRepo}
                  size="xs"
                  ariaLabel="Planning runs in"
                  onChange={setDefaultRepo}
                  options={repoRows
                    .filter((r) => REPO_NAME.test(r.name.trim()) && r.path.trim())
                    .map((r) => ({ value: r.path.trim(), label: r.name.trim(), hint: r.path.trim() }))}
                />
              </div>
            ) : null}
            <p className="text-[11px] text-muted-foreground/70">
              Add the folders Skye may work in - any short label and its absolute path.
              Skye assigns each step to a folder itself and shows why on the task page;
              "Planning runs in" is only where the planning step itself runs. Repositories
              must be distinct - the server validates them exactly like a task file.
            </p>
          </div>
        </details>
        {error ? <p className="mt-2 pl-4 text-xs text-destructive">{error}</p> : null}
        <Glanceables projects={projects} missions={missions} features={features} />
      </div>
    </div>
  );
}
