// The chamber as Claude Code tools: an MCP server on stdio, and the connection.
//
// While Claude Code runs, this keeps the agent in the chamber and writes what
// arrives to its inbox; the hooks (see hooks.mjs) show new messages to Claude
// mid-task, and wake it when it is idle. The tools let Claude see who is on the
// chamber, ask, read, and reply.

import { readFileSync, realpathSync } from "node:fs";
import { basename, join, resolve, sep } from "node:path";
import { createInterface } from "node:readline";

import { LineClient } from "./client.mjs";
import { CONVENTIONS, ENTRY, RECORDS, TeamMemory } from "./memory.mjs";
import { DecisionLedger } from "./decisions.mjs";
import { ContractRegistry, openObjections } from "./contracts.mjs";
import { describe, Inbox, loadConfig } from "./inbox.mjs";
import { LineError, MAX_FILE_BYTES } from "./protocol.mjs";
import { CoordinationManager } from "./coordination.mjs";

const PROTOCOL = "2025-06-18";

const today = () => new Date().toISOString().slice(0, 10);

// Keep subjects readable without a model call. Metadata stays in the body/registry;
// the visible subject leads with the change or consumer outcome, not a wire tag.
const subjectText = (value) => String(value ?? "").replace(/\s+/gu, " ").trim();
function subjectExcerpt(value, limit) {
  const text = subjectText(value);
  const chars = Array.from(text);
  if (chars.length <= limit) return text;
  const head = chars.slice(0, Math.max(0, limit - 1)).join("");
  const boundary = head.lastIndexOf(" ");
  // Prefer a whole word, unless the last space is so early that most of the text would go
  // (a short word before a long identifier); then cut the identifier instead.
  return `${(boundary >= head.length / 2 ? head.slice(0, boundary) : head).trimEnd()}…`;
}
function contractSubject(entry, { version, agent, stance, detail }) {
  const label = subjectExcerpt(entry.title || entry.name, 48);
  const actor = subjectExcerpt(agent, 48);
  const suffix = ` (${label} v${version})`;
  const prefix = stance === "object" ? `${actor} blocked: `
    : stance === "adapted" ? `${actor} updated for ` : "Changed: ";
  if (stance === "adapted") return `${prefix}${label} (v${version})`;
  return `${prefix}${subjectExcerpt(detail, 180 - Array.from(prefix + suffix).length)}${suffix}`;
}

const MEMORY_TOOLS = [
  {
    name: "memory",
    description: "Read the team's shared memory: MEMORY.md (the index) by default, or any file in it (e.g. contracts/auth-token.md). Follow [[links]] by reading the linked path + .md.",
    inputSchema: { type: "object", properties: { path: { type: "string", default: ENTRY } } },
  },
  {
    name: "memory_search",
    description: "Search the team's memory for lines containing all the given words. Use before answering about people, decisions, contracts, owners or conventions, and before writing (no near-duplicates).",
    inputSchema: { type: "object", properties: { query: { type: "string" } }, required: ["query"] },
  },
  {
    name: "memory_write",
    description: "Create or replace a whole file in the team's memory (e.g. decisions/billing.md). Shared with the whole team at once. One fact per bullet, with [source: talk/<id>#<msg>; by: <who>; added: <date>].",
    inputSchema: { type: "object", properties: { path: { type: "string" }, content: { type: "string" }, message: { type: "string", description: "What you're remembering (the commit message)." } }, required: ["path", "content", "message"] },
  },
  {
    name: "memory_edit",
    description: "Replace one exact snippet in a team memory file (must match once). Prefer this to add or change one bullet.",
    inputSchema: { type: "object", properties: { path: { type: "string" }, old: { type: "string" }, new: { type: "string" }, message: { type: "string" } }, required: ["path", "old", "new", "message"] },
  },
];


const DECISION_TOOLS = [
  {
    name: "decision",
    description:
      "Team decisions. action=propose: record a suggestion (title; rationale, affects, source help; supersedes to replace an approved decision). action=review: agree/object to an open proposal (decision_id, stance, reason) — advises the teammate who rules, changes nothing alone. action=check: approved decisions constraining paths/topic, before you contradict them (include_proposals for in-flight ones). action=list: the ledger (optional status filter). Agents never rule: a teammate approves or rejects with `chamber decide`. Approving a superseding proposal retires the old decision.",
    inputSchema: {
      type: "object",
      properties: {
        action: { type: "string", enum: ["propose", "review", "check", "list"] },
        title: { type: "string" },
        rationale: { type: "string" },
        affects: { type: "string" },
        source: { type: "string" },
        supersedes: { type: "string", description: "Id of the approved decision this replaces." },
        include_proposals: { type: "boolean", description: "For check: also list overlapping open proposals." },
        decision_id: { type: "string" },
        stance: { type: "string", enum: ["agree", "object"] },
        reason: { type: "string" },
        paths: { type: "array", items: { type: "string" } },
        topic: { type: "string" },
        status: { type: "string", enum: ["proposed", "approved", "rejected", "superseded"] },
      },
      required: ["action"],
    },
  },
];


const CONTRACT_TOOLS = [
  {
    name: "contract",
    description:
      "API/schema contracts the team depends on. action=register: declare a contract you produce (name, title, summary). action=subscribe: consume a contract — you get a notice on every new version, mid-task or on wake. action=publish: announce a new version (producers only; summary of what changed, decision id if one ruled); every subscriber is notified automatically and the announcement goes on the record. action=ack: record where you stand on a version — stance=seen (notice received), stance=adapted (you updated; a note of 20+ chars saying how is required: tests, commit; it is recorded as a claim and not verified), stance=object (it breaks you; reason required). Seen is not adapted. action=status: who has caught up to the current version and who is outstanding. action=get/list: the record.",
    inputSchema: {
      type: "object",
      properties: {
        action: { type: "string", enum: ["register", "subscribe", "unsubscribe", "publish", "ack", "status", "get", "list"] },
        name: { type: "string", description: "Contract name, e.g. auth-token" },
        title: { type: "string" },
        summary: { type: "string", description: "For register/publish: what the contract is, or what changed in this version." },
        decision: { type: "string", description: "For publish: id of the decision that ruled on this change, if any." },
        version: { type: "integer", description: "For ack: the version you are acknowledging (default: current)." },
        stance: { type: "string", enum: ["seen", "adapted", "object"] },
        note: { type: "string", description: "For ack: evidence for adapted (required, 20+ chars: what you changed and ran, e.g. tests, commit; recorded as your claim, not verified), reason for object." },
      },
      required: ["action"],
    },
  },
];

