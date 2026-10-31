// Settings → Plugins: what root can do beyond its core tools, as one quiet
// list (after Codex): a row per plugin — icon, name, one line, and a switch or
// its connection state. A row opens to show what it gives the agent and, for a
// connection, how to connect it. The owner's MCP servers come last.

import { AlarmClock, Brain, ChevronDown, ChevronRight, FileText, GitMerge, Github, Globe, Mail, MessageCircle, Monitor, Plus, Search, Server, Slack, Terminal, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { api } from "../../lib/api.ts";
import type { Connections, DeviceFlow, KeikiStatus, McpServerInfo, Plugin, PluginCatalog, SlackStatus } from "../../lib/types.ts";
import { Group, Section, Switch } from "./controls.tsx";
import { GeneratedSection } from "./GeneratedSection.tsx";

const ICONS: Record<string, ReactNode> = {
  file: <FileText size={16} />,
  terminal: <Terminal size={16} />,
  github: <Github size={16} />,
  mail: <Mail size={16} />,
  search: <Search size={16} />,
  clock: <AlarmClock size={16} />,
  branch: <GitMerge size={16} />,
  globe: <Globe size={16} />,
  brain: <Brain size={16} />,
  slack: <Slack size={16} />,
  message: <MessageCircle size={16} />,
  monitor: <Monitor size={16} />,
};

function PluginRow({ icon, name, summary, right, children }: { icon: ReactNode; name: string; summary: string; right: ReactNode; children?: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`pluginRow${open ? " open" : ""}`}>
      <div className="pluginHead">
        <button type="button" className="pluginMain" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          <span className="pluginIcon">{icon}</span>
          <span className="pluginText">
            <span className="pluginName">{name}</span>
            <span className="pluginSummary">{summary}</span>
          </span>
          {children ? open ? <ChevronDown size={14} className="faint" /> : <ChevronRight size={14} className="faint" /> : null}
        </button>
        <div className="pluginRight">{right}</div>
      </div>
      {open && children ? <div className="pluginBody">{children}</div> : null}
    </div>
  );
}

function Capabilities({ items }: { items: string[] }) {
  return (
    <div className="pluginCaps">
      {items.map((item) => (
        <span key={item} className="pluginCap">
          {item}
        </span>
      ))}
    </div>
  );
}

