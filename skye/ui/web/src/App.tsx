// App shell adapted from block/berd (Apache-2.0, see skye/ui/NOTICE): a full-viewport
// dot-grid canvas with a FLOATING glass sidebar (Projects / Chats sections) and views
// backed by the persisted mission engine - no decorative controls.
import { useCallback, useEffect, useState } from "react";
import { getMissions, getProjects, type MissionRow, type Project } from "./api";
import { AgentsView } from "./components/AgentsView";
import { BacklogView } from "./components/BacklogView";
import { HomeView } from "./components/HomeView";
import { MissionDetailView } from "./components/MissionDetailView";
import { MissionsOverview } from "./components/MissionsOverview";
import { SettingsView } from "./components/SettingsView";
import { Sidebar } from "./components/Sidebar";

function MissionsView({ missions, projects }: { missions: MissionRow[]; projects: Project[] }) {
  const query = new URLSearchParams(window.location.hash.split("?")[1] ?? "");
  const projectFilter = query.get("project");
  const project = projects.find((p) => p.id === projectFilter);
  const rows = projectFilter ? missions.filter((m) => m.project === projectFilter) : missions;
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-medium">{project ? project.name : "Missions"}</h2>
        <p className="text-sm text-muted-foreground">
          {project ? (
            <>
              Conversations in this workspace · <code className="text-xs">{project.repo}</code> ·{" "}
              <a href="#/missions" className="underline">
                all missions
              </a>
            </>
          ) : (
            "Journaled workflows with one-time approvals - local only."
          )}
        </p>
      </div>
      <MissionsOverview
        missions={rows}
        onOpen={(id) => (window.location.hash = `/mission/${encodeURIComponent(id)}`)}
      />
    </div>
  );
}

export default function App() {
  const [route, setRoute] = useState<string>(() => window.location.hash.slice(1) || "/");
  const [missions, setMissions] = useState<MissionRow[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);

  useEffect(() => {
    const onHash = () => setRoute(window.location.hash.slice(1) || "/");
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [missionData, projectData] = await Promise.all([getMissions(), getProjects()]);
      const live = missionData.live ?? {};
      setMissions(
        missionData.missions.map((m) => ({
          ...m,
          live: m.live || Boolean(live[m.id]?.running),
        })),
      );
      setProjects(projectData.projects);
    } catch {
      // the sidebar and views surface their own errors on demand
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 3000);
    return () => clearInterval(timer);
  }, [refresh]);

  const path = route.split("?")[0];
  const isDetail = path.startsWith("/mission/");
  const detailId = isDetail ? decodeURIComponent(path.slice("/mission/".length)) : null;
  const activeNav = isDetail ? "/missions" : ["/", "/missions", "/backlog", "/agents", "/settings"].includes(path) ? path : "/";

  return (
    <div className="bg-dot-grid min-h-screen">
      <Sidebar
        activePath={isDetail ? route : activeNav === "/missions" ? route : activeNav}
        projects={projects}
        missions={missions}
        onProjectsChanged={() => void refresh()}
      />
      <main className="min-w-0 pl-64">
        {isDetail && detailId ? (
          <div className="mx-auto max-w-3xl px-4 py-8">
            <MissionDetailView id={detailId} onBack={() => (window.location.hash = "/missions")} />
          </div>
        ) : path === "/missions" ? (
          <div className="mx-auto max-w-3xl px-4 py-8">
            <MissionsView missions={missions} projects={projects} />
          </div>
        ) : path === "/backlog" ? (
          <div className="mx-auto max-w-3xl px-4 py-8">
            <BacklogView />
          </div>
        ) : path === "/agents" ? (
          <div className="mx-auto max-w-3xl px-4 py-8">
            <AgentsView />
          </div>
        ) : path === "/settings" ? (
          <div className="mx-auto max-w-3xl px-4 py-8">
            <SettingsView />
          </div>
        ) : (
          <HomeView
            projects={projects}
            onCreated={(id) => (window.location.hash = `/mission/${encodeURIComponent(id)}`)}
          />
        )}
      </main>
    </div>
  );
}
