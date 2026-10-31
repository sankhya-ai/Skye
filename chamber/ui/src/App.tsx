import { LayoutGrid, List, Plus, Users } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AgentInspectorDialog } from "@/components/AgentInspectorDialog";
import { AwayDialog } from "@/components/AwayDialog";
import { CommandMenu } from "@/components/CommandMenu";
import { Composer } from "@/components/Composer";
import { Conversation } from "@/components/Conversation";
import { CoordinationDialog } from "@/components/CoordinationDialog";
import { ChannelDialog, ChannelMembersDialog, InviteDialog, MembersDialog } from "@/components/Dialogs";
import { GatesDialog } from "@/components/GatesDialog";
import { MemoryView } from "@/components/MemoryView";
import { OnboardingView } from "@/components/OnboardingView";
import { SettingsDialog } from "@/components/SettingsDialog";
import { Sidebar } from "@/components/Sidebar";
import { TalkList } from "@/components/TalkList";
import { TeamWorkView } from "@/components/TeamWorkView";
import { Button } from "@/components/ui/button";
import { TooltipProvider } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { addressFor, inView, people, titleOf, type BoardData, type View } from "@/lib/board";
import { format, parse, type Route } from "@/lib/route";
import { cn } from "@/lib/utils";

// chamber: Slack's layout (sidebar of channels and people) with Linear's lists and board,
// in root's design language. Everything your agents talk about, and your seat in it.

function stored(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) || fallback;
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

const EMPTY: Record<View["kind"], string> = {
  work: "No active obligations yet.",
  inbox: "You're all caught up.",
  all: "No talks yet. Start one, or wait for a teammate's agent to ask.",
  channel: "Nothing said here yet.",
  person: "You haven't talked yet.",
  agent: "This agent hasn't talked with anyone yet.",
  project: "This project's agents haven't talked with anyone yet.",
  memory: "",
};

