// Automated Cloudflare Relay deployment for Chamber admins
import { execFileSync, spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { homedir, tmpdir, userInfo } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { issuePass, newAdminKey } from "./admin.mjs";
import { HOME, markAdmin, saveConfig } from "./inbox.mjs";
import { Invite, LineError } from "./protocol.mjs";

const ROOT = resolve(fileURLToPath(import.meta.url), "../..");
const RELAY_DIR = join(ROOT, "relay");

// Wrangler ships inside the app (relay/node_modules) and runs on the app's own Node, so an admin needs
// nothing installed. Run from a clone, it is installed once, the first time a chamber is created.
const WRANGLER = join(RELAY_DIR, "node_modules", "wrangler", "bin", "wrangler.js");

function wranglerCommand(args) {
  if (!existsSync(WRANGLER)) {
    // npm is a .cmd file on Windows, which Node only runs through a shell.
    execFileSync(process.platform === "win32" ? "npm.cmd" : "npm", ["install", "--silent", "--no-audit", "--no-fund"], { cwd: RELAY_DIR, stdio: "ignore", shell: process.platform === "win32" });
  }
  return [process.execPath, [WRANGLER, ...args]];
}

const stripAnsi = (text) => text.replace(/\x1b\[[0-9;]*m/g, "");

/**
 * The relay's wrangler.toml without the optional R2 binding, for accounts that haven't enabled R2:
 * deploying with the binding fails when the bucket doesn't exist. Written to a temp folder (the app
 * bundle is read-only) with an absolute entry point, since wrangler resolves `main` from the config.
 */
function configWithoutR2() {
  const toml = readFileSync(join(RELAY_DIR, "wrangler.toml"), "utf8")
    .split(/^# Optional: files too big/m)[0]
    .replace(/^main = .*$/m, `main = ${JSON.stringify(join(RELAY_DIR, "src", "index.js"))}`);
  const path = join(mkdtempSync(join(tmpdir(), "chamber-relay-")), "wrangler.toml");
  writeFileSync(path, toml);
  return path;
}

/**
 * Run wrangler without blocking the server (it answers progress polls meanwhile). Resolves with
 * stdout; rejects with an error carrying stderr. An abort signal kills the wrangler it started.
 */
function runWrangler(args, { input, signal } = {}) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason);
    const [cmd, fullArgs] = wranglerCommand(args);
    const child = spawn(cmd, fullArgs, { cwd: RELAY_DIR, stdio: ["pipe", "pipe", "pipe"], signal });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => (stdout += chunk));
    child.stderr.on("data", (chunk) => (stderr += chunk));
    child.stdin.end(input ?? "");
    child.on("error", (err) => reject(signal?.aborted ? signal.reason : err));
    child.on("close", (code) => {
      if (signal?.aborted) return reject(signal.reason);
      if (code === 0) return resolve(stdout);
      reject(Object.assign(new Error(`wrangler ${args[0]} exited with code ${code}`), { stdout, stderr }));
    });
  });
}

/** `wrangler whoami --json`, or null when not signed in. It also refreshes an expired sign-in token. */
async function whoami(signal) {
  try {
    return JSON.parse(await runWrangler(["whoami", "--json"], { signal }));
  } catch (err) {
    if (signal?.aborted) throw err;
    return null;
  }
}

/** Check if the developer is already logged in to Cloudflare. */
export async function checkCloudflareAuth({ signal } = {}) {
  const me = await whoami(signal);
  if (!me?.loggedIn) return { authenticated: false, account: null };
  return { authenticated: true, account: me.accounts?.[0]?.name || "Cloudflare Account" };
}

/** Launch Cloudflare OAuth login in the user's browser, and say why if it does not finish. */
export function loginCloudflare({ signal } = {}) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason);
    const [cmd, fullArgs] = wranglerCommand(["login"]);
    const child = spawn(cmd, fullArgs, { cwd: RELAY_DIR, stdio: ["ignore", "pipe", "pipe"], signal });
    let output = "";
    for (const stream of [child.stdout, child.stderr]) stream.on("data", (chunk) => (output = (output + chunk).slice(-600)));
    child.on("close", (code) => {
      if (signal?.aborted) return reject(signal.reason);
      if (code === 0) return resolve(true);
      const detail = stripAnsi(output).trim().split("\n").filter(Boolean).slice(-2).join(" ");
      reject(new LineError(`Cloudflare sign-in didn't finish${detail ? ` (${detail})` : ""}. Finish signing in in the browser window that opens, then try again.`));
    });
    child.on("error", (err) => reject(signal?.aborted ? signal.reason : err));
  });
}

