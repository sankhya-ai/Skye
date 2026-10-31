import { useCallback, useEffect, useRef, useState } from "react";

import { ConfirmDialog } from "./components/ConfirmDialog.tsx";
import { Home } from "./components/Home.tsx";
import { NewSessionDialog } from "./components/NewSessionDialog.tsx";
import { SchedulesPage } from "./components/pages/SchedulesPage.tsx";
import { Rail, type View } from "./components/Rail.tsx";
import { SearchDialog } from "./components/SearchDialog.tsx";
import { SessionView } from "./components/SessionView.tsx";
import { SettingsPage, type SettingsTab } from "./components/settings/SettingsPage.tsx";
import { SignIn } from "./components/SignIn.tsx";
import { api } from "./lib/api.ts";
import { native, sessionNews } from "./lib/native.ts";
import type { Health, Me, PermissionMode, Session, Settings } from "./lib/types.ts";

type Theme = "dark" | "light";
type Route = { view: View | "settings"; id: string | null; tab?: SettingsTab };

function stored<T extends string>(key: string, fallback: T): T {
  try {
    return (localStorage.getItem(key) as T) || fallback;
  } catch {
    return fallback;
  }
}

function persist(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable */
  }
}

function routeFromHash(): Route {
  const session = /^#\/s\/([0-9a-f]+)$/.exec(location.hash);
  if (session) return { view: "session", id: session[1] };
  const settings = /^#\/settings(?:\/(general|model|agent|memory|plugins|data))?$/.exec(location.hash);
  if (settings) return { view: "settings", id: null, tab: (settings[1] as SettingsTab) ?? "general" };
  const page = /^#\/(schedules)$/.exec(location.hash);
  return { view: page ? (page[1] as View) : "home", id: null };
}