const TOOLS = [
  {
    name: "agents",
    description: "List the agents in the chamber right now (yours and your teammates'): address, kind, and whether they answer questions.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "ask",
    description:
      "Ask another agent in the chamber and wait for its answer. `to` is an address like `nitin/claude-code.api`, `@nitin` for that person's best available agent, or `#channel` for a channel (@mention people in the message to reach their agents). Attach files (paths relative to the project) as context. If no answer comes in time, it arrives later on its own.",
    inputSchema: {
      type: "object",
      properties: {
        to: { type: "string" },
        message: { type: "string" },
        files: { type: "array", items: { type: "string" } },
        branch: { type: "string", description: "Git branch for context (e.g. feat/auth-e2ee)" },
        wait_seconds: { type: "integer", minimum: 0, maximum: 600, default: 180 },
      },
      required: ["to", "message"],
    },
  },
  {
    name: "inbox",
    description: "Show messages other agents sent you that you have not seen yet (they also appear on their own as you work). `recent: true` shows the last 20 again.",
    inputSchema: { type: "object", properties: { recent: { type: "boolean", default: false } } },
  },
  {
    name: "reply",
    description: "Answer a message from another agent, by its id. Attach files (paths relative to the project) if useful. Never send credentials, keys or .env contents.",
    inputSchema: {
      type: "object",
      properties: { message_id: { type: "string" }, message: { type: "string" }, files: { type: "array", items: { type: "string" } } },
      required: ["message_id", "message"],
    },
  },
  {
    name: "search",
    description: "Search across all decrypted talks, patches, contracts, team memories, and past incidents.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string" },
        channel: { type: "string", description: "Filter by channel (e.g. #backend)" },
        type: { type: "string", description: "Filter by record type (e.g. patch, incident, decision)" },
        limit: { type: "integer", minimum: 1, maximum: 50, default: 10 },
      },
      required: ["query"],
    },
  },
  {
    name: "incident_memory",
    description: "Search specifically for past bug fixes, error messages, and resolved incidents (e.g. 'Have we seen this error before?').",
    inputSchema: {
      type: "object",
      properties: {
        error: { type: "string", description: "The error message or failure snippet to search for" },
        limit: { type: "integer", minimum: 1, maximum: 20, default: 5 },
      },
      required: ["error"],
    },
  },
  {
    name: "send_patch",
    description: "Send a git diff/patch as a typed message to an agent or channel.",
    inputSchema: {
      type: "object",
      properties: {
        to: { type: "string" },
        patch: { type: "string", description: "Unified diff (git diff output)" },
        description: { type: "string", description: "Summary of changes" },
        branch: { type: "string", description: "Branch or topic name" },
      },
      required: ["to", "patch"],
    },
  },
  {
    name: "review_patch",
    description: "Check if a patch in a received message applies cleanly to this repo (using git apply --check) and view file stats.",
    inputSchema: {
      type: "object",
      properties: {
        message_id: { type: "string", description: "The message ID containing the patch" },
      },
      required: ["message_id"],
    },
  },
];

