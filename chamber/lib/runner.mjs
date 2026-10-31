// The Always-On Headless Runner & Cryptographically Signed Workflow Engine.
//
// Security Architecture:
// 1. Admin-Signed Workflows: Workflow files (.chamber/workflows/*.yaml) must be signed
//    with the Chamber Admin's Ed25519 private key and verified against the pinned public key.
//    No HMAC or shared-secret fallbacks. Domain-separated by chamber lineId.
// 2. Cryptographic Approval Gates: Sensitive actions pause at an approval gate.
//    Gates store encrypted payloads on the relay, can be approved locally on the board,
//    via CLI, or via ephemeral remote mobile links where details decrypt client-side.
// 3. Safe Execution: Headless runner uses answers: false so it never captures @owner
//    messages meant for human or interactive agents. Git patches apply via execFileSync with argv;
//    other steps execute with strict 60s timeout and pipe isolation.

import { execFileSync, execSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { LineClient } from "./client.mjs";
import { adminState, HOME, loadConfig } from "./inbox.mjs";
import { LineError } from "./protocol.mjs";
import { signWorkflow, verifyWorkflow } from "./admin.mjs";

export { signWorkflow, verifyWorkflow };

async function sealGatePayload(keyB64, obj) {
  const rawKey = Buffer.from(keyB64, "base64url");
  const cryptoKey = await crypto.subtle.importKey("raw", rawKey, "AES-GCM", false, ["encrypt"]);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const data = new TextEncoder().encode(JSON.stringify(obj));
  const encrypted = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, cryptoKey, data));
  const combined = new Uint8Array(12 + encrypted.length);
  combined.set(iv);
  combined.set(encrypted, 12);
  return Buffer.from(combined).toString("base64url");
}

export class ApprovalGateManager {
  constructor(baseDir = HOME) {
    this.gatesDir = join(baseDir, "gates");
    mkdirSync(this.gatesDir, { recursive: true, mode: 0o700 });
  }

  async createGate({ description, action = "action", requestedBy = "agent", expiresMinutes = 60, relayUrl = null, lineId = null, authToken = null }) {
    const gateId = [...crypto.getRandomValues(new Uint8Array(16))]
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
    const token = [...crypto.getRandomValues(new Uint8Array(24))]
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
    const gateKey = Buffer.from(crypto.getRandomValues(new Uint8Array(16))).toString("base64url");

    const expiresAt = Date.now() + expiresMinutes * 60 * 1000;
    const gate = {
      id: gateId,
      token,
      gateKey,
      lineId: lineId || "",
      description,
      action,
      requestedBy,
      status: "pending",
      createdAt: Date.now(),
      expiresAt,
    };

    if (relayUrl) {
      const cleanRelay = relayUrl.replace(/\/+$/, "");
      gate.mobileUrl = lineId
        ? `${cleanRelay}/gate/${lineId}/${gateId}?token=${token}#${gateKey}`
        : `${cleanRelay}/gate/${gateId}?token=${token}#${gateKey}`;

      // Seal description & action so relay stores ciphertext only
      const sealed = await sealGatePayload(gateKey, { description, action, requestedBy });

      const headers = { "content-type": "application/json" };
      if (authToken) headers["authorization"] = `Bearer ${authToken}`;

      const endpoint = lineId
        ? `${cleanRelay}/v1/lines/${lineId}/gates`
        : `${cleanRelay}/v1/gate/${gateId}`;

      fetch(endpoint, {
        method: "POST",
        headers,
        body: JSON.stringify({
          id: gateId,
          token,
          line: lineId || "",
          sealed,
          expiresAt: gate.expiresAt,
        }),
      }).catch(() => {});
    }

    writeFileSync(join(this.gatesDir, `${gateId}.json`), JSON.stringify(gate, null, 2), { mode: 0o600 });
    return gate;
  }

  getGate(gateId) {
    try {
      const data = JSON.parse(readFileSync(join(this.gatesDir, `${gateId}.json`), "utf8"));
      if (Date.now() > data.expiresAt && data.status === "pending") {
        data.status = "expired";
      }
      return data;
    } catch {
      return null;
    }
  }

