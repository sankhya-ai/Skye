import { Brain, Compass, Cpu, FileDiff, FileText, FlaskConical, ListTodo, PanelRight, Shield, SquareTerminal, WifiOff, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "../lib/api.ts";
import { useSession } from "../lib/useSession.ts";
import type { Attachment, Health, PermissionMode, Session, SlashCommand } from "../lib/types.ts";
import { Composer } from "./Composer.tsx";
import { Inspector, type InspectorTab } from "./inspector/Inspector.tsx";
import { TerminalDrawer } from "./TerminalDrawer.tsx";
import { MODE_ORDER, ModeMenu } from "./ModeMenu.tsx";
import { Transcript } from "./Transcript.tsx";
import { shortcut } from "../lib/keys.ts";

const DEFAULT_COMMANDS: SlashCommand[] = [
  { name: "verify", description: "Run automated tests & verification suite", aliases: ["test"] },
  { name: "plan", description: "Toggle plan mode (think & propose steps without writing files)", aliases: [] },
  { name: "diff", description: "Inspect uncommitted code changes in the inspector", aliases: ["changes"] },
  { name: "yolo", description: "Toggle auto-approve for every action", aliases: [] },
  { name: "init", description: "Analyze codebase architecture and generate AGENTS.md", aliases: [] },
  { name: "compact", description: "Compact session context to save token budget", aliases: [] },
  { name: "clear", description: "Clear context and start a fresh session turn", aliases: ["reset"] },
];

interface QuickAction {
  id: string;
  icon: typeof Compass;
  iconClass: "icon-green" | "icon-blue" | "icon-gold" | "icon-red";
  title: string;
  desc: string;
  prompt: string;
  badge?: string;
}

const ACTION_SUGGESTIONS: QuickAction[] = [
  {
    id: "verify",
    icon: FlaskConical,
    iconClass: "icon-green",
    title: "Run tests & verification",
    desc: "Run tests, detect regressions, and verify fixes",
    prompt: "Run the verification suite for this codebase and report passing or failing status.",
    badge: "Checks",
  },
  {
    id: "tour",
    icon: Compass,
    iconClass: "icon-blue",
    title: "Codebase architecture tour",
    desc: "Map directory structure, entry points, and workflows",
    prompt: "Give me a tour of this codebase: structure, entry points, how to run it, and key conventions.",
  },
  {
    id: "diff",
    icon: FileDiff,
    iconClass: "icon-gold",
    title: "Review uncommitted diffs",
    desc: "Audit changes for edge cases, syntax, and potential bugs",
    prompt: "Review all uncommitted changes and git diffs in this workspace for potential bugs or regressions.",
    badge: "Diff",
  },
  {
    id: "plan",
    icon: ListTodo,
    iconClass: "icon-blue",
    title: "Propose implementation plan",
    desc: "Propose step-by-step changes before touching code",
    prompt: "Propose a structured implementation plan for our next task before editing files.",
    badge: "Plan",
  },
  {
    id: "init",
    icon: FileText,
    iconClass: "icon-red",
    title: "Audit & init AGENTS.md",
    desc: "Scan repository build & test commands into AGENTS.md",
    prompt: "/init",
    badge: "Setup",
  },
];

export function SessionView({
  sessionId,
  health,
  initialPrompt,
  onInitialSent,
  inspectorOpen,
  onToggleInspector,
  onSessionChange,
  onOpenSettings,
}: {
  sessionId: string;
  health: Health | null;
  initialPrompt?: string;
  onInitialSent?: () => void;
  inspectorOpen: boolean;
  onToggleInspector: () => void;
  onSessionChange: (session: Session) => void;
  onOpenSettings: () => void;
}) {
  const handle = useSession(sessionId, onSessionChange);
  const { transcript, session, connection, error } = handle;
  const [tab, setTab] = useState<InspectorTab>("changes");
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [files, setFiles] = useState<string[]>([]);
  const [refreshKey, setRefreshKey] = useState(0);
  const [injected, setInjected] = useState<{ text: string; n: number } | null>(null);
  const [restoreError, setRestoreError] = useState("");
  const turnsSeen = useRef(0);
  const sentInitial = useRef(false);

  const workspace = session?.workspace ?? "";
  const turnEnds = transcript.blocks.filter((b) => b.kind === "turn_end").length;

  useEffect(() => {
    if (turnEnds !== turnsSeen.current) {
      turnsSeen.current = turnEnds;
      setRefreshKey((k) => k + 1);
    }
  }, [turnEnds]);

  useEffect(() => {
    if (!workspace) return;
    api.files(workspace).then((d) => setFiles(d.files), () => setFiles([]));
  }, [workspace, refreshKey]);

  useEffect(() => {
    if (initialPrompt && connection === "open" && session && !sentInitial.current) {
      sentInitial.current = true;
      handle.send(initialPrompt, []);
      onInitialSent?.();
    }
  }, [initialPrompt, connection, session, handle, onInitialSent]);

  const slashCommands = useMemo(() => {
    const fromRuntime = session?.runtime?.slash_commands;
    return fromRuntime && fromRuntime.length ? fromRuntime : DEFAULT_COMMANDS;
  }, [session]);

  const setMode = useCallback((mode: PermissionMode) => handle.setMode(mode), [handle]);

  // Esc: reject a waiting approval, else stop the turn. Shift+Tab: cycle modes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        setTerminalOpen((open) => !open);
        return;
      }
      // Keys typed in the terminal are the shell's (Esc in vim must not reject an approval).
      if (e.target instanceof Element && e.target.closest(".terminalPanel")) return;
      if (e.key === "Escape" && !document.querySelector(".backdrop")) {
        const waiting = [...transcript.pending.values()][0];
        if (waiting) {
          e.preventDefault();
          handle.approve(waiting.request.id, "reject");
        } else if (transcript.running) {
          e.preventDefault();
          handle.cancel();
        }
      }
      if (e.key === "Tab" && e.shiftKey && session) {
        e.preventDefault();
        setMode(MODE_ORDER[(MODE_ORDER.indexOf(session.permission_mode) + 1) % MODE_ORDER.length]);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [transcript, handle, session, setMode]);

  // During a turn a message waits its turn, as in Codex; Esc still stops the run.
  const send = (text: string, attachments: Attachment[]) => (transcript.running ? handle.queue(text, attachments) : handle.send(text, attachments));
  const queued = session?.queued ?? [];
  const approvePlan = () => {
    setMode("accept_edits");
    handle.send("The plan is approved. Implement it now, then verify the result.", []);
  };

  const context = transcript.contextUsage;
  const needsKey = health && !health.api_key_set && !health.scripted;
  const runtime = session?.runtime;

  const empty = (
    <div className="home" style={{ minHeight: 400 }}>
      <div className="eyebrow">{workspace.split("/").filter(Boolean).pop() ?? "workspace"}</div>
      <h2 className="greeting">What should we build?</h2>
      <div className="actionCardsGrid">
        {ACTION_SUGGESTIONS.map((action) => {
          const Icon = action.icon;
          return (
            <button
              key={action.id}
              className="actionCard"
              onClick={() => setInjected({ text: action.prompt, n: Date.now() })}
            >
              <div className="actionCardHead">
                <span className={`actionCardIcon ${action.iconClass}`}>
                  <Icon size={16} />
                </span>
                <span className="actionCardTitle">{action.title}</span>
                {action.badge ? <span className="actionCardBadge">{action.badge}</span> : null}
              </div>
              <span className="actionCardDesc">{action.desc}</span>
            </button>
          );
        })}
      </div>
    </div>
  );

  const doneTodos = transcript.todos.filter((t) => t.status === "completed").length;

  return (
    <>
      <main className="card body">
        <header className="header">
          <div className="headerTitle">
            <h1 title={session?.title}>{session?.title ?? "Loading…"}</h1>
            <div className="path" title={workspace}>
              {workspace}
            </div>
          </div>
          {turnEnds > 0 ? (
            <button
              className={`chip quiet${tab === "changes" && inspectorOpen ? " on" : ""}`}
              onClick={() => {
                setTab("changes");
                if (!inspectorOpen) onToggleInspector();
              }}
              title="Inspect uncommitted changes"
            >
              <FileDiff size={13} className="icon-gold" /> Changes
            </button>
          ) : null}
          {transcript.todos.length > 0 ? (
            <button
              className={`chip quiet${tab === "plan" && inspectorOpen ? " on" : ""}`}
              onClick={() => {
                setTab("plan");
                if (!inspectorOpen) onToggleInspector();
              }}
              title="Inspect active plan"
            >
              <ListTodo size={13} className="icon-blue" /> Plan ({doneTodos}/{transcript.todos.length})
            </button>
          ) : null}
          {runtime?.director && runtime.director !== "off" ? (
            <span className="chip quiet" title="Jev decides each step: reply, or which tool to call">
              <Compass size={13} /> Jev{runtime.director === "shadow" ? " (shadow)" : ""}
            </span>
          ) : null}
          {runtime?.memory && runtime.memory !== "off" ? (
            <span className="chip quiet" title="Dhee long-term memory">
              <Brain size={13} /> Dhee
            </span>
          ) : null}
          {runtime?.sandbox && runtime.sandbox !== "none" ? (
            <span className="chip quiet" title="Commands run in a sandbox">
              <Shield size={13} /> Sandboxed
            </span>
          ) : null}
          {context != null ? (
            <span className="meter" title={`Context used: ${Math.round(context * 100)}%`}>
              <span className="track">
                <span className="fill" style={{ width: `${Math.min(100, Math.round(context * 100))}%` }} />
              </span>
              {Math.round(context * 100)}%
            </span>
          ) : null}
          <button className={`iconButton${terminalOpen ? " on" : ""}`} onClick={() => setTerminalOpen((open) => !open)} title={`Terminal (${shortcut("J")})`} aria-label="Terminal">
            <SquareTerminal size={17} />
          </button>
          <button className={`iconButton${inspectorOpen ? " on" : ""}`} onClick={onToggleInspector} title="Changes, files and plan">
            <PanelRight size={17} />
          </button>
        </header>
        {health?.runtime.error ? <div className="banner error">{health.runtime.error}</div> : null}
        {needsKey ? (
          <div className="banner">
            <span className="grow">Add an OpenRouter API key to start working.</span>
            <button className="pill ink small" onClick={onOpenSettings}>
              Open settings
            </button>
          </div>
        ) : null}
        {connection === "closed" ? (
          <div className="banner">
            <WifiOff size={15} /> <span className="grow">Connection lost — reconnecting…</span>
          </div>
        ) : null}
        {error ? (
          <div className="banner error">
            <span className="grow">{error}</span>
            <button className="iconButton" onClick={handle.clearError} aria-label="Dismiss">
              <X size={14} />
            </button>
          </div>
        ) : null}
        {restoreError ? (
          <div className="banner error">
            <span className="grow">{restoreError}</span>
            <button className="iconButton" onClick={() => setRestoreError("")} aria-label="Dismiss">
              <X size={14} />
            </button>
          </div>
        ) : null}
        <Transcript
          state={transcript}
          workspace={workspace}
          onApprove={handle.approve}
          empty={empty}
          hasMore={handle.hasMore}
          onLoadEarlier={() => void handle.loadEarlier()}
          planMode={session?.permission_mode === "plan"}
          onApprovePlan={approvePlan}
          fileUrl={(file, download) => api.fileUrl(sessionId, file.path, file.scope, download)}
          onRestore={(checkpoint) => {
            api.rollback(sessionId, checkpoint).catch((e: Error) => setRestoreError(e.message));
          }}
        />
        <div className="composerWrap">
          <div className="column">
            {queued.length ? (
              <div className="queued">
                {queued.map((item, i) => (
                  <div key={item.id} className="queuedItem">
                    <span className="faint small">{i === 0 && !transcript.running ? "Paused" : "Queued"}</span>
                    <span className="queuedText" title={item.text}>
                      {item.text || `${item.attachments} file${item.attachments === 1 ? "" : "s"}`}
                    </span>
                    {!transcript.running ? (
                      <button className="pill small" onClick={() => handle.sendQueued(item.id)} title="Send this now">
                        Send
                      </button>
                    ) : null}
                    <button
                      className="pill small"
                      onClick={() => {
                        handle.unqueue(item.id);
                        setInjected({ text: item.text, n: Date.now() });
                      }}
                      title="Take it back into the composer"
                    >
                      Edit
                    </button>
                    <button className="iconButton" onClick={() => handle.unqueue(item.id)} title="Remove" aria-label="Remove queued message">
                      <X size={13} />
                    </button>
                  </div>
                ))}
              </div>
            ) : null}
            <ComposerWithInjection
              key={sessionId}
              injected={injected}
              running={transcript.running}
              disabled={!session || connection !== "open"}
              placeholder={transcript.running ? "Working… Enter queues a message for after this turn · Esc stops" : "Ask root to change, fix, or explain something"}
              slashCommands={slashCommands}
              files={files}
              onSend={send}
              onStop={handle.cancel}
              draftKey={sessionId}
              chips={
                <>
                  {session ? <ModeMenu mode={session.permission_mode} onChange={setMode} /> : null}
                  {runtime?.model ? (
                    <button className="chip quiet" onClick={onOpenSettings} title="Model (change in settings)">
                      <Cpu size={13} /> {runtime.model}
                    </button>
                  ) : null}
                </>
              }
            />
          </div>
        </div>
        <TerminalDrawer sessionId={sessionId} open={terminalOpen} onClose={() => setTerminalOpen(false)} />
      </main>
      {inspectorOpen && session ? (
        <Inspector
          tab={tab}
          onTab={setTab}
          onClose={onToggleInspector}
          workspace={workspace}
          session={session}
          todos={transcript.todos}
          refreshKey={refreshKey}
          files={files}
          onMention={(path) => setInjected({ text: `@${path} `, n: Date.now() })}
        />
      ) : null}
    </>
  );
}

/** The composer, remounted with text when a suggestion or mention is pushed into it. */
function ComposerWithInjection({ injected, ...props }: { injected: { text: string; n: number } | null } & React.ComponentProps<typeof Composer>) {
  useEffect(() => {
    if (!injected) return;
    try {
      const key = `draft:${props.draftKey}`;
      const current = localStorage.getItem(key) ?? "";
      localStorage.setItem(key, injected.text.startsWith("@") ? `${current}${current && !current.endsWith(" ") ? " " : ""}${injected.text}` : injected.text);
    } catch {
      /* storage unavailable */
    }
  }, [injected, props.draftKey]);
  return <Composer key={injected?.n ?? 0} {...props} />;
}