export function App() {
  const [theme, setTheme] = useState<Theme>(() => stored<Theme>("root:theme", matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));
  const [railCollapsed, setRailCollapsed] = useState(() => stored<string>("root:rail", "open") === "collapsed");
  const [inspectorOpen, setInspectorOpen] = useState(() => stored<string>("root:inspector", "closed") === "open");
  const [route, setRoute] = useState<Route>(routeFromHash);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [dialog, setDialog] = useState<"pick" | "search" | null>(null);
  const [confirmModal, setConfirmModal] = useState<{
    title: string;
    message: string;
    confirmText?: string;
    onConfirm: () => Promise<void> | void;
  } | null>(null);
  const [picked, setPicked] = useState<((path: string) => void) | null>(null);
  const [firstPrompt, setFirstPrompt] = useState<{ id: string; text: string } | null>(null);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    persist("root:theme", theme);
  }, [theme]);

  // What the last poll saw, so the Mac app can say what changed while you were away.
  const seen = useRef<Map<string, { running: boolean; pending: number }> | null>(null);
  const refresh = useCallback(() => {
    api.sessions().then(
      (d) => {
        setSessions(d.sessions);
        setLoadError("");
        if (native) {
          const news = sessionNews(seen.current ?? new Map(), d.sessions);
          native.setBadge(news.pending);
          if (seen.current && !document.hasFocus()) {
            for (const s of news.asking) native.notify("root needs your approval", s.title, s.id);
            for (const s of news.finished) native.notify("root finished", s.title, s.id);
          }
          seen.current = new Map(d.sessions.map((s) => [s.id, { running: s.running, pending: s.pending_approvals ?? 0 }]));
          window.__rootBusy = () => d.sessions.filter((s) => s.running).length;
        }
      },
      (e) => setLoadError(String(e.message)),
    );
  }, []);

  useEffect(() => {
    api.health().then((h) => {
      setHealth(h);
      if (h.mode === "hosted" && !h.signed_in) return;
      refresh();
      api.me().then(setMe, () => undefined);
      api.settings().then(setSettings, () => undefined);
    }, (e) => setLoadError(String(e.message)));
    // In the Mac app the list also drives notifications and the Dock badge: poll more often.
    const timer = setInterval(refresh, native ? 3000 : 8000);
    const onHash = () => setRoute(routeFromHash());
    window.addEventListener("hashchange", onHash);
    return () => {
      clearInterval(timer);
      window.removeEventListener("hashchange", onHash);
    };
  }, [refresh]);

  const go = (hash: string) => {
    location.hash = hash;
    setRoute(routeFromHash());
  };

  const onSessionChange = useCallback((session: Session) => {
    setSessions((current) => {
      const index = current.findIndex((s) => s.id === session.id);
      if (index < 0) return [session, ...current];
      const next = current.slice();
      next[index] = { ...current[index], ...session };
      return next;
    });
  }, []);

  const pickFolder = (then: (path: string) => void) => {
    if (native && health?.mode !== "hosted") {
      void native.pickFolder().then((path) => path && then(path));
      return;
    }
    const browse = () => {
      setPicked(() => then);
      setDialog("pick");
    };
    // In a browser on the owner's Mac the host shows the Finder dialog instead.
    if (health?.folder_picker) {
      void api.pickFolder(settings?.recent_workspaces?.[0]).then(({ path }) => path && then(path), browse);
      return;
    }
    browse();
  };

  // ⌘K searches, as in Tura (and most apps with a session list).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setDialog((current) => (current === "search" ? null : "search"));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // The Mac app's menu: File → New Session (⌘N) / Open Folder (⌘O), root → Settings (⌘,).
  useEffect(() => {
    const onMenu = (event: Event) => {
      const action = (event as CustomEvent<string>).detail;
      if (action === "newSession") {
        go("#/");
      } else if (action === "openFolder") {
        pickFolder((path) => void startSession(path, "", settings?.default_permission_mode ?? "default"));
      } else if (action === "settings") {
        go("#/settings");
      } else if (action.startsWith("session:")) {
        go(`#/s/${action.slice("session:".length)}`);
      }
    };
    window.addEventListener("root-menu", onMenu);
    return () => window.removeEventListener("root-menu", onMenu);
  });

  const startSession = async (workspace: string, text: string, mode: PermissionMode) => {
    const session = await api.createSession(workspace);
    if (mode !== session.permission_mode) await api.updateSession(session.id, { permission_mode: mode });
    setSessions((current) => [{ ...session, permission_mode: mode }, ...current]);
    if (text.trim()) setFirstPrompt({ id: session.id, text });
    go(`#/s/${session.id}`);
    api.settings().then(setSettings, () => undefined);
  };

  const deleteSession = (session: Session) => {
    setConfirmModal({
      title: "Delete session",
      message: `Delete “${session.title}”? The conversation will be removed, but files it changed are not touched.`,
      confirmText: "Delete",
      onConfirm: async () => {
        await api.deleteSession(session.id);
        setSessions((current) => current.filter((s) => s.id !== session.id));
        if (route.id === session.id) go("#/");
      },
    });
  };

  const deleteWorkspace = (workspace: string, list: Session[]) => {
    const name = workspace.split("/").filter(Boolean).pop() ?? workspace;
    const count = list.length;
    const message = count > 0
      ? `Delete workspace “${name}” and all ${count} conversation(s) inside it? Files in the folder are not touched.`
      : `Remove workspace “${name}”?`;
    setConfirmModal({
      title: "Delete workspace",
      message,
      confirmText: "Delete",
      onConfirm: async () => {
        try {
          await api.deleteWorkspace(workspace);
        } catch {
          for (const session of list) {
            await api.deleteSession(session.id).catch(() => undefined);
          }
        }
        setSessions((current) => current.filter((s) => s.workspace !== workspace));
        if (route.view === "session" && route.id && list.some((s) => s.id === route.id)) {
          go("#/");
        }
        api.settings().then(setSettings, () => undefined);
      },
    });
  };

  if (health?.mode === "hosted" && !health.signed_in) return <SignIn />;

  if (route.view === "settings") {
    return (
      <SettingsPage
        tab={route.tab ?? "general"}
        health={health}
        theme={theme}
        onTab={(tab) => go(`#/settings/${tab}`)}
        onBack={() => go("#/")}
        onTheme={setTheme}
        onSaved={(saved) => {
          setSettings(saved);
          api.health().then(setHealth, () => undefined);
        }}
      />
    );
  }

  const inspector = inspectorOpen && route.view === "session";
  const stageClass = ["stage", inspector ? "withInspector" : "", railCollapsed ? "railCollapsed" : ""].filter(Boolean).join(" ");

  return (
    <div className={stageClass}>
      <Rail
        sessions={sessions}
        activeId={route.id}
        view={route.view}
        collapsed={railCollapsed}
        theme={theme}
        user={me?.mode === "hosted" ? me.user.name : null}
        onToggleCollapsed={() => {
          setRailCollapsed((v) => !v);
          persist("root:rail", railCollapsed ? "open" : "collapsed");
        }}
        onNavigate={(view) => go(view === "home" ? "#/" : `#/${view}`)}
        onSearch={() => setDialog("search")}
        onSelect={(id) => go(`#/s/${id}`)}
        onNew={() => pickFolder((path) => void startSession(path, "", settings?.default_permission_mode ?? "default"))}
        onDelete={deleteSession}
        onDeleteWorkspace={deleteWorkspace}
        onSettings={() => go("#/settings")}
        onTheme={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
        onSignOut={me?.mode === "hosted" ? () => api.logout().then(() => location.reload()) : undefined}
      />
      {route.view === "session" && route.id ? (
        <SessionView
          key={route.id}
          sessionId={route.id}
          health={health}
          initialPrompt={firstPrompt?.id === route.id ? firstPrompt.text : undefined}
          onInitialSent={() => setFirstPrompt(null)}
          inspectorOpen={inspectorOpen}
          onToggleInspector={() => {
            setInspectorOpen((v) => !v);
            persist("root:inspector", inspectorOpen ? "closed" : "open");
          }}
          onSessionChange={onSessionChange}
          onOpenSettings={() => go("#/settings/model")}
        />
      ) : null}
      {route.view === "home" ? (
        <Home
          key={settings?.recent_workspaces?.[0] ?? "none"}
          recent={settings?.recent_workspaces ?? []}
          error={loadError || health?.runtime.error || (health && !health.api_key_set && !health.scripted ? "Add an OpenRouter key in Settings to start." : "")}
          onPickFolder={pickFolder}
          onStart={startSession}
        />
      ) : null}
      {route.view === "schedules" ? <SchedulesPage workspaces={settings?.recent_workspaces ?? []} onOpenSession={(id) => go(`#/s/${id}`)} onPickFolder={pickFolder} /> : null}
      {dialog === "search" ? <SearchDialog sessions={sessions} onOpen={(id) => go(`#/s/${id}`)} onClose={() => setDialog(null)} /> : null}
      {dialog === "pick" ? (
        <NewSessionDialog
          recent={settings?.recent_workspaces ?? []}
          hosted={health?.mode === "hosted"}
          title="Choose a folder"
          action="Use this folder"
          onClose={() => setDialog(null)}
          onCreate={async (path) => {
            setDialog(null);
            picked?.(path);
          }}
        />
      ) : null}
      {confirmModal ? (
        <ConfirmDialog
          title={confirmModal.title}
          message={confirmModal.message}
          confirmText={confirmModal.confirmText}
          onClose={() => setConfirmModal(null)}
          onConfirm={async () => {
            await confirmModal.onConfirm();
            setConfirmModal(null);
          }}
        />
      ) : null}
    </div>
  );
}