  async syncRemote(gateId, relayUrl, lineId = null, authToken = null) {
    const gate = this.getGate(gateId);
    if (!gate || gate.status !== "pending" || !relayUrl) return gate;
    const cleanRelay = relayUrl.replace(/\/+$/, "");
    const targetLine = lineId || gate.lineId;
    const url = targetLine
      ? `${cleanRelay}/v1/lines/${targetLine}/gates/${gateId}?token=${gate.token}&format=json`
      : `${cleanRelay}/gate/${gateId}?token=${gate.token}&format=json`;

    try {
      const headers = authToken ? { authorization: `Bearer ${authToken}` } : {};
      const res = await fetch(url, { headers });
      if (res.ok) {
        const remote = await res.json();
        if (remote.gate && remote.gate.status && remote.gate.status !== "pending") {
          return this.resolveGate(gateId, remote.gate.status, {
            token: gate.token,
            approver: remote.gate.approver || "remote-mobile",
          });
        }
      }
    } catch {}
    return gate;
  }

  async waitForGate(gateId, { relayUrl = null, lineId = null, authToken = null, timeoutMs = 300000, pollIntervalMs = 1500 } = {}) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      let gate = this.getGate(gateId);
      if (!gate) break;
      if (gate.status !== "pending") return gate;
      if (relayUrl) {
        gate = await this.syncRemote(gateId, relayUrl, lineId, authToken);
        if (gate.status !== "pending") return gate;
      }
      await new Promise((r) => setTimeout(r, pollIntervalMs));
    }
    return this.getGate(gateId) || { id: gateId, status: "expired" };
  }

  resolveGate(gateId, decision, { approver = "admin", token = null, relayUrl = null, lineId = null, authToken = null } = {}) {
    const gate = this.getGate(gateId);
    if (!gate) throw new LineError("Gate not found");
    if (gate.status !== "pending") throw new LineError(`Gate already ${gate.status}`);
    if (token && gate.token !== token) throw new LineError("Invalid gate approval token");

    gate.status = decision === "approve" || decision === "approved" ? "approved" : "rejected";
    gate.resolvedAt = Date.now();
    gate.resolvedBy = approver;

    writeFileSync(join(this.gatesDir, `${gateId}.json`), JSON.stringify(gate, null, 2), { mode: 0o600 });

    if (relayUrl) {
      const cleanRelay = relayUrl.replace(/\/+$/, "");
      const targetLine = lineId || gate.lineId;
      const url = targetLine
        ? `${cleanRelay}/v1/lines/${targetLine}/gates/${gateId}?token=${gate.token}`
        : `${cleanRelay}/gate/${gateId}?token=${gate.token}`;
      const headers = { "content-type": "application/json" };
      if (authToken) headers["authorization"] = `Bearer ${authToken}`;
      fetch(url, {
        method: "POST",
        headers,
        body: JSON.stringify({ decision, approver, token: gate.token }),
      }).catch(() => {});
    }

    return gate;
  }

  listPending() {
    if (!existsSync(this.gatesDir)) return [];
    const out = [];
    for (const f of readdirSync(this.gatesDir)) {
      if (f.endsWith(".json")) {
        const g = this.getGate(f.replace(".json", ""));
        if (g && g.status === "pending") out.push(g);
      }
    }
    return out;
  }
}

export async function installDefaultWorkflows(workflowsDir, adminPrivate = null, lineId = "") {
  mkdirSync(workflowsDir, { recursive: true, mode: 0o755 });

  const onPatch = `name: on-patch
on: patch
description: Automatically verify patches with git apply --check
gate:
  action: git apply
  description: "Review and approve code patch before applying"
  expires_minutes: 30
steps:
  - name: verify-patch
    run: git apply --check -`;

  const onMigration = `name: on-migration
on: migration
description: Require explicit human approval before running database migrations
gate:
  action: db-migrate
  description: "Review and approve database migration before applying"
  expires_minutes: 30
steps:
  - name: dry-run
    run: echo "Migration gate passed for dry run"`;

  const patchFile = join(workflowsDir, "on-patch.yaml");
  const gateFile = join(workflowsDir, "on-gate.yaml");

  const signedPatch = adminPrivate ? signWorkflow(onPatch, adminPrivate, { lineId }) : onPatch;
  const signedGate = adminPrivate ? signWorkflow(onMigration, adminPrivate, { lineId }) : onMigration;

  if (!existsSync(patchFile)) writeFileSync(patchFile, signedPatch, { mode: 0o644 });
  if (!existsSync(gateFile)) writeFileSync(gateFile, signedGate, { mode: 0o644 });

  return { patchFile, gateFile };
}

