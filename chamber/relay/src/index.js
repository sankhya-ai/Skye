// The chamber relay.
//
// A line is a shared room where people's agents (root, Claude Code, …) meet.
// The Worker is stateless: it checks the caller's token and hands the request to
// the line's Durable Object, which keeps two things and nothing else:
//
//   - the roster: which agents are connected right now (one WebSocket each)
//   - messages, so an agent that is offline gets what was sent to it when it
//     reconnects (kept 30 days)
//
// The relay never sees content. Message bodies are sealed by the clients with
// a key derived from the line's invite secret; the relay routes on the
// envelope (from, to, reply_to) only.
//
// Auth needs no stored state either: the line id is a hash of the line token,
// so a token is valid for exactly one line and the relay keeps no secret.
//
// Files too big for a message go to R2 (optional binding BLOBS), sealed by the
// clients like bodies are. They live only for the line's session: once no agent
// has been connected for IDLE_WIPE_MS (var IDLE_WIPE_SECONDS overrides), every blob of the line is deleted except
// those a not-yet-delivered message still points at.
//
// Personal invites: with the secret ADMIN_KEY (the admin's Ed25519 public key) every agent
// must show a pass the admin signed for its member, a name belongs to the first member to
// use it, and the admin can revoke a member (a signed order). Without ADMIN_KEY, anyone
// with the chamber key may join (the original, open mode).
//
// Team memory: a small versioned store of markdown files (sealed by the clients like
// messages), with Devin's rule for concurrent writers: a commit names the version of each
// file it was based on, and is rejected with the current versions if any moved.
//
// A relay can be private: with the secret ALLOWED_LINES (comma-separated chamber ids, set by
// create.sh) it serves only those chambers, so a public URL is not an open relay.
//
// Addressing (the `to` of a message):
//   "owner/name"  one agent, e.g. "nitin/claude-code"
//   "@owner"      a person: their best agent online (one that answers, then the
//                 highest priority), else held until one of theirs connects
//   "*"           everyone on the line
//
// Hosted (var HOSTED = "1", Chamber Cloud): one relay serves many chambers. Each chamber registers
// itself (the same claim call), keeps its own admin key, and gets a 7-day trial; after that it needs
// an active plan with a seat for each member, which the billing service sets with the secret
// HOSTED_SECRET (POST /v1/hosted/billing). A chamber past its trial without a plan can still be
// managed by its admin, but its members can't connect until it's paid.
//
// Knocks: a message to an agent that isn't connected also sends its owner's boards a knock
// ({t: "knock", id, from, to, ts}, no content), so the owner's app can wake that agent if the
// owner allows the sender to.

import { install } from "./install.js";

/** Bumped when the relay changes, so a client can tell which code answered. */
const RELAY_VERSION = 9;
const RETENTION_MS = 30 * 24 * 3600 * 1000;
const MAX_ROWS = 50000;
const MAX_BODY = 900_000;
const AGENT = /^[a-z0-9][a-z0-9._-]{0,31}\/[a-z0-9][a-z0-9._-]{0,31}$/;
const LINE = /^[0-9a-f]{32}$/;
const CHANNEL = /^#[a-z0-9][a-z0-9_-]{0,39}$/;
const OWNER = /^[a-z0-9][a-z0-9._-]{0,31}$/;
const BLOB = /^[0-9a-f]{32}$/;
const MSG_ID = /^[0-9a-f]{32}$/;
const PUBKEY = /^[A-Za-z0-9_-]{43}$/; // a raw 32-byte Ed25519 public key, base64url
const V2_CLOCK_SKEW_MS = 5 * 60 * 1000; // a v2 message's signed ts must be within this of the relay's clock
const MAX_BLOB = 25 * 1024 * 1024;
const IDLE_WIPE_MS = 10 * 60 * 1000;
const POLL_WAIT_MS = 25_000; // a long poll answers at once when there's something, else after this
const POLL_IDLE_MS = 60_000; // a poll session not heard from for this long has left
const KNOCK_WINDOW_MS = 24 * 3600 * 1000; // a board that connects hears of waiting messages this recent
const TRIAL_MS = 7 * 24 * 3600 * 1000;
const BILLING_GRACE_MS = 3 * 24 * 3600 * 1000; // a payment a few days late doesn't lock a team out
const BILLING_URL = "https://chamber.sankhyaailabs.com/billing";
const hosted = (env) => String(env.HOSTED || "") === "1";
const MAX_MEMORY_FILE = 400_000; // sealed, base64
const MAX_MEMORY_FILES = 2000;
const MEMORY_PATH = /^[A-Za-z0-9][A-Za-z0-9._\/-]{0,199}\.md$/;

/**
 * How long a relay keeps what it holds. Cloudflare's free plan is the default: messages for
 * RETENTION_MS (the newest MAX_ROWS), files only while someone is connected (see alarm). A relay on
 * your own server (relay/serve.mjs) keeps everything unless told otherwise. Vars: RETENTION_DAYS
 * (0 = forever), MAX_MESSAGES (0 = no cap), KEEP_FILES ("1" = never wipe files).
 */
function retention(env) {
  const days = env.RETENTION_DAYS === undefined || env.RETENTION_DAYS === "" ? null : Number(env.RETENTION_DAYS);
  const rows = env.MAX_MESSAGES === undefined || env.MAX_MESSAGES === "" ? null : Number(env.MAX_MESSAGES);
  return {
    keepMs: days === null || !Number.isFinite(days) ? RETENTION_MS : days > 0 ? days * 24 * 3600 * 1000 : 0,
    maxRows: rows === null || !Number.isFinite(rows) ? MAX_ROWS : rows > 0 ? rows : 0,
    keepFiles: String(env.KEEP_FILES || "") === "1",
  };
}

async function lineOf(token) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/") return request.headers.get("accept")?.includes("text/html") ? landing(url, await relayConfig(env)) : new Response("chamber relay\n");
    if (url.pathname === "/v1/claim" && request.method === "POST") return hosted(env) ? register(request, env) : claim(request, env);
    // `files`: whether this relay can hold files too big for a message (the optional R2 binding).
    if (url.pathname === "/v1/relay") return json(hosted(env) ? { claimed: false, hosted: true, files: Boolean(env.BLOBS), relay: RELAY_VERSION } : { claimed: (await relayConfig(env)).claimed, files: Boolean(env.BLOBS), relay: RELAY_VERSION });
    if (url.pathname.startsWith("/v1/hosted/")) return hostedApi(request, env, url);
    const served = install(url);
    if (served) return served;

    // Mobile approval routes: /gate/:line/:gateId or /gate/:gateId?line=:line
    const mobileLineGateMatch = url.pathname.match(/^\/gate\/([0-9a-f]{32})\/([0-9a-f]{16,64})$/i);
    if (mobileLineGateMatch) {
      const [, line, gateId] = mobileLineGateMatch;
      const config = await relayConfig(env);
      const allowed = config.lines;
      if (allowed.length && !allowed.includes(line)) return json({ error: "this relay serves only its own chambers" }, 403);
      const stub = env.LINES.get(env.LINES.idFromName(line));
      const forwarded = new Request(`https://line/gate/${gateId}${url.search}`, request);
      return stub.fetch(forwarded);
    }

    const singleGateMatch = url.pathname.match(/^\/(?:v1\/)?gate\/([0-9a-f]{16,64})(?:\/([a-z0-9_-]+))?$/i);
    if (singleGateMatch) {
      const [, gateId, sub] = singleGateMatch;
      const config = await relayConfig(env);
      const line = url.searchParams.get("line") || (config.lines.length === 1 ? config.lines[0] : null);
      if (line) {
        const stub = env.LINES.get(env.LINES.idFromName(line));
        const forwarded = new Request(`https://line/gate/${gateId}${sub ? "/" + sub : ""}${url.search}`, request);
        return stub.fetch(forwarded);
      }
      return json({ error: "line required for gate review" }, 400);
    }

    const match = url.pathname.match(/^\/v1\/lines\/([^/]+)\/(ws|poll(?:\/(?:open|send|close))?|roster|members|admin|sync|coordination(?:\/[a-zA-Z0-9_-]+)?|memory(?:\/(?:file|commit|history))?|gates(?:\/([0-9a-f]{16,64}))?|gate\/([0-9a-f]{16,64})|restart|test-fail-transition|blobs\/([^/]+))$/);
    if (!match) return json({ error: "not found" }, 404);
    const [, line, what, , , blob] = match;
    const token = (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
    if (!LINE.test(line) || !token || (await lineOf(token)) !== line) return json({ error: "unauthorized" }, 401);
    // Hosted: each chamber keeps its own admin key (in its Durable Object); nothing relay-wide.
    const config = hosted(env) ? { adminKey: null, lines: [] } : await relayConfig(env);
    const allowed = config.lines;
    if (allowed.length && !allowed.includes(line)) return json({ error: "this relay serves only its own chambers" }, 403);
    if (hosted(env)) {
      const refused = await env.LINES.get(env.LINES.idFromName(line)).fetch(new Request("https://line/hosted-check", { headers: { "x-line": line, "x-what": what } }));
      if (!refused.ok) return refused;
    }
    if (blob !== undefined) return blobs(request, env, line, blob, config, env.LINES.get(env.LINES.idFromName(line)));
    if (what === "ws" && request.headers.get("upgrade") !== "websocket") return json({ error: "expected a WebSocket" }, 426);
    const stub = env.LINES.get(env.LINES.idFromName(line));
    const forwarded = new Request(`https://line/${what}${url.search}`, request);
    forwarded.headers.set("x-line", line);
    if (config.adminKey) forwarded.headers.set("x-admin-key", config.adminKey); // personal invites, from secrets or the claim
    return stub.fetch(forwarded);
  },
};

const b64 = (text) => Uint8Array.from(atob(text.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));

