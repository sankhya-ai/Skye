// A waiting approval, as Chotu's message approval card: what, why it asks, and three answers.

import { ShieldAlert } from "lucide-react";

import { relativePath } from "../../lib/tools.ts";
import type { ApprovalState } from "../../lib/transcript.ts";
import { Displays } from "./Steps.tsx";

export type ApproveFn = (requestId: string, response: "approve" | "approve_for_session" | "reject") => void;

function headline(action: string, description: string, workspace: string): string {
  if (action === "run command") return description.startsWith("Start in the background") ? "Start this in the background?" : "Run this command?";
  if (action === "edit file") return `${description.replace(/^Edit file/, "Edit").replace(/^Write file/, "Write").replace(workspace + "/", "")}?`;
  if (action === "edit file outside of working directory") return `${description} — outside the workspace?`;
  if (action === "read file outside of working directory") return `${description} — outside the workspace?`;
  if (action === "apply coding flow") return "Merge the reviewed patch into your checkout?";
  if (action === "roll back coding flow") return "Roll back the applied patch?";
  return `${description || action}?`;
}

/** Text with `code` spans, as the runtime writes its descriptions. */
function Inline({ text }: { text: string }) {
  return (
    <>
      {text.split(/(`[^`]+`)/).map((part, index) => (part.startsWith("`") && part.endsWith("`") && part.length > 2 ? <code key={index}>{part.slice(1, -1)}</code> : part))}
    </>
  );
}

export function ApprovalCard({ approval, workspace, onApprove }: { approval: ApprovalState; workspace: string; onApprove: ApproveFn }) {
  const { request } = approval;
  const trust = request.trust;
  const tier = trust?.tier === "verify" ? "runs project code" : trust?.tier === "needs_trust" ? "changes state" : null;
  const outsideFolder = trust?.key.startsWith("outside workspace: ") ? trust.key.slice("outside workspace: ".length) : "";
  const keyLabel = outsideFolder || (trust?.key.replace(/^run command:/, "") ?? request.action);
  return (
    <div className="approvalCard" role="alertdialog" aria-label="Approval needed">
      <header>
        <strong>
          <ShieldAlert size={15} style={{ color: "var(--accent)", verticalAlign: "-2px", marginRight: 6 }} />
          <Inline text={headline(request.action, relativePath(request.description, workspace), workspace)} />
        </strong>
        <small>
          {request.sender} wants to {request.action}.
        </small>
      </header>
      {trust ? (
        <div className="approvalMeta">
          {trust.outside ? (
            <span className="approvalChip outside" title="Outside the session's folder, root asks unless Auto-approve all is on.">
              outside the workspace: <code>{trust.outside}</code>
            </span>
          ) : null}
          {tier ? <span className="approvalChip">{tier}</span> : null}
          {trust.promotable ? (
            <span className={`approvalChip${trust.approvals > 0 ? " trusted" : ""}`} title="root stops asking after this many approvals in a row; one rejection resets it">
              {trust.approvals}/{trust.promote_after} approvals to trust <code>{keyLabel}</code>
            </span>
          ) : null}
        </div>
      ) : null}
      {request.display?.length ? (
        <div className="approvalBody">
          <Displays blocks={request.display} workspace={workspace} />
        </div>
      ) : null}
      <div className="approvalActions">
        <button className="pill ink" autoFocus onClick={() => onApprove(request.id, "approve")}>
          {request.action === "run command" ? "Run" : "Approve"} <span className="kbd">↵</span>
        </button>
        <button className="pill" onClick={() => onApprove(request.id, "approve_for_session")} title={outsideFolder ? `Allow reaching ${outsideFolder} for the rest of this session` : `Stop asking about “${keyLabel}” in this session`}>
          Allow for session
        </button>
        <button className="pill danger" onClick={() => onApprove(request.id, "reject")}>
          Reject <span className="kbd">esc</span>
        </button>
      </div>
    </div>
  );
}
