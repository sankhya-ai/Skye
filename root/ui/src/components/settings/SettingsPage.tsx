// Settings as a page: a quiet rail of a few sections on the left, rows in
// rounded groups on the right. Every change saves as it is made.

import { ArrowLeft, Bot, Brain, Cpu, Puzzle, Settings2, Shield } from "lucide-react";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { api } from "../../lib/api.ts";
import type { Connections, Health, PermissionMode, Settings } from "../../lib/types.ts";
import { Group, Row, Section, SecretField, Segmented, Switch, TextField } from "./controls.tsx";
import { LearnedGroups, TrustedCommandsGroup } from "./LearnedGroups.tsx";
import { ModelSection } from "./ModelSection.tsx";
import { PluginsSection } from "./PluginsSection.tsx";

export type SettingsTab = "general" | "model" | "agent" | "memory" | "plugins" | "data";

const TABS: { id: SettingsTab; label: string; icon: ReactNode }[] = [
  { id: "general", label: "General", icon: <Settings2 size={16} /> },
  { id: "model", label: "Model", icon: <Cpu size={16} /> },
  { id: "agent", label: "Agent", icon: <Bot size={16} /> },
  { id: "memory", label: "Memory", icon: <Brain size={16} /> },
  { id: "plugins", label: "Plugins", icon: <Puzzle size={16} /> },
  { id: "data", label: "Data & privacy", icon: <Shield size={16} /> },
];

const MODES: { value: PermissionMode; label: string }[] = [
  { value: "default", label: "Ask before acting" },
  { value: "accept_edits", label: "Accept edits" },
  { value: "plan", label: "Plan" },
  { value: "yolo", label: "Auto-approve all" },
];

export function SettingsPage({
  tab,
  health,
  theme,
  onTab,
  onBack,
  onTheme,
  onSaved,
}: {
  tab: SettingsTab;
  health: Health | null;
  theme: "dark" | "light";
  onTab: (tab: SettingsTab) => void;
  onBack: () => void;
  onTheme: (theme: "dark" | "light") => void;
  onSaved: (settings: Settings) => void;
}) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.settings().then(setSettings, (e: Error) => setError(e.message));
  }, []);

  const save = useCallback(
    (changes: Record<string, unknown>) => {
      setError("");
      api.saveSettings(changes).then(
        (next) => {
          setSettings(next);
          onSaved(next);
          setSaved(true);
          setTimeout(() => setSaved(false), 1600);
        },
        (e: Error) => setError(e.message),
      );
    },
    [onSaved],
  );

  return (
    <div className="settingsPage">
      <aside className="settingsNav">
        <button className="settingsBack" onClick={onBack}>
          <ArrowLeft size={16} /> Back to root
        </button>
        <nav>
          {TABS.map((t) => (
            <button key={t.id} className={`settingsNavItem${t.id === tab ? " active" : ""}`} onClick={() => onTab(t.id)}>
              {t.icon}
              <span>{t.label}</span>
            </button>
          ))}
        </nav>
      </aside>
      <main className="settingsMain scroll">
        <div className="settingsColumn">
          {error ? <div className="settingsError">{error}</div> : null}
          {tab === "plugins" ? (
            <PluginsSection />
          ) : !settings ? (
            <p className="settingsRowHelp">Loading…</p>
          ) : tab === "general" ? (
            <GeneralSection settings={settings} theme={theme} onTheme={onTheme} save={save} />
          ) : tab === "model" ? (
            <ModelSection settings={settings} health={health} save={save} />
          ) : tab === "agent" ? (
            <AgentSection settings={settings} save={save} />
          ) : tab === "memory" ? (
            <MemorySection settings={settings} health={health} save={save} />
          ) : (
            <DataSection health={health} />
          )}
        </div>
        <div className={`settingsSaved${saved ? " show" : ""}`} aria-live="polite">
          Saved — applies from the next message
        </div>
      </main>
    </div>
  );
}

type Save = (changes: Record<string, unknown>) => void;