/** sha256 hex of a string (the sealed body): v2 signatures cover the hash, not the body. */
async function sha256hex(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** The exact bytes a v2/v3 message signature covers. Mirrored in lib/identity.mjs: change both.
 *  v3 signatures also commit to the epoch, so ciphertext cannot be re-labeled across epochs. */
function envelopePayload({ line, from, to, id, replyTo, ts, hash, epoch = 0 }) {
  return epoch ? ["chamber-msg-v3", line, from, to, id, replyTo || "", String(ts), String(epoch), hash].join("\n") : ["chamber-msg-v2", line, from, to, id, replyTo || "", String(ts), hash].join("\n");
}

/** Verify a v2 message signature against the sender's enrolled public key. */
async function verifyEnvelopeSig(pubkey, fields, sig) {
  try {
    const key = await crypto.subtle.importKey("raw", b64(pubkey), { name: "Ed25519" }, false, ["verify"]);
    return await crypto.subtle.verify({ name: "Ed25519" }, key, b64(sig), new TextEncoder().encode(envelopePayload(fields)));
  } catch {
    return false;
  }
}

/** The claims of a token the admin signed (pass or order), or null. */
async function signedByAdmin(adminKey, token) {
  if (!adminKey || typeof token !== "string") return null;
  const [body, sig] = token.split(".");
  if (!body || !sig) return null;
  try {
    const key = await crypto.subtle.importKey("raw", b64(adminKey), { name: "Ed25519" }, false, ["verify"]);
    if (!(await crypto.subtle.verify({ name: "Ed25519" }, key, b64(sig), new TextEncoder().encode(body)))) return null;
    return JSON.parse(new TextDecoder().decode(b64(body)));
  } catch {
    return null;
  }
}

// ----- who owns this relay --------------------------------------------------------------
// create.sh sets the secrets ADMIN_KEY and ALLOWED_LINES. A relay deployed with the Deploy
// button has neither: it is unclaimed until the first `chamber new` claims it (like
// company-brain, "the first person to sign in owns the deployment"). The claim lives in a
// config Durable Object.

const CONFIG = "__chamber_relay_config__";
let cached = { at: 0, value: null };

async function relayConfig(env, fresh = false) {
  if (env.ADMIN_KEY || env.ALLOWED_LINES) return { adminKey: env.ADMIN_KEY || null, lines: String(env.ALLOWED_LINES || "").split(",").map((s) => s.trim()).filter(Boolean), claimed: true };
  if (!fresh && cached.value && Date.now() - cached.at < 30_000) return cached.value;
  const stub = env.LINES.get(env.LINES.idFromName(CONFIG));
  const value = await (await stub.fetch("https://line/config")).json();
  cached = { at: Date.now(), value };
  return value;
}

async function claim(request, env) {
  const config = await relayConfig(env, true);
  if (config.claimed) return json({ error: "this relay already belongs to a chamber" }, 409);
  const body = await request.json().catch(() => ({}));
  const claims = await signedByAdmin(String(body.adminPublic || ""), body.proof);
  if (!claims || claims.op !== "claim" || !LINE.test(String(claims.line || ""))) return json({ error: "not a valid claim" }, 400);
  const stub = env.LINES.get(env.LINES.idFromName(CONFIG));
  const result = await stub.fetch("https://line/config-claim", { method: "POST", body: JSON.stringify({ adminKey: body.adminPublic, lines: [claims.line] }) });
  cached = { at: 0, value: null };
  return new Response(result.body, result);
}

// ----- Chamber Cloud ------------------------------------------------------------------------

/** A chamber registers itself on the hosted relay: its admin key, and its trial starts. */
async function register(request, env) {
  const body = await request.json().catch(() => ({}));
  const claims = await signedByAdmin(String(body.adminPublic || ""), body.proof);
  if (!claims || claims.op !== "claim" || !LINE.test(String(claims.line || ""))) return json({ error: "not a valid claim" }, 400);
  const stub = env.LINES.get(env.LINES.idFromName(claims.line));
  const result = await stub.fetch("https://line/hosted-register", { method: "POST", headers: { "x-line": claims.line }, body: JSON.stringify({ adminKey: body.adminPublic }) });
  return new Response(result.body, result);
}

/**
 * The billing service's side: POST /v1/hosted/billing { line, status, seats, paidUntil } sets a
 * chamber's plan; GET /v1/hosted/status?line= tells its trial, plan and members. Both need
 * HOSTED_SECRET. GET /v1/hosted/status also answers a member (the chamber's own token).
 */
async function hostedApi(request, env, url) {
  if (!hosted(env)) return json({ error: "not found" }, 404);
  const secret = String(env.HOSTED_SECRET || "");
  const bearer = (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  const service = Boolean(secret) && bearer === secret;
  if (url.pathname === "/v1/hosted/billing" && request.method === "POST") {
    if (!service) return json({ error: "unauthorized" }, 401);
    const body = await request.json().catch(() => ({}));
    if (!LINE.test(String(body.line || ""))) return json({ error: "line required" }, 400);
    const stub = env.LINES.get(env.LINES.idFromName(body.line));
    return stub.fetch("https://line/hosted-billing", { method: "POST", headers: { "x-line": body.line }, body: JSON.stringify(body) });
  }
  if (url.pathname === "/v1/hosted/status" && request.method === "GET") {
    const line = url.searchParams.get("line") || "";
    if (!LINE.test(line)) return json({ error: "line required" }, 400);
    if (!service && (!bearer || (await lineOf(bearer)) !== line)) return json({ error: "unauthorized" }, 401);
    return env.LINES.get(env.LINES.idFromName(line)).fetch("https://line/hosted-status", { headers: { "x-line": line } });
  }
  return json({ error: "not found" }, 404);
}

function landing(url, config) {
  const make = `curl -fsSL ${url.origin}/new | sh`;
  const body = config.claimed
    ? `<p>This relay belongs to a chamber. To join it, ask its admin for your personal invite.</p>`
    : `<p>Your relay is up. Make your chamber: run this inside a project folder (needs Node 22.4+):</p><pre>${make}</pre><p>You'll be the admin: you invite your team, and this relay serves only your chamber.</p>`;
  return new Response(
    `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>chamber relay</title><style>body{font:15px/1.6 -apple-system,system-ui,sans-serif;max-width:640px;margin:12vh auto;padding:0 20px;color:#1b1c1f;background:#f6f5f1}h1{font-size:22px}pre{background:#fff;border:1px solid #e4e4e1;border-radius:8px;padding:12px;white-space:pre-wrap;word-break:break-all}@media(prefers-color-scheme:dark){body{background:#0a0a0c;color:#ebecee}pre{background:#16171a;border-color:#26272b}}</style><h1>chamber<span style="color:#4e83b0">.</span></h1>${body}`,
    { headers: { "content-type": "text/html; charset=utf-8" } },
  );
}

async function blobs(request, env, line, blob, config = null, stub = null) {
  if (!env.BLOBS) return json({ error: "this relay has no file storage; send smaller files" }, 501);
  if (!BLOB.test(blob)) return json({ error: "bad blob id" }, 400);
  // Hosted: the chamber's own admin key, in its Durable Object, checks the pass (member-check).
  if (config?.adminKey || hosted(env)) {
    const pass = request.headers.get("x-chamber-pass");
    if (!pass) return json({ error: "a personal pass is required" }, 401);
    if (config?.adminKey) {
      const claims = await signedByAdmin(config.adminKey, pass);
      if (!claims || claims.line !== line || !claims.member) return json({ error: "invalid pass" }, 403);
    }
    if (stub) {
      const chk = await stub.fetch(new Request("https://line/member-check", {
        headers: { "x-chamber-pass": pass, "x-line": line, ...(config?.adminKey ? { "x-admin-key": config.adminKey } : {}) }
      }));
      if (!chk.ok) return chk;
    }
  }
  const key = `${line}/${blob}`;
  if (request.method === "PUT") {
    const size = Number(request.headers.get("content-length") || 0);
    if (!size || size > MAX_BLOB) return json({ error: `a file must be under ${MAX_BLOB / 1024 / 1024} MB` }, 413);
    await env.BLOBS.put(key, request.body);
    return json({ ok: true, blob, size });
  }
  if (request.method === "GET") {
    const object = await env.BLOBS.get(key);
    if (!object) return json({ error: "gone: files are wiped when the line's session ends" }, 404);
    return new Response(object.body, { headers: { "content-type": "application/octet-stream" } });
  }
  return json({ error: "PUT or GET" }, 405);
}

/** A long-poll session, shaped like the WebSockets the Line keeps (send, attachment, close). */
class PollSocket {
  constructor(id) {
    this.id = id;
    this.queue = [];
    this.attachment = null;
    this.closed = null;
    this.seen = Date.now();
    this.waiting = null;
  }
  send(frame) {
    if (this.closed) throw new Error("closed");
    this.queue.push(JSON.parse(frame));
    this.waiting?.();
  }
  serializeAttachment(value) {
    this.attachment = value;
  }
  deserializeAttachment() {
    return this.attachment;
  }
  close(code = 1000, reason = "") {
    if (this.closed) return;
    this.closed = { code, reason };
    this.waiting?.();
  }
  /** Until a frame arrives, the session closes, or `ms` pass. */
  wait(ms) {
    return new Promise((resolve) => {
      const timer = setTimeout(done, ms);
      const self = this;
      function done() {
        clearTimeout(timer);
        if (self.waiting === done) self.waiting = null;
        resolve();
      }
      this.waiting = done;
    });
  }
}

export class Line {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
    this.sql = ctx.storage.sql;
    this.sql.exec("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)");
    this.sql.exec(`CREATE TABLE IF NOT EXISTS messages (
      seq INTEGER PRIMARY KEY AUTOINCREMENT,
      id TEXT NOT NULL,
      from_agent TEXT NOT NULL,
      to_agent TEXT,
      to_owner TEXT,
      reply_to TEXT,
      body TEXT NOT NULL,
      ts INTEGER NOT NULL
    )`);
    // Additive columns, for lines created before them.
    const columns = this.sql.exec("PRAGMA table_info(messages)").toArray().map((c) => c.name);
    if (!columns.includes("delivered")) this.sql.exec("ALTER TABLE messages ADD COLUMN delivered INTEGER NOT NULL DEFAULT 0");
    if (!columns.includes("blobs")) this.sql.exec("ALTER TABLE messages ADD COLUMN blobs TEXT NOT NULL DEFAULT ''");
    if (!columns.includes("mentions")) this.sql.exec("ALTER TABLE messages ADD COLUMN mentions TEXT NOT NULL DEFAULT ''");
    if (!columns.includes("sig")) this.sql.exec("ALTER TABLE messages ADD COLUMN sig TEXT NOT NULL DEFAULT ''"); // v2 message signatures
    if (!columns.includes("epoch")) this.sql.exec("ALTER TABLE messages ADD COLUMN epoch INTEGER"); // v3: the epoch a body is sealed under (0 = bootstrap channel)
    this.sql.exec("CREATE INDEX IF NOT EXISTS messages_to ON messages (to_agent, seq)");
    this.sql.exec("CREATE TABLE IF NOT EXISTS channels (name TEXT NOT NULL, owner TEXT NOT NULL, PRIMARY KEY (name, owner))");
    // Personal invites: which member holds a name, and members the admin removed.
    this.sql.exec("CREATE TABLE IF NOT EXISTS names (owner TEXT PRIMARY KEY, member TEXT NOT NULL, since INTEGER NOT NULL)");
    this.sql.exec("CREATE TABLE IF NOT EXISTS revoked (member TEXT PRIMARY KEY, at INTEGER NOT NULL)");
    // Names the admin removed: refused from then on, even for someone who never had a pass.
    this.sql.exec("CREATE TABLE IF NOT EXISTS removed_names (owner TEXT PRIMARY KEY, at INTEGER NOT NULL)");
    // Identity v2: the admin-certified enrollment, member -> (name, public key, certificate).
    this.sql.exec("CREATE TABLE IF NOT EXISTS enrolled (member TEXT PRIMARY KEY, owner TEXT NOT NULL, pubkey TEXT NOT NULL, cert TEXT NOT NULL, since INTEGER NOT NULL)");
    // Team memory.
    this.sql.exec("CREATE TABLE IF NOT EXISTS mem_files (path TEXT PRIMARY KEY, version INTEGER NOT NULL, body TEXT NOT NULL, author TEXT NOT NULL, ts INTEGER NOT NULL)");
    this.sql.exec("CREATE TABLE IF NOT EXISTS mem_versions (path TEXT NOT NULL, version INTEGER NOT NULL, body TEXT, PRIMARY KEY (path, version))");
    this.sql.exec("CREATE TABLE IF NOT EXISTS mem_commits (seq INTEGER PRIMARY KEY AUTOINCREMENT, author TEXT NOT NULL, message TEXT NOT NULL, changes TEXT NOT NULL, ts INTEGER NOT NULL)");
    // Ephemeral remote approval gates
    this.sql.exec(`CREATE TABLE IF NOT EXISTS gates (
      id TEXT PRIMARY KEY,
      token TEXT NOT NULL,
      line TEXT,
      sealed TEXT NOT NULL DEFAULT '',
      description TEXT,
      action TEXT,
      requestedBy TEXT,
      status TEXT NOT NULL,
      approver TEXT,
      createdAt INTEGER NOT NULL,
      expiresAt INTEGER NOT NULL,
      resolvedAt INTEGER
    )`);
    const gateColumns = this.sql.exec("PRAGMA table_info(gates)").toArray().map((c) => c.name);
    if (!gateColumns.includes("sealed")) this.sql.exec("ALTER TABLE gates ADD COLUMN sealed TEXT NOT NULL DEFAULT ''");
    // Cloudflare Free-Tier Quota & Usage Safety Tracking
    this.sql.exec(`CREATE TABLE IF NOT EXISTS usage_stats (
      day TEXT PRIMARY KEY,
      daily_requests INTEGER NOT NULL DEFAULT 0,
      messages_count INTEGER NOT NULL DEFAULT 0,
      blobs_bytes INTEGER NOT NULL DEFAULT 0,
      last_ts INTEGER NOT NULL
    )`);
    // Shared Authoritative Coordination Tasks (Distributed ACP)
    this.sql.exec(`CREATE TABLE IF NOT EXISTS coordination_tasks (
      seq INTEGER PRIMARY KEY AUTOINCREMENT,
      id TEXT UNIQUE NOT NULL,
      op TEXT NOT NULL,
      status TEXT NOT NULL,
      from_agent TEXT NOT NULL,
      to_agent TEXT NOT NULL,
      title TEXT NOT NULL,
      description TEXT NOT NULL DEFAULT '',
      branch TEXT NOT NULL DEFAULT '',
      contract TEXT,
      files TEXT NOT NULL DEFAULT '[]',
      evidence TEXT,
      resolution TEXT,
      rejection_reason TEXT,
      claimed_by TEXT,
      idempotency_key TEXT,
      deadline_ms INTEGER NOT NULL,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    )`);
    this.sql.exec(`CREATE TABLE IF NOT EXISTS coordination_events (
      seq INTEGER PRIMARY KEY AUTOINCREMENT,
      task_id TEXT NOT NULL,
      op TEXT NOT NULL,
      status TEXT NOT NULL,
      agent TEXT NOT NULL,
      details TEXT NOT NULL DEFAULT '{}',
      ts INTEGER NOT NULL
    )`);
    this.sql.exec("CREATE INDEX IF NOT EXISTS coord_status ON coordination_tasks (status, updated_at)");
    this.sql.exec("CREATE INDEX IF NOT EXISTS coord_idempotency ON coordination_tasks (idempotency_key)");
  }

  recordUsage(requests = 1, messages = 0, blobBytes = 0) {
    try {
      const day = new Date().toISOString().slice(0, 10);
      const now = Date.now();
      this.sql.exec(`INSERT INTO usage_stats (day, daily_requests, messages_count, blobs_bytes, last_ts)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (day) DO UPDATE SET
          daily_requests = daily_requests + excluded.daily_requests,
          messages_count = messages_count + excluded.messages_count,
          blobs_bytes = blobs_bytes + excluded.blobs_bytes,
          last_ts = excluded.last_ts`, day, requests, messages, blobBytes, now);
    } catch {}
  }

  quota() {
    const day = new Date().toISOString().slice(0, 10);
    const row = this.sql.exec("SELECT daily_requests, messages_count, blobs_bytes FROM usage_stats WHERE day = ?", day).toArray()[0] || { daily_requests: 0, messages_count: 0, blobs_bytes: 0 };
    const dailyRequests = Number(row.daily_requests || 0);
    const dailyLimit = 100_000;
    const r2Bytes = Number(row.blobs_bytes || 0);
    const r2LimitBytes = 10 * 1024 * 1024 * 1024; // 10 GB
    const warning = dailyRequests > 80_000 || r2Bytes > 8 * 1024 * 1024 * 1024;
    return {
      day,
      dailyRequests,
      dailyRequestsLimit: dailyLimit,
      requestsPercentage: Math.round((dailyRequests / dailyLimit) * 1000) / 10,
      r2StorageBytes: r2Bytes,
      r2StorageLimitBytes: r2LimitBytes,
      warning,
      status: warning ? "warning" : "healthy",
    };
  }

  // ----- channels ---------------------------------------------------------------

  isPrivateChannel(name) {
    return name.startsWith("#p-") || name.startsWith("#private-");
  }

  channels(forOwner = null) {
    const byName = new Map();
    for (const row of this.sql.exec("SELECT name, owner FROM channels ORDER BY name, owner").toArray()) {
      if (!byName.has(row.name)) byName.set(row.name, []);
      byName.get(row.name).push(row.owner);
    }
    const result = [];
    for (const [name, members] of byName) {
      if (this.isPrivateChannel(name)) {
        if (!forOwner || members.includes(forOwner)) {
          result.push({ name, members, private: true });
        }
      } else {
        result.push({ name, members, private: false });
      }
    }
    return result;
  }

  isMember(channel, owner) {
    return this.sql.exec("SELECT 1 FROM channels WHERE name = ? AND owner = ?", channel, owner).toArray().length > 0;
  }

  broadcastChannels() {
    for (const { ws, who } of this.members()) {
      const chs = this.channels(who.owner);
      this.send(ws, JSON.stringify({ t: "channels", channels: chs }));
    }
  }

  /** join | leave | add (someone else): any member manages channels, like Slack. */
  channel(ws, who, frame) {
    const name = String(frame.name || "").toLowerCase();
    const owner = String(frame.owner || who.owner).toLowerCase();
    if (!CHANNEL.test(name) || !OWNER.test(owner)) return this.send(ws, JSON.stringify({ t: "error", error: "a channel is #name (letters, digits, - or _)" }));
    if (owner !== who.owner && !this.isMember(name, who.owner)) return this.send(ws, JSON.stringify({ t: "error", error: `only people in ${name} can add or remove people there` }));

    // Private channel protection: an unauthorized user cannot self-join an existing private channel
    if (this.isPrivateChannel(name) && frame.op === "join") {
      const existingMembers = this.sql.exec("SELECT 1 FROM channels WHERE name = ?", name).toArray();
      if (existingMembers.length > 0 && !this.isMember(name, who.owner)) {
        return this.send(ws, JSON.stringify({ t: "error", error: `${name} is private: an existing member must add you` }));
      }
    }

    if (frame.op === "join" || frame.op === "add") this.sql.exec("INSERT OR IGNORE INTO channels (name, owner) VALUES (?, ?)", name, owner);
    else if (frame.op === "leave") this.sql.exec("DELETE FROM channels WHERE name = ? AND owner = ?", name, owner);
    else return this.send(ws, JSON.stringify({ t: "error", error: "channel op is join, add or leave" }));
    this.broadcastChannels();
  }

  /** Should this connected agent get this channel message? */
  hears(who, row) {
    if (!this.isMember(row.to_agent, who.owner)) return false;
    if (who.listens) return true;
    return String(row.mentions || "").includes(`,${who.owner},`) && this.choose(who.owner) === who.agent;
  }

  // ----- Chamber Cloud ---------------------------------------------------------------------

  async hostedRegister(request) {
    const { adminKey } = await request.json();
    const line = request.headers.get("x-line");
    const existing = await this.ctx.storage.get("hosted");
    if (existing) {
      if (existing.adminKey === adminKey) return json({ ok: true, claimed: true });
      return json({ error: "this chamber already exists on Chamber Cloud" }, 409);
    }
    const now = Date.now();
    const trialMs = Number(this.env.TRIAL_SECONDS) * 1000 || TRIAL_MS;
    await this.ctx.storage.put("hosted", { adminKey, createdAt: now, trialEnds: now + trialMs, status: "trial", seats: 0, paidUntil: 0 });
    await this.ctx.storage.put("adminKey", adminKey);
    await this.ctx.storage.put("line", line);
    this.key = adminKey;
    this.line = line;
    return json({ ok: true, claimed: true, trialEnds: now + trialMs });
  }

  async hostedBilling(request) {
    const body = await request.json();
    const plan = await this.ctx.storage.get("hosted");
    if (!plan) return json({ error: "no such chamber on Chamber Cloud" }, 404);
    const status = ["active", "cancelled", "enterprise"].includes(body.status) ? body.status : plan.status;
    const next = {
      ...plan,
      status,
      seats: Math.max(0, Math.floor(Number(body.seats ?? plan.seats) || 0)),
      paidUntil: Number(body.paidUntil ?? plan.paidUntil) || 0,
      account: String(body.account ?? plan.account ?? "").slice(0, 128),
    };
    await this.ctx.storage.put("hosted", next);
    return json(await this.hostedStatus());
  }

  /** Where this chamber stands on Chamber Cloud: trial, plan, seats, members, and may it be used. */
  async hostedStatus() {
    const plan = await this.ctx.storage.get("hosted");
    if (!plan) return { registered: false, access: false };
    const now = Date.now();
    const members = this.sql.exec("SELECT COUNT(*) AS n FROM names").toArray()[0].n;
    const trial = now < plan.trialEnds;
    const paid = plan.status === "enterprise" || (plan.status === "active" && plan.paidUntil + BILLING_GRACE_MS > now);
    const access = trial || paid;
    const reason = access ? "" : plan.status === "trial"
      ? `this chamber's 7-day trial has ended: your admin can subscribe (US$5 per teammate a month) at ${BILLING_URL}`
      : `this chamber's plan isn't active: your admin can renew it at ${BILLING_URL}`;
    // adminKey (public): the billing site checks that a billing link was signed by this chamber's admin.
    return { registered: true, adminKey: plan.adminKey, account: plan.account || "", createdAt: plan.createdAt, trialEnds: plan.trialEnds, trial, status: plan.status, seats: plan.seats, paidUntil: plan.paidUntil, members, access, reason, billing: BILLING_URL };
  }

  /** The admin key in force for this chamber (from the Worker; kept for hibernated wake-ups). */
  async adminKey() {
    if (this.env.ADMIN_KEY) return this.env.ADMIN_KEY;
    this.key ??= (await this.ctx.storage.get("adminKey")) ?? null;
    return this.key;
  }

  async fetch(request) {
    const what = new URL(request.url).pathname.slice(1);
    if (what === "config") {
      const c = (await this.ctx.storage.get("config")) ?? null;
      return json(c ? { ...c, claimed: true } : { adminKey: null, lines: [], claimed: false });
    }
    if (what === "config-claim") {
      if (await this.ctx.storage.get("config")) return json({ error: "this relay already belongs to a chamber" }, 409);
      const body = await request.json();
      await this.ctx.storage.put("config", { adminKey: body.adminKey, lines: body.lines, at: Date.now() });
      return json({ ok: true, claimed: true });
    }
    if (what === "hosted-register") return this.hostedRegister(request);
    if (what === "hosted-billing") return this.hostedBilling(request);
    if (what === "hosted-status") return json(await this.hostedStatus());
    if (what === "hosted-check") {
      const plan = await this.hostedStatus();
      if (!plan.registered) return json({ error: "this chamber isn't on Chamber Cloud; create it again from the app" }, 404);
      // Managing the chamber (members, invites, removal) always works, so an admin can sort out billing.
      // Connections go through too: the relay refuses them itself, with the reason (see admit).
      if (/^(admin|members|roster|ws|poll(\/\w+)?)$/.test(request.headers.get("x-what") || "")) return json({ ok: true });
      return plan.access ? json({ ok: true }) : json({ error: plan.reason, billing: BILLING_URL }, 402);
    }
    const headerKey = request.headers.get("x-admin-key");
    if (headerKey && headerKey !== this.key) {
      this.key = headerKey;
      await this.ctx.storage.put("adminKey", headerKey);
    }
    const line = request.headers.get("x-line");
    if (line && line !== this.line) {
      this.line = line;
      await this.ctx.storage.put("line", line); // the alarm may wake in a fresh instance
    }
    this.recordUsage(1);
    if (what === "quota" || what === "stats") return json(this.quota());
    if (what === "roster") return json({ agents: this.roster() });
    if (what === "members") {
      const online = new Set(this.roster().map((w) => w.owner));
      const rows = this.sql.exec("SELECT owner, member, since FROM names ORDER BY owner").toArray();
      const removed = this.sql.exec("SELECT owner, at FROM removed_names ORDER BY owner").toArray();
      const enrolled = Object.fromEntries(this.sql.exec("SELECT member, owner, pubkey, cert FROM enrolled").toArray().map((e) => [e.member, e]));
      return json({ personal: Boolean(await this.adminKey()), version: (await this.lineVersion()) || 1, members: rows.map((r) => ({ ...r, online: online.has(r.owner), pubkey: enrolled[r.member]?.pubkey || null, cert: enrolled[r.member]?.cert || null })), removed, relay: RELAY_VERSION });
    }
    if (what === "member-check") {
      const pass = request.headers.get("x-chamber-pass");
      const err = await this.memberCheck(pass);
      if (err) return json({ error: err }, 403);
      return json({ ok: true });
    }
    if (what === "admin") return this.admin(await request.json().catch(() => ({})));
    if (what === "test-fail-transition") {
      // Test-only fault injection, live solely on relays started with CHAMBER_TEST_HOOKS (the
      // test harness); on any other relay this route does nothing at all.
      if (!this.env.CHAMBER_TEST_HOOKS) return json({ error: "not found" }, 404);
      await this.ctx.storage.put("testFailTransition", true);
      return json({ ok: true });
    }
    if (what === "sync") {
      const pass = request.headers.get("x-chamber-pass");
      const refused = await this.memberCheck(pass);
      if (refused) return json({ error: refused }, 403);

      const since = Number(new URL(request.url).searchParams.get("since") || 0);
      const limit = Math.min(500, Math.max(1, Number(new URL(request.url).searchParams.get("limit") || 100)));
      const adminKey = await this.adminKey();
      let owner = null;
      if (adminKey && pass) {
        const claims = await signedByAdmin(adminKey, pass);
        if (claims?.member) {
          const row = this.sql.exec("SELECT owner FROM names WHERE member = ?", claims.member).toArray()[0];
          owner = row?.owner ?? null;
        }
      }
      if (!adminKey && !owner) {
        const asHeader = request.headers.get("x-chamber-owner") || new URL(request.url).searchParams.get("as");
        if (asHeader && OWNER.test(asHeader)) owner = asHeader.toLowerCase();
      }

      let rows;
      if (owner) {
        rows = this.sql.exec(`
          SELECT * FROM messages
          WHERE seq > ?
            AND (
              from_agent LIKE ? || '/%'
              OR to_owner = ?
              OR to_agent LIKE ? || '/%'
              OR to_agent = '*'
              OR (to_agent LIKE '#%' AND to_agent IN (SELECT name FROM channels WHERE owner = ?))
            )
          ORDER BY seq ASC LIMIT ?
        `, since, owner, owner, owner, owner, limit).toArray();
      } else {
        rows = this.sql.exec(`
          SELECT * FROM messages
          WHERE seq > ?
            AND (to_agent = '*' OR to_agent = '#general')
          ORDER BY seq ASC LIMIT ?
        `, since, limit).toArray();
      }
      const head = this.sql.exec("SELECT MAX(seq) AS max_seq FROM messages").toArray()[0]?.max_seq ?? 0;
      return json({
        messages: rows.map((r) => this.envelope(r)),
        head,
        hasMore: rows.length === limit,
      });
    }
    if (what === "restart") {
      if (request.method !== "POST") return json({ error: "method not allowed" }, 405);
      const adminKey = await this.adminKey();
      if (adminKey) {
        const order = request.headers.get("x-chamber-order");
        const pass = request.headers.get("x-chamber-pass");
        let authorized = false;
        if (order) {
          const claims = await signedByAdmin(adminKey, order);
          if (claims?.op === "restart" || claims?.admin) authorized = true;
        }
        if (!authorized && pass) {
          const claims = await signedByAdmin(adminKey, pass);
          const lineId = this.line || (await this.ctx.storage.get("line"));
          if (claims?.member && claims.line === lineId && !this.sql.exec("SELECT 1 FROM revoked WHERE member = ?", claims.member).toArray().length) {
            authorized = true;
          }
        }
        if (!authorized) return json({ error: "restart requires admin authorization" }, 403);
      }
      // After a deploy: a busy chamber keeps running the old code until it restarts. Its
      // clients reconnect at once and get the new code.
      setTimeout(() => this.ctx.abort("the relay was updated"), 50);
      return json({ ok: true, restarting: true, relay: RELAY_VERSION });
    }
    if (what.startsWith("memory")) {
      const refused = await this.memberCheck(request.headers.get("x-chamber-pass"));
      if (refused) return json({ error: refused }, 403);
      return this.memory(what, request);
    }
    if (what.startsWith("gate/") || what === "gates" || what.startsWith("gates/")) {
      return this.handleGate(what, request);
    }
    if (what === "coordination" || what.startsWith("coordination/")) {
      return this.handleCoordination(what, request);
    }
    if (what === "poll" || what.startsWith("poll/")) return this.poll(what, request);
    const pair = new WebSocketPair();
    this.ctx.acceptWebSocket(pair[1]);
    return new Response(null, { status: 101, webSocket: pair[0] });
  }

  // ----- long polling: the same conversation over plain HTTPS ----------------------------
  // For networks and hosts that refuse WebSockets (some proxies, Maritime's public URLs). A poll
  // session stands in for a socket (PollSocket below): frames from the client arrive by POST
  // poll/send, frames for it wait until its next GET poll, which holds up to POLL_WAIT_MS. A
  // session not polled for POLL_IDLE_MS has left. Sessions live in memory: if this object
  // restarts, the client opens a new one and catches up from its cursor, as after a reconnect.

  async poll(what, request) {
    this.pollers ??= new Map();
    const now = Date.now();
    for (const [id, session] of this.pollers) {
      if (now - session.seen > POLL_IDLE_MS) {
        this.pollers.delete(id);
        session.close(1001, "stopped polling");
        this.left(session);
      }
    }
    if (what === "poll/open") {
      const id = [...crypto.getRandomValues(new Uint8Array(16))].map((b) => b.toString(16).padStart(2, "0")).join("");
      this.pollers.set(id, new PollSocket(id));
      return json({ sid: id, wait: POLL_WAIT_MS });
    }
    const id = new URL(request.url).searchParams.get("sid") || "";
    const session = this.pollers.get(id);
    if (!session) return json({ error: "no such poll session; open a new one" }, 404);
    session.seen = now;
    if (what === "poll/send") {
      await this.webSocketMessage(session, await request.text());
      return json({ ok: true });
    }
    if (what === "poll/close") {
      this.pollers.delete(id);
      session.close(1000, "closed");
      this.left(session);
      return json({ ok: true });
    }
    if (!session.queue.length && !session.closed) await session.wait(POLL_WAIT_MS);
    session.seen = Date.now();
    const frames = session.queue.splice(0);
    if (session.closed) this.pollers.delete(id);
    return json({ frames, closed: session.closed });
  }

  // ----- the roster ---------------------------------------------------------

  members() {
    const out = [];
    for (const ws of [...this.ctx.getWebSockets(), ...(this.pollers?.values() ?? [])]) {
      const who = ws.deserializeAttachment();
      if (who && who.agent) out.push({ ws, who });
    }
    return out;
  }

  roster() {
    return this.members().map(({ who }) => who);
  }

  broadcastRoster() {
    const frame = JSON.stringify({ t: "roster", agents: this.roster(), ...(this._lv >= 2 ? { certs: this.enrolledCerts() } : {}) });
    for (const { ws } of this.members()) this.send(ws, frame);
  }

  /** A person's agent that should take a message for them, or null if none is online. */
  choose(owner, answering = false) {
    const theirs = this.roster().filter((who) => who.owner === owner && (!answering || who.answers));
    theirs.sort((a, b) => Number(b.answers) - Number(a.answers) || b.priority - a.priority || a.since - b.since);
    return theirs[0]?.agent ?? null;
  }

  send(ws, frame) {
    try {
      ws.send(frame);
    } catch {
      // closing; the roster update on close covers it
    }
  }

  // ----- team memory -------------------------------------------------------------------

  /** With personal invites, memory is for members only: a valid, unrevoked pass. */
  async memberCheck(pass) {
    const adminKey = await this.adminKey();
    if (!adminKey) return null;
    const claims = await signedByAdmin(adminKey, pass);
    const line = this.line || (await this.ctx.storage.get("line"));
    if (!claims || claims.line !== line || !claims.member) return "team memory is for members: a personal invite is needed";
    if (this.sql.exec("SELECT 1 FROM revoked WHERE member = ?", claims.member).toArray().length) return "you were removed from this chamber by its admin";
    return null;
  }

  async memory(what, request) {
    const url = new URL(request.url);
    if (what === "memory") {
      const head = this.sql.exec("SELECT MAX(seq) AS head FROM mem_commits").toArray()[0]?.head ?? 0;
      return json({ head, files: this.sql.exec("SELECT path, version, author, ts FROM mem_files ORDER BY path").toArray() });
    }
    if (what === "memory/file") {
      const path = url.searchParams.get("path") || "";
      const version = Number(url.searchParams.get("version") || 0);
      const row = version
        ? this.sql.exec("SELECT path, version, body FROM mem_versions WHERE path = ? AND version = ?", path, version).toArray()[0]
        : this.sql.exec("SELECT path, version, body FROM mem_files WHERE path = ?", path).toArray()[0];
      return row ? json(row) : json({ error: "no such file" }, 404);
    }
    if (what === "memory/history") {
      const limit = Math.min(200, Number(url.searchParams.get("limit") || 50));
      const rows = this.sql.exec("SELECT seq, author, message, changes, ts FROM mem_commits ORDER BY seq DESC LIMIT ?", limit).toArray();
      return json({ commits: rows.map((r) => ({ ...r, changes: JSON.parse(r.changes) })) });
    }
    // memory/commit: { author, message, changes: [{ path, base, body | null }] }, all or nothing.
    if (request.method !== "POST") return json({ error: "POST a commit" }, 405);
    const commit = await request.json().catch(() => null);
    const changes = Array.isArray(commit?.changes) ? commit.changes : [];
    if (!changes.length || changes.length > 50) return json({ error: "a commit changes 1 to 50 files" }, 400);
    for (const c of changes) {
      if (!MEMORY_PATH.test(String(c.path || "")) || String(c.path).includes("..")) return json({ error: `bad path: ${c.path}` }, 400);
      if (c.body !== null && (typeof c.body !== "string" || c.body.length > MAX_MEMORY_FILE)) return json({ error: `${c.path} is too large` }, 413);
    }
    const stale = changes
      .map((c) => ({ c, row: this.sql.exec("SELECT path, version, body FROM mem_files WHERE path = ?", c.path).toArray()[0] }))
      .filter(({ c, row }) => (row?.version ?? 0) !== Number(c.base || 0));
    if (stale.length) return json({ error: "stale", current: stale.map(({ c, row }) => row ?? { path: c.path, version: 0, body: null }) }, 409);
    const count = this.sql.exec("SELECT COUNT(*) AS n FROM mem_files").toArray()[0]?.n ?? 0;
    if (count + changes.filter((c) => c.body !== null && !Number(c.base)).length > MAX_MEMORY_FILES) return json({ error: "memory is full" }, 413);
    const now = Date.now();
    let author = String(commit.author || "").slice(0, 80);
    const adminKey = await this.adminKey();
    const pass = request.headers.get("x-chamber-pass");
    if (adminKey && pass) {
      const claims = await signedByAdmin(adminKey, pass);
      if (claims?.member) {
        const row = this.sql.exec("SELECT owner FROM names WHERE member = ?", claims.member).toArray()[0];
        if (row?.owner) author = row.owner;
      }
    }
    const applied = [];
    for (const c of changes) {
      const highest = this.sql.exec("SELECT MAX(version) AS v FROM mem_versions WHERE path = ?", c.path).toArray()[0]?.v ?? 0;
      const to = Math.max(Number(c.base || 0), highest) + 1; // a re-created file continues its history
      this.sql.exec("INSERT OR REPLACE INTO mem_versions (path, version, body) VALUES (?, ?, ?)", c.path, to, c.body);
      if (c.body === null) this.sql.exec("DELETE FROM mem_files WHERE path = ?", c.path);
      else this.sql.exec("INSERT OR REPLACE INTO mem_files (path, version, body, author, ts) VALUES (?, ?, ?, ?, ?)", c.path, to, c.body, author, now);
      applied.push({ path: c.path, from: Number(c.base || 0), to, deleted: c.body === null });
    }
    const row = this.sql.exec("INSERT INTO mem_commits (author, message, changes, ts) VALUES (?, ?, ?, ?) RETURNING seq", author, String(commit.message || "").slice(0, 500), JSON.stringify(applied), now).toArray()[0] ?? { seq: 0 };
    const frame = JSON.stringify({ t: "memory", head: row.seq, author, changes: applied });
    for (const { ws } of this.members()) this.send(ws, frame);
    return json({ ok: true, seq: row.seq, changes: applied });
  }

  // ----- the admin ----------------------------------------------------------------

  /** A signed order from the admin: { order: <token> }. Today: revoke a member by name. */
  async admin(body) {
    const claims = await signedByAdmin(await this.adminKey(), body.order);
    const line = this.line || (await this.ctx.storage.get("line"));
    if (!claims || claims.line !== line || !["revoke", "restore", "rotate"].includes(claims.op)) return json({ error: "not a valid order from this chamber's admin" }, 403);
    if (claims.op === "rotate") {
      // Advance the trusted current epoch (v3). Monotonic: replay/rollback is rejected.
      if (!Number.isInteger(claims.epoch) || claims.epoch < 1) return json({ error: "a rotation names the new epoch" }, 400);
      const current = await this.currentEpoch();
      if (claims.epoch <= current) return json({ error: `the chamber is already at epoch ${current}: rotations never roll back` }, 409);
      const testFail = this.env.CHAMBER_TEST_HOOKS && (await this.ctx.storage.get("testFailTransition"));
      if (testFail) await this.ctx.storage.delete("testFailTransition");
      try {
        this.ctx.storage.transactionSync(() => {
          this.setCurrentEpochSync(claims.epoch);
          if (testFail) throw new Error("injected transition failure");
        });
      } catch (error) {
        return json({ error: `the rotation was NOT applied (nothing changed): ${error.message}` }, 500);
      }
      return json({ ok: true, epoch: claims.epoch });
    }
    if (!OWNER.test(String(claims.owner || ""))) return json({ error: "not a valid order from this chamber's admin" }, 403);
    if (claims.op === "restore") {
      this.sql.exec("DELETE FROM removed_names WHERE owner = ?", claims.owner);
      return json({ ok: true, restored: claims.owner });
    }
    if (claims.epoch != null) {
      if (!Number.isInteger(claims.epoch) || claims.epoch < 1) return json({ error: "a rotation names the new epoch" }, 400);
      const current = await this.currentEpoch();
      if (claims.epoch <= current) return json({ error: `the chamber is already at epoch ${current}: rotations never roll back` }, 409);
    }
    // ONE Durable Object storage transaction: the revocation rows and the epoch advance commit
    // together or not at all - there is no "revoked but not rotated" (a revoked holder's retained
    // key gaining NEW old-epoch ciphertext) and no "rotated but not revoked" partial state, not
    // even across a crash mid-handler. A failed order leaves the EXACT old consistent state and
    // is retried as-is (idempotent: a fully-applied order answers 409 naming the current epoch).
    const testFail = this.env.CHAMBER_TEST_HOOKS && (await this.ctx.storage.get("testFailTransition"));
    if (testFail) await this.ctx.storage.delete("testFailTransition"); // consumed once, before the transaction
    try {
      this.ctx.storage.transactionSync(() => {
        this.sql.exec("INSERT OR REPLACE INTO removed_names (owner, at) VALUES (?, ?)", claims.owner, Date.now());
        const row = this.sql.exec("SELECT member FROM names WHERE owner = ?", claims.owner).toArray()[0];
        if (row) {
          this.sql.exec("INSERT OR IGNORE INTO revoked (member, at) VALUES (?, ?)", row.member, Date.now());
          this.sql.exec("DELETE FROM enrolled WHERE member = ?", row.member); // its signatures stop verifying at the relay from now on
        }
        this.sql.exec("DELETE FROM names WHERE owner = ?", claims.owner);
        this.sql.exec("DELETE FROM channels WHERE owner = ?", claims.owner);
        if (claims.epoch != null) this.setCurrentEpochSync(claims.epoch);
        // Test-only fault injection (relay started with CHAMBER_TEST_HOOKS): fail inside the
        // transaction so tests prove the rollback leaves NOTHING applied, not just the happy path.
        if (testFail) throw new Error("injected transition failure");
      });
    } catch (error) {
      return json({ error: `the order was NOT applied (nothing changed): ${error.message}` }, 500);
    }
    // Committed: now the non-storage effects (dropping sockets, refreshing rosters).
    for (const { ws, who } of this.members()) {
      if (who.owner === claims.owner) {
        ws.serializeAttachment(null);
        try {
          ws.close(4003, "removed from the chamber by its admin");
        } catch {}
      }
    }
    this.broadcastRoster();
    this.broadcastChannels();
    return json({ ok: true, revoked: claims.owner, ...(claims.epoch != null ? { epoch: claims.epoch } : {}) });
  }

  /** The line's trusted current epoch (v3): 0 until the admin's first rotation order. Durable
   *  (Durable Object storage, like the line version): a relay restart does not forget it. */
  async currentEpoch() {
    // SQL is the store of record and there is NO JS cache: a rolled-back transaction must never
    // leave a phantom epoch behind (a cache the rollback cannot undo is exactly that bug). The
    // read is one local SQLite lookup, cheap enough to run uncached. A relay persisted before
    // this change still holds the epoch in KV: adopt it as the fallback.
    const row = this.sql.exec("SELECT v FROM meta WHERE k = 'currentEpoch'").toArray()[0];
    return row ? Number(row.v) : Number((await this.ctx.storage.get("currentEpoch")) || 0);
  }

  async setCurrentEpoch(epoch) {
    this.setCurrentEpochSync(epoch);
  }

  /** Synchronous SQL write only: safe inside the atomic revoke+rotate transaction. */
  setCurrentEpochSync(epoch) {
    this.sql.exec("INSERT OR REPLACE INTO meta (k, v) VALUES ('currentEpoch', ?)", String(epoch));
  }

  /** The line's protocol version: fixed at the FIRST admit, never changed afterwards (no silent downgrade). */
  async lineVersion() {
    if (this._lv == null) this._lv = Number((await this.ctx.storage.get("lineVersion")) || 0);
    return this._lv;
  }

  async setLineVersion(version) {
    this._lv = version;
    await this.ctx.storage.put("lineVersion", version);
  }

  /** owner -> the admin-signed certificate of every enrolled member (clients verify the chain themselves). */
  enrolledCerts() {
    const out = {};
    for (const row of this.sql.exec("SELECT n.owner AS owner, e.cert AS cert FROM names n JOIN enrolled e ON e.member = n.member").toArray()) out[row.owner] = row.cert;
    return out;
  }

  /** May this hello use this name? With personal invites: a valid pass, not revoked, and the name is free or theirs.
   *  Returns null (open relay), an error string, or { member, pubkey, cert, version } for the admitted member. */
  async admit(owner, pass) {
    const adminKey = await this.adminKey();
    if (!adminKey) return null;
    if (hosted(this.env)) {
      const plan = await this.hostedStatus();
      if (!plan.access) return plan.reason;
    }
    const claims = await signedByAdmin(adminKey, pass);
    const line = this.line || (await this.ctx.storage.get("line"));
    if (!claims || claims.line !== line || !claims.member) return "this chamber needs a personal invite: ask its admin for one";
    // The protocol version is fixed at the first admit: a v2/v3 line never downgrades to lower
    // semantics silently, and a v1 line never pretends v2/v3 properties.
    const wantsV2 = claims.v === 2;
    const wantsV3 = claims.v === 3;
    let version = await this.lineVersion();
    if (version === 3 && !wantsV3) return "this chamber needs chamber >= v3: ask its admin for a v3 certificate (older passes and v2 certificates do not work here)";
    if (version === 2 && !wantsV2 && !wantsV3) return "this chamber needs chamber >= v2: ask its admin for a v2 certificate (the old passes do not work here)";
    if (version === 2 && wantsV3) return "this chamber is v2 but your certificate is v3: ask its admin for a v2 certificate";
    if (!version) {
      version = wantsV3 ? 3 : wantsV2 ? 2 : 1;
      await this.setLineVersion(version);
    }
    if (this.sql.exec("SELECT 1 FROM revoked WHERE member = ?", claims.member).toArray().length) return "you were removed from this chamber by its admin";
    if (this.sql.exec("SELECT 1 FROM removed_names WHERE owner = ?", owner).toArray().length) return `the name ${owner} was removed from this chamber by its admin`;
    if ((wantsV2 && version === 2) || (wantsV3 && version === 3)) {
      // A v2/v3 certificate binds this member to ONE name and one public key, both signed by the
      // admin; a v3 certificate additionally binds the X25519 encryption key epoch keys wrap to.
      if (!claims.owner || claims.owner !== owner) return `this certificate is for ${claims.owner || "another name"}, not ${owner}`;
      if (!PUBKEY.test(String(claims.pubkey || ""))) return `this v${claims.v} certificate carries no valid public key`;
      if (wantsV3 && !PUBKEY.test(String(claims.enc || ""))) return "this v3 certificate carries no valid encryption key";
      const enrolled = this.sql.exec("SELECT owner, pubkey FROM enrolled WHERE member = ?", claims.member).toArray()[0];
      if (enrolled && (enrolled.owner !== claims.owner || enrolled.pubkey !== claims.pubkey)) return "this certificate does not match the key this member enrolled with";
    }
    const holder = this.sql.exec("SELECT member FROM names WHERE owner = ?", owner).toArray()[0];
    if (holder && holder.member !== claims.member) return `the name ${owner} belongs to someone else in this chamber; pick another (chamber name <new>)`;
    if (!holder && hosted(this.env)) {
      const plan = await this.hostedStatus();
      const isNewMember = !this.sql.exec("SELECT 1 FROM names WHERE member = ?", claims.member).toArray().length;
      if (plan.status === "active" && isNewMember && plan.members >= plan.seats) {
        return `this chamber's plan has ${plan.seats} seat${plan.seats === 1 ? "" : "s"}, all taken: ask your admin to add one at ${BILLING_URL}`;
      }
    }
    if (!holder) {
      this.sql.exec("DELETE FROM names WHERE member = ?", claims.member); // a member holds one name: renaming frees the old one
      this.sql.exec("INSERT INTO names (owner, member, since) VALUES (?, ?, ?)", owner, claims.member, Date.now());
    }
    if ((wantsV2 && version === 2) || (wantsV3 && version === 3)) {
      this.sql.exec("INSERT OR IGNORE INTO enrolled (member, owner, pubkey, cert, since) VALUES (?, ?, ?, ?, ?)", claims.member, claims.owner, claims.pubkey, pass, Date.now());
      return { member: claims.member, pubkey: claims.pubkey, version };
    }
    return { member: claims.member, pubkey: null, version: 1 };
  }

  // ----- frames ---------------------------------------------------------------

  async webSocketMessage(ws, raw) {
    let frame;
    try {
      frame = JSON.parse(typeof raw === "string" ? raw : new TextDecoder().decode(raw));
    } catch {
      return this.send(ws, JSON.stringify({ t: "error", error: "frames are JSON" }));
    }
    const who = ws.deserializeAttachment();
    if (frame.t === "hello") return await this.hello(ws, frame);
    if (!who || !who.agent) return this.send(ws, JSON.stringify({ t: "error", error: "say hello first" }));
    if (frame.t === "send") return await this.deliver(ws, who, frame);
    if (frame.t === "channel") return this.channel(ws, who, frame);
    if (frame.t === "ping") return this.send(ws, JSON.stringify({ t: "pong" }));
    this.send(ws, JSON.stringify({ t: "error", error: `unknown frame ${frame.t}` }));
  }

  async hello(ws, frame) {
    const agent = String(frame.agent || "").toLowerCase();
    if (!AGENT.test(agent)) return this.send(ws, JSON.stringify({ t: "error", error: "agent must be owner/name" }));
    const admitted = await this.admit(agent.split("/")[0], frame.pass);
    if (typeof admitted === "string") {
      this.send(ws, JSON.stringify({ t: "error", error: admitted }));
      try {
        ws.close(4003, admitted.slice(0, 120));
      } catch {}
      return;
    }
    for (const { ws: other, who } of this.members()) {
      if (who.agent === agent && other !== ws) {
        other.serializeAttachment(null); // off the roster now, even if the old side never answers the close
        try {
          other.close(4001, "replaced by a newer connection");
        } catch {}
      }
    }
    const who = {
      agent,
      owner: agent.split("/")[0],
      member: admitted?.member || null, // personal invites / v2 enrollment
      pubkey: admitted?.pubkey || null, // v2: the enrolled key this socket may sign with
      kind: String(frame.kind || "agent").slice(0, 32),
      label: String(frame.label || "").slice(0, 120),
      priority: Number.isFinite(frame.priority) ? frame.priority : 0,
      answers: Boolean(frame.answers),
      listens: Boolean(frame.listens), // gets all of its channels' traffic (a board, root), not only @mentions
      since: Date.now(),
    };
    ws.serializeAttachment(who);
    this.sql.exec("INSERT OR IGNORE INTO channels (name, owner) VALUES ('#general', ?)", who.owner);
    const version = await this.lineVersion();
    this.send(ws, JSON.stringify({ t: "welcome", agent, version, agents: this.roster(), channels: this.channels(), ...(version >= 2 ? { certs: this.enrolledCerts() } : {}) }));

    // What it missed: messages to it (or to everyone) after its cursor…
    const since = Number.isFinite(frame.since) ? frame.since : 0;
    const missed = this.sql
      .exec("SELECT * FROM messages WHERE seq > ? AND from_agent != ? AND (to_agent = ? OR to_agent = '*') ORDER BY seq", since, agent, agent)
      .toArray();
    // …and its channels' messages (all of them if it listens, else the ones @mentioning its owner).
    const heard = this.sql
      .exec("SELECT * FROM messages WHERE seq > ? AND from_agent != ? AND to_agent LIKE '#%' ORDER BY seq", since, agent)
      .toArray()
      .filter((row) => this.hears(who, row));
    missed.push(...heard);
    missed.sort((a, b) => a.seq - b.seq);
    // …and messages for its owner that waited because none of theirs was online.
    // A message to a person waits for one of their agents that answers; their board (a seat
    // that listens) gets a copy to show, without taking it from the agent.
    if (who.answers && this.choose(who.owner, true) === agent) {
      const held = this.sql.exec("SELECT * FROM messages WHERE to_agent IS NULL AND to_owner = ? ORDER BY seq", who.owner).toArray();
      if (held.length) {
        this.sql.exec("UPDATE messages SET to_agent = ? WHERE to_agent IS NULL AND to_owner = ?", agent, who.owner);
        missed.push(...held.map((row) => ({ ...row, to_agent: agent })));
      }
    } else if (who.listens) {
      const seen = new Set(missed.map((r) => r.seq));
      const copies = this.sql.exec("SELECT * FROM messages WHERE to_owner = ? AND seq > ? AND from_agent != ? ORDER BY seq", who.owner, since, agent).toArray();
      missed.push(...copies.filter((r) => !seen.has(r.seq)));
    }
    missed.sort((a, b) => a.seq - b.seq);
    for (const row of missed) this.send(ws, JSON.stringify(this.envelope(row)));
    // A board that was away: knocks for what still waits for its owner's agents that aren't connected.
    if (who.listens) {
      const online = new Set(this.roster().map((w) => w.agent));
      const waiting = this.sql
        .exec("SELECT id, from_agent, to_agent, ts FROM messages WHERE delivered = 0 AND to_agent LIKE ? AND ts > ? ORDER BY seq DESC LIMIT 50", `${who.owner}/%`, Date.now() - KNOCK_WINDOW_MS)
        .toArray()
        .filter((row) => AGENT.test(row.to_agent) && !online.has(row.to_agent));
      for (const row of waiting.reverse()) this.send(ws, JSON.stringify({ t: "knock", id: row.id, from: row.from_agent, to: row.to_agent, ts: row.ts }));
    }
    if (missed.length) this.sql.exec(`UPDATE messages SET delivered = 1 WHERE seq IN (${missed.map((r) => Number(r.seq)).join(",")})`);
    this.ctx.storage.deleteAlarm();
    this.broadcastRoster();
  }

  async deliver(ws, who, frame) {
    const id = String(frame.id || "");
    const replyTo = frame.reply_to ? String(frame.reply_to) : null;
    if (!MSG_ID.test(id)) return this.send(ws, JSON.stringify({ t: "error", error: "message id must be 32 hex chars", id }));
    if (replyTo && !MSG_ID.test(replyTo)) return this.send(ws, JSON.stringify({ t: "error", error: "reply_to must be 32 hex chars", id }));
    const to = String(frame.to || "").toLowerCase();
    const body = String(frame.body || "");
    if (!body) return this.send(ws, JSON.stringify({ t: "error", error: "a message needs an id and a body", id }));
    if (body.length > MAX_BODY) return this.send(ws, JSON.stringify({ t: "error", error: "message too large", id }));
    let toAgent = null;
    let toOwner = null;
    const mentions = (Array.isArray(frame.mentions) ? frame.mentions : []).map((m) => String(m).toLowerCase()).filter((m) => OWNER.test(m)).slice(0, 50);
    if (to === "*") toAgent = "*";
    else if (CHANNEL.test(to)) {
      if (!this.isMember(to, who.owner)) return this.send(ws, JSON.stringify({ t: "error", error: `join ${to} first`, id }));
      toAgent = to;
    } else if (to.startsWith("@") && AGENT.test(`${to.slice(1)}/x`)) {
      toOwner = to.slice(1);
      toAgent = this.choose(toOwner, true); // null: held until one of their agents that answers connects
    } else if (AGENT.test(to)) toAgent = to;
    else return this.send(ws, JSON.stringify({ t: "error", error: "to must be owner/name, @owner, #channel or *", id }));

    const now = Date.now();
    const version = await this.lineVersion();
    let sig = "";
    let ts = now;
    let epoch = null;
    if (version >= 2) {
      // Identity v2/v3: the envelope must be signed by the sender's ENROLLED key over
      // (line, from, to, id, reply_to, ts, [epoch,] sha256(body)); the relay verifies before
      // storing or forwarding, and clients verify again on receipt. Replays (same id) and stale
      // timestamps are rejected; a revoked member's socket is dropped even if it was still
      // connected. v3 messages also carry their epoch, committed by the signature.
      if (!who.pubkey) return this.send(ws, JSON.stringify({ t: "error", error: `this chamber needs chamber >= v${version}: reconnect with a certificate`, id }));
      ts = Number(frame.ts);
      if (!Number.isFinite(ts) || Math.abs(now - ts) > V2_CLOCK_SKEW_MS) return this.send(ws, JSON.stringify({ t: "error", error: "the message timestamp is outside the acceptance window", id }));
      if (this.sql.exec("SELECT 1 FROM messages WHERE id = ?", id).toArray().length) return this.send(ws, JSON.stringify({ t: "error", error: "that message id was already used", id }));
      if (this.sql.exec("SELECT 1 FROM revoked WHERE member = ?", who.member).toArray().length) {
        this.send(ws, JSON.stringify({ t: "error", error: "you were removed from this chamber by its admin", id }));
        try {
          ws.close(4003, "removed from the chamber by its admin");
        } catch {}
        return;
      }
      sig = String(frame.sig || "");
      const line = this.line || (await this.ctx.storage.get("line"));
      const hash = await sha256hex(body);
      if (version === 3) {
        epoch = Number(frame.epoch);
        if (!Number.isInteger(epoch) || epoch < 0) return this.send(ws, JSON.stringify({ t: "error", error: "a v3 message needs its epoch (0 is the bootstrap channel)", id }));
        const current = await this.currentEpoch();
        if (epoch > 0 && epoch !== current) {
          return this.send(ws, JSON.stringify({ t: "error", error: epoch < current ? `stale epoch ${epoch}: the chamber is at epoch ${current} - sync and resend under the current epoch` : `epoch ${epoch} is ahead of the chamber's epoch ${current}`, id }));
        }
      }
      if (!(await verifyEnvelopeSig(who.pubkey, { line, from: who.agent, to, id, replyTo: replyTo || "", ts, hash, epoch: epoch || 0 }, sig))) {
        return this.send(ws, JSON.stringify({ t: "error", error: "bad message signature", id }));
      }
    }
    const { keepMs, maxRows } = retention(this.env);
    if (keepMs) this.sql.exec("DELETE FROM messages WHERE ts < ?", now - keepMs);
    if (maxRows) this.sql.exec("DELETE FROM messages WHERE seq <= (SELECT MAX(seq) FROM messages) - ?", maxRows);
    const blobIds = (Array.isArray(frame.blobs) ? frame.blobs : []).map(String).filter((b) => BLOB.test(b)).slice(0, 20);
    const row = this.sql
      .exec(
        "INSERT INTO messages (id, from_agent, to_agent, to_owner, reply_to, body, ts, blobs, mentions, sig, epoch) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *",
        id,
        who.agent,
        toAgent,
        toOwner,
        frame.reply_to ? String(frame.reply_to).slice(0, 64) : null,
        body,
        ts,
        blobIds.join(","),
        mentions.length ? `,${mentions.join(",")},` : "",
        sig,
        epoch,
      )
      .toArray()[0];
    const envelope = JSON.stringify(this.envelope(row));
    let delivered = [];
    for (const { ws: other, who: them } of this.members()) {
      if (other === ws) continue;
      const copy = toOwner && them.owner === toOwner && them.listens; // their board sees messages to them
      if (toAgent === "*" || them.agent === toAgent || copy || (toAgent?.startsWith("#") && this.hears(them, row))) {
        this.send(other, envelope);
        delivered.push(them.agent);
      }
    }
    // An agent that is not connected can't read this: knock on its owner's board, which may wake it.
    if (toAgent && AGENT.test(toAgent) && !delivered.includes(toAgent)) this.knock(toAgent, [row], ws);
    const waiting = Boolean(toOwner && !toAgent); // only a board copy so far: the person's agents haven't got it
    if (delivered.length && !waiting) this.sql.exec("UPDATE messages SET delivered = 1 WHERE seq = ?", row.seq);
    this.send(ws, JSON.stringify({ t: "sent", id, seq: row.seq, to: toAgent, delivered, held: waiting || delivered.length === 0 }));
  }

  /** Tell `agent`'s owner's boards (seats that listen) that messages wait for it. No content: only who and when. */
  knock(agent, rows, except = null) {
    const owner = agent.split("/")[0];
    for (const { ws, who } of this.members()) {
      if (ws === except || who.owner !== owner || !who.listens) continue;
      for (const row of rows) this.send(ws, JSON.stringify({ t: "knock", id: row.id, from: row.from_agent, to: agent, ts: row.ts }));
    }
  }

  envelope(row) {
    return {
      t: "msg",
      seq: row.seq,
      id: row.id,
      from: row.from_agent,
      to: row.to_agent,
      to_owner: row.to_owner,
      reply_to: row.reply_to,
      body: row.body,
      ts: row.ts,
      mentions: String(row.mentions || "").split(",").filter(Boolean),
      ...(row.sig ? { sig: row.sig } : {}),
      ...(row.epoch != null ? { epoch: row.epoch } : {}),
    };
  }

  async handleGate(what, request) {
    const parts = what.replace(/^v1\//, "").split("/").filter(Boolean);
    const gateId = (parts[0] === "gates" || parts[0] === "gate") ? (parts[1] || "") : parts[0];
    const sub = (parts[0] === "gates" || parts[0] === "gate") ? (parts[2] || "") : (parts[1] || "");
    const url = new URL(request.url);
    const token = url.searchParams.get("token") || request.headers.get("x-gate-token") || "";

    // Registration: POST /v1/lines/:line/gates or POST /gates or POST /gate/:id
    if (request.method === "POST" && (!gateId || sub === "register" || !sub && request.headers.get("content-type")?.includes("application/json"))) {
      const body = await request.clone().json().catch(() => ({}));
      const id = gateId || body.id;
      if (id && (body.sealed || body.action || body.description)) {
        const row = this.sql.exec("SELECT * FROM gates WHERE id = ?", id).toArray()[0] ?? null;
        if (row) {
          if (body.token && row.token !== body.token) return json({ error: "gate already exists" }, 409);
          return json({ ok: true, id, status: row.status });
        }
        const gateToken = body.token || token;
        if (!gateToken) return json({ error: "gate token is required" }, 400);
        const expiresAt = body.expiresAt || (Date.now() + (Number(body.expiresMinutes) || 60) * 60 * 1000);
        this.sql.exec(
          `INSERT INTO gates (id, token, line, sealed, description, action, requestedBy, status, createdAt, expiresAt)
           VALUES (?, ?, ?, ?, '', '', '', 'pending', ?, ?)`,
          id,
          gateToken,
          body.line || this.line || "",
          body.sealed || "",
          Date.now(),
          expiresAt
        );
        return json({ ok: true, id, status: "pending" });
      }
    }

    // List gates: GET /gates
    if (!gateId && request.method === "GET") {
      const rows = this.sql.exec("SELECT id, line, sealed, description, action, requestedBy, status, approver, createdAt, expiresAt, resolvedAt FROM gates ORDER BY createdAt DESC LIMIT 50").toArray();
      return json({ gates: rows });
    }

    let gate = this.sql.exec("SELECT * FROM gates WHERE id = ?", gateId).toArray()[0] ?? null;
    if (!gate) {
      if (request.headers.get("accept")?.includes("text/html")) {
        return new Response(gateHtml({ id: gateId, status: "not_found" }), {
          status: 404,
          headers: { "content-type": "text/html; charset=utf-8" },
        });
      }
      return json({ error: "gate not found" }, 404);
    }

    let reqToken = token;
    let decision = "";
    let approver = "mobile-approver";

    if (request.method === "POST") {
      const contentType = request.headers.get("content-type") || "";
      if (contentType.includes("application/json")) {
        const body = await request.json().catch(() => ({}));
        decision = body.decision;
        if (body.token) reqToken ||= body.token;
        if (body.approver) approver = body.approver;
      } else {
        const text = await request.text();
        const params = new URLSearchParams(text);
        decision = params.get("decision") || "";
        if (params.get("token")) reqToken ||= params.get("token");
        if (params.get("approver")) approver = params.get("approver");
      }
    }

    if (!gate.token || reqToken !== gate.token) {
      if (request.headers.get("accept")?.includes("text/html")) {
        return new Response(gateUnauthorizedHtml(), {
          status: 401,
          headers: { "content-type": "text/html; charset=utf-8" },
        });
      }
      return json({ error: "unauthorized: invalid gate token" }, 401);
    }

    if (gate.status === "pending" && Date.now() > gate.expiresAt) {
      this.sql.exec("UPDATE gates SET status = 'expired' WHERE id = ?", gateId);
      gate.status = "expired";
    }

    if (request.method === "POST" && (decision === "approve" || decision === "reject")) {
      if (gate.status === "pending") {
        const newStatus = decision === "approve" ? "approved" : "rejected";
        this.sql.exec(
          "UPDATE gates SET status = ?, approver = ?, resolvedAt = ? WHERE id = ?",
          newStatus,
          approver,
          Date.now(),
          gateId
        );
        gate = this.sql.exec("SELECT * FROM gates WHERE id = ?", gateId).toArray()[0] ?? null;
      }
      if (request.headers.get("accept")?.includes("application/json") || url.searchParams.get("format") === "json") {
        return json({ ok: true, gate });
      }
      return new Response(gateHtml({ ...gate, justResolved: true }), {
        headers: { "content-type": "text/html; charset=utf-8" },
      });
    }

    // GET status check or HTML
    if (sub === "status" || request.headers.get("accept")?.includes("application/json") || url.searchParams.get("format") === "json") {
      return json({ ok: true, gate });
    }

    return new Response(gateHtml(gate), {
      headers: { "content-type": "text/html; charset=utf-8" },
    });
  }

  // ----- shared coordination (Distributed ACP) --------------------------------

  formatCoordTask(r) {
    let files = [];
    let evidence = null;
    try { files = JSON.parse(r.files || "[]"); } catch {}
    try { evidence = r.evidence ? JSON.parse(r.evidence) : null; } catch {}
    return {
      seq: r.seq,
      id: r.id,
      op: r.op,
      status: r.status,
      from: r.from_agent,
      to: r.to_agent,
      title: r.title,
      description: r.description || "",
      branch: r.branch || "",
      contract: r.contract || null,
      files,
      evidence,
      resolution: r.resolution || null,
      rejectionReason: r.rejection_reason || null,
      claimedBy: r.claimed_by || null,
      idempotencyKey: r.idempotency_key || null,
      deadlineMs: Number(r.deadline_ms),
      createdAt: Number(r.created_at),
      updatedAt: Number(r.updated_at),
    };
  }

  broadcastCoordination(action, task) {
    const frame = JSON.stringify({ t: "coord", action, task });
    for (const { ws } of this.members()) this.send(ws, frame);
  }

  async handleCoordination(what, request) {
    const url = new URL(request.url);
    const sub = what.slice("coordination".length).replace(/^\/+/, "");
    const now = Date.now();

    // Enforce deadline expiry automatically for active tasks whose deadline has passed
    this.sql.exec(`
      UPDATE coordination_tasks
      SET status = 'timed_out', updated_at = ?
      WHERE deadline_ms < ? AND status NOT IN ('resolved', 'rejected', 'timed_out')
    `, now, now);

    if (request.method === "GET") {
      if (!sub || sub === "list") {
        const status = url.searchParams.get("status");
        const op = url.searchParams.get("op");
        const to = url.searchParams.get("to");
        const from = url.searchParams.get("from");
        const since = Number(url.searchParams.get("since") || 0);

        let query = "SELECT * FROM coordination_tasks WHERE seq > ?";
        const params = [since];
        if (status) { query += " AND status = ?"; params.push(status); }
        if (op) { query += " AND op = ?"; params.push(op); }
        if (to) { query += " AND (to_agent = ? OR to_agent = ?)"; params.push(to, `@${to.replace(/^@/, "")}`); }
        if (from) { query += " AND (from_agent = ? OR from_agent = ?)"; params.push(from, `@${from.replace(/^@/, "")}`); }
        query += " ORDER BY updated_at DESC LIMIT 100";

        const tasks = this.sql.exec(query, ...params).toArray().map((r) => this.formatCoordTask(r));
        const maxSeq = this.sql.exec("SELECT MAX(seq) AS max_seq FROM coordination_tasks").toArray()[0]?.max_seq ?? 0;
        return json({ tasks, maxSeq });
      }

      const task = this.sql.exec("SELECT * FROM coordination_tasks WHERE id = ?", sub).toArray()[0];
      if (!task) return json({ error: "coordination task not found" }, 404);
      return json({ task: this.formatCoordTask(task) });
    }

    if (request.method === "POST") {
      let body;
      try {
        body = await request.json();
      } catch {
        return json({ error: "invalid json body" }, 400);
      }

      const action = sub || body.action || "request";

      if (action === "request") {
        const { id, op = "ask", from, to, title, description = "", branch = "", contract = null, files = [], deadlineMs, idempotencyKey } = body;
        if (!from || !to) return json({ error: "from and to addresses are required" }, 400);
        if (!title) return json({ error: "title is required" }, 400);

        const taskId = id || crypto.randomUUID().replace(/-/g, "");
        const ik = idempotencyKey || taskId;

        // Idempotency: return existing canonical task if already submitted
        const existing = this.sql.exec("SELECT * FROM coordination_tasks WHERE id = ? OR (idempotency_key IS NOT NULL AND idempotency_key = ?)", taskId, ik).toArray()[0];
        if (existing) {
          return json({ task: this.formatCoordTask(existing), alreadyExists: true });
        }

        const deadline = Number(deadlineMs) || (now + 30 * 60 * 1000);
        const filesJson = JSON.stringify(Array.isArray(files) ? files : []);

        this.sql.exec(`
          INSERT INTO coordination_tasks (
            id, op, status, from_agent, to_agent, title, description, branch, contract, files, idempotency_key, deadline_ms, created_at, updated_at
          ) VALUES (?, ?, 'requested', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        `, taskId, op, from, to, title, description, branch, contract, filesJson, ik, deadline, now, now);

        this.sql.exec(`
          INSERT INTO coordination_events (task_id, op, status, agent, details, ts)
          VALUES (?, ?, 'requested', ?, ?, ?)
        `, taskId, op, from, JSON.stringify({ title, branch, contract }), now);

        const created = this.sql.exec("SELECT * FROM coordination_tasks WHERE id = ?", taskId).toArray()[0];
        const formatted = this.formatCoordTask(created);
        this.broadcastCoordination("request", formatted);
        return json({ task: formatted, alreadyExists: false }, 201);
      }

      const taskId = body.id || sub;
      const task = this.sql.exec("SELECT * FROM coordination_tasks WHERE id = ?", taskId).toArray()[0];
      if (!task) return json({ error: `coordination task "${taskId}" not found` }, 404);

      if (action === "claim") {
        const agent = String(body.agent || "");
        if (!agent) return json({ error: "agent is required to claim task" }, 400);
        if (task.status === "resolved") return json({ error: `task "${taskId}" is already resolved` }, 400);
        if (task.status === "rejected") return json({ error: `task "${taskId}" is rejected` }, 400);
        if (task.status === "timed_out") return json({ error: `task "${taskId}" has timed out` }, 400);

        this.sql.exec(`
          UPDATE coordination_tasks
          SET status = 'claimed', claimed_by = ?, updated_at = ?
          WHERE id = ?
        `, agent, now, taskId);

        this.sql.exec(`
          INSERT INTO coordination_events (task_id, op, status, agent, details, ts)
          VALUES (?, ?, 'claimed', ?, '{}', ?)
        `, taskId, task.op, agent, now);

        const updated = this.sql.exec("SELECT * FROM coordination_tasks WHERE id = ?", taskId).toArray()[0];
        const formatted = this.formatCoordTask(updated);
        this.broadcastCoordination("claim", formatted);
        return json({ task: formatted });
      }

      if (action === "start" || action === "in_progress") {
        const agent = String(body.agent || task.claimed_by || "");
        if (task.status === "resolved" || task.status === "rejected" || task.status === "timed_out") {
          return json({ error: `cannot start terminal task "${taskId}" (status: ${task.status})` }, 400);
        }

        this.sql.exec(`
          UPDATE coordination_tasks
          SET status = 'in_progress', claimed_by = COALESCE(?, claimed_by), updated_at = ?
          WHERE id = ?
        `, agent || null, now, taskId);

        this.sql.exec(`
          INSERT INTO coordination_events (task_id, op, status, agent, details, ts)
          VALUES (?, ?, 'in_progress', ?, '{}', ?)
        `, taskId, task.op, agent || task.from_agent, now);

        const updated = this.sql.exec("SELECT * FROM coordination_tasks WHERE id = ?", taskId).toArray()[0];
        const formatted = this.formatCoordTask(updated);
        this.broadcastCoordination("start", formatted);
        return json({ task: formatted });
      }

      if (action === "resolve") {
        const by = String(body.by || "");
        const resolution = String(body.resolution || "Resolved");
        const evidenceJson = body.evidence ? JSON.stringify(body.evidence) : null;
        const contract = body.contract || null;

        if (task.status === "resolved") {
          return json({ task: this.formatCoordTask(task), alreadyResolved: true });
        }
        if (task.status === "rejected" || task.status === "timed_out") {
          return json({ error: `cannot resolve task "${taskId}" in status ${task.status}` }, 400);
        }

        this.sql.exec(`
          UPDATE coordination_tasks
          SET status = 'resolved', resolution = ?, evidence = ?, contract = COALESCE(?, contract), updated_at = ?
          WHERE id = ?
        `, resolution, evidenceJson, contract, now, taskId);

        this.sql.exec(`
          INSERT INTO coordination_events (task_id, op, status, agent, details, ts)
          VALUES (?, ?, 'resolved', ?, ?, ?)
        `, taskId, task.op, by, JSON.stringify({ resolution, contract }), now);

        const updated = this.sql.exec("SELECT * FROM coordination_tasks WHERE id = ?", taskId).toArray()[0];
        const formatted = this.formatCoordTask(updated);
        this.broadcastCoordination("resolve", formatted);
        return json({ task: formatted, alreadyResolved: false });
      }

      if (action === "reject") {
        const by = String(body.by || "");
        const reason = String(body.reason || "Declined by agent");

        if (task.status === "resolved") {
          return json({ error: `cannot reject already resolved task "${taskId}"` }, 400);
        }

        this.sql.exec(`
          UPDATE coordination_tasks
          SET status = 'rejected', rejection_reason = ?, updated_at = ?
          WHERE id = ?
        `, reason, now, taskId);

        this.sql.exec(`
          INSERT INTO coordination_events (task_id, op, status, agent, details, ts)
          VALUES (?, ?, 'rejected', ?, ?, ?)
        `, taskId, task.op, by, JSON.stringify({ reason }), now);

        const updated = this.sql.exec("SELECT * FROM coordination_tasks WHERE id = ?", taskId).toArray()[0];
        const formatted = this.formatCoordTask(updated);
        this.broadcastCoordination("reject", formatted);
        return json({ task: formatted });
      }

      if (action === "handoff") {
        const fromAgent = String(body.fromAgent || task.claimed_by || task.to_agent);
        const toAgent = String(body.toAgent || "");
        const summary = String(body.summary || "");
        const nextStep = String(body.nextStep || "");
        const newFiles = Array.isArray(body.files) ? body.files : [];
        if (!toAgent) return json({ error: "toAgent is required for handoff" }, 400);

        let existingFiles = [];
        try { existingFiles = JSON.parse(task.files || "[]"); } catch {}
        const mergedFiles = [...new Set([...existingFiles, ...newFiles])];
        const newDesc = `${summary}\n\nNext step: ${nextStep}`;

        this.sql.exec(`
          UPDATE coordination_tasks
          SET op = 'handoff', status = 'requested', from_agent = ?, to_agent = ?, claimed_by = NULL,
              description = ?, files = ?, updated_at = ?
          WHERE id = ?
        `, fromAgent, toAgent, newDesc, JSON.stringify(mergedFiles), now, taskId);

        this.sql.exec(`
          INSERT INTO coordination_events (task_id, op, status, agent, details, ts)
          VALUES (?, 'handoff', 'requested', ?, ?, ?)
        `, taskId, fromAgent, JSON.stringify({ toAgent, summary, nextStep }), now);

        const updated = this.sql.exec("SELECT * FROM coordination_tasks WHERE id = ?", taskId).toArray()[0];
        const formatted = this.formatCoordTask(updated);
        this.broadcastCoordination("handoff", formatted);
        return json({ task: formatted });
      }

      return json({ error: `unknown action "${action}"` }, 400);
    }

    return json({ error: "method not allowed" }, 405);
  }

  async webSocketClose(ws, code, reason) {
    try {
      ws.close(code === 1005 ? 1000 : code, reason); // finish the close handshake, or the client waits it out
    } catch {
      // already closed
    }
    this.left(ws);
  }

  async webSocketError(ws) {
    this.left(ws);
  }

  left(ws) {
    ws.serializeAttachment(null);
    this.broadcastRoster();
    if (this.members().length === 0 && this.env.BLOBS && !retention(this.env).keepFiles) this.ctx.storage.setAlarm(Date.now() + (Number(this.env.IDLE_WIPE_SECONDS) * 1000 || IDLE_WIPE_MS));
  }

  // ----- the end of a session: wipe its files ------------------------------------

  async alarm() {
    const line = this.line || (await this.ctx.storage.get("line"));
    if (this.members().length > 0 || !this.env.BLOBS || !line || retention(this.env).keepFiles) return;
    const keep = new Set();
    const cutoff = Date.now() - (Number(this.env.BLOB_RETENTION_HOURS || 24) * 3600 * 1000);
    for (const row of this.sql.exec("SELECT blobs FROM messages WHERE (delivered = 0 OR ts > ?) AND blobs != ''", cutoff).toArray()) {
      for (const blob of row.blobs.split(",")) keep.add(`${line}/${blob}`);
    }
    let cursor;
    do {
      const page = await this.env.BLOBS.list({ prefix: `${line}/`, cursor });
      const doomed = page.objects.map((o) => o.key).filter((key) => !keep.has(key));
      if (doomed.length) await this.env.BLOBS.delete(doomed);
      cursor = page.truncated ? page.cursor : undefined;
    } while (cursor);
  }
}

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function gateUnauthorizedHtml() {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Unauthorized · chamber</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #09090b; color: #f4f4f5; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 16px; }
    .card { background: #18181b; border: 1px solid #ef444433; border-radius: 12px; padding: 24px; max-width: 400px; text-align: center; }
    h2 { color: #ef4444; margin-top: 0; font-size: 18px; }
    p { color: #a1a1aa; font-size: 14px; line-height: 1.5; margin-bottom: 0; }
  </style>
</head>
<body>
  <div class="card">
    <h2>Access Denied</h2>
    <p>A valid approval token is required to review or authorize this gate.</p>
  </div>
</body>
</html>`;
}

function gateHtml(gate) {
  const isPending = gate.status === "pending";
  const isApproved = gate.status === "approved";
  const isRejected = gate.status === "rejected";
  const isExpired = gate.status === "expired";
  const isNotFound = gate.status === "not_found";

  const statusColor = isApproved ? "#10b981" : isRejected ? "#f43f5e" : isPending ? "#f59e0b" : "#71717a";
  const statusLabel = isApproved ? "Approved" : isRejected ? "Rejected" : isPending ? "Pending Review" : isExpired ? "Expired" : "Not Found";
  const actionUrl = gate.line
    ? `/gate/${encodeURIComponent(gate.line)}/${encodeURIComponent(gate.id)}?token=${encodeURIComponent(gate.token || "")}`
    : `/gate/${encodeURIComponent(gate.id)}?token=${encodeURIComponent(gate.token || "")}`;

  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Approval Gate · chamber</title>
  <style>
    :root {
      --bg: #09090b;
      --card-bg: #18181b;
      --border: #27272a;
      --text: #f4f4f5;
      --muted: #a1a1aa;
      --accent: #eab308;
      --approve: #10b981;
      --approve-hover: #059669;
      --reject: #ef4444;
      --reject-hover: #dc2626;
    }
    @media (prefers-color-scheme: light) {
      :root {
        --bg: #fafafa;
        --card-bg: #ffffff;
        --border: #e4e4e7;
        --text: #09090b;
        --muted: #71717a;
        --accent: #ca8a04;
      }
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 16px;
    }
    .container {
      width: 100%;
      max-width: 440px;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 24px;
      box-shadow: 0 4px 20px rgba(0,0,0,0.15);
    }
    .header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 20px;
    }
    .logo {
      font-size: 18px;
      font-weight: 700;
      letter-spacing: -0.5px;
    }
    .logo span { color: var(--accent); }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      font-weight: 600;
      padding: 4px 10px;
      border-radius: 9999px;
      background: ${statusColor}18;
      color: ${statusColor};
      border: 1px solid ${statusColor}33;
    }
    .dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: ${statusColor};
    }
    .meta-box {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 14px;
      margin-bottom: 20px;
    }
    .row {
      display: flex;
      justify-content: space-between;
      font-size: 12px;
      margin-bottom: 8px;
    }
    .row:last-child { margin-bottom: 0; }
    .label { color: var(--muted); }
    .val { font-weight: 500; font-family: monospace; }
    .desc-title {
      font-size: 13px;
      font-weight: 600;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: 6px;
    }
    .desc-text {
      font-size: 14px;
      line-height: 1.5;
      margin-bottom: 24px;
    }
    .actions {
      display: flex;
      gap: 12px;
    }
    .btn {
      flex: 1;
      height: 44px;
      border-radius: 8px;
      border: none;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      transition: all 0.15s ease;
      touch-action: manipulation;
    }
    .btn-approve {
      background: var(--approve);
      color: #ffffff;
    }
    .btn-approve:hover { background: var(--approve-hover); }
    .btn-reject {
      background: transparent;
      color: var(--reject);
      border: 1px solid var(--reject);
    }
    .btn-reject:hover { background: var(--reject)15; }
    .notice {
      text-align: center;
      font-size: 12px;
      color: var(--muted);
      margin-top: 16px;
    }
    .resolved-box {
      text-align: center;
      padding: 16px;
      background: ${statusColor}10;
      border: 1px solid ${statusColor}30;
      border-radius: 10px;
      color: ${statusColor};
      font-size: 14px;
      font-weight: 500;
      line-height: 1.5;
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="logo">chamber<span>.</span></div>
      <div class="badge"><span class="dot"></span> ${escapeHtml(statusLabel)}</div>
    </div>

    ${isNotFound ? `
      <div class="resolved-box">
        Gate not found or already purged.
      </div>
    ` : `
      <div class="meta-box">
        <div class="row"><span class="label">Gate ID</span><span class="val">${escapeHtml((gate.id || "").slice(0, 12))}…</span></div>
        <div class="row"><span class="label">Action</span><span class="val" id="val-action">🔒 Encrypted Action</span></div>
        <div class="row"><span class="label">Requested By</span><span class="val" id="val-requested-by">🔒 Encrypted</span></div>
      </div>

      <div class="desc-title">Description</div>
      <div class="desc-text" id="val-desc">Decrypting details locally with key from URL fragment…</div>

      <div id="sealed-box" data-sealed="${escapeHtml(gate.sealed || "")}" style="display:none"></div>

      ${isPending ? `
        <form method="POST" action="${escapeHtml(actionUrl)}">
          <input type="hidden" name="token" value="${escapeHtml(gate.token || "")}">
          <input type="hidden" name="approver" value="mobile-user">
          <div class="actions">
            <button type="submit" name="decision" value="approve" class="btn btn-approve">
              Approve
            </button>
            <button type="submit" name="decision" value="reject" class="btn btn-reject">
              Reject
            </button>
          </div>
        </form>
        <div class="notice">Remote authorization via E2EE Cloudflare Relay</div>
      ` : `
        <div class="resolved-box">
          ${isApproved ? `✓ Action Approved${gate.approver ? " by " + escapeHtml(gate.approver) : ""}.<br><span style="font-size:12px;opacity:0.8;">The runner has been unblocked.</span>` : ""}
          ${isRejected ? `✕ Action Rejected${gate.approver ? " by " + escapeHtml(gate.approver) : ""}.<br><span style="font-size:12px;opacity:0.8;">The runner will abort this task.</span>` : ""}
          ${isExpired ? `⏱ Gate Expired.<br><span style="font-size:12px;opacity:0.8;">This request timed out before review.</span>` : ""}
        </div>
      `}
    `}
  </div>
  <script>
  (async () => {
    const hash = window.location.hash.slice(1);
    const el = document.getElementById("sealed-box");
    const sealed = el ? el.getAttribute("data-sealed") : null;
    if (!hash || !sealed) {
      const descEl = document.getElementById("val-desc");
      if (descEl) descEl.textContent = "🔒 Encrypted gate: decryption key required in link hash (#<key>).";
      return;
    }
    try {
      const rawKey = Uint8Array.from(atob(hash.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
      const cryptoKey = await crypto.subtle.importKey("raw", rawKey, "AES-GCM", false, ["decrypt"]);
      const rawCipher = Uint8Array.from(atob(sealed.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
      const iv = rawCipher.slice(0, 12);
      const data = rawCipher.slice(12);
      const decrypted = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, cryptoKey, data);
      const parsed = JSON.parse(new TextDecoder().decode(decrypted));
      if (parsed.action) {
        const actEl = document.getElementById("val-action");
        if (actEl) actEl.textContent = parsed.action;
      }
      if (parsed.description) {
        const descEl = document.getElementById("val-desc");
        if (descEl) descEl.textContent = parsed.description;
      }
      if (parsed.requestedBy) {
        const reqEl = document.getElementById("val-requested-by");
        if (reqEl) reqEl.textContent = parsed.requestedBy;
      }
    } catch {
      const descEl = document.getElementById("val-desc");
      if (descEl) descEl.textContent = "🔒 Could not decrypt gate payload with the provided key.";
    }
  })();
  </script>
</body>
</html>`;
}