// ---------------------------------------------------------------------------
// File storage (R2): optional, and off until the account owner turns R2 on in the dashboard
// ---------------------------------------------------------------------------

const BUCKET = "chamber-blobs";

/** Where the account owner turns R2 on (Cloudflare asks for a payment method; the free tier is 10 GB). */
function r2DashboardUrl(accountId) {
  return accountId ? `https://dash.cloudflare.com/${accountId}/r2/overview` : "https://dash.cloudflare.com/?to=/:account/r2/overview";
}

/**
 * Whether the signed-in Cloudflare account can hold the relay's files: R2 must be turned on in the
 * dashboard first, which wrangler reports as code 10042 when listing buckets.
 */
export async function checkR2({ signal } = {}) {
  const me = await whoami(signal);
  if (!me?.loggedIn) return { authenticated: false, enabled: false, bucket: false, dashboard: r2DashboardUrl(null) };
  const dashboard = r2DashboardUrl(me.accounts?.[0]?.id);
  try {
    const list = await runWrangler(["r2", "bucket", "list"], { signal });
    return { authenticated: true, enabled: true, bucket: list.includes(BUCKET), dashboard };
  } catch (err) {
    if (signal?.aborted) throw err;
    const detail = stripAnsi((err.stderr || err.message || "").toString());
    const reason = /10042|enable R2/i.test(detail) ? "R2 isn't turned on for this Cloudflare account yet." : detail.match(/\[ERROR\][^\n]*/)?.[0]?.replace("[ERROR]", "").trim() || "Couldn't check R2 on this account.";
    return { authenticated: true, enabled: false, bucket: false, dashboard, reason };
  }
}

/** Create the relay's bucket if it isn't there, with files expiring after a week. Throws if R2 is off. */
async function ensureBucket(signal) {
  const list = await runWrangler(["r2", "bucket", "list"], { signal });
  if (!list.includes(BUCKET)) await runWrangler(["r2", "bucket", "create", BUCKET], { signal });
  try {
    await runWrangler(["r2", "bucket", "lifecycle", "add", BUCKET, "expire-week", "", "--expire-days", "7", "--force"], { signal });
  } catch (err) {
    if (signal?.aborted) throw err;
  }
}

/**
 * Turn on file storage for a relay this machine deployed: create the bucket, then redeploy the relay
 * with it bound. Its secrets (who may use it) carry over a redeploy. Only for the Cloudflare account
 * the relay runs in: a different sign-in would deploy a second relay instead.
 */
export async function enableRelayStorage({ relay, onProgress = () => {}, signal } = {}) {
  const [, worker, sub] = new URL(relay).hostname.match(/^(chamber(?:-[a-z0-9]+)?)\.([^.]+)\.workers\.dev$/) || [];
  if (!sub) throw new LineError("File storage can be turned on here only for a relay Chamber deployed to Cloudflare.");

  onProgress("Checking your Cloudflare sign-in…");
  const me = await whoami(signal);
  if (!me?.loggedIn) throw new LineError("Sign in to Cloudflare first.");
  const token = await wranglerAuthToken(signal);
  const accountId = me.accounts?.[0]?.id;
  if (token && accountId) {
    const res = await fetch(`https://api.cloudflare.com/client/v4/accounts/${accountId}/workers/subdomain`, { headers: { Authorization: `Bearer ${token}` }, signal }).catch(() => null);
    const mine = res?.ok ? (await res.json()).result?.subdomain : null;
    if (mine && mine !== sub) {
      throw new LineError(`You're signed in to the Cloudflare account "${me.accounts[0].name}", but this chamber's relay runs in another one (${sub}.workers.dev). Sign in to that account and try again.`);
    }
  }

  onProgress("Creating the storage bucket…");
  try {
    await ensureBucket(signal);
  } catch (err) {
    if (signal?.aborted) throw err;
    throw new LineError("R2 isn't turned on for this Cloudflare account yet. Turn it on in the Cloudflare dashboard, then try again.");
  }

  onProgress("Updating your relay…");
  try {
    await runWrangler(["deploy", "--name", worker], { signal });
  } catch (err) {
    if (signal?.aborted) throw err;
    throw new LineError(`Couldn't update the relay:\n${stripAnsi((err.stderr || err.message || "").toString()).trim()}`);
  }
  return { ok: true, files: true };
}

// ---------------------------------------------------------------------------
// Auto-register a workers.dev subdomain for new Cloudflare accounts
// ---------------------------------------------------------------------------

/**
 * The Cloudflare token Wrangler signs in with: `wrangler auth token` (any OS, refreshed if expired),
 * else the TOML config `wrangler login` wrote to an OS-specific path.
 */