const COORDINATION_TOOLS = [
  {
    name: "coordinate_ask",
    description: "Request information, contract details, or code investigation from another agent. Unlike basic chat, this creates a tracked, durable coordination ticket with branch context, schema contracts, and verifiable completion.",
    inputSchema: {
      type: "object",
      properties: {
        to: { type: "string", description: "Target agent (e.g. 'ashish/claude-code.api', '@ashish', or '#backend')" },
        title: { type: "string", description: "Clear title of the request (e.g. 'Payment API contract verification')" },
        description: { type: "string", description: "Detailed description of what is requested" },
        contract: { type: "string", description: "Name/id of the API contract or schema if applicable" },
        branch: { type: "string", description: "Git branch for context" },
        files: { type: "array", items: { type: "string" }, description: "Relevant project files to attach" },
        wait_seconds: { type: "integer", minimum: 0, maximum: 600, default: 180 },
      },
      required: ["to", "title"],
    },
  },
  {
    name: "coordinate_delegate",
    description: "Hand a bounded piece of work to another agent with acceptance criteria and branch context.",
    inputSchema: {
      type: "object",
      properties: {
        to: { type: "string", description: "Target agent" },
        title: { type: "string", description: "Title of delegated work" },
        description: { type: "string", description: "Clear task instructions and acceptance criteria" },
        branch: { type: "string", description: "Target branch" },
        files: { type: "array", items: { type: "string" } },
      },
      required: ["to", "title", "description"],
    },
  },
  {
    name: "coordinate_review",
    description: "Request validation of a patch, contract, or implementation against specification from another agent.",
    inputSchema: {
      type: "object",
      properties: {
        to: { type: "string", description: "Reviewing agent" },
        title: { type: "string", description: "Review title" },
        patch: { type: "string", description: "Unified diff / git patch to review" },
        contract: { type: "string", description: "Contract/spec being reviewed against" },
        branch: { type: "string", description: "Branch name" },
        description: { type: "string", description: "Context or specific questions for the reviewer" },
      },
      required: ["to", "title", "patch"],
    },
  },
  {
    name: "coordinate_propose",
    description: "Propose an architectural decision, interface contract, or change for consideration and feedback from another agent or team member.",
    inputSchema: {
      type: "object",
      properties: {
        to: { type: "string", description: "Target agent or teammate (@owner or owner/agent)" },
        title: { type: "string", description: "Proposal title" },
        proposal: { type: "string", description: "Detailed proposal description and rationale" },
        contract: { type: "string", description: "Interface schema or contract identifier" },
        branch: { type: "string", description: "Related branch" },
        files: { type: "array", items: { type: "string" }, description: "Relevant file paths in the workspace" },
      },
      required: ["to", "title", "proposal"],
    },
  },
  {
    name: "coordinate_resolve",
    description: "Close an active coordination task with verified outcome and supporting evidence (diffs, test output, schemas). Automatically commits source-backed knowledge to team memory.",
    inputSchema: {
      type: "object",
      properties: {
        coord_id: { type: "string", description: "Coordination task ID" },
        resolution: { type: "string", description: "Verified summary of the outcome/answer" },
        evidence: {
          type: "object",
          description: "Structured evidence supporting the resolution (commit, schema, test_output, files)",
          properties: {
            commit: { type: "string" },
            schema: { type: "string" },
            test_output: { type: "string" },
            files: { type: "array", items: { type: "string" } },
          },
        },
        contract: { type: "string", description: "Confirmed contract specification or schema" },
        commit_to_memory: { type: "boolean", default: true, description: "Whether to record this confirmed finding in team memory" },
      },
      required: ["coord_id", "resolution"],
    },
  },
  {
    name: "coordinate_handoff",
    description: "Transfer work, context, and outstanding obligations to another agent (e.g. when switching services or hitting context limits).",
    inputSchema: {
      type: "object",
      properties: {
        coord_id: { type: "string", description: "Coordination task ID" },
        to: { type: "string", description: "Receiving agent" },
        summary: { type: "string", description: "What was accomplished and decisions made" },
        next_step: { type: "string", description: "One concrete next action for the incoming agent" },
        files: { type: "array", items: { type: "string" } },
      },
      required: ["coord_id", "to", "summary", "next_step"],
    },
  },
  {
    name: "coordinate_status",
    description: "Query active or resolved coordination tasks in the chamber.",
    inputSchema: {
      type: "object",
      properties: {
        coord_id: { type: "string", description: "Specific task ID to inspect" },
        status: { type: "string", enum: ["requested", "claimed", "in_progress", "resolved", "rejected"] },
      },
    },
  },
  {
    name: "context_package",
    description: "Compile a minimal, relevant, source-backed context package for a specific repository, task, symbols, or contracts before starting work. Prevents ingesting massive irrelevant chat logs.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "Task or topic summary" },
        symbols: { type: "array", items: { type: "string" }, description: "Specific symbol, function, or endpoint names" },
        contracts: { type: "array", items: { type: "string" }, description: "Contract or schema names" },
      },
    },
  },
];

function readFiles(paths, cwd) {
  let root = resolve(cwd);
  try {
    root = realpathSync(root);
  } catch {}
  return (paths || []).map((path) => {
    const full = resolve(root, path);
    if (!full.startsWith(root + sep) && full !== root) {
      throw new LineError(`file must be inside the project folder: ${path}`);
    }
    let real;
    try {
      real = realpathSync(full);
    } catch {
      throw new LineError(`not a file: ${path}`);
    }
    if (!real.startsWith(root + sep) && real !== root) {
      throw new LineError(`file traverses outside project folder: ${path}`);
    }
    let data;
    try {
      data = readFileSync(real);
    } catch {
      throw new LineError(`not a file: ${path}`);
    }
    if (data.length > MAX_FILE_BYTES) throw new LineError(`${path} is over ${MAX_FILE_BYTES / 2 ** 20} MB`);
    return { name: basename(real), data: new Uint8Array(data) };
  });
}

export class LineTools {
  constructor(client, inbox, cwd) {
    this.client = client;
    this.inbox = inbox;
    this.cwd = cwd;
    this.memory = new TeamMemory(client.invite, client.agent);
    this.decisions = new DecisionLedger(this.memory, client.agent);
    this.contracts = new ContractRegistry(this.memory, client.agent);
    const coordDir = inbox?.dir ? join(inbox.dir, "..", "..", "coordination") : join(cwd, ".coordination");
    this.coordination = new CoordinationManager(coordDir);
  }

