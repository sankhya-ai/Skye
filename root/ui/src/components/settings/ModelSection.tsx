// Settings → Model: the OpenRouter key, the coding model (with the catalogue),
// fallbacks and how the model is run.

import { RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { api } from "../../lib/api.ts";
import type { Health, ModelInfo, Settings } from "../../lib/types.ts";
import { Group, Row, Section, SecretField, Segmented, TextField } from "./controls.tsx";

function price(value?: string | null): string {
  const n = Number(value ?? NaN);
  if (!Number.isFinite(n)) return "";
  if (n === 0) return "free";
  return `$${(n * 1_000_000).toFixed(2)}/M`;
}

export function ModelSection({ settings, health, save }: { settings: Settings; health: Health | null; save: (changes: Record<string, unknown>) => void }) {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [modelsError, setModelsError] = useState("");
  const [filter, setFilter] = useState("");
  const [picking, setPicking] = useState(false);

  useEffect(() => {
    api.models().then(
      (d) => {
        setModels(d.models);
        setModelsError(d.error);
      },
      (e: Error) => setModelsError(e.message),
    );
  }, []);

  const shown = useMemo(() => {
    const q = filter.toLowerCase();
    return models.filter((m) => m.tools && (!q || m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q))).slice(0, 150);
  }, [models, filter]);
  const current = models.find((m) => m.id === settings.model);

  return (
    <Section title="Model" lede="root reaches every model through OpenRouter with one key. The agent never sees the key: runtimes go through root.">
      <Group title="OpenRouter">
        <Row
          label="API key"
          help={
            settings.api_key_source === "environment"
              ? "From OPENROUTER_API_KEY in root's environment."
              : health?.mode === "hosted"
                ? "Your own key, used instead of the server's."
                : "Kept on this machine (in the Keychain in the Mac app)."
          }
        >
          <SecretField
            isSet={settings.api_key_set}
            hint={settings.api_key_hint}
            placeholder="sk-or-v1-…"
            locked={settings.api_key_source === "environment" ? "Set by the environment" : undefined}
            onSave={(key) => save({ openrouter_api_key: key })}
            onClear={settings.api_key_source === "settings" ? () => save({ clear_api_key: true }) : undefined}
          />
        </Row>
      </Group>

      <Group title="Coding model">
        <Row
          label="Model"
          help={current ? `${current.context_length ? `${Math.round(current.context_length / 1000)}k context · ` : ""}${price(current.prompt_price) || "price unknown"}` : "Any OpenRouter model that supports tools."}
        >
          <div className="settingsInline">
            <TextField mono value={settings.model} onSave={(model) => save({ model: model.trim() })} width={260} />
            <button className="pill" onClick={() => setPicking((v) => !v)}>
              {picking ? "Done" : "Browse"}
            </button>
          </div>
        </Row>
        {picking ? (
          <div className="settingsRow stacked">
            <div className="settingsInline" style={{ width: "100%" }}>
              <input className="input" placeholder="Filter models with tool support" value={filter} onChange={(e) => setFilter(e.target.value)} />
              <button type="button" className="iconButton" title="Refresh the catalogue" onClick={() => api.models(true).then((d) => setModels(d.models))}>
                <RefreshCw size={14} />
              </button>
            </div>
            {modelsError && !models.length ? <p className="settingsRowHelp">Catalogue unavailable ({modelsError}); type a model id above.</p> : null}
            <div className="modelList scroll">
              {shown.map((m) => (
                <button
                  type="button"
                  key={m.id}
                  className={`modelRow${m.id === settings.model ? " active" : ""}`}
                  onClick={() => save({ model: m.id, max_context_size: m.context_length ? Math.max(8000, m.context_length) : settings.max_context_size })}
                >
                  <span className="mono small">{m.id}</span>
                  <span className="faint small">
                    {m.context_length ? `${Math.round(m.context_length / 1000)}k` : ""} {price(m.prompt_price)}
                    {m.capabilities.includes("image_in") ? " · vision" : ""}
                  </span>
                </button>
              ))}
            </div>
          </div>
        ) : null}
        <Row label="Fallback models" help="Up to three, tried in order when the model fails or its providers are down.">
          <TextField
            mono
            width={260}
            placeholder="qwen/qwen3-coder, z-ai/glm-4.6"
            value={settings.fallback_models.join(", ")}
            onSave={(text) =>
              save({
                fallback_models: text
                  .split(",")
                  .map((m) => m.trim())
                  .filter(Boolean)
                  .slice(0, 3),
              })
            }
          />
        </Row>
        <Row label="Reasoning effort">
          <Segmented
            value={(settings.reasoning_effort || "") as string}
            options={[
              { value: "", label: "Default" },
              { value: "none", label: "Off" },
              { value: "low", label: "Low" },
              { value: "medium", label: "Medium" },
              { value: "high", label: "High" },
            ]}
            onChange={(v) => save({ reasoning_effort: v })}
          />
        </Row>
        <Row label="Provider preference" help="One model is served by several providers; the fastest can be ~3× quicker at ~2× the price.">
          <Segmented
            value={(settings.provider_sort || "") as string}
            options={[
              { value: "", label: "Balanced" },
              { value: "throughput", label: "Fastest" },
              { value: "price", label: "Cheapest" },
            ]}
            onChange={(v) => save({ provider_sort: v })}
          />
        </Row>
      </Group>

      <Group title="Limits">
        <Row label="Context window" help="Tokens. root compacts the conversation before it runs out.">
          <TextField type="number" width={120} value={String(settings.max_context_size)} onSave={(v) => save({ max_context_size: Number(v) })} />
        </Row>
        <Row label="Steps per turn" help="The most tool calls in one turn before root stops and asks you to continue.">
          <TextField type="number" width={120} value={String(settings.max_steps_per_turn)} onSave={(v) => save({ max_steps_per_turn: Number(v) })} />
        </Row>
      </Group>
    </Section>
  );
}
