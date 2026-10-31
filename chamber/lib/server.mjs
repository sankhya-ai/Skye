// `chamber board`: the board in your browser, served from this machine only.
//
//   http://127.0.0.1:7878         the board (prebuilt, in ../board)
//   /api/board…                   cards, a conversation, saying something, done, workspaces, invite
//
// Only this machine can use it: it listens on 127.0.0.1, answers only to a
// localhost Host (no DNS rebinding), and takes changes only from its own page
// (same Origin, JSON bodies), so another website cannot drive it.

import { execFile, execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { createReadStream, existsSync, readFileSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { Board } from "./board.mjs";
import { chamberHome, chamberId, HOME, homeFor, listChambers, loadConfig, logout, newChamberHome, rename, saveConfig, setActiveChamber, useChamber } from "./inbox.mjs";
import { LineError } from "./protocol.mjs";
import { setup, teardown } from "./setup.mjs";

/** Chamber Cloud: the hosted relay Sankhya AI Labs runs (on Maritime). CHAMBER_CLOUD_RELAY overrides. */
export const CHAMBER_CLOUD_RELAY = process.env.CHAMBER_CLOUD_RELAY || "https://3707fcfe-177b-4d71-a79f-31009486cdad.mtm.sh";
const STATIC = resolve(fileURLToPath(import.meta.url), "../../board");
const TYPES = { ".html": "text/html; charset=utf-8", ".mjs": "text/javascript", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };

function json(res, status, body) {
  res.writeHead(status, { "content-type": "application/json", "cache-control": "no-store" });
  res.end(JSON.stringify(body));
}

async function body(req) {
  let text = "";
  for await (const chunk of req) {
    text += chunk;
    if (text.length > 2_000_000) throw new LineError("too large");
  }
  return text ? JSON.parse(text) : {};
}

function serveFile(res, path) {
  res.writeHead(200, { "content-type": TYPES[extname(path)] || "application/octet-stream", "cache-control": "no-cache" });
  createReadStream(path).pipe(res);
}

function serveHtmlWithToken(res, htmlPath, sessionToken) {
  try {
    let content = readFileSync(htmlPath, "utf8");
    if (sessionToken) {
      content = content.replace("</head>", `<meta name="chamber-token" content="${sessionToken}">\n</head>`);
    }
    const buf = Buffer.from(content, "utf8");
    res.writeHead(200, { "content-type": "text/html; charset=utf-8", "cache-control": "no-cache", "content-length": buf.length });
    res.end(buf);
  } catch {
    json(res, 500, { detail: "Failed to load index.html" });
  }
}

/** Stop the chamber in use (its board, the away responder): before switching to another, or adding one. */
async function closeBoard(live) {
  if (live.awayResponder) {
    await live.awayResponder.stop();
    live.awayResponder = null;
  }
  live.board?.close();
  live.board = null;
}

/** Open the chamber kept in `home` (its folder) in the app, and make it the one picked. */
async function openChamber(live, home) {
  await closeBoard(live);
  useChamber(home);
  setActiveChamber(home);
  live.adding = null;
  live.board = new Board(loadConfig());
  live.board.start();
}

/**
 * Create or join a chamber into `home` (a folder of its own when this machine is in others): if it
 * fails, the chamber in use stays the one it was. The new chamber is the one picked after.
 */
async function intoChamber(live, home, work) {
  const back = live.adding?.previous ?? HOME;
  await closeBoard(live);
  useChamber(home);
  try {
    const result = await work();
    setActiveChamber(HOME);
    live.adding = null;
    return result;
  } catch (err) {
    useChamber(back);
    throw err;
  }
}

/**
 * The folder a new chamber sets up its agents in: the one the app was opened in, but only for your
 * first chamber. A folder is in one chamber at a time, so adding another never takes a project away
 * from the chamber it's in; its folders are added in it after.
 */
function firstFolder(project) {
  return listChambers().length ? null : project || startingProject();
}

/** The chambers on this machine, for the switcher: names and relays, not their folders. */
function chambersFor(live) {
  return listChambers().map(({ home, added, lineId, ...c }) => ({ ...c, active: Boolean(live.board) && home === HOME }));
}

/** `live.board` is the current Board: renaming yourself replaces it. */
export function handler(live, port) {
  live.sessionToken ||= randomBytes(24).toString("hex");
  const hosts = new Set([`127.0.0.1:${port}`, `localhost:${port}`]);
  return async (req, res) => {
    const board = live.board;
    try {
      if (!hosts.has(req.headers.host || "")) return json(res, 403, { detail: "Host not allowed." });
      const url = new URL(req.url, `http://${req.headers.host}`);
      const path = url.pathname;
      if (req.method !== "GET") {
        const origin = req.headers.origin;
        const isAllowedOrigin =
          !origin ||
          origin === `http://${req.headers.host}` ||
          origin === `http://127.0.0.1:${port}` ||
          origin === `http://localhost:${port}` ||
          origin === "tauri://localhost" ||
          origin === "http://tauri.localhost";
        if (!isAllowedOrigin || !String(req.headers["content-type"] || "").startsWith("application/json")) {
          return json(res, 403, { detail: "Only the board itself can do that." });
        }
        const token = req.headers["x-chamber-token"];
        if (live.sessionToken && (!token || token !== live.sessionToken)) {
          return json(res, 403, { detail: "Invalid or missing session token." });
        }
      }

      if (path === "/api/board/session" && req.method === "GET") return json(res, 200, { token: live.sessionToken, me: board?.owner || "" });
      if (path === "/api/board" && req.method === "GET") {
        if (!board) {
          const { harnessesHere } = await import("./setup.mjs");
          return json(res, 200, {
            configured: false,
            me: "",
            admin: false,
            connected: false,
            problem: "Not in a chamber yet",
            chambers: chambersFor(live),
            adding: Boolean(live.adding),
            detectedHarnesses: harnessesHere(),
            workspaces: [],
            projects: [],
            agents: [],
            channels: [],
            cards: [],
          });
        }
        return json(res, 200, { ...board.overview(), chamber: { id: chamberId(HOME), name: board.config.name || loadConfig()?.name }, chambers: chambersFor(live) });
      }

      // Several chambers, like Slack workspaces: list them, switch, add one (the onboarding, keeping
      // the others), or go back to the one you were in.
      if (path === "/api/board/chambers" && req.method === "GET") return json(res, 200, { chambers: chambersFor(live) });
      if (path === "/api/board/chambers/switch" && req.method === "POST") {
        await openChamber(live, chamberHome(String((await body(req)).id || "")));
        return json(res, 200, { ok: true, chambers: chambersFor(live) });
      }
      if (path === "/api/board/chambers/add" && req.method === "POST") {
        live.adding ||= { previous: HOME };
        await closeBoard(live);
        return json(res, 200, { ok: true });
      }
      if (path === "/api/board/chambers/cancel-add" && req.method === "POST") {
        const previous = live.adding?.previous;
        live.adding = null;
        if (previous && loadConfig(previous)) await openChamber(live, previous);
        return json(res, 200, { ok: true });
      }

      // In-App Onboarding: Join an existing chamber
      if (path === "/api/board/onboarding/join" && req.method === "POST") {
        const { invite: inviteInput, as, project, name } = await body(req);
        if (!inviteInput) return json(res, 400, { detail: "Invite token or command required" });

        const { Invite } = await import("./protocol.mjs");
        const parsedInvite = Invite.fromInput(inviteInput);
        const owner = String(as || defaultOwner()).toLowerCase().trim();
        const projectPath = firstFolder(project);
        await intoChamber(live, homeFor(parsedInvite), () => saveConfig(parsedInvite, owner, name || undefined));

        if (projectPath && existsSync(projectPath)) {
          try {
            const { setup } = await import("./setup.mjs");
            setup(projectPath, { register: true });
          } catch {}
        }

        const { Board } = await import("./board.mjs");
        live.board = new Board(loadConfig());
        live.board.start();
        return json(res, 200, { ok: true, owner, relay: parsedInvite.relay });
      }

      // In-App Onboarding: Create a new chamber
      if (path === "/api/board/onboarding/new" && req.method === "POST") {
        const given = await body(req);
        const { as, project, name } = given;
        // "cloud": Chamber Cloud, the hosted relay (a 7-day trial, then a seat per teammate).
        const relay = given.relay === "cloud" ? CHAMBER_CLOUD_RELAY : given.relay;
        if (!relay) return json(res, 400, { detail: "Paste your relay's address, e.g. https://relay.example.com" });

        const { Invite } = await import("./protocol.mjs");
        const { markAdmin } = await import("./inbox.mjs");
        const { claimRelay } = await import("./admin.mjs");

        const parsedInvite = await Invite.create(String(relay).trim()).ready();
        const owner = String(as || defaultOwner()).toLowerCase().trim();
        const claimed = await claimRelay(parsedInvite);

        const projectPath = firstFolder(project);
        await intoChamber(live, newChamberHome(), () => {
          saveConfig(claimed.pass ? parsedInvite.withPass(claimed.pass) : parsedInvite, owner, name || undefined);
          markAdmin(parsedInvite, claimed.key ? { adminPublic: claimed.key.public, adminPrivate: claimed.key.private } : {});
        });

        if (projectPath && existsSync(projectPath)) {
          try {
            const { setup } = await import("./setup.mjs");
            setup(projectPath, { register: true });
          } catch {}
        }

        try {
          if (!projectPath) throw new Error("no project");
          const { installDefaultWorkflows } = await import("./runner.mjs");
          await installDefaultWorkflows(resolve(projectPath, ".chamber", "workflows"), claimed.key?.private || null, parsedInvite.lineId);
        } catch {}

        live.board = new Board(loadConfig());
        live.board.start();
        return json(res, 200, { ok: true, owner, relay: parsedInvite.relay, admin: true });
      }

      // In-App Agent Discovery
      if (path === "/api/board/detect" && req.method === "GET") {
        const { harnessesHere } = await import("./setup.mjs");
        return json(res, 200, { harnesses: harnessesHere(), project: startingProject() });
      }

      // Check Cloudflare auth status
      if (path === "/api/board/onboarding/cloudflare-auth" && req.method === "GET") {
        const { checkCloudflareAuth } = await import("./deploy.mjs");
        const status = await checkCloudflareAuth();
        return json(res, 200, status);
      }

      // Sign in to Cloudflare in the browser; the page waits on it (and can cancel it).
      if (path === "/api/board/onboarding/cloudflare-login" && req.method === "POST") {
        const { checkCloudflareAuth, loginCloudflare } = await import("./deploy.mjs");
        live.login?.abort();
        const login = (live.login = new AbortController());
        try {
          const status = await checkCloudflareAuth({ signal: login.signal });
          if (!status.authenticated) await loginCloudflare({ signal: login.signal });
          return json(res, 200, await checkCloudflareAuth({ signal: login.signal }));
        } catch (err) {
          if (login.signal.aborted) return json(res, 409, { detail: "Cloudflare sign-in was cancelled." });
          throw err;
        } finally {
          if (live.login === login) live.login = null;
        }
      }

      // Whether the signed-in Cloudflare account has R2 on, for files up to 25 MB (optional).
      if (path === "/api/board/onboarding/r2-status" && req.method === "GET") {
        const { checkR2 } = await import("./deploy.mjs");
        return json(res, 200, await checkR2());
      }

      if (path === "/api/board/onboarding/cloudflare-progress" && req.method === "GET") {
        return json(res, 200, { running: Boolean(live.setup), progress: live.setup?.progress || "" });
      }

      if (path === "/api/board/onboarding/cloudflare-cancel" && req.method === "POST") {
        live.setup?.controller.abort();
        live.login?.abort();
        return json(res, 200, { ok: true });
      }

      // Automated Cloudflare Setup & Deployment
      if (path === "/api/board/onboarding/cloudflare-setup" && req.method === "POST") {
        const { as, project, storage, name } = await body(req);
        const owner = String(as || defaultOwner()).toLowerCase().trim();
        const { deployRelay } = await import("./deploy.mjs");
        // One setup at a time; the page polls its progress and can cancel it.
        live.setup?.controller.abort();
        const setup = (live.setup = { controller: new AbortController(), progress: "" });
        let result;
        try {
          const projectPath = firstFolder(project);
          result = await intoChamber(live, newChamberHome(), () =>
            deployRelay({ owner, name: name || undefined, projectPath, storage: storage !== false, signal: setup.controller.signal, onProgress: (msg) => (setup.progress = msg) }),
          );
        } catch (err) {
          if (setup.controller.signal.aborted) return json(res, 409, { detail: "Chamber setup was cancelled." });
          throw err;
        } finally {
          if (live.setup === setup) live.setup = null;
        }

        live.board = new Board(loadConfig());
        live.board.start();
        return json(res, 200, result);
      }

      // The page itself loads before there is a chamber (it shows the onboarding); its API does not.
      if (!board && path.startsWith("/api/")) return json(res, 400, { detail: "Chamber is not configured yet. Complete onboarding first." });

      if (path === "/api/board/logout" && req.method === "POST") {
        await closeBoard(live);
        logout();
        // Leaving one chamber opens the next one you're in, like Slack; with none left, the onboarding.
        const next = listChambers()[0];
        if (next) await openChamber(live, next.home);
        else useChamber(newChamberHome());
        return json(res, 200, { ok: true, chambers: chambersFor(live) });
      }

      // Rename the chamber in use, on this machine (what the switcher shows).
      if (path === "/api/board/chambers/rename" && req.method === "POST") {
        const name = String((await body(req)).name || "").trim();
        if (!name) return json(res, 400, { detail: "Give the chamber a name." });
        saveConfig(board.config.invite, board.config.owner, name);
        board.config.name = loadConfig().name;
        return json(res, 200, { ok: true, chambers: chambersFor(live) });
      }

      if (path === "/api/board/invite") {
        if (!board.overview().admin) return json(res, 403, { detail: "Only your chamber's admin invites people." });
        return json(res, 200, { command: await board.invite(), code: await board.inviteCode() });
      }
      if (path === "/api/board/members") return json(res, 200, await board.members());
      // Open a web page in the person's browser: links in the app's window (billing, sign-in pages).
      if (path === "/api/board/open" && req.method === "POST") {
        const { url: target } = await body(req);
        if (!/^https:\/\/[^\s]+$/.test(String(target || ""))) return json(res, 400, { detail: "Only https links open." });
        openBrowser(String(target));
        return json(res, 200, { ok: true });
      }
      // Chamber Cloud: the team's trial or plan, and (for the admin) a signed link to billing.
      if (path === "/api/board/plan" && req.method === "GET") {
        const { cloudPlan } = await import("./admin.mjs");
        return json(res, 200, { plan: await cloudPlan(board.config).catch(() => null) });
      }

      // Search over local talks, contracts, decisions, and incidents
      if (path === "/api/board/search" && req.method === "GET") {
        const { ChamberSearch } = await import("./search.mjs");
        const search = new ChamberSearch(board.owner);
        const q = url.searchParams.get("q") || "";
        const channel = url.searchParams.get("channel") || "";
        const type = url.searchParams.get("type") || "";
        const hits = await search.search({ query: q, channel, type, limit: 20 });
        search.close();
        return json(res, 200, { hits });
      }

      // Cryptographic approval gates
      if (path === "/api/board/gates") {
        const { ApprovalGateManager } = await import("./runner.mjs");
        const mgr = new ApprovalGateManager(join(HOME, board.owner));
        if (req.method === "GET") {
          return json(res, 200, { gates: mgr.listPending() });
        }
        if (req.method === "POST") {
          const { gateId, decision, token } = await body(req);
          const resolved = mgr.resolveGate(String(gateId), decision === "approve" ? "approve" : "reject", {
            token: token || null,
            approver: board.owner,
          });
          return json(res, 200, { gate: resolved });
        }
      }

      // Claude Code and Codex: installed? signed in? Install or sign in from the app.
      if (path === "/api/board/agent-tools") {
        const { agentTools, startAgentTool } = await import("./agent-cli.mjs");
        if (req.method === "POST") {
          const { tool, action } = await body(req);
          try {
            startAgentTool(String(tool), String(action));
          } catch (err) {
            return json(res, 400, { detail: err.message });
          }
        }
        return json(res, 200, { tools: agentTools() });
      }

      // Who may wake your agents, what a woken agent may do, and what woke them.
      if (path === "/api/board/wake") {
        const { readPolicy, policyFor, setPolicy, wakeLog } = await import("./wake.mjs");
        if (req.method === "POST") {
          const { agent, who, can } = await body(req);
          if (!String(agent || "").startsWith(`${board.owner}/`)) return json(res, 403, { detail: "You can only set the rules for your own agents." });
          try {
            setPolicy(String(agent), { who, can });
          } catch (err) {
            return json(res, 400, { detail: err.message });
          }
        }
        const policy = readPolicy();
        const agents = Object.fromEntries(Object.keys(policy.agents).map((a) => [a, policyFor(a, policy)]));
        return json(res, 200, { agents, log: wakeLog() });
      }
      const wakeRun = path.match(/^\/api\/board\/wake\/([0-9a-f]{16})$/);
      if (wakeRun && req.method === "GET") {
        const { wakeTranscript } = await import("./wake.mjs");
        const events = wakeTranscript(wakeRun[1]);
        return events ? json(res, 200, { events }) : json(res, 404, { detail: "No record of that run." });
      }

      // Away mode responder toggle
      if (path === "/api/board/away") {
        if (req.method === "GET") {
          return json(res, 200, { running: Boolean(live.awayResponder), status: live.awayStatus || "away in a meeting" });
        }
        if (req.method === "POST") {
          const { enabled, status } = await body(req);
          if (enabled && !live.awayResponder) {
            const { AwayResponder } = await import("./away.mjs");
            live.awayStatus = String(status || "away in a meeting");
            live.awayResponder = new AwayResponder(board.config, { statusMessage: live.awayStatus });
            await live.awayResponder.start();
          } else if (!enabled && live.awayResponder) {
            await live.awayResponder.stop();
            live.awayResponder = null;
          }
          return json(res, 200, { running: Boolean(live.awayResponder), status: live.awayStatus });
        }
      }

      // Interactive patch verification
      if (path === "/api/board/review_patch" && req.method === "POST") {
        const { patch, workspacePath } = await body(req);
        if (!patch) return json(res, 400, { detail: "Missing patch" });
        const { spawnSync } = await import("node:child_process");
        const cwd = workspacePath || process.cwd();
        const check = spawnSync("git", ["apply", "--check", "-"], { cwd, input: String(patch), encoding: "utf8" });
        const stat = spawnSync("git", ["apply", "--stat", "-"], { cwd, input: String(patch), encoding: "utf8" });
        return json(res, 200, {
          appliesCleanly: check.status === 0,
          stat: stat.stdout || "",
          error: check.status === 0 ? null : (check.stderr || check.stdout || "Patch failed to apply"),
        });
      }

      // Apply patch directly to the working tree
      if (path === "/api/board/apply_patch" && req.method === "POST") {
        const { patch, workspacePath } = await body(req);
        if (!patch) return json(res, 400, { detail: "Missing patch" });
        const { spawnSync } = await import("node:child_process");
        const cwd = workspacePath || process.cwd();
        const apply = spawnSync("git", ["apply", "-"], { cwd, input: String(patch), encoding: "utf8" });
        return json(res, apply.status === 0 ? 200 : 400, {
          success: apply.status === 0,
          output: apply.stdout || apply.stderr || (apply.status === 0 ? "Applied cleanly to worktree." : "Failed to apply patch."),
        });
      }

      // Message emoji reactions
      if (path === "/api/board/reactions" && req.method === "POST") {
        const { messageId, emoji } = await body(req);
        if (!messageId || !emoji) return json(res, 400, { detail: "messageId and emoji required" });
        if (!board) return json(res, 400, { detail: "Board not configured" });
        const updated = board.toggleReaction(String(messageId), String(emoji));
        return json(res, 200, { messageId, reactions: updated });
      }

      // User custom status (emoji + text)
      if (path === "/api/board/user_status") {
        const statusFile = join(HOME, "status.json");
        if (req.method === "GET") {
          try {
            const data = existsSync(statusFile) ? JSON.parse(readFileSync(statusFile, "utf8")) : {};
            return json(res, 200, data);
          } catch {
            return json(res, 200, {});
          }
        }
        if (req.method === "POST") {
          const { emoji = "💬", text = "" } = await body(req);
          mkdirSync(HOME, { recursive: true });
          writeFileSync(statusFile, JSON.stringify({ emoji, text, updatedAt: Date.now() }, null, 2));
          return json(res, 200, { emoji, text });
        }
      }

      // Coordination tasks & lifecycle (ACP)
      if (path === "/api/board/coordination") {
        const { CoordinationManager } = await import("./coordination.mjs");
        const mgr = new CoordinationManager(join(HOME, "coordination"));
        if (req.method === "GET") {
          const status = url.searchParams.get("status") || undefined;
          const op = url.searchParams.get("op") || undefined;
          if (board?.config?.invite) {
            await mgr.syncWithRelay(board.config.invite, { status, op }).catch(() => {});
          }
          return json(res, 200, { tasks: mgr.list({ status, op }).map((t) => t.toJSON()) });
        }
        if (req.method === "POST") {
          const b = await body(req);
          if (b.action === "claim") {
            const agentName = String(b.agent || `${board.owner}/board`);
            const task = mgr.claim(String(b.id), agentName);
            if (board?.config?.invite) {
              await mgr.pushToRelay(board.config.invite, task, "claim", { agent: agentName }).catch(() => {});
            }
            return json(res, 200, { task: task.toJSON() });
          }
          if (b.action === "resolve") {
            const { task, memoryEntry } = mgr.resolve(String(b.id), {
              by: String(b.by || board.owner),
              resolution: String(b.resolution || "Resolved by user"),
              evidence: b.evidence || null,
              contract: b.contract || null,
            });
            if (board?.config?.invite) {
              await mgr.pushToRelay(board.config.invite, task, "resolve", { by: String(b.by || board.owner), resolution: String(b.resolution || "Resolved by user"), evidence: b.evidence || null, contract: b.contract || null }).catch(() => {});
            }
            if (memoryEntry) {
              try {
                const { TeamMemory } = await import("./memory.mjs");
                const memory = new TeamMemory(board.config.invite, `${board.owner}/board`);
                const entry = await memory.read("MEMORY.md");
                const newContent = `${(entry?.text || "").trim()}\n${memoryEntry}\n`;
                await memory.write("MEMORY.md", newContent, `Resolve task #${task.id.slice(0, 8)}`);
              } catch {}
            }
            return json(res, 200, { task: task.toJSON() });
          }
          if (b.action === "reject") {
            const task = mgr.reject(String(b.id), { by: board.owner, reason: b.reason || "Declined on board" });
            if (board?.config?.invite) {
              await mgr.pushToRelay(board.config.invite, task, "reject", { by: board.owner, reason: b.reason || "Declined on board" }).catch(() => {});
            }
            return json(res, 200, { task: task.toJSON() });
          }
          if (b.action === "request") {
            const task = mgr.request(b);
            if (board?.config?.invite) {
              await mgr.pushToRelay(board.config.invite, task, "request").catch(() => {});
            }
            return json(res, 200, { task: task.toJSON() });
          }
          return json(res, 400, { detail: "Unknown coordination action" });
        }
      }

      // File storage: whether the relay holds files up to 25 MB, and (admins, Cloudflare relays) turning it on.
      if (path === "/api/board/storage" && req.method === "GET") {
        const { isAdmin } = await import("./inbox.mjs");
        const relay = board.config.invite.relay;
        let files = null;
        try {
          files = (await (await fetch(`${relay}/v1/relay`, { signal: AbortSignal.timeout(5000) })).json()).files ?? null;
        } catch {}
        const cloudflare = /^chamber(?:-[a-z0-9]+)?\.[^.]+\.workers\.dev$/.test(new URL(relay).hostname);
        return json(res, 200, { files, cloudflare, admin: isAdmin(board.config), progress: live.storage?.progress || "" });
      }

      if (path === "/api/board/storage/enable" && req.method === "POST") {
        const { isAdmin } = await import("./inbox.mjs");
        if (!isAdmin(board.config)) return json(res, 403, { detail: "Only the chamber's admin can turn on file storage." });
        const { enableRelayStorage } = await import("./deploy.mjs");
        live.storage?.controller.abort();
        const job = (live.storage = { controller: new AbortController(), progress: "" });
        try {
          return json(res, 200, await enableRelayStorage({ relay: board.config.invite.relay, signal: job.controller.signal, onProgress: (msg) => (job.progress = msg) }));
        } catch (err) {
          if (job.controller.signal.aborted) return json(res, 409, { detail: "Cancelled." });
          throw err;
        } finally {
          if (live.storage === job) live.storage = null;
        }
      }

      // Cloudflare free-tier quota visibility
      if (path === "/api/board/quota" && req.method === "GET") {
        const quota = await board.quota();
        return json(res, 200, quota || { status: "unknown", dailyRequests: 0, dailyRequestsLimit: 100000 });
      }

      // The decision ledger: the person at the board rules on what agents proposed.
      if (path === "/api/board/decisions" || path === "/api/board/decisions/decide") {
        const { TeamMemory } = await import("./memory.mjs");
        const { DecisionLedger } = await import("./decisions.mjs");
        const ledger = new DecisionLedger(new TeamMemory(board.config.invite, `${board.owner}/board`), `${board.owner}/board`);
        if (req.method === "POST") {
          const { id, outcome, reason } = await body(req);
          return json(res, 200, { entry: await ledger.decide(String(id || ""), String(outcome || ""), { by: board.owner, reason: String(reason || "") }) });
        }
        return json(res, 200, { entries: (await ledger.list()).map(({ raw, ...entry }) => entry) });
      }

      // The contract registry, read-only here: where each consumer stands on each contract.
      if (path === "/api/board/contracts") {
        const { TeamMemory } = await import("./memory.mjs");
        const { ContractRegistry, openObjections } = await import("./contracts.mjs");
        const registry = new ContractRegistry(new TeamMemory(board.config.invite, `${board.owner}/board`), `${board.owner}/board`);
        const entries = await registry.list();
        const contracts = await Promise.all(
          entries.map(async (e) => {
            const { consumers } = await registry.status(e.name);
            return {
              name: e.name, title: e.title, version: e.version, summary: e.summary || "", producer: e.producer, updated: e.updated, decision: e.decision || "",
              consumers: consumers.map((c) => ({ agent: c.agent, state: c.state, note: c.ack?.note || "", date: c.ack?.date || "", lastVersion: c.lastVersion || 0 })),
              objections: openObjections(e),
            };
          })
        );
        return json(res, 200, { contracts });
      }

      // Team memory and dreaming.
      if (path.startsWith("/api/board/memory") || path === "/api/board/settings") {
        const { TeamMemory } = await import("./memory.mjs");
        const dreaming = await import("./dream.mjs");
        const memory = new TeamMemory(board.config.invite, `${board.owner}/board`);
        if (path === "/api/board/memory") return json(res, 200, { files: await memory.list() });
        if (path === "/api/board/memory/file") {
          if (req.method === "POST") {
            const { path: filePath, text, message, base } = await body(req);
            const current = await memory.read(filePath || "MEMORY.md");
            const resCommit = await memory.commit(
              [{ path: filePath || "MEMORY.md", text: String(text || ""), base: base ?? (current ? current.version : 0) }],
              message || `Edit ${filePath || "MEMORY.md"}`
            );
            return json(res, 200, resCommit);
          }
          const file = await memory.read(url.searchParams.get("path") || "MEMORY.md", Number(url.searchParams.get("version") || 0));
          return file ? json(res, 200, file) : json(res, 404, { detail: "No such file." });
        }
        if (path === "/api/board/memory/history") return json(res, 200, { commits: await memory.history(100) });
        if (path === "/api/board/memory/search") return json(res, 200, { hits: await memory.search(url.searchParams.get("q") || "") });
        if (path === "/api/board/memory/dreams") return json(res, 200, { dreams: dreaming.dreams(), running: live.dreaming ?? null });
        if (path === "/api/board/memory/dream" && req.method === "POST") {
          if (live.dreaming) return json(res, 409, { detail: "A dream is already running." });
          live.dreaming = { started: Date.now(), log: [] };
          dreaming
            .dream(board.config, { log: (kind, text) => live.dreaming?.log.push({ kind, text, ts: Date.now() }) })
            .catch((error) => ({ ok: false, reason: String(error.message) }))
            .then(() => (live.dreaming = null));
          return json(res, 200, { started: true });
        }
        if (path === "/api/board/settings") {
          if (req.method === "POST") {
            const { openrouterKey, dreamModel } = await body(req);
            dreaming.saveSettings({ ...(openrouterKey !== undefined ? { openrouterKey: String(openrouterKey).trim() } : {}), ...(dreamModel ? { dreamModel: String(dreamModel).trim() } : {}) });
          }
          const s = dreaming.settings();
          return json(res, 200, { hasKey: Boolean(dreaming.modelKey()), dreamModel: s.dreamModel || dreaming.DEFAULT_MODEL, lastDream: s.lastDream || 0 });
        }
      }
      if (path === "/api/board/revoke" && req.method === "POST") {
        if (!board.overview().admin) return json(res, 403, { detail: "Only your chamber's admin removes people." });
        const { owner, op } = await body(req);
        return json(res, 200, await board.revoke(String(owner || ""), op === "restore" ? "restore" : "revoke"));
      }
      if (path === "/api/board/name" && req.method === "POST") {
        rename(String((await body(req)).name || ""));
        board.close();
        live.board = new Board(loadConfig());
        live.board.start();
        return json(res, 200, { name: live.board.owner });
      }
      if (path === "/api/board/files") {
        const file = resolve(url.searchParams.get("path") || "");
        if (!file.startsWith(resolve(HOME, "agents") + sep) || !existsSync(file) || !statSync(file).isFile()) return json(res, 404, { detail: "No such file." });
        res.writeHead(200, { "content-type": "application/octet-stream", "content-disposition": `attachment; filename="${file.split(sep).pop()}"` });
        return createReadStream(file).pipe(res);
      }
      if (path === "/api/board/workspaces" && req.method === "POST") {
        const target = String((await body(req)).path || "").trim();
        if (!target || !existsSync(target) || !statSync(target).isDirectory()) throw new LineError(`not a folder: ${target || "(empty)"}`);
        const result = setup(target);
        return json(res, 200, { output: `In the chamber as ${board.owner}/${result.name}. Restart Claude Code there.` });
      }
      if (path === "/api/board/workspaces/pick" && req.method === "POST") {
        // The system's own folder picker (this server runs on your machine).
        const picked = await pickFolder();
        return json(res, 200, picked ? { path: picked } : { cancelled: true });
      }
      if (path === "/api/board/workspaces/remove" && req.method === "POST") {
        const target = String((await body(req)).path || "").trim();
        if (!target) throw new LineError("which folder?");
        teardown(target);
        return json(res, 200, { output: "Removed." });
      }
      if (path === "/api/board/channels" && req.method === "POST") {
        const { op, name, owner } = await body(req);
        const channel = String(name || "").trim().toLowerCase().replace(/^#?/, "#");
        await board.channel(String(op || "join"), channel, owner ? String(owner) : null);
        return json(res, 200, { ok: true, channel });
      }
      if (path === "/api/board/talks" && req.method === "POST") {
        const { to, text, title, files } = await body(req);
        return json(res, 200, await board.say(String(to || ""), String(text || ""), { title: String(title || ""), files }));
      }
      const talk = /^\/api\/board\/talks\/([^/]+)(\/done)?$/.exec(path);
      if (talk) {
        const id = decodeURIComponent(talk[1]);
        if (talk[2] && req.method === "POST") {
          const { done = true } = await body(req);
          board.markDone(id, Boolean(done));
          return json(res, 200, { ok: true, done: Boolean(done) });
        }
        if (req.method === "POST") {
          const { to, text, files } = await body(req);
          return json(res, 200, await board.say(String(to || ""), String(text || ""), { thread: id, files }));
        }
        if (url.searchParams.get("seen")) board.markSeen(id); // opened: read, like Gmail
        const found = board.conversation(id);
        return found ? json(res, 200, found) : json(res, 404, { detail: "No such conversation." });
      }
      if (path.startsWith("/api/")) return json(res, 404, { detail: "Not found." });

      // The board itself: a file from ../board, else its page.
      const file = resolve(STATIC, `.${path}`);
      if (path !== "/" && file.startsWith(STATIC + sep) && existsSync(file) && statSync(file).isFile()) return serveFile(res, file);
      if (!existsSync(join(STATIC, "index.html"))) return json(res, 500, { detail: "The board is not built (npm run build in ui/)." });
      return serveHtmlWithToken(res, join(STATIC, "index.html"), live.sessionToken);
    } catch (error) {
      return json(res, error instanceof LineError || error instanceof SyntaxError ? 400 : 500, { detail: String(error.message || error) });
    }
  };
}

/** The project the app was started in, if it was started in one (a Finder-launched app is at "/"). */
function startingProject() {
  try {
    return execFileSync("git", ["rev-parse", "--show-toplevel"], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim() || null;
  } catch {
    return null;
  }
}

function defaultOwner() {
  let git = "";
  try {
    git = execFileSync("git", ["config", "--global", "user.name"], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim().split(/\s+/)[0];
  } catch {}
  for (const guess of [git, process.env.USER || ""]) {
    const name = guess.toLowerCase().replace(/[^a-z0-9._-]+/g, "").replace(/^[^a-z0-9]+/, "").slice(0, 32);
    if (name) return name;
  }
  return `member-${randomBytes(2).toString("hex")}`;
}

/** Ask the OS to pick a folder: Finder on macOS, zenity/kdialog on Linux, the shell on Windows. */
function pickFolder() {
  const run = (cmd, args) =>
    new Promise((done) => execFile(cmd, args, { timeout: 300_000 }, (error, stdout) => done(error ? null : String(stdout).trim().replace(/\/$/, "") || null)));
  if (process.platform === "darwin") return run("osascript", ["-e", 'POSIX path of (choose folder with prompt "Choose a project folder to add to the chamber")']);
  if (process.platform === "win32")
    return run("powershell", ["-NoProfile", "-Command", "Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.FolderBrowserDialog; if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath }"]);
  return run("zenity", ["--file-selection", "--directory", "--title=Choose a project folder"]).then((p) => p ?? run("kdialog", ["--getexistingdirectory", "."]));
}

function openBrowser(url) {
  // No shell on any OS: on Windows `cmd /c start` would read "&" in a link as a second command.
  const [cmd, ...args] = process.platform === "darwin" ? ["open", url] : process.platform === "win32" ? ["rundll32", "url.dll,FileProtocolHandler", url] : ["xdg-open", url];
  execFile(cmd, args, () => {});
}

/** Serve the board; if one is already running on the port, just open it. */
export async function serveBoard(config = null, { port = 7878, open = true } = {}) {
  // Opened from the Finder, the app has the bare system PATH: find claude, codex, git like a terminal does.
  const { useFullPath } = await import("./path.mjs");
  useFullPath();
  const url = `http://127.0.0.1:${port}`;
  try {
    const running = await fetch(`${url}/api/board`, { signal: AbortSignal.timeout(1500) });
    if (running.ok) {
      console.log(`The board is already running: ${url}`);
      if (open) openBrowser(url);
      return 0;
    }
  } catch {}
  const live = { board: config ? new Board(config) : null, dreaming: null, awayResponder: null, awayStatus: "away in a meeting", setup: null };
  if (live.board) live.board.start();
  // Dreaming is off for now (lib/dream.mjs keeps it; `chamber dream` runs it by hand).
  const server = createServer(handler(live, port));
  await new Promise((done, fail) => server.once("error", fail).listen(port, "127.0.0.1", done));
  console.log(`Board: ${url}  (Ctrl-C to stop)`);
  if (open) openBrowser(url);
  await new Promise((done) => {
    for (const signal of ["SIGINT", "SIGTERM"]) process.once(signal, done);
  });
  if (live.awayResponder) await live.awayResponder.stop();
  if (live.board) live.board.close();
  server.close();
  return 0;
}