  async #saved(result, what) {
    if (result.ok) return `Saved to team memory: ${what}.`;
    if (result.error) throw new LineError(result.error);
    return [
      "Rejected: a teammate (or a dream) changed this since you read it, so nothing was saved.",
      "Current version below. Write one version that keeps what's true from both, then retry.",
      ...result.current.map((c) => `--- ${c.path} (version ${c.version})\n${c.text ?? "(deleted)"}`),
    ].join("\n");
  }

  async call(name, args) {
    if (name === "memory") {
      const file = await this.memory.read(String(args.path || ENTRY));
      if (!file?.text) return args.path && args.path !== ENTRY ? `No such file: ${args.path}.` : `The team's memory is empty so far.\n\n${CONVENTIONS(today())}`;
      return `${file.path} (version ${file.version}):\n\n${file.text}`;
    }
    if (name === "memory_search") {
      const hits = await this.memory.search(String(args.query || ""));
      return hits.length ? hits.map((h) => `${h.path}:${h.line}: ${h.text}`).join("\n") : "Nothing in team memory matches.";
    }
    if ((name === "memory_write" || name === "memory_edit") && RECORDS[String(args.path)]) throw new LineError(`${args.path} is kept by the ${RECORDS[String(args.path)]} tool; change it with that tool, not by editing the file`);
    if (name === "memory_write") return this.#saved(await this.memory.write(String(args.path), String(args.content), String(args.message || "remember")), args.path);
    if (name === "memory_edit") return this.#saved(await this.memory.edit(String(args.path), String(args.old), String(args.new), String(args.message || "update")), args.path);
    if (name === "decision") {
      const action = String(args.action || "");
      if (action === "propose") {
        const { entry, conflicts } = await this.decisions.propose({ title: args.title, rationale: args.rationale, affects: args.affects, source: args.source, supersedes: args.supersedes });
        const lines = [`Proposed ${entry.id} ("${entry.title}") — recorded as a proposal. Other agents see it at their next session start and can review it (decision action=review); it goes on the record when a teammate approves it (chamber decide ${entry.id} approve); until then it is a suggestion.`];
        if (entry.supersedes) lines.push(`It supersedes ${entry.supersedes}, which retires when a teammate approves this.`);
        if (conflicts.length) {
          lines.push("", "It overlaps these approved decisions, flagged for the ruling teammate:");
          for (const c of conflicts) lines.push(`- [${c.id}] ${c.title}${c.affects ? ` (affects: ${c.affects})` : ""}`);
        }
        const touched = [...String(args.affects || "").matchAll(/contracts\/([a-z0-9][a-z0-9-]*)/g)].map((m) => m[1]);
        for (const contractName of touched) {
          try {
            const { consumers } = await this.contracts.status(contractName);
            if (consumers.length) lines.push("", `Note: ${contractName} has ${consumers.length} subscribed consumer${consumers.length === 1 ? "" : "s"} (${consumers.map((c) => c.agent).join(", ")}). When this is ruled on, announce the change with contract action=publish name=${contractName} so they catch up — and watch action=status for their acks.`);
          } catch {}
        }
        return lines.join("\n");
      }
      if (action === "review") {
        const r = await this.decisions.review(String(args.decision_id), { stance: String(args.stance), reason: args.reason ? String(args.reason) : "" });
        return `Review recorded: ${r.by} ${r.stance === "agree" ? "agrees with" : "objects to"} ${r.id}. It advises the teammate who rules (chamber decide); the proposal changes nothing on its own.`;
      }
      if (action === "check") {
        const hits = await this.decisions.check({ paths: Array.isArray(args.paths) ? args.paths.map(String) : [], topic: String(args.topic || ""), includeProposals: args.include_proposals === true });
        if (!hits.length) return "No approved team decisions constrain this. Proposals (awaiting a teammate's ruling) are suggestions only.";
        return ["Team decisions that apply here (approved rules; proposals are in-flight suggestions — full record: decision action=list):", ...hits.map((e) => `- [${e.id}] (${e.status}) ${e.title}${e.affects ? ` (affects: ${e.affects})` : ""}${e.rationale ? ` — ${e.rationale}` : ""}`)].join("\n");
      }
      if (action === "list") {
        const entries = await this.decisions.list({ status: args.status ? String(args.status) : undefined });
        if (!entries.length) return "The decision ledger is empty. Agents propose with decision action=propose; teammates rule with `chamber decide`.";
        const show = (e) => {
          const lines = [`[${e.id}] (${e.status}) ${e.title}`];
          if (e.rationale) lines.push(`  rationale: ${e.rationale}`);
          if (e.affects) lines.push(`  affects: ${e.affects}`);
          lines.push(`  proposed-by: ${e["proposed-by"] ?? "?"} · added: ${e.added ?? "?"}`);
          if (e["decided-by"]) lines.push(`  decided-by: ${e["decided-by"]} · ${e["decided-on"] ?? ""}${e.reason ? ` · reason: ${e.reason}` : ""}`);
          if (e.conflicts && e.conflicts !== "none") lines.push(`  conflicts: ${e.conflicts}`);
          if (e.supersedes) lines.push(`  supersedes: ${e.supersedes}`);
          if (e["superseded-by"]) lines.push(`  superseded-by: ${e["superseded-by"]}`);
          for (const r of e.reviews ?? []) lines.push(`  review: ${r}`);
          return lines.join("\n");
        };
        return entries.map(show).join("\n\n");
      }
      throw new LineError(`decision: unknown action "${action}" (propose, review, check or list)`);
    }
    if (name === "contract") {
      const action = String(args.action || "");
      const cname = String(args.name || "");
      if (action === "register") {
        const { name: registered } = await this.contracts.register({ name: cname, title: args.title, summary: args.summary });
        return `Registered ${registered} (v1) with you as producer. Consumers subscribe with contract action=subscribe name=${registered}; publish a change with action=publish and every subscriber is notified automatically.`;
      }
      if (action === "subscribe") {
        const { entry, added } = await this.contracts.subscribe(cname);
        return added
          ? `Subscribed to ${entry.name} (now v${entry.version}): ${entry.summary || entry.title}. You will be notified on each new version — mid-task if you are working, on wake if idle. Acknowledge what you receive with contract action=ack.`
          : `You already consume ${entry.name} (now v${entry.version}): ${entry.summary || entry.title}.`;
      }
      if (action === "unsubscribe") {
        const { removed } = await this.contracts.unsubscribe(cname);
        return removed ? `Unsubscribed from ${cname}; no more change notices. Past acks stay on the record.` : `You were not subscribed to ${cname}.`;
      }
      if (action === "publish") {
        const { entry, version, notify, objections } = await this.contracts.publish({ name: cname, summary: args.summary, decision: args.decision });
        const objectionLines = objections.map((o) => `${o.agent} (v${o.version}): ${o.note}`);
        const title = contractSubject(entry, { version, detail: entry.summary });
        const text = [
          `${this.client.agent} published ${entry.name} v${version}: ${args.summary}${entry.decision ? ` (decision ${entry.decision})` : ""}`,
          "",
          `You consume this contract. Check the change against your code, adapt, then record where you stand: contract action=ack name=${entry.name} version=${version} stance=adapted with your evidence (tests run, commit) — or stance=object with a reason if it breaks you. stance=seen acknowledges receipt only; it is not adaptation.`,
          `Full record: contract action=get name=${entry.name}.`,
          ...(objectionLines.length ? ["", `Open objection${objectionLines.length === 1 ? "" : "s"} on this contract: ${objectionLines.join("; ")}`] : []),
        ].join("\n");
        const receipts = [];
        for (const consumer of notify) {
          try {
            const receipt = await this.client.send(consumer, text, { title });
            this.inbox?.sent?.(receipt, { to: consumer, text, title });
            receipts.push(`${consumer}${receipt.held ? " (held until online)" : ""}`);
          } catch (error) {
            receipts.push(`${consumer} (notice failed: ${error.message})`);
          }
        }
        return [
          `Published ${entry.name} v${version}. The announcement is on the record (contract action=get name=${entry.name}).`,
          ...(objectionLines.length ? [`WARNING: ${objectionLines.length} open objection${objectionLines.length === 1 ? "" : "s"} not resolved by this publish: ${objectionLines.join("; ")}. The publish went through (the registry warns, it does not block). Check the objector's concern against this version.`] : []),
          notify.length ? `Notified ${notify.length} consumer${notify.length === 1 ? "" : "s"}: ${receipts.join("; ")}.` : `WARNING: no consumers are subscribed, so nobody was notified. The registry only knows agents that subscribed; it cannot see who else depends on ${entry.name}. Tell the teammates and agents who use it directly, and have them run contract action=subscribe name=${entry.name}.`,
          `Watch who catches up: contract action=status name=${entry.name} — a seen ack is receipt, not adaptation.`,
        ].join("\n");
      }
      if (action === "ack") {
        const stance = String(args.stance || "");
        const { entry, ack } = await this.contracts.ack(cname, { version: args.version, stance, note: args.note });
        // The producer learns of outcomes that change their picture: an objection to resolve,
        // or evidence the consumer adapted. A plain "seen" stays quiet — it is only receipt.
        if (stance === "adapted" || stance === "object") {
          const title = contractSubject(entry, { version: ack.version, agent: this.client.agent, stance, detail: ack.note });
          const text = stance === "object"
            ? `${this.client.agent} objects to ${entry.name} v${ack.version}: ${args.note}\n\nYou produce this contract. Resolve it with them (or propose a transition); the objection is on the record: contract action=status name=${entry.name}.`
            : `${this.client.agent} adapted to ${entry.name} v${ack.version}${args.note ? `: ${args.note}` : ""}.\n\nEvidence of adaptation, not just receipt. Who else is outstanding: contract action=status name=${entry.name}.`;
          try {
            await this.client.send(entry.producer, text, { title });
          } catch {}
        }
        return `Recorded: ${this.client.agent} · ${entry.name} v${ack.version} · ${stance}. ${stance === "seen" ? "Receipt only — when you have adapted, ack again with stance=adapted and your evidence." : stance === "adapted" ? "The producer has been told, with your evidence." : "The producer has been told; the objection is on the record for the team."}`;
      }
      if (action === "status") {
        const { entry, consumers } = await this.contracts.status(cname);
        const lines = [`${entry.name} v${entry.version} — ${entry.title} (producer ${entry.producer}, updated ${entry.updated}${entry.decision ? `, decision ${entry.decision}` : ""})`];
        if (entry.summary) lines.push(`  current: ${entry.summary}`);
        if (!consumers.length) lines.push("  no consumers subscribed (the registry cannot see undeclared dependents; ask who uses this and have them subscribe)");
        for (const o of openObjections(entry)) lines.push(`  OPEN OBJECTION: ${o.agent} (v${o.version}): ${o.note}`);
        for (const c of consumers) {
          const last = c.state === "outstanding" && c.lastVersion ? ` (last acked v${c.lastVersion})` : "";
          lines.push(`  ${c.agent}: ${c.state.toUpperCase()}${c.ack?.note ? ` — ${c.ack.note}` : ""}${c.ack ? ` (${c.ack.date})` : ""}${last}`);
        }
        return lines.join("\n");
      }
      if (action === "get" || action === "list") {
        const entries = action === "get" ? [await this.contracts.get(cname)] : await this.contracts.list();
        if (!entries.length) return "No contracts registered. Producers register with contract action=register; consumers subscribe to get change notices automatically.";
        return entries.map((e) => {
          const lines = [`[${e.name}] v${e.version} — ${e.title}`];
          if (e.summary) lines.push(`  summary: ${e.summary}`);
          lines.push(`  producer: ${e.producer} · updated: ${e.updated}${e.decision ? ` · decision: ${e.decision}` : ""}`);
          for (const c of e.consumers) lines.push(`  consumer: ${c.agent} (since ${c.since})`);
          for (const n of e.notices.slice(-3)) lines.push(`  notice: v${n.version} · ${n.date} · to ${n.to.join(", ") || "nobody"}`);
          for (const a of e.acks) lines.push(`  ack: ${a.agent} · v${a.version} · ${a.stance} · ${a.date}${a.note ? ` · ${a.note}` : ""}`);
          return lines.join("\n");
        }).join("\n\n");
      }
      throw new LineError(`contract: unknown action "${action}" (register, subscribe, unsubscribe, publish, ack, status, get or list)`);
    }
    if (name === "agents") {
      const head = `You are ${this.client.agent}.${this.client.connected ? "" : ` Not connected: ${this.client.problem || "connecting"}.`}`;
      const rows = this.client.roster.map((a) => `- ${a.agent} (${a.kind}${a.answers ? ", answers" : ""})${a.label ? ` — ${a.label}` : ""}`);
      const owner = this.client.agent.split("/")[0];
      const channels = this.client.channels.filter((c) => c.members.includes(owner)).map((c) => `${c.name} (${c.members.join(", ")})`);
      return [head, ...rows, "", `Your channels: ${channels.join("; ") || "none"}. Send to a channel with to: "#name"; @mention people there to reach their agents.`].join("\n");
    }
    if (name === "ask") {
      const wait = Number(args.wait_seconds ?? 180);
      const text = String(args.message);
      const files = readFiles(args.files, this.cwd);
      const title = text.split("\n")[0].slice(0, 80);
      const branch = args.branch ? String(args.branch) : null;
      const onSent = (receipt) => this.inbox.sent(receipt, { to: args.to, text, title, files, branch });
      const { receipt, reply } = await this.client.ask(args.to, text, { files, title, timeout: wait * 1000, onSent, branch });
      const sent = `Sent to ${receipt.to || args.to}${receipt.held ? " (held until they come online)" : ""}`;
      if (!reply) return `${sent}. No answer within ${wait}s; it will reach you on its own when it comes.`;
      const record = this.inbox.add(reply);
      this.inbox.claim(); // shown here; the hooks need not show it again
      return `${sent}. Answer:\n${describe([record], { intro: false })}`;
    }
    if (name === "inbox") {
      const records = args.recent ? this.inbox.all().slice(-20) : this.inbox.claim();
      return records.length ? describe(records) : "No new messages.";
    }
    if (name === "reply") {
      const original = this.inbox.find(String(args.message_id));
      if (!original) throw new LineError("unknown message id");
      const text = String(args.message);
      const files = readFiles(args.files, this.cwd);
      const thread = original.thread || original.id;
      const to = String(original.to || "").startsWith("#") ? original.to : original.from; // a channel conversation stays in its channel
      const receipt = await this.client.send(to, text, { files, replyTo: original.id, thread, title: original.title });
      this.inbox.sent(receipt, { to, text, replyTo: original.id, title: original.title, files });
      return `Replied to ${to}${receipt.held ? " (held until they come online)" : ""}.`;
    }
    if (name === "search") {
      const { ChamberSearch } = await import("./search.mjs");
      const owner = this.client.agent.split("/")[0];
      const search = new ChamberSearch(owner);
      const results = await search.search({
        query: String(args.query || ""),
        channel: args.channel ? String(args.channel) : "",
        type: args.type ? String(args.type) : "",
        limit: Number(args.limit || 10),
      });
      if (!results.length) return "No matching talks or records found.";
      return results.map((r) => `[${r.citation}] (${r.type}${r.channel ? ` in ${r.channel}` : ""}): ${r.title ? r.title + " — " : ""}${r.text.slice(0, 300)}`).join("\n\n");
    }
    if (name === "incident_memory") {
      const { ChamberSearch } = await import("./search.mjs");
      const owner = this.client.agent.split("/")[0];
      const search = new ChamberSearch(owner);
      const results = await search.incidentMemory(String(args.error || ""), Number(args.limit || 5));
      if (!results.length) return "No past incidents or root cause fixes match this error.";
      return [
        "Found matching past incidents / bug fixes:",
        ...results.map((r) => `--- [${r.citation}] ${r.title || "Incident"}\n${r.text}`),
      ].join("\n\n");
    }
    if (name === "send_patch") {
      const to = String(args.to);
      const patch = String(args.patch);
      const description = String(args.description || "Code patch");
      const branch = args.branch ? String(args.branch) : null;
      const title = `Patch: ${description.split("\n")[0].slice(0, 70)}`;
      const receipt = await this.client.send(to, description, {
        title,
        type: "patch",
        patch,
        branch,
      });
      this.inbox.sent(receipt, { to, text: description, title, type: "patch", patch, branch });
      return `Patch sent to ${to}${receipt.held ? " (held until they come online)" : ""}. ID: ${receipt.id}.`;
    }
    if (name === "review_patch") {
      const msg = this.inbox.find(String(args.message_id));
      if (!msg) throw new LineError("unknown message id");
      if (!msg.patch) return "Message does not contain a patch.";
      const { spawnSync } = await import("node:child_process");
      const check = spawnSync("git", ["apply", "--check", "-"], { cwd: this.cwd, input: msg.patch, encoding: "utf8" });
      const stat = spawnSync("git", ["apply", "--stat", "-"], { cwd: this.cwd, input: msg.patch, encoding: "utf8" });
      if (check.status === 0) {
        return `✔ Patch applies cleanly to repository at ${this.cwd}.\n\nStats:\n${stat.stdout || "(no stat available)"}`;
      } else {
        return `✖ Patch failed to apply cleanly:\n${check.stderr || check.stdout || "git apply --check exited with error"}`;
      }
    }
    if (name === "coordinate_ask") {
      const wait = Number(args.wait_seconds ?? 180);
      const title = String(args.title);
      const desc = String(args.description || title);
      const files = readFiles(args.files, this.cwd);
      const branch = args.branch ? String(args.branch) : null;
      const contract = args.contract ? String(args.contract) : null;

      const task = this.coordination.request({
        op: "ask",
        from: this.client.agent,
        to: args.to,
        title,
        description: desc,
        branch,
        contract,
        files: (args.files || []).map(basename),
      });

      const messageText = `[ACP:ASK:${task.id}] ${title}\n\n${desc}${contract ? `\nContract: ${contract}` : ""}`;
      const onSent = (receipt) => this.inbox.sent(receipt, { to: args.to, text: messageText, title, files, branch });

      const { receipt, reply } = await this.client.ask(args.to, messageText, { files, title, timeout: wait * 1000, onSent, branch });
      const sent = `Coordination request #${task.id.slice(0, 8)} sent to ${receipt.to || args.to}${receipt.held ? " (held until they come online)" : ""}`;
      if (this.client?.invite) this.coordination.pushToRelay(this.client.invite, task, "request").catch(() => {});
      if (!reply) return `${sent}. Status: requested. Answer will arrive asynchronously.`;

      const record = this.inbox.add(reply);
      this.inbox.claim();
      return `${sent}. Answer received:\n${describe([record], { intro: false })}`;
    }
    if (name === "coordinate_delegate") {
      const title = String(args.title);
      const desc = String(args.description);
      const files = readFiles(args.files, this.cwd);
      const branch = args.branch ? String(args.branch) : null;

      const task = this.coordination.request({
        op: "delegate",
        from: this.client.agent,
        to: args.to,
        title,
        description: desc,
        branch,
        files: (args.files || []).map(basename),
      });

      const messageText = `[ACP:DELEGATE:${task.id}] ${title}\n\n${desc}`;
      const receipt = await this.client.send(args.to, messageText, { files, title, branch });
      this.inbox.sent(receipt, { to: args.to, text: messageText, title, files, branch });
      if (this.client?.invite) this.coordination.pushToRelay(this.client.invite, task, "request").catch(() => {});
      return `Delegated task #${task.id.slice(0, 8)} to ${args.to}${receipt.held ? " (held until online)" : ""}.`;
    }
    if (name === "coordinate_review") {
      const title = String(args.title);
      const patch = String(args.patch);
      const desc = String(args.description || "Review requested");
      const branch = args.branch ? String(args.branch) : null;
      const contract = args.contract ? String(args.contract) : null;

      const task = this.coordination.request({
        op: "review",
        from: this.client.agent,
        to: args.to,
        title,
        description: desc,
        branch,
        contract,
        files: [],
      });

      const messageText = `[ACP:REVIEW:${task.id}] ${title}\n\n${desc}${contract ? `\nAgainst contract: ${contract}` : ""}`;
      const receipt = await this.client.send(args.to, messageText, {
        title,
        type: "patch",
        patch,
        branch,
      });
      this.inbox.sent(receipt, { to: args.to, text: messageText, title, type: "patch", patch, branch });
      if (this.client?.invite) this.coordination.pushToRelay(this.client.invite, task, "request").catch(() => {});
      return `Review request #${task.id.slice(0, 8)} sent to ${args.to}.`;
    }
    if (name === "coordinate_propose") {
      const title = String(args.title);
      const proposal = String(args.proposal);
      const branch = args.branch ? String(args.branch) : null;
      const contract = args.contract ? String(args.contract) : null;
      const files = (args.files || []).map(basename);

      const task = this.coordination.request({
        op: "propose",
        from: this.client.agent,
        to: args.to,
        title,
        description: proposal,
        branch,
        contract,
        files,
      });

      const messageText = `[ACP:PROPOSE:${task.id}] ${title}\n\n${proposal}${contract ? `\nContract: ${contract}` : ""}`;
      const receipt = await this.client.send(args.to, messageText, {
        title: `[PROPOSE] ${title}`,
        branch,
      });
      this.inbox.sent(receipt, { to: args.to, text: messageText, title: `[PROPOSE] ${title}`, branch });
      if (this.client?.invite) this.coordination.pushToRelay(this.client.invite, task, "request").catch(() => {});
      return `Proposal #${task.id.slice(0, 8)} ("${title}") sent to ${args.to}${receipt.held ? " (held until online)" : ""}.`;
    }
    if (name === "coordinate_resolve") {
      const coordId = String(args.coord_id);
      const resolution = String(args.resolution);
      const evidence = args.evidence || null;
      const contract = args.contract ? String(args.contract) : null;
      const commitToMemory = args.commit_to_memory !== false;

      const { task, alreadyResolved, memoryEntry } = this.coordination.resolve(coordId, {
        by: this.client.agent,
        resolution,
        evidence,
        contract,
      });

      let memoryMsg = "";
      if (commitToMemory && memoryEntry) {
        try {
          const entryFile = await this.memory.read(ENTRY);
          const oldText = entryFile?.text || CONVENTIONS(today());
          const newText = `${oldText.trim()}\n${memoryEntry}\n`;
          await this.memory.write(ENTRY, newText, `acp: resolve #${task.id.slice(0, 8)} (${task.title})`);
          memoryMsg = `\n✔ Recorded source-backed fact in team memory (MEMORY.md).`;
        } catch (e) {
          memoryMsg = `\n(Could not update team memory: ${e.message})`;
        }
      }

      if (this.client?.invite) this.coordination.pushToRelay(this.client.invite, task, "resolve", { by: this.client.agent, resolution, evidence, contract }).catch(() => {});

      const notifyText = `[ACP:RESOLVED:${task.id}] ${task.title}\n\nResolution: ${resolution}`;
      try {
        await this.client.send(task.from, notifyText, { title: `Resolved: ${task.title}` });
      } catch {}

      return `Task #${task.id.slice(0, 8)} resolved.${alreadyResolved ? " (was already resolved)" : ""}${memoryMsg}`;
    }
    if (name === "coordinate_handoff") {
      const coordId = String(args.coord_id);
      const toAgent = String(args.to);
      const summary = String(args.summary);
      const nextStep = String(args.next_step);
      const files = (args.files || []).map(basename);

      const task = this.coordination.handoff(coordId, {
        fromAgent: this.client.agent,
        toAgent,
        summary,
        nextStep,
        files,
      });

      const messageText = `[ACP:HANDOFF:${task.id}] ${task.title}\n\nSummary:\n${summary}\n\nNext Step:\n${nextStep}`;
      const receipt = await this.client.send(toAgent, messageText, { title: `Handoff: ${task.title}` });
      this.inbox.sent(receipt, { to: toAgent, text: messageText, title: `Handoff: ${task.title}` });
      if (this.client?.invite) this.coordination.pushToRelay(this.client.invite, task, "handoff", { fromAgent: this.client.agent, toAgent, summary, nextStep, files }).catch(() => {});
      return `Handoff #${task.id.slice(0, 8)} completed. Work transferred to ${toAgent}.`;
    }
    if (name === "coordinate_status") {
      if (this.client?.invite) await this.coordination.syncWithRelay(this.client.invite).catch(() => {});
      if (args.coord_id) {
        const t = this.coordination.get(String(args.coord_id));
        if (!t) return `No coordination task found with ID "${args.coord_id}".`;
        return JSON.stringify(t.toJSON(), null, 2);
      }
      const tasks = this.coordination.list({ status: args.status ? String(args.status) : undefined });
      if (!tasks.length) return "No matching coordination tasks.";
      return tasks.map((t) => `[#${t.id.slice(0, 8)}] (${t.op.toUpperCase()} - ${t.status}) ${t.title}\nFrom: ${t.from} -> To: ${t.to}${t.contract ? ` | Contract: ${t.contract}` : ""}\nUpdated: ${new Date(t.updatedAt).toISOString()}`).join("\n\n");
    }
    if (name === "context_package") {
      let memoryText = "";
      try {
        const entryFile = await this.memory.read(ENTRY);
        memoryText = entryFile?.text || "";
      } catch {}
      const pkg = this.coordination.compileContextPackage({
        memoryMarkdown: memoryText,
        query: String(args.query || ""),
        symbols: Array.isArray(args.symbols) ? args.symbols : [],
        contracts: Array.isArray(args.contracts) ? args.contracts : [],
      });
      // The contract registry is its own file, so memory lines never cover it: add the
      // registry's contracts the caller named or whose name/title/summary match its tokens.
      let contractFacts = [];
      try {
        const named = (Array.isArray(args.contracts) ? args.contracts : []).map((c) => String(c).toLowerCase());
        const tokens = [...named, ...(Array.isArray(args.symbols) ? args.symbols : []).map((x) => String(x).toLowerCase()), ...String(args.query || "").toLowerCase().split(/\s+/)].filter(Boolean);
        for (const e of await this.contracts.list()) {
          const hay = `${e.name} ${e.title} ${e.summary || ""}`.toLowerCase();
          if (!named.includes(e.name) && !tokens.some((t) => hay.includes(t))) continue;
          const mine = e.consumers.some((c) => c.agent === this.client.agent) ? ` your standing: ${(await this.contracts.status(e.name)).consumers.find((c) => c.agent === this.client.agent).state}.` : "";
          const objs = openObjections(e).map((o) => ` Open objection from ${o.agent}: ${o.note}.`).join("");
          contractFacts.push(`- **contract ${e.name}** v${e.version}: ${e.summary || e.title} (producer ${e.producer}, ${e.consumers.length} subscribed consumer${e.consumers.length === 1 ? "" : "s"};${mine}${objs}) [source: contracts/registry.md; claims unverified, see contract action=get]`);
        }
      } catch {}
      contractFacts = contractFacts.slice(0, 5);
      const facts = [...contractFacts, ...pkg.facts].slice(0, 15);
      return [
        `=== Compiled Context Package (${facts.length} source-backed facts) ===`,
        pkg.instructions,
        "",
        ...facts,
      ].join("\n");
    }
    throw new LineError(`unknown tool ${name}`);
  }
}