export class ChamberRunner {
  constructor(config = loadConfig(), { cwd = process.cwd(), workflowsDir = null } = {}) {
    if (!config) throw new Error("not in a chamber yet");
    this.config = config;
    this.owner = config.owner;
    this.cwd = cwd;
    this.agent = `${this.owner}/runner`;
    this.workflowsDir = workflowsDir || join(cwd, ".chamber", "workflows");
    this.gateManager = new ApprovalGateManager(join(HOME, this.owner));
    this.client = null;
    this.running = false;
  }

  async start() {
    const admin = adminState();
    const adminPrivate = admin?.adminPrivate || null;
    const adminPublic = admin?.adminPublic || this.config.adminPublic || null;
    this.adminPublic = adminPublic;

    await this.config.invite.ready();
    const lineId = this.config.invite.lineId;

    if (!existsSync(this.workflowsDir)) {
      await installDefaultWorkflows(this.workflowsDir, adminPrivate, lineId);
    }

    this.client = new LineClient(this.config.invite, this.agent, {
      kind: "runner",
      label: "Always-on Chamber Runner & Workflow Engine",
      priority: 5,
      answers: false, // Ensures messages addressed to @owner are NOT captured by runner
      onMessage: (msg) => this.#onEvent(msg),
    });

    this.client.run();
    await this.client.ready();
    this.running = true;
    return this;
  }

  async stop() {
    this.running = false;
    if (this.client) {
      this.client.close();
      this.client = null;
    }
  }

