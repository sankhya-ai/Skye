import "../src/styles/app.css";
import { createRoot } from "react-dom/client";
import { GeneratedReview } from "../src/components/settings/GeneratedSection.tsx";
import type { PluginReview } from "../src/lib/types.ts";

// Fixture in the exact shape of GET /api/registry/plugins/{name}/review (preview harness only, not shipped).
const review: PluginReview = {
  name: "numstats",
  version: 1,
  status: "verified",
  lifetime: "session",
  session: "a1b2c3",
  created_by: "model",
  created_at: "2026-10-31T09:41:00Z",
  verified: { ok: true, results: [{ name: "test_mean", ok: true }, { name: "test_empty", ok: true }] },
  sha256: "3c434fc4cf80b7a9f2d1e4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5",
  on_disk_sha256: "3c434fc4cf80b7a9f2d1e4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5",
  hash_matches_record: true,
  files: {
    "plugin.py": "import math\nimport statistics\n\n\ndef stats_summary(numbers, kind=\"mean\"):\n    values = [float(n) for n in numbers]\n    if not values:\n        raise ValueError(\"no numbers\")\n    if kind == \"mean\":\n        return {\"mean\": statistics.fmean(values)}\n    if kind == \"stdev\":\n        return {\"stdev\": statistics.stdev(values) if len(values) > 1 else 0.0}\n    raise ValueError(kind)\n",
    "plugin.json": '{\n  "tools": [\n    {\n      "name": "stats_summary",\n      "description": "Summarize a list of numbers.",\n      "parameters": {\n        "type": "object",\n        "properties": {\n          "numbers": { "type": "array", "items": { "type": "number" } },\n          "kind": { "type": "string", "enum": ["mean", "stdev"] }\n        },\n        "required": ["numbers"],\n        "additionalProperties": false\n      }\n    }\n  ]\n}\n',
    "tests.py": "from plugin import stats_summary\n\n\ndef test_mean():\n    assert stats_summary([2.0, 4.0])[\"mean\"] == 3.0\n\n\ndef test_empty():\n    try:\n        stats_summary([])\n        assert False\n    except ValueError:\n        pass\n",
  },
  tools: [{ name: "stats_summary", description: "Summarize a list of numbers." }],
  execution: {
    available: true,
    reason: "",
    isolation: "each run is a fresh bubblewrap jail: no network, scrubbed environment, minimal mounts (no /etc, /opt, home or tenant paths), rlimit ceilings, exact-hash snapshot execution; aggregate memory/task limits apply only where the cgroup probe proves them",
  },
  activation: 'POST /api/registry/plugins/numstats/activate with {"version": 1, "sha256": "3c434fc4cf80..."} - the exact hash of what you reviewed; anything else is refused',
};

createRoot(document.getElementById("root")!).render(
  <div style={{ maxWidth: 640, margin: "24px auto", padding: "0 16px" }}>
    <div className="pluginRow open">
      <div className="pluginBody">
        <GeneratedReview review={review} busy={false} notice="" onAction={() => {}} />
      </div>
    </div>
  </div>,
);