function GitHubDetails({ state, refresh }: { state: Connections["github"]; refresh: () => void }) {
  const [token, setToken] = useState("");
  const [device, setDevice] = useState<DeviceFlow | null>(null);
  const [error, setError] = useState("");
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => () => (timer.current ? clearInterval(timer.current) : undefined), []);

  const signIn = async () => {
    setError("");
    try {
      const flow = await api.githubDevice();
      setDevice(flow);
      timer.current = setInterval(() => {
        api.githubDevicePoll(flow.device_code).then(
          (r) => {
            if (!r.pending) {
              if (timer.current) clearInterval(timer.current);
              setDevice(null);
              refresh();
            }
          },
          (e: Error) => {
            if (timer.current) clearInterval(timer.current);
            setDevice(null);
            setError(e.message);
          },
        );
      }, Math.max(flow.interval, 2) * 1000);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (state.connected && state.source === "connected") {
    return (
      <p className="pluginNote">
        Connected as @{state.login} with a token root keeps.{" "}
        <button className="linkish" onClick={() => api.githubDisconnect().then(refresh)}>
          Disconnect
        </button>
      </p>
    );
  }
  return (
    <>
      {state.connected ? <p className="pluginNote">Using your {state.source} login. Connect a token below to use another account.</p> : null}
      <div className="settingsInline">
        <input className="input" type="password" autoComplete="off" placeholder="GitHub token with the repo scope" value={token} onChange={(e) => setToken(e.target.value)} />
        <button
          className="pill ink"
          disabled={!token.trim()}
          onClick={() =>
            api.githubToken(token.trim()).then(
              () => {
                setToken("");
                refresh();
              },
              (e: Error) => setError(e.message),
            )
          }
        >
          Connect
        </button>
        {state.device_flow ? (
          <button className="pill" disabled={!!device} onClick={() => void signIn()}>
            Sign in with GitHub
          </button>
        ) : null}
      </div>
      {device ? (
        <p className="pluginNote">
          Enter <strong className="mono">{device.user_code}</strong> at{" "}
          <a href={device.verification_uri} target="_blank" rel="noreferrer">
            {device.verification_uri}
          </a>
          ; this updates when you are done.
        </p>
      ) : null}
      {error ? <p className="pluginError">{error}</p> : null}
    </>
  );
}

function SlackDetails({ state, refresh }: { state: SlackStatus; refresh: () => void }) {
  const [bot, setBot] = useState("");
  const [app, setApp] = useState("");
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (state.configured) {
    return (
      <>
        <p className="pluginNote">
          {state.connected ? `Answering in ${state.team || "Slack"} as @${state.bot}.` : `Connecting to ${state.team || "Slack"}…`} Invite it to a channel with{" "}
          <span className="mono">/invite @{state.bot || "root"}</span>, then @mention it.{" "}
          <button className="linkish" onClick={() => api.slackDisconnect().then(refresh)}>
            Disconnect
          </button>
        </p>
        {!state.answers ? <p className="pluginError">root has no model key yet, so it can't answer. Add one in Settings → Model.</p> : null}
        {state.problem ? <p className="pluginError">{state.problem}</p> : null}
      </>
    );
  }
  const copyManifest = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(await api.slackManifest(), null, 2));
      setCopied(true);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const connect = () => {
    setBusy(true);
    setError("");
    api.slackConnect(bot.trim(), app.trim()).then(
      () => {
        setBot("");
        setApp("");
        setBusy(false);
        refresh();
      },
      (e: Error) => {
        setBusy(false);
        setError(e.message);
      },
    );
  };
  return (
    <>
      <ol className="pluginSteps">
        <li>
          <button className="linkish" onClick={() => void copyManifest()}>
            {copied ? "Copied the app manifest" : "Copy the app manifest"}
          </button>
          , then at{" "}
          <a href="https://api.slack.com/apps?new_app=1" target="_blank" rel="noreferrer">
            api.slack.com/apps
          </a>{" "}
          choose <em>From a manifest</em>, paste it, and install the app to your workspace.
        </li>
        <li>
          From <em>OAuth &amp; Permissions</em>, copy the bot token (<span className="mono">xoxb-…</span>).
        </li>
        <li>
          In <em>Basic Information → App-Level Tokens</em>, generate one with <span className="mono">connections:write</span> (<span className="mono">xapp-…</span>).
        </li>
      </ol>
      <div className="settingsInline">
        <input className="input" type="password" autoComplete="off" placeholder="Bot token (xoxb-…)" value={bot} onChange={(e) => setBot(e.target.value)} />
        <input className="input" type="password" autoComplete="off" placeholder="App-level token (xapp-…)" value={app} onChange={(e) => setApp(e.target.value)} />
        <button className="pill ink" disabled={busy || !bot.trim() || !app.trim()} onClick={connect}>
          {busy ? "Checking…" : "Connect"}
        </button>
      </div>
      {error ? <p className="pluginError">{error}</p> : null}
    </>
  );
}

function KeikiDetails({ state, refresh }: { state: KeikiStatus; refresh: () => void }) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (state.configured) {
    return (
      <>
        <p className="pluginNote">
          {state.connected ? `Taking turns from Keiki · ${state.turns_answered} answered since root started.` : "Connecting to Keiki…"}{" "}
          <button className="linkish" onClick={() => api.keikiDisconnect().then(refresh)}>
            Disconnect
          </button>
        </p>
        {state.problem ? <p className="pluginError">{state.problem}</p> : null}
      </>
    );
  }
  const connect = () => {
    setBusy(true);
    setError("");
    api.keikiConnect(key.trim()).then(
      () => {
        setKey("");
        setBusy(false);
        refresh();
      },
      (e: Error) => {
        setBusy(false);
        setError(e.message);
      },
    );
  };
  return (
    <>
      <ol className="pluginSteps">
        <li>
          At{" "}
          <a href="https://onkeiki.com/dashboard" target="_blank" rel="noreferrer">
            onkeiki.com
          </a>
          , create an agent and add its channels (WhatsApp, iMessage, Telegram, Slack…).
        </li>
        <li>
          Set its harness to <em>External</em> in <em>inbox</em> mode, and copy the harness API key.
        </li>
      </ol>
      <div className="settingsInline">
        <input className="input" type="password" autoComplete="off" placeholder="Keiki harness API key" value={key} onChange={(e) => setKey(e.target.value)} />
        <button className="pill ink" disabled={busy || !key.trim()} onClick={connect}>
          {busy ? "Checking…" : "Connect"}
        </button>
      </div>
      {error ? <p className="pluginError">{error}</p> : null}
    </>
  );
}

function GmailDetails({ state, refresh }: { state: Connections["gmail"]; refresh: () => void }) {
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState("");
  if (state.connected) {
    return (
      <p className="pluginNote">
        Connected as {state.email}.{" "}
        <button className="linkish" onClick={() => api.googleDisconnect().then(refresh)}>
          Disconnect
        </button>
      </p>
    );
  }
  if (!state.configured) return <p className="pluginNote">This install has no Google sign-in configured, so Gmail cannot be connected here.</p>;
  return (
    <>
      <button
        className="pill ink"
        disabled={waiting}
        onClick={() =>
          api.googleStart().then(
            ({ url }) => {
              window.open(url, "_blank", "noopener");
              setWaiting(true);
              let checks = 0;
              const timer = setInterval(() => {
                checks += 1;
                api.connections().then((c) => {
                  if (c.gmail.connected || checks > 90) {
                    clearInterval(timer);
                    setWaiting(false);
                    refresh();
                  }
                });
              }, 2000);
            },
            (e: Error) => setError(e.message),
          )
        }
      >
        {waiting ? "Waiting for Google…" : "Connect Gmail"}
      </button>
      {error ? <p className="pluginError">{error}</p> : null}
    </>
  );
}