export function App() {
  const [data, setData] = useState<BoardData | null>(null);
  const [error, setError] = useState("");
  const [route, setRoute] = useState<Route>(() => parse(location.hash));
  const [layout, setLayout] = useState<"list" | "board">(() => stored("chamber:layout", "list") as "list" | "board");
  // Inbox and All talks: everything, talks with teammates, or only you and your own agents.
  const [scope, setScope] = useState<"all" | "team" | "mine">(() => stored("chamber:scope", "all") as "all" | "team" | "mine");
  const [dark, setDark] = useState(() => stored("chamber:theme", matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") === "dark");
  const [menu, setMenu] = useState(false);
  const [channels, setChannels] = useState(false);
  const [invite, setInvite] = useState(false);
  const [members, setMembers] = useState(false);
  const [channelPeople, setChannelPeople] = useState(false);
  const [gatesOpen, setGatesOpen] = useState(false);
  const [coordinationOpen, setCoordinationOpen] = useState(false);
  const [agentsOpen, setAgentsOpen] = useState(false);
  const [awayOpen, setAwayOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [pendingGatesCount, setPendingGatesCount] = useState(0);
  const [activeCoordinationCount, setActiveCoordinationCount] = useState(0);
  const [awayRunning, setAwayRunning] = useState(false);
  const [awayStatusText, setAwayStatusText] = useState("");

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    persist("chamber:theme", dark ? "dark" : "light");
  }, [dark]);

  const refresh = useCallback(() => {
    api.board().then(
      (d) => {
        setData((prev) => (JSON.stringify(prev) === JSON.stringify(d) ? prev : d));
        setError("");
      },
      (e) => setError(e instanceof TypeError ? "The board is not running: start it with `chamber board`." : String(e.message)),
    );
    api.gates().then(
      (res) => {
        const count = (res.gates || []).filter((g) => g.status === "pending").length;
        setPendingGatesCount((prev) => (prev === count ? prev : count));
      },
      () => undefined
    );
    api.coordinationTasks().then(
      (res) => {
        const count = (res.tasks || []).filter((t) => t.status === "requested" || t.status === "claimed" || t.status === "in_progress").length;
        setActiveCoordinationCount((prev) => (prev === count ? prev : count));
      },
      () => undefined
    );
    api.awayStatus().then(
      (res) => {
        const running = Boolean(res.running);
        setAwayRunning((prev) => (prev === running ? prev : running));
        if (res.status) setAwayStatusText((prev) => (prev === res.status ? prev : res.status));
      },
      () => undefined
    );
  }, []);

  // Desktop notifications when app is in background
  useEffect(() => {
    if (typeof Notification !== "undefined" && Notification.permission === "default") {
      Notification.requestPermission().catch(() => {});
    }
  }, []);

  const prevUnreadRef = useRef<number>(0);
  const prevGatesRef = useRef<number>(0);

  useEffect(() => {
    if (!data) return;
    const totalUnread = data.cards.filter((c) => c.unread).length;
    if (
      typeof Notification !== "undefined" &&
      Notification.permission === "granted" &&
      document.hidden &&
      totalUnread > prevUnreadRef.current
    ) {
      const latest = data.cards.find((c) => c.unread);
      if (latest) {
        new Notification(`Chamber: ${latest.title}`, {
          body: `${latest.last?.from || "Teammate"}: ${latest.last?.text || "New message"}`,
        });
      }
    }
    prevUnreadRef.current = totalUnread;
  }, [data]);

  // Obligation attention model: notify immediately when an approval gate needs human review
  useEffect(() => {
    if (
      typeof Notification !== "undefined" &&
      Notification.permission === "granted" &&
      pendingGatesCount > prevGatesRef.current
    ) {
      new Notification("Chamber: Action Required", {
        body: `${pendingGatesCount} approval gate${pendingGatesCount === 1 ? "" : "s"} require your review to proceed.`,
      });
    }
    prevGatesRef.current = pendingGatesCount;
  }, [pendingGatesCount]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 3500);
    const onHash = () => setRoute(parse(location.hash));
    const onVisibility = () => {
      if (!document.hidden) refresh();
    };
    addEventListener("hashchange", onHash);
    addEventListener("popstate", onHash);
    addEventListener("focus", onVisibility);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      clearInterval(timer);
      removeEventListener("hashchange", onHash);
      removeEventListener("popstate", onHash);
      removeEventListener("focus", onVisibility);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [refresh]);

  const go = (next: Route) => {
    history.pushState(null, "", format(next));
    setRoute(next);
  };
  const openView = (view: View) => go({ view, talk: null });
  const openTalk = (talk: string | null) => go({ ...route, talk });

  // Keyboard: ⌘K anywhere; C for a new talk and Esc to close, when not typing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = /input|textarea/i.test((e.target as HTMLElement)?.tagName ?? "");
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setMenu((v) => !v);
      } else if ((e.metaKey || e.ctrlKey) && e.key === ",") {
        e.preventDefault();
        setSettingsOpen(true);
      } else if (!typing && !menu && e.key === "c" && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        openTalk("new");
      } else if (!typing && e.key === "Escape" && route.talk) openTalk(null);
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  });

  const me = data?.me ?? "";
  const cards = useMemo(() => {
    const all = inView(data?.cards ?? [], route.view, data?.projects ?? []);
    if (scope === "all" || (route.view.kind !== "inbox" && route.view.kind !== "all")) return all;
    const mine = (c: (typeof all)[number]) => !c.channel && c.with.length === 0;
    return all.filter((c) => (scope === "mine" ? mine(c) : !mine(c)));
  }, [data, route.view, scope]);
  const known = useMemo(() => {
    const names = people(data).map((p) => p.owner);
    const chans = (data?.channels ?? []).filter((c) => c.joined).map((c) => c.name);
    const agents = (data?.agents ?? []).map((a) => a.agent).filter((a) => !a.endsWith("/board"));
    return [...names, ...chans, ...agents];
  }, [data]);
  const here = addressFor(route.view);

  if (data && !data.configured) {
    return <OnboardingView onConfigured={refresh} chambers={data.chambers} adding={data.adding} />;
  }

  return (
    <TooltipProvider delayDuration={300}>
      <div className="flex h-screen w-screen gap-2 p-2 overflow-hidden bg-background">
        <Sidebar
          data={data}
            view={route.view}
            dark={dark}
          pendingGatesCount={pendingGatesCount}
          activeCoordinationCount={activeCoordinationCount}
          awayActive={awayRunning}
          awayStatusText={awayStatusText}
          onView={openView}
          onSearch={() => setMenu(true)}
          onNewChannel={() => setChannels(true)}
          onInvite={() => setInvite(true)}
          onMembers={() => setMembers(true)}
          onGates={() => setGatesOpen(true)}
          onCoordination={() => setCoordinationOpen(true)}
          onAway={() => setAwayOpen(true)}
          onSettings={() => setSettingsOpen(true)}
          onTheme={() => setDark((v) => !v)}
          onOpenAgents={() => setAgentsOpen(true)}
          onChanged={refresh}
        />

        <main className="flex min-w-0 flex-1 overflow-clip rounded-xl border bg-background shadow-sm">
          <div className="flex min-w-0 flex-1 flex-col">
            {route.view.kind !== "work" ? (
              <header className="flex h-12 shrink-0 items-center gap-3 border-b px-4">
                <h1 className="font-semibold capitalize text-sm">{titleOf(route.view, me)}</h1>
                {route.view.kind === "channel" ? (
                  <button onClick={() => setChannelPeople(true)} className="flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[12px] text-muted-foreground hover:bg-accent hover:text-foreground" title="People in this channel">
                    <Users className="size-3.5" />
                    {data?.channels.find((c) => route.view.kind === "channel" && c.name === route.view.name)?.members.length ?? 0}
                  </button>
                ) : null}
                {error ? <span className="truncate text-[12px] text-destructive">{error}</span> : null}

                <div className="ml-auto flex items-center gap-2">
                  <div className={cn("flex rounded-md border p-0.5 text-[12px]", route.view.kind !== "all" && route.view.kind !== "inbox" && "hidden")} role="group" aria-label="Whose talks">
                    {(["all", "team", "mine"] as const).map((s) => (
                      <button
                        key={s}
                        onClick={() => {
                          setScope(s);
                          persist("chamber:scope", s);
                        }}
                        title={s === "mine" ? "Only you and your own agents" : s === "team" ? "Talks with teammates and channels" : "Everything"}
                        className={cn("h-6 rounded px-2 text-muted-foreground", scope === s && "bg-accent text-foreground")}
                      >
                        {s === "all" ? "All" : s === "team" ? "Team" : "Mine"}
                      </button>
                    ))}
                  </div>
                  <div className={cn("flex rounded-md border p-0.5", route.view.kind !== "all" && "hidden")}>
                    {(["list", "board"] as const).map((l) => (
                      <button
                        key={l}
                        aria-label={l}
                        onClick={() => {
                          setLayout(l);
                          persist("chamber:layout", l);
                        }}
                        className={cn("inline-flex h-6 w-7 items-center justify-center rounded text-muted-foreground", layout === l && "bg-accent text-foreground")}
                      >
                        {l === "list" ? <List className="size-3.5" /> : <LayoutGrid className="size-3.5" />}
                      </button>
                    ))}
                  </div>

                  <Button size="sm" onClick={() => openTalk("new")}>
                    <Plus /> New talk
                  </Button>
                </div>
              </header>
            ) : null}

            {route.view.kind === "work" ? (
              <TeamWorkView
                data={data}
                me={me}
                onOpenTalk={(id) => go({ view: { kind: "all" }, talk: id })}
                onOpenGates={() => setGatesOpen(true)}
                onChanged={refresh}
                onNewTalk={() => openTalk("new")}
                onAddWorkspace={() => setAgentsOpen(true)}
              />
            ) : route.view.kind === "memory" ? (
              <MemoryView onTalk={(id) => go({ view: { kind: "all" }, talk: id })} />
            ) : (
              <TalkList
                cards={cards}
                me={me}
                layout={route.view.kind === "inbox" ? "inbox" : route.view.kind === "all" ? layout : "list"}
                open={route.talk}
                onOpen={openTalk}
                onDone={(id) => void api.markDone(id).then(refresh)}
                empty={EMPTY[route.view.kind]}
              />
            )}

            {here && !route.talk ? (
              <div className="border-t pt-3">
                <Composer
                  placeholder={`Message ${route.view.kind === "channel" ? here : titleOf(route.view, me)}… (starts a new talk)`}
                  people={people(data).map((p) => p.owner)}
                  channel={(() => {
                    const found = route.view.kind === "channel" ? data?.channels.find((c) => c.name === here) : undefined;
                    return found ? { name: found.name, members: found.members, add: async (owner: string) => { await api.channel("add", found.name, owner); refresh(); } } : undefined;
                  })()}
                  onSend={async (text) => {
                    const started = await api.startTalk(here, text, text.split("\n")[0].slice(0, 80));
                    refresh();
                    openTalk(started.thread);
                  }}
                />
              </div>
            ) : null}
          </div>

          {route.talk ? (
            <Conversation
              key={route.talk}
              talkId={route.talk === "new" ? null : route.talk}
              me={me}
              to={here}
              people={known}
              channels={data?.channels ?? []}
              onClose={() => openTalk(null)}
              onStarted={(thread) => openTalk(thread)}
              onChanged={refresh}
            />
          ) : null}
        </main>
      </div>

      <CommandMenu
        open={menu}
        onOpenChange={setMenu}
        data={data}
        onView={openView}
        onTalk={(id) => go({ view: { kind: "all" }, talk: id })}
        onNewTalk={() => openTalk("new")}
        onNewChannel={() => setChannels(true)}
        onInvite={() => setInvite(true)}
        onTheme={() => setDark((v) => !v)}
      />
      <ChannelDialog
        open={channels}
        onOpenChange={setChannels}
        data={data}
        onJoined={(name) => {
          refresh();
          openView({ kind: "channel", name });
        }}
      />
      <InviteDialog open={invite} onOpenChange={setInvite} />
      {route.view.kind === "channel" ? (
        <ChannelMembersDialog
          open={channelPeople}
          onOpenChange={setChannelPeople}
          data={data}
          channel={route.view.name}
          onLeft={() => openView({ kind: "inbox" })}
          onChanged={refresh}
        />
      ) : null}
      <MembersDialog open={members} onOpenChange={setMembers} admin={data?.admin ?? false} me={me} data={data} onChanged={refresh} />
      <GatesDialog open={gatesOpen} onOpenChange={setGatesOpen} me={me} onChanged={refresh} />
      <CoordinationDialog open={coordinationOpen} onOpenChange={setCoordinationOpen} me={me} />
      <AgentInspectorDialog open={agentsOpen} onOpenChange={setAgentsOpen} data={data} onChanged={refresh} />
      <AwayDialog open={awayOpen} onOpenChange={setAwayOpen} me={me} onChanged={refresh} />
      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} data={data} me={me} onChanged={refresh} dark={dark} onToggleTheme={setDark} />
    </TooltipProvider>
  );
}
