// A job the session proposed. The session starts nothing: the owner's Accept here (an owner-authenticated
// POST) is the only way the job runs, and it runs with exactly the fields the session stored. The card reads
// the request's live status from the API, so a replayed transcript event still shows what actually happened.

import { useEffect, useState } from "react";

import { Briefcase } from "lucide-react";

import { api, ApiError } from "../../lib/api.ts";

const CODE_TOOLS = new Set(["Shell", "process", "run_python"]);

export type JobProposal = { id: string; goal: string; role: string; version: number | null; reason: string; workspace: string };

export function JobRequestCard({ job }: { job: JobProposal }) {
  const [status, setStatus] = useState<string | null>(null);
  const [jobId, setJobId] = useState("");
  const [scope, setScope] = useState<string[]>([]);
  const [limits, setLimits] = useState("");
  const [lineage, setLineage] = useState<{ parent: string; depth: number } | null>(null);
  const [escalation, setEscalation] = useState(false);
  const [busy, setBusy] = useState(false);
  const [protection, setProtection] = useState<"" | "jail" | "unprotected">("");
  const [error, setError] = useState("");

  const refresh = () => {
    api
      .jobRequests()
      .then(({ requests }) => {
        const found = requests.find((r) => r.id === job.id);
        setStatus(found ? found.status : "gone");
        setJobId(found?.job_id ?? "");
        setScope(found?.scope ?? []);
        setLineage(found?.parent_job_id ? { parent: found.parent_job_id, depth: found.depth ?? 1 } : null);
        setEscalation(Boolean(found?.escalation));
        const b = found?.budget ?? {};
        const parts = [`wall ${b.timeout_seconds ?? 900}s`];
        parts.push(b.max_steps ? `${b.max_steps} steps` : "no step cap");
        parts.push(b.max_tokens ? `${b.max_tokens} tokens` : "no token cap");
        parts.push(b.max_cost_usd ? `$${b.max_cost_usd} cost cap` : "no cost cap");
        setLimits(parts.join(" · "));
      })
      .catch(() => setStatus("unknown"));
  };
  useEffect(refresh, [job.id]);

  const decide = (action: "accept" | "dismiss") => {
    setBusy(true);
    setError("");
    const options =
      protection === "jail" ? { isolation: "bwrap-model-only" } : protection === "unprotected" ? { accept_unprotected_credentials: true } : {};
    (action === "accept" ? api.acceptJobRequest(job.id, options) : api.dismissJobRequest(job.id))
      .catch((e) => setError(e instanceof ApiError ? e.message : "The accept failed; the job did not start."))
      .finally(() => {
        setBusy(false);
        refresh();
      });
  };

  // A DELEGATED child whose role can run commands: its scope ceiling means nothing against its own code, so the
  // server refuses a plain accept. The owner must pick explicitly - nothing is preselected and Start stays off
  // until they do. Mirrors the server's guard; the server's answer stays authoritative (a refusal is shown).
  const needsProtectionChoice = Boolean(lineage) && scope.some((t) => CODE_TOOLS.has(t));

  return (
    <div className="approvalCard" role="alertdialog" aria-label="Job recommended">
      <header>
        <strong>
          <Briefcase size={15} style={{ color: "var(--accent)", verticalAlign: "-2px", marginRight: 6 }} />
          Start this job?
        </strong>
        <small>
          The session recommends a <code>{job.role}</code>
          {job.version ? ` v${job.version}` : ""} job. Nothing has started - accepting runs exactly what was proposed.
        </small>
      </header>
      <div className="approvalMeta">
        <span className="approvalChip outside" title="The goal, exactly as the session stored it">
          <code>{job.goal}</code>
        </span>
        {job.reason ? <span className="approvalChip" title="Why the session says a job, not this chat, should do it">{job.reason}</span> : null}
      </div>
      <div className="approvalMeta">
        <span className="approvalChip" title="The exact tools the job would run with, resolved when the session proposed it. The job gets no budget beyond the job defaults.">
          tools: {scope.length ? scope.join(", ") : "resolving…"}
        </span>
      </div>
      {lineage ? (
        <div className="approvalMeta">
          <span className="approvalChip" title="A running job asked for this help. Accepting starts a new worker job whose lineage is recorded. Result handoff back to the asking job is not built yet.">
            asked by job {lineage.parent.slice(0, 8)} (delegation depth {lineage.depth})
          </span>
        </div>
      ) : null}
      {escalation ? (
        <div className="approvalMeta">
          <span className="approvalChip outside" title="This role's tool scope reaches beyond the proposer's own scope. Accepting grants more than the asker had: that widening is your decision, exactly as with a chat proposal.">
            beyond the asker's own tools - review the scope
          </span>
        </div>
      ) : null}
      <div className="approvalMeta">
        <span className="approvalChip" title="Pinned when the session proposed the job; accepting applies exactly these, even if the defaults changed since.">
          limits: {limits || "job defaults"}
        </span>
      </div>
      {needsProtectionChoice && (status === "pending" || status === null) ? (
        <div className="approvalMeta" role="radiogroup" aria-label="How this job's code may run">
          <span className="approvalChip outside" title="This role can run commands (Shell, process or run_python). A delegated worker's scope ceiling means nothing against its own code: unprotected, it runs as you and can read your credential store and fetch the owner token.">
            this job's code can run commands - choose how it runs:
          </span>
          <label className="approvalChip" style={{ cursor: "pointer" }} title="Its code runs in the isolated jail: files and network cut off, only the filtered model bridge stays. The one way the scope ceiling holds against the job's own code.">
            <input type="radio" name={`protect-${job.id}`} checked={protection === "jail"} onChange={() => setProtection("jail")} /> isolated jail (recommended)
          </label>
          <label className="approvalChip outside" style={{ cursor: "pointer" }} title="Its code runs as you, unjailed: it can read your credential store and fetch the owner token from the local API. Only for work you would trust with your own keys.">
            <input type="radio" name={`protect-${job.id}`} checked={protection === "unprotected"} onChange={() => setProtection("unprotected")} /> run unprotected - it can read my credentials and owner token
          </label>
        </div>
      ) : null}
      {error ? (
        <div className="approvalMeta">
          <span className="approvalChip outside" role="alert" style={{ whiteSpace: "normal" }} title="The server refused the accept; nothing started. The request is still pending.">
            refused: {error}
          </span>
        </div>
      ) : null}
      {status === "pending" || status === null ? (
        <div className="approvalActions">
          <button className="pill ink" disabled={busy || (needsProtectionChoice && !protection)} title={needsProtectionChoice && !protection ? "Choose how the job's code runs first" : undefined} onClick={() => decide("accept")}>
            Start job
          </button>
          <button className="pill danger" disabled={busy} onClick={() => decide("dismiss")}>
            Dismiss
          </button>
        </div>
      ) : (
        <div className="approvalMeta">
          <span className="approvalChip">
            {status === "accepted" ? `Started as job ${jobId}` : status === "dismissed" ? "Dismissed - nothing ran" : "No longer pending"}
          </span>
        </div>
      )}
    </div>
  );
}