  async #onEvent(message) {
    if (!this.running) return;
    if (message.type === "patch" && message.patch) {
      await this.#handlePatchEvent(message);
    } else if (message.type === "migration") {
      await this.#handleMigrationEvent(message);
    }
  }

  async #handlePatchEvent(message) {
    if (!existsSync(this.workflowsDir)) return;
    const lineId = this.config.invite.lineId;

    const files = readdirSync(this.workflowsDir).filter((f) => f.endsWith(".yaml") || f.endsWith(".yml"));
    for (const file of files) {
      const fullPath = join(this.workflowsDir, file);
      const content = readFileSync(fullPath, "utf8");

      if (!this.adminPublic) {
        console.warn(`[Runner] Skipping workflow ${file}: no pinned admin public key to verify signature`);
        continue;
      }

      const check = verifyWorkflow(content, this.adminPublic, { lineId });
      if (!check.valid) {
        console.warn(`[Runner] Skipping unverified/tampered workflow ${file}: ${check.error}`);
        continue;
      }

      if (/on:\s*patch/i.test(check.body)) {
        const to = String(message.to || "").startsWith("#") ? message.to : message.from;
        const thread = message.thread || message.id;

        // Check if workflow requires gate
        const hasGate = /gate:/i.test(check.body);
        if (hasGate) {
          const gate = await this.gateManager.createGate({
            description: `Apply patch from ${message.from}: ${message.title || "Code patch"}`,
            action: "git apply --check",
            requestedBy: message.from,
            relayUrl: this.config.invite.relay,
            lineId: this.config.invite.lineId,
            authToken: this.config.invite.token,
          });

          // Announce gate in thread without leaking secret token
          await this.client.send(
            to,
            `Workflow [${file}] paused at approval gate: \`${gate.id}\`. Awaiting admin approval on the board or via \`chamber gate approve ${gate.id}\`.`,
            { replyTo: message.id, thread }
          );

          // Await human authorization
          const resolved = await this.gateManager.waitForGate(gate.id, {
            relayUrl: this.config.invite.relay,
            lineId: this.config.invite.lineId,
            authToken: this.config.invite.token,
            timeoutMs: 180000,
          });

          if (resolved.status !== "approved") {
            await this.client.send(
              to,
              `Workflow [${file}] gate \`${gate.id}\` was ${resolved.status}. Execution aborted.`,
              { replyTo: message.id, thread }
            );
            continue;
          }
        }

        // Execute verified steps
        const result = await this.#executeSteps(check.body, { patch: message.patch, branch: message.branch });
        if (result.ok) {
          await this.client.send(
            to,
            `✔ Workflow [${file}] executed cleanly:\n${result.output || "Patch verified."}`,
            { replyTo: message.id, thread }
          );
        } else {
          await this.client.send(
            to,
            `✕ Workflow [${file}] failed:\n${result.error}`,
            { replyTo: message.id, thread }
          );
        }
      }
    }
  }

  async #handleMigrationEvent(message) {
    if (!existsSync(this.workflowsDir)) return;
    const lineId = this.config.invite.lineId;
    const files = readdirSync(this.workflowsDir).filter((f) => f.endsWith(".yaml") || f.endsWith(".yml"));
    for (const file of files) {
      const fullPath = join(this.workflowsDir, file);
      const content = readFileSync(fullPath, "utf8");
      if (!this.adminPublic) continue;
      const check = verifyWorkflow(content, this.adminPublic, { lineId });
      if (!check.valid || !/on:\s*migration/i.test(check.body)) continue;

      const to = String(message.to || "").startsWith("#") ? message.to : message.from;
      const thread = message.thread || message.id;

      const gate = await this.gateManager.createGate({
        description: `Database migration requested by ${message.from}`,
        action: "db-migrate",
        requestedBy: message.from,
        relayUrl: this.config.invite.relay,
        lineId: this.config.invite.lineId,
        authToken: this.config.invite.token,
      });

      await this.client.send(
        to,
        `Migration workflow [${file}] paused at approval gate: \`${gate.id}\`. Awaiting admin review.`,
        { replyTo: message.id, thread }
      );

      const resolved = await this.gateManager.waitForGate(gate.id, {
        relayUrl: this.config.invite.relay,
        lineId: this.config.invite.lineId,
        authToken: this.config.invite.token,
        timeoutMs: 180000,
      });

      if (resolved.status === "approved") {
        const execResult = await this.#executeSteps(check.body, { message });
        if (execResult.ok) {
          await this.client.send(
            to,
            `✔ Migration gate approved. Migration workflow executed successfully:\n${execResult.output || "(no output)"}`.trim(),
            { replyTo: message.id, thread }
          );
        } else {
          await this.client.send(
            to,
            `✕ Migration gate was approved, but workflow execution failed:\n${execResult.error}`,
            { replyTo: message.id, thread }
          );
        }
      } else {
        await this.client.send(to, `✕ Migration gate was ${resolved.status}. Aborted.`, { replyTo: message.id, thread });
      }
    }
  }

  async #executeSteps(workflowYaml, context = {}) {
    try {
      const runLines = [...workflowYaml.matchAll(/^\s*run:\s*(.+)$/gm)].map((m) => m[1].trim());
      if (!runLines.length) {
        return { ok: false, error: "Workflow contains no executable steps ('run:' directives)" };
      }
      const outputs = [];
      for (const cmd of runLines) {
        if (/^git\s+apply/i.test(cmd)) {
          const args = cmd.split(/\s+/).slice(1);
          const stdinData = context.patch ? Buffer.from(context.patch, "utf8") : undefined;
          const out = execFileSync("git", args, { cwd: this.cwd, input: stdinData, encoding: "utf8" });
          if (out) outputs.push(out);
        } else if (/^echo\s+/i.test(cmd)) {
          outputs.push(cmd.replace(/^echo\s+/i, "").replace(/^["']|["']$/g, ""));
        } else {
          try {
            const out = execSync(cmd, { cwd: this.cwd, encoding: "utf8", timeout: 60000, stdio: ["pipe", "pipe", "pipe"] });
            if (out) outputs.push(out.trim());
          } catch (cmdErr) {
            return { ok: false, error: `Step failed [${cmd}]: ${cmdErr.stderr?.trim() || cmdErr.message}` };
          }
        }
      }
      return { ok: true, output: outputs.join("\n") };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  }

  testExecuteSteps(workflowYaml, context = {}) {
    return this.#executeSteps(workflowYaml, context);
  }
}