async function wranglerAuthToken(signal) {
  try {
    const { token } = JSON.parse(await runWrangler(["auth", "token", "--json"], { signal }));
    if (token) return token;
  } catch (err) {
    if (signal?.aborted) throw err;
  }
  return readWranglerAuthToken();
}

function readWranglerAuthToken() {
  if (process.env.CLOUDFLARE_API_TOKEN) return process.env.CLOUDFLARE_API_TOKEN;

  const home = homedir();
  const paths = [];
  if (process.env.WRANGLER_HOME) paths.push(join(process.env.WRANGLER_HOME, "config", "default.toml"));
  // macOS: ~/Library/Preferences/.wrangler/config/default.toml
  if (process.platform === "darwin") paths.push(join(home, "Library", "Preferences", ".wrangler", "config", "default.toml"));
  // XDG (Linux, macOS fallback): ~/.config/.wrangler/config/default.toml
  paths.push(join(process.env.XDG_CONFIG_HOME || join(home, ".config"), ".wrangler", "config", "default.toml"));
  // Windows: %APPDATA%/xdg.config/.wrangler/config/default.toml
  if (process.platform === "win32") paths.push(join(process.env.APPDATA || join(home, "AppData", "Roaming"), "xdg.config", ".wrangler", "config", "default.toml"));
  paths.push(join(home, ".wrangler", "config", "default.toml"));

  for (const p of paths) {
    try {
      const text = readFileSync(p, "utf8");
      const m = text.match(/(?:oauth_token|access_token)\s*=\s*"([^"]+)"/);
      if (m) return m[1];
    } catch {}
  }
  return null;
}

/** A workers.dev subdomain: lowercase letters, digits and inner hyphens, at most 63 characters. */
export function subdomainName(username = "") {
  const base = username.toLowerCase().replace(/[^a-z0-9-]/g, "").replace(/^-+|-+$/g, "").slice(0, 40).replace(/-+$/, "");
  return base ? `${base}-${randomBytes(2).toString("hex")}` : `chamber-${randomBytes(3).toString("hex")}`;
}

/**
 * Make sure the Cloudflare account has a workers.dev subdomain registered.
 * New free-plan accounts need this before the first Worker can be deployed.
 * Fully automated: picks a name from the OS username + random hex, registers
 * it via the Cloudflare API using the wrangler OAuth token that was already
 * stored during `wrangler login`.
 *
 * Best-effort: returns silently on any failure (the deploy step has its own
 * clear error handling as a fallback).
 */
async function ensureWorkersSubdomain(onProgress, signal) {
  // whoami first: it refreshes an expired sign-in token, so the token read after it is current.
  const accountId = (await whoami(signal))?.accounts?.[0]?.id;
  const token = await wranglerAuthToken(signal);
  if (!accountId || !token) return;
  const endpoint = `https://api.cloudflare.com/client/v4/accounts/${accountId}/workers/subdomain`;

  // Check if a subdomain is already registered
  try {
    const res = await fetch(endpoint, { headers: { Authorization: `Bearer ${token}` }, signal });
    if (res.ok && (await res.json()).result?.subdomain) return;
  } catch (err) {
    if (signal?.aborted) throw err;
    return;
  }

  let username = "";
  try { username = userInfo().username; } catch {}
  let name = subdomainName(username);
  onProgress?.(`Registering ${name}.workers.dev for your account…`);

  // Try to register, retry once with a fully random name if taken
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const res = await fetch(endpoint, {
        method: "PUT",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ subdomain: name }),
        signal,
      });
      if (res.ok) return;
      name = subdomainName();
    } catch (err) {
      if (signal?.aborted) throw err;
      return;
    }
  }
  // If both attempts failed, fall through silently — deploy will show a clear error
}

/**
 * Deploy the Cloudflare relay worker, with file storage (R2) when `storage` and the account has it, lock the relay with admin
 * keys, and initialize the chamber on this machine.
 */