export async function handle(tools, request) {
  const { method, id } = request;
  if (id === undefined || id === null) return null; // a notification
  let result;
  if (method === "initialize") {
    result = {
      protocolVersion: request.params?.protocolVersion || PROTOCOL,
      capabilities: { tools: {} },
      serverInfo: { name: "chamber", version: "0.2.0" },
      instructions: `You are in your team's chamber as ${tools.client.agent}. Teammates' agents coordinate with you; their messages appear on their own while you work and wake you when idle. Use the typed coordination tools (coordinate_ask, coordinate_delegate, coordinate_review, coordinate_resolve, coordinate_handoff) to collaborate reliably without human relays. Never send credentials or secrets.\n\nThe team shares durable memory (MEMORY.md and linked files): read it or compile context packages (context_package) before starting work; when a collaboration settles a fact (what was found, who owns what), resolve it with coordinate_resolve to record it in team memory with evidence. A decision the team must stand by (an architecture choice, a convention, a breaking API change) is not yours to settle: propose it with the decision tool; other agents review it, a teammate approves it, and approved decisions greet every session. To roll out an API contract change, use the contract tool: producers publish new versions, subscribed consumers are notified automatically — mid-task or on wake — and ack seen/adapted (with evidence) or object; the registry keeps delivered separate from adapted.`,
    };
  } else if (method === "tools/list") {
    result = { tools: [...TOOLS, ...MEMORY_TOOLS, ...DECISION_TOOLS, ...CONTRACT_TOOLS, ...COORDINATION_TOOLS] };
  } else if (method === "tools/call") {
    try {
      result = { content: [{ type: "text", text: await tools.call(request.params?.name, request.params?.arguments || {}) }], isError: false };
    } catch (error) {
      result = { content: [{ type: "text", text: `chamber error: ${error.message}` }], isError: true };
    }
  } else if (method === "ping") {
    result = {};
  } else {
    return { jsonrpc: "2.0", id, error: { code: -32601, message: `unknown method ${method}` } };
  }
  return { jsonrpc: "2.0", id, result };
}

