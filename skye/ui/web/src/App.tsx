// App shell adapted from block/berd's navigation (Apache-2.0, see skye/ui/NOTICE):
// a slim sidebar on a light dot-grid canvas; views are real surfaces backed by the
// persisted mission engine - no decorative controls.
import { Home, Settings, Users, Workflow } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { getMissions, type MissionRow } from "./api";
import { AgentsView } from "./components/AgentsView";
import { HomeView } from "./components/HomeView";
import { MissionDetailView } from "./components/MissionDetailView";
import { MissionsOverview } from "./components/MissionsOverview";
import { SettingsView } from "./components/SettingsView";

const NAV = [
  { path: "/", label: "Home", icon: Home },
  { path: "/missions", label: "Missions", icon: Workflow },
  { path: "/agents", label: "Agents", icon: Users },
  { path: "/settings", label: "Settings", icon: Settings },
];

function NavItem({
  path,
  label,
  icon: Icon,
  active,
}: {
  path: string;
  label: string;
  icon: typeof Home;
  active: boolean;
}) {
  return (
    <a
      href={`#${path}`}
      className={`flex h-7 items-center gap-2 rounded-sm px-3 py-1 text-sm transition ${
        active ? "bg-foreground/[0.03] font-medium" : "text-muted-foreground hover:bg-foreground/[0.02] hover:text-foreground"
      }`}
    >
      <Icon className="size-4" />
      {label}
    </a>
  );
}

function MissionsView() {
  const [missions, setMissions] = useState<MissionRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await getMissions();
      const live = data.live ?? {};
      setMissions(data.missions.map((m) => ({ ...m, live: m.live || Boolean(live[m.id]?.running) })));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 3000);
    return () => clearInterval(timer);
  }, [refresh]);

  if (error) return <p className="text-sm text-destructive">{error}</p>;
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-medium">Missions</h2>
        <p className="text-sm text-muted-foreground">
          Journaled workflows with one-time approvals - local only.
        </p>
      </div>
      <MissionsOverview
        missions={missions}
        onOpen={(id) => (window.location.hash = `/mission/${encodeURIComponent(id)}`)}
      />
    </div>
  );
}

export default function App() {
  const [route, setRoute] = useState<string>(() => window.location.hash.slice(1) || "/");

  useEffect(() => {
    const onHash = () => setRoute(window.location.hash.slice(1) || "/");
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const isDetail = route.startsWith("/mission/");
  const detailId = isDetail ? decodeURIComponent(route.slice("/mission/".length)) : null;
  const activeNav = isDetail ? "/missions" : NAV.some((n) => n.path === route) ? route : "/";

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-52 shrink-0 flex-col border-r border-border bg-card px-2 py-4">
        <p className="mb-4 px-3 text-sm font-semibold tracking-tight">Skye</p>
        <nav className="space-y-0.5">
          {NAV.map((item) => (
            <NavItem key={item.path} {...item} active={activeNav === item.path} />
          ))}
        </nav>
        <p className="mt-auto px-3 text-[11px] leading-4 text-muted-foreground">
          Local only. Runs are journaled; interrupted work waits for an explicit operator decision.
        </p>
      </aside>
      <main className="bg-dot-grid min-w-0 flex-1">
        {isDetail && detailId ? (
          <div className="mx-auto max-w-3xl px-4 py-8">
            <MissionDetailView id={detailId} onBack={() => (window.location.hash = "/missions")} />
          </div>
        ) : route === "/missions" ? (
          <div className="mx-auto max-w-3xl px-4 py-8">
            <MissionsView />
          </div>
        ) : route === "/agents" ? (
          <div className="mx-auto max-w-3xl px-4 py-8">
            <AgentsView />
          </div>
        ) : route === "/settings" ? (
          <div className="mx-auto max-w-3xl px-4 py-8">
            <SettingsView />
          </div>
        ) : (
          <HomeView
            onCreated={(id) => (window.location.hash = `/mission/${encodeURIComponent(id)}`)}
          />
        )}
      </main>
    </div>
  );
}