function ToolboxDetails({ path }: { path: string }) {
  const [packages, setPackages] = useState<{ name: string; version: string }[] | null>(null);
  useEffect(() => {
    api.toolboxPackages().then((d) => setPackages(d.packages), () => setPackages([]));
  }, []);
  return (
    <>
      <p className="pluginNote">
        Packages root installed for tasks live here, not in your system or projects: <span className="mono">{path}</span>
      </p>
      {packages === null ? null : packages.length ? (
        <Capabilities items={packages.map((p) => `${p.name} ${p.version}`)} />
      ) : (
        <p className="pluginNote">Nothing installed yet — root installs on demand.</p>
      )}
    </>
  );
}

function McpForm({ onSaved, onCancel }: { onSaved: (servers: McpServerInfo[]) => void; onCancel: () => void }) {
  const [name, setName] = useState("");
  const [remote, setRemote] = useState(false);
  const [command, setCommand] = useState("");
  const [url, setUrl] = useState("");
  const [env, setEnv] = useState("");
  const [error, setError] = useState("");
  const save = () => {
    const words = command.trim().split(/\s+/).filter(Boolean);
    const vars = Object.fromEntries(
      env
        .split("\n")
        .map((line) => line.trim())
        .filter((line) => line.includes("="))
        .map((line) => [line.slice(0, line.indexOf("=")).trim(), line.slice(line.indexOf("=") + 1).trim()]),
    );
    api
      .saveMcp(name.trim(), remote ? { url: url.trim(), env: vars } : { command: words[0] ?? "", args: words.slice(1), env: vars })
      .then((r) => onSaved(r.mcp), (e: Error) => setError(e.message));
  };
  return (
    <div className="mcpForm">
      <div className="settingsInline">
        <input className="input" placeholder="Name, e.g. linear" value={name} onChange={(e) => setName(e.target.value)} />
        <div className="segmented">
          <button type="button" className={remote ? "" : "active"} onClick={() => setRemote(false)}>
            Local command
          </button>
          <button type="button" className={remote ? "active" : ""} onClick={() => setRemote(true)}>
            Remote URL
          </button>
        </div>
      </div>
      {remote ? (
        <input className="input mono" placeholder="https://mcp.example.com/sse" value={url} onChange={(e) => setUrl(e.target.value)} />
      ) : (
        <input className="input mono" placeholder="npx -y @modelcontextprotocol/server-github" value={command} onChange={(e) => setCommand(e.target.value)} />
      )}
      <textarea className="input mono" rows={2} placeholder={"Environment, one per line: TOKEN=…"} value={env} onChange={(e) => setEnv(e.target.value)} />
      {error ? <p className="pluginError">{error}</p> : null}
      <div className="mcpFormActions">
        <button className="pill" onClick={onCancel}>
          Cancel
        </button>
        <button className="pill ink" disabled={!name.trim() || !(remote ? url.trim() : command.trim())} onClick={save}>
          Add server
        </button>
      </div>
    </div>
  );
}