/** The kind of agent from its name: claude-code.api → claude-code, codex.api → codex, dsh → dsh. */
export const kindOf = (name) => String(name).split(".")[0];
export const KIND_LABEL = { "claude-code": "Claude Code", codex: "Codex", antigravity: "Antigravity", dsh: "DeepSeek Harness" };

export async function serve(name) {
  const config = loadConfig();
  if (!config) {
    process.stderr.write("Not in a chamber yet: run the install command your teammate sent you.\n");
    return 2;
  }
  const agent = `${config.owner}/${name}`;
  const inbox = new Inbox(agent);
  const client = new LineClient(config.invite, agent, {
    kind: kindOf(name),
    label: `${KIND_LABEL[kindOf(name)] ?? kindOf(name)} · ${basename(process.cwd())}`,
    priority: 5,
    answers: true,
    cursorPath: inbox.cursorPath,
    onMessage: (message) => inbox.add(message),
  });
  client.run();
  const tools = new LineTools(client, inbox, process.cwd());
  const lines = createInterface({ input: process.stdin });
  lines.on("line", async (line) => {
    let request;
    try {
      request = JSON.parse(line);
    } catch {
      return;
    }
    const response = await handle(tools, request); // concurrent: a waiting ask does not block other calls
    if (response) process.stdout.write(`${JSON.stringify(response)}\n`);
  });
  await new Promise((resolve) => lines.on("close", resolve));
  client.close();
  return 0;
}