export async function deployRelay({ owner = "admin", name, projectPath = null, storage = true, onProgress = () => {}, signal } = {}) {
  // Each chamber is its own Worker (chamber-3f9a.<you>.workers.dev), so a second chamber in the same
  // Cloudflare account never replaces the first one's relay and its keys.
  const worker = `chamber-${randomBytes(3).toString("hex")}`;
  onProgress("1/5 Checking Cloudflare authentication…");
  const auth = await checkCloudflareAuth({ signal });
  if (!auth.authenticated) {
    onProgress("Waiting for Cloudflare sign-in in your browser…");
    await loginCloudflare({ signal });
  }

  // Without file storage (R2 off, or skipped), deploy without the bucket binding: with it, the deploy
  // fails on a bucket that doesn't exist. Small files still travel inside messages.
  let deployArgs = ["deploy", "--name", worker];
  let files = false;
  if (storage) {
    onProgress("2/5 Setting up file storage…");
    try {
      await ensureBucket(signal);
      files = true;
    } catch (e) {
      if (signal?.aborted) throw e;
      onProgress("2/5 File storage isn't available on this account; continuing without it…");
    }
  }
  if (!files) deployArgs.push("--config", configWithoutR2());

  // New Cloudflare accounts need a workers.dev subdomain before the first deploy.
  // Wrangler prompts interactively in a terminal, but the desktop app has no TTY.
  // Auto-register one via the Cloudflare API so users never have to visit the dashboard.
  await ensureWorkersSubdomain(onProgress, signal);

  onProgress("3/5 Deploying relay worker to Cloudflare Edge…");
  let deployOutput;
  try {
    deployOutput = await runWrangler(deployArgs, { signal });
  } catch (deployErr) {
    if (signal?.aborted) throw deployErr;
    const stderr = (deployErr.stderr || deployErr.message || "").toString();
    // New Cloudflare accounts need a workers.dev subdomain registered before the first deploy.
    // Wrangler prompts interactively in a terminal, but in programmatic mode it just fails.
    // Extract the onboarding URL from the error so users know exactly where to go.
    if (/workers\.dev subdomain/i.test(stderr) || /register a workers\.dev/i.test(stderr)) {
      const onboardingUrl = stderr.match(/https:\/\/dash\.cloudflare\.com\/[^\s\x1b\]]+\/workers\/onboarding/)?.[0];
      throw new LineError(
        `Your Cloudflare account needs a workers.dev subdomain before Chamber can deploy.\n\n` +
        `Open ${onboardingUrl || "https://dash.cloudflare.com → Workers & Pages"} in your browser, ` +
        `pick a subdomain (e.g. yourname.workers.dev), then try again.`
      );
    }
    // Strip ANSI codes from any other Wrangler error so the UI stays clean
    const clean = stripAnsi(stderr).trim();
    throw new LineError(`Relay deployment failed:\n${clean || deployErr.message}`);
  }
  const relayMatch = deployOutput.match(/https:\/\/[a-zA-Z0-9.-]+\.workers\.dev/);
  if (!relayMatch) {
    throw new LineError(`Relay deployment did not return a workers.dev URL:\n${deployOutput}`);
  }
  const relayUrl = relayMatch[0];

  onProgress(`4/5 Generating cryptographic keys for ${relayUrl}…`);
  const secret = randomBytes(32).toString("base64url");
  const invite = await new Invite(relayUrl, secret).ready();
  const lineId = invite.lineId;
  const adminKey = newAdminKey();
  const adminPass = issuePass(adminKey.private, lineId);

  // Bind secrets so relay is private to this chamber only
  onProgress("Locking relay with cryptographic admin pass…");
  await runWrangler(["secret", "put", "ALLOWED_LINES", "--name", worker], { input: lineId, signal });
  await runWrangler(["secret", "put", "ADMIN_KEY", "--name", worker], { input: adminKey.public, signal });

  // Cancelled (the person went back): stop before this machine is set up as a chamber.
  signal?.throwIfAborted();
  onProgress("5/5 Finalizing local Chamber configuration…");
  mkdirSync(HOME, { recursive: true });
  const configuredInvite = invite.withPass(adminPass);
  saveConfig(configuredInvite, owner, name);
  markAdmin(configuredInvite, { adminPublic: adminKey.public, adminPrivate: adminKey.private });

  // Install default workflows and setup project workspace if given
  // No project (an app opened from Finder): workspaces are added later, in the app.
  const targetProject = projectPath ? resolve(projectPath) : null;
  try {
    if (!targetProject) throw new Error("no project");
    const { installDefaultWorkflows } = await import("./runner.mjs");
    await installDefaultWorkflows(resolve(targetProject, ".chamber", "workflows"), adminKey.private, lineId);
  } catch {}

  if (targetProject && existsSync(targetProject)) {
    try {
      const { setup } = await import("./setup.mjs");
      setup(targetProject, { register: true });
    } catch {}
  }

  const joinCmd = `curl -fsSL ${relayUrl}/install | sh -s -- ${secret}~${adminPass}`;
  return {
    ok: true,
    relay: relayUrl,
    lineId,
    owner,
    adminPublic: adminKey.public,
    adminPass,
    files,
    inviteCommand: joinCmd,
  };
}