export function PluginsSection() {
  const [catalog, setCatalog] = useState<PluginCatalog | null>(null);
  const [connections, setConnections] = useState<Connections | null>(null);
  const [slack, setSlack] = useState<SlackStatus | null>(null);
  const [keiki, setKeiki] = useState<KeikiStatus | null>(null);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    api.plugins().then(setCatalog, (e: Error) => setError(e.message));
    api.connections().then(setConnections, () => undefined);
    api.slack().then(setSlack, () => undefined); // a hosted root has no Slack or Keiki
    api.keiki().then(setKeiki, () => undefined);
  }, []);
  useEffect(refresh, [refresh]);

  const toggle = (plugin: Plugin, enabled: boolean) => {
    setCatalog((c) => (c ? { ...c, plugins: c.plugins.map((p) => (p.id === plugin.id ? { ...p, enabled } : p)) } : c));
    api.switchPlugin(plugin.id, enabled).catch((e: Error) => {
      setError(e.message);
      refresh();
    });
  };

  const setMcp = (servers: McpServerInfo[]) => setCatalog((c) => (c ? { ...c, mcp: servers } : c));

  if (!catalog) return <Section title="Plugins">{error ? <p className="pluginError">{error}</p> : null}</Section>;
  const connectionPlugins = catalog.plugins.filter((p) => p.kind === "connection" && (p.id !== "slack" || slack) && (p.id !== "keiki" || keiki));
  const builtIn = catalog.plugins.filter((p) => p.kind === "built-in");

  const connectionState = (plugin: Plugin) => {
    if (plugin.id === "keiki") {
      return keiki?.configured ? (
        <span className={`pluginStatus${keiki.connected ? " on" : ""}`}>{keiki.connected ? "Connected" : keiki.problem ? "Problem" : "Connecting"}</span>
      ) : (
        <span className="pluginStatus">Not connected</span>
      );
    }
    if (plugin.id === "slack") {
      return slack?.configured ? (
        <span className={`pluginStatus${slack.connected ? " on" : ""}`}>{slack.connected ? slack.team || "Connected" : slack.problem ? "Problem" : "Connecting"}</span>
      ) : (
        <span className="pluginStatus">Not connected</span>
      );
    }
    const state = plugin.id === "github" ? connections?.github : connections?.gmail;
    return state?.connected ? (
      <span className="pluginStatus on">{plugin.id === "github" ? (state as Connections["github"]).login ? `@${(state as Connections["github"]).login}` : "Connected" : (state as Connections["gmail"]).email || "Connected"}</span>
    ) : (
      <span className="pluginStatus">Not connected</span>
    );
  };

  return (
    <Section title="Plugins" lede="What root can do beyond reading and editing code. Switch off what you don't use: root then gets none of its tools.">
      <Group title="Connections">
        {connectionPlugins.map((plugin) => (
          <PluginRow
            key={plugin.id}
            icon={ICONS[plugin.icon]}
            name={plugin.name}
            summary={plugin.summary}
            right={
              <>
                {connectionState(plugin)}
                <Switch checked={plugin.enabled} label={`${plugin.name} on`} onChange={(v) => toggle(plugin, v)} />
              </>
            }
          >
            <Capabilities items={plugin.capabilities} />
            {plugin.id === "slack" ? (
              slack ? <SlackDetails state={slack} refresh={refresh} /> : null
            ) : plugin.id === "keiki" ? (
              keiki ? <KeikiDetails state={keiki} refresh={refresh} /> : null
            ) : connections ? (
              plugin.id === "github" ? (
                <GitHubDetails state={connections.github} refresh={refresh} />
              ) : (
                <GmailDetails state={connections.gmail} refresh={refresh} />
              )
            ) : null}
          </PluginRow>
        ))}
      </Group>

      <GeneratedSection />

      <Group title="Built in">
        {builtIn.map((plugin) => (
          <PluginRow key={plugin.id} icon={ICONS[plugin.icon]} name={plugin.name} summary={plugin.summary} right={<Switch checked={plugin.enabled} label={`${plugin.name} on`} onChange={(v) => toggle(plugin, v)} />}>
            <Capabilities items={plugin.capabilities} />
            {plugin.id === "toolbox" ? <ToolboxDetails path={catalog.toolbox.path} /> : null}
          </PluginRow>
        ))}
      </Group>

      <Group
        title="MCP servers"
        action={
          adding ? null : (
            <button className="pill" onClick={() => setAdding(true)}>
              <Plus size={13} /> Add server
            </button>
          )
        }
      >
        {catalog.mcp.map((server) => (
          <PluginRow
            key={server.name}
            icon={<Server size={16} />}
            name={server.name}
            summary={server.url || [server.command, ...server.args].join(" ")}
            right={
              <>
                <button className="iconButton" title={`Remove ${server.name}`} onClick={() => api.deleteMcp(server.name).then((r) => setMcp(r.mcp))}>
                  <Trash2 size={14} />
                </button>
                <Switch
                  checked={server.enabled}
                  label={`${server.name} on`}
                  onChange={(enabled) =>
                    api
                      .saveMcp(server.name, server.url ? { url: server.url, enabled } : { command: server.command, args: server.args, enabled })
                      .then((r) => setMcp(r.mcp), (e: Error) => setError(e.message))
                  }
                />
              </>
            }
          >
            {server.env.length ? <p className="pluginNote">Environment: {server.env.join(", ")} (values stay on this machine)</p> : <p className="pluginNote">No environment variables.</p>}
          </PluginRow>
        ))}
        {adding ? (
          <McpForm
            onSaved={(servers) => {
              setMcp(servers);
              setAdding(false);
            }}
            onCancel={() => setAdding(false)}
          />
        ) : catalog.mcp.length === 0 ? (
          <div className="pluginEmpty">Connect any Model Context Protocol server — Linear, Sentry, a database — and its tools join root's. Changes apply from the next message.</div>
        ) : null}
      </Group>
      {error ? <p className="pluginError">{error}</p> : null}
    </Section>
  );
}