function GeneralSection({ settings, theme, onTheme, save }: { settings: Settings; theme: "dark" | "light"; onTheme: (t: "dark" | "light") => void; save: Save }) {
  return (
    <Section title="General">
      <Group>
        <Row label="Appearance">
          <Segmented
            value={theme}
            options={[
              { value: "dark", label: "Dark" },
              { value: "light", label: "Light" },
            ]}
            onChange={onTheme}
          />
        </Row>
        <Row label="New sessions start in" help="How much root may do before asking. You can change it in any session.">
          <select className="input" value={settings.default_permission_mode} onChange={(e) => save({ default_permission_mode: e.target.value })}>
            {MODES.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </Row>
      </Group>
    </Section>
  );
}

function AgentSection({ settings, save }: { settings: Settings; save: Save }) {
  return (
    <Section title="Agent" lede="How root works through a task.">
      <Group title="Steering">
        <Row label="Jev step director" help="Before each step, Jev decides whether to reply or which tool to call, so smaller models act instead of describing. Shadow records its decisions without acting on them.">
          <Segmented
            value={settings.director_mode}
            options={[
              { value: "on", label: "On" },
              { value: "shadow", label: "Shadow" },
              { value: "off", label: "Off" },
            ]}
            onChange={(v) => save({ director_mode: v })}
          />
        </Row>
      </Group>
      <Group title="Learning">
        <Row label="After substantial work" help="A reviewer proposes skills and memories. “Ask me” waits for you on the Learning page; “Automatic” keeps what passes the overfit filter.">
          <Segmented
            value={settings.learning_mode}
            options={[
              { value: "review", label: "Ask me" },
              { value: "auto", label: "Automatic" },
              { value: "off", label: "Off" },
            ]}
            onChange={(v) => save({ learning_mode: v })}
          />
        </Row>
        <Row label="Reviewer model" help="Empty: the coding model.">
          <TextField mono width={260} value={settings.learning_model} placeholder={settings.model} onSave={(v) => save({ learning_model: v.trim() })} />
        </Row>
      </Group>
      <LearnedGroups />
      <Group title="Web search">
        <Row label="Search the web" help="Keyless by default (Parallel, then Firecrawl).">
          <Switch checked={settings.web_search} label="Web search" onChange={(v) => save({ web_search: v })} />
        </Row>
        <Row label="Parallel key" help="Optional: lifts the keyless rate limit.">
          <SecretField isSet={settings.parallel_key_set} placeholder="Optional" onSave={(v) => save({ parallel_api_key: v })} onClear={() => save({ clear_parallel_api_key: true })} />
        </Row>
        <Row label="Firecrawl key" help="Optional.">
          <SecretField isSet={settings.firecrawl_key_set} placeholder="Optional" onSave={(v) => save({ firecrawl_api_key: v })} onClear={() => save({ clear_firecrawl_api_key: true })} />
        </Row>
        <Row label="Paid fallback" help="OpenRouter's web plugin when the free tiers fail. Billed to your key.">
          <Switch checked={settings.web_search_paid_fallback} label="Paid web search fallback" onChange={(v) => save({ web_search_paid_fallback: v })} />
        </Row>
      </Group>
    </Section>
  );
}

function MemorySection({ settings, health, save }: { settings: Settings; health: Health | null; save: Save }) {
  const memory = health?.memory;
  return (
    <Section title="Memory" lede="Dhee remembers your projects' conventions, commands and decisions, and your preferences, across sessions.">
      <Group>
        <Row label="Memory" help="OpenRouter uses real embeddings and merges restated facts instead of duplicating them; until a key is set it runs locally. Local works offline with word matching.">
          <Segmented
            value={settings.memory_mode}
            options={[
              { value: "openrouter", label: "OpenRouter" },
              { value: "local", label: "Local" },
              { value: "off", label: "Off" },
            ]}
            onChange={(v) => save({ memory_mode: v })}
          />
        </Row>
        <Row label="Status">
          <span className="settingsValue">
            {!memory ? "…" : !memory.installed ? "Dhee is not installed" : `Dhee ${memory.version} · ${memory.active_mode}${memory.error ? ` · ${memory.error}` : ""}`}
          </span>
        </Row>
      </Group>
      {settings.memory_mode === "openrouter" ? (
        <Group title="Models">
          <Row label="Embeddings">
            <TextField mono width={260} value={settings.memory_embedding_model} onSave={(v) => save({ memory_embedding_model: v.trim() })} />
          </Row>
          <Row label="Embedding dimensions">
            <TextField type="number" width={120} value={String(settings.memory_embedding_dims)} onSave={(v) => save({ memory_embedding_dims: Number(v) })} />
          </Row>
          <Row label="Enrichment model">
            <TextField mono width={260} value={settings.memory_llm_model} onSave={(v) => save({ memory_llm_model: v.trim() })} />
          </Row>
        </Group>
      ) : null}
    </Section>
  );
}

function DataSection({ health }: { health: Health | null }) {
  const [connections, setConnections] = useState<Connections | null>(null);
  useEffect(() => {
    api.connections().then(setConnections, () => undefined);
  }, []);
  return (
    <Section title="Data & privacy">
      <Group>
        <Row label="Where root keeps your data" help="Sessions, memory, learned skills, schedules and the Python toolbox.">
          <span className="settingsValue mono">{health?.data_dir || "On the server"}</span>
        </Row>
        <Row label="Keys and tokens" help="Your OpenRouter key and connected accounts' tokens. They never reach the agent: root makes those calls itself.">
          <span className="settingsValue">{connections ? (connections.secrets === "keychain" ? "macOS Keychain" : "A private file (0600)") : "…"}</span>
        </Row>
        <Row label="The agent's reach" help="root works in the folder you open. Reading or changing anything outside it asks you, except in Auto-approve all.">
          <span className="settingsValue">Workspace only</span>
        </Row>
      </Group>
      <TrustedCommandsGroup />
    </Section>
  );
}
