// Settings → Plugins → Generated: the owner's review and activation surface for code the agent wrote.
// The owner sees exactly what would run - every file, the manifest tools, the verification report, the
// isolation facts - and activates the exact reviewed hash. A stale review cannot authorize changed code:
// the button sends the hash of what is on screen, and the server refuses anything else.

import { FlaskConical } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { api } from "../../lib/api.ts";
import type { GeneratedPluginListing, PluginReview } from "../../lib/types.ts";
import { Group } from "./controls.tsx";

const FILE_LABELS: Record<string, string> = {
  "plugin.py": "Source",
  "plugin.json": "Manifest",
  "tests.py": "Tests",
};

export function GeneratedReview({
  review,
  busy,
  notice,
  onAction,
}: {
  review: PluginReview;
  busy: boolean;
  notice: string;
  onAction: (action: "activate" | "promote" | "revoke") => void;
}) {
  const files = Object.keys(review.files).filter((name) => review.files[name]);
  const [file, setFile] = useState(files[0] ?? "plugin.py");
  const shown = files.includes(file) ? file : files[0];
  const verifiedOk = review.verified?.ok === true;
  const total = review.verified?.results?.length ?? 0;
  return (
    <div className="generatedReview">
      <p className="pluginNote">
        v{review.version} · {review.status}
        {review.lifetime ? ` · ${review.lifetime}` : ""} · proposed by {review.created_by ?? "the agent"}
        {review.created_at ? ` · ${review.created_at}` : ""}
      </p>
      <p className="pluginNote">
        Tools it would add: {review.tools.map((t) => t.name).join(", ") || "none"}
        {review.tools.some((t) => t.description) ? ` — ${review.tools.map((t) => t.description).filter(Boolean).join("; ")}` : ""}
      </p>
      <p className="pluginNote">
        Verification: {review.verified ? (verifiedOk ? `all ${total} jail test(s) passed` : "failed - this version cannot be activated") : "not run yet"}.
        Runs as: {review.execution.isolation}.
      </p>
      {!review.execution.available ? <p className="reviewWarn">Execution unavailable on this machine: {review.execution.reason} Activation stays refused until that changes.</p> : null}
      {!review.hash_matches_record ? <p className="reviewWarn">The files on disk no longer match the recorded hash - the candidate changed after review. Do not activate; ask the agent to propose it again.</p> : null}
      <div className="reviewTabs">
        {files.map((name) => (
          <button key={name} type="button" className={`pill${shown === name ? " reviewTabOn" : ""}`} onClick={() => setFile(name)}>
            {FILE_LABELS[name] ?? name}
          </button>
        ))}
      </div>
      {shown ? <pre className="reviewCode">{review.files[shown]}</pre> : null}
      <p className="pluginNote">
        Hash of these exact bytes: <span className="reviewHash">{review.sha256}</span>
      </p>
      <p className="pluginNote">Activation runs only this hash: {review.activation}</p>
      {notice ? <p className="reviewWarn">{notice}</p> : null}
      <div className="reviewActions">
        {review.status === "verified" && review.hash_matches_record && review.execution.available ? (
          <button type="button" className="pill reviewActivate" disabled={busy} onClick={() => onAction("activate")}>
            Activate v{review.version} (this session)
          </button>
        ) : null}
        {review.status === "active" && review.lifetime !== "durable" ? (
          <button type="button" className="pill" disabled={busy} onClick={() => onAction("promote")}>
            Make durable for future sessions
          </button>
        ) : null}
        {review.status === "active" ? (
          <button type="button" className="pill" disabled={busy} onClick={() => onAction("revoke")}>
            Revoke
          </button>
        ) : null}
      </div>
    </div>
  );
}

function GeneratedPluginRow({ name, summary }: { name: string; summary: string }) {
  const [open, setOpen] = useState(false);
  const [review, setReview] = useState<PluginReview | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  const load = useCallback(() => {
    api.reviewGeneratedPlugin(name).then(setReview, (e: Error) => setNotice(e.message));
  }, [name]);

  useEffect(() => {
    if (open && !review) load();
  }, [open, review, load]);

  const act = (action: "activate" | "promote" | "revoke") => {
    if (!review) return;
    setBusy(true);
    setNotice("");
    const call =
      action === "activate"
        ? api.activateGeneratedPlugin(name, review.version, review.sha256) // the hash of what is on screen - nothing else
        : action === "promote"
          ? api.promoteGeneratedPlugin(name)
          : api.revokeGeneratedPlugin(name);
    call.then(
      () => {
        setBusy(false);
        setReview(null);
        load();
      },
      (e: Error) => {
        // A 409 here means the review is stale or the gate refused: show the server's reason and reload
        // the actual current bytes so the owner never acts on an old view.
        setBusy(false);
        setNotice(e.message);
        setReview(null);
        load();
      },
    );
  };

  return (
    <div className={`pluginRow${open ? " open" : ""}`}>
      <div className="pluginHead">
        <button type="button" className="pluginMain" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          <span className="pluginIcon">
            <FlaskConical size={16} />
          </span>
          <span className="pluginText">
            <span className="pluginName">{name}</span>
            <span className="pluginSummary">{summary}</span>
          </span>
        </button>
      </div>
      {open ? (
        <div className="pluginBody">
          {review ? <GeneratedReview review={review} busy={busy} notice={notice} onAction={act} /> : <p className="pluginNote">{notice || "Loading the candidate…"}</p>}
        </div>
      ) : null}
    </div>
  );
}

export function GeneratedSection() {
  const [listing, setListing] = useState<GeneratedPluginListing | null>(null);
  useEffect(() => {
    api.generatedPlugins().then(setListing, () => setListing({}));
  }, []);
  const names = Object.keys(listing ?? {});
  if (!names.length) return null;
  return (
    <Group title="Generated">
      {names.map((name) => {
        const entry = listing![name];
        const latest = entry.versions[entry.versions.length - 1];
        const summary = latest
          ? `v${latest.version} ${latest.status}${entry.active_version ? ` · active v${entry.active_version}` : ""}${latest.lifetime ? ` · ${latest.lifetime}` : ""}`
          : "proposed";
        return <GeneratedPluginRow key={name} name={name} summary={summary} />;
      })}
    </Group>
  );
}
