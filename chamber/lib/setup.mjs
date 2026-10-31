// Put a project's Claude Code in the chamber: its MCP server (the connection and
// the tools) and the hooks that bring messages in. Everything goes in local,
// per-machine places — `claude mcp add -s local` and .claude/settings.local.json
// (kept out of git) — so nothing about the chamber is committed to the project.

import { execFileSync } from "node:child_process";
import { appendFileSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";

import * as others from "./harnesses.mjs";
import { HOME, loadConfig } from "./inbox.mjs";
import { LineError } from "./protocol.mjs";

/** Where root's board learns which folder an agent works in (so it can list and remove it). */
function projectFile(name) {
  const config = loadConfig();
  return config ? join(HOME, "agents", `${config.owner}__${name}`, "project.json") : null;
}


const slugOf = (project) => basename(project).toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^[^a-z0-9]+/, "") || "project";

export function defaultName(project, kind = "claude-code") {
  return `${kind}.${slugOf(project)}`.slice(0, 32);
}

/** Every coding agent chamber can connect, and whether this machine has it. */
export function harnessesHere() {
  // CHAMBER_HARNESSES (e.g. "claude,codex") overrides detection.
  const only = process.env.CHAMBER_HARNESSES;
  if (only) return Object.fromEntries(["claude", "codex", "antigravity", "dsh"].map((h) => [h, only.split(",").includes(h)]));
  return { claude: true, ...others.detected() };
}

function command(verb, name) {
  return others.agentCommand(verb, name);
}

function withoutOurs(groups) {
  return (groups || [])
    .map((group) => ({ ...group, hooks: (group.hooks || []).filter((h) => !others.MARKS.some((mark) => String(h.command || "").includes(mark))) }))
    .filter((group) => group.hooks.length);
}

export function hooksFor(name, { wake = true } = {}) {
  const show = { type: "command", command: command("hook", name), timeout: 10 };
  const waiter = { type: "command", command: command("wait", name), asyncRewake: true, timeout: 86400 };
  return {
    PostToolUse: [{ matcher: "*", hooks: [show] }],
    UserPromptSubmit: [{ hooks: [show] }],
    SessionStart: [{ hooks: wake ? [show, waiter] : [show] }],
    ...(wake ? { Stop: [{ hooks: [waiter] }] } : {}),
  };
}

function settingsPath(project) {
  return join(project, ".claude", "settings.local.json");
}

function writeHooks(project, hooks) {
  const path = settingsPath(project);
  let settings = {};
  if (existsSync(path)) {
    try {
      settings = JSON.parse(readFileSync(path, "utf8"));
    } catch {
      throw new LineError(`${path} is not valid JSON; fix it first`);
    }
  }
  const merged = {};
  for (const event of new Set([...Object.keys(settings.hooks || {}), ...Object.keys(hooks)])) {
    const groups = [...withoutOurs(settings.hooks?.[event]), ...(hooks[event] || [])];
    if (groups.length) merged[event] = groups;
  }
  settings.hooks = merged;
  if (!Object.keys(merged).length) delete settings.hooks;
  mkdirSync(join(project, ".claude"), { recursive: true });
  writeFileSync(path, `${JSON.stringify(settings, null, 2)}\n`);
  return path;
}

function keepOutOfGit(project) {
  const exclude = join(project, ".git", "info", "exclude");
  if (!existsSync(join(project, ".git"))) return;
  const text = existsSync(exclude) ? readFileSync(exclude, "utf8") : "";
  if (!text.includes(".claude/settings.local.json")) {
    mkdirSync(join(project, ".git", "info"), { recursive: true });
    appendFileSync(exclude, `${text.endsWith("\n") || !text ? "" : "\n"}.claude/settings.local.json\n`);
  }
}

function claude(args, project) {
  try {
    execFileSync("claude", args, { cwd: project, stdio: "pipe" });
    return true;
  } catch (error) {
    if (error.code === "ENOENT") return false;
    return null;
  }
}

function remember(agent, project) {
  const marker = projectFile(agent);
  if (marker) {
    mkdirSync(dirname(marker), { recursive: true });
    writeFileSync(marker, JSON.stringify({ path: project }));
  }
}

function keepOut(project, paths) {
  const exclude = join(project, ".git", "info", "exclude");
  if (!existsSync(join(project, ".git"))) return;
  let text = existsSync(exclude) ? readFileSync(exclude, "utf8") : "";
  for (const path of paths) {
    let tracked = false;
    try {
      execFileSync("git", ["ls-files", "--error-unmatch", path], { cwd: project, stdio: "ignore" });
      tracked = true;
    } catch {}
    if (!tracked && !text.split("\n").includes(path)) text += `${text.endsWith("\n") || !text ? "" : "\n"}${path}\n`;
  }
  mkdirSync(join(project, ".git", "info"), { recursive: true });
  writeFileSync(exclude, text);
}

/**
 * Put a project's coding agents in the chamber: Claude Code always, plus Codex and
 * Antigravity (per project) and DeepSeek Harness (one agent for you) where this machine has
 * them, or just `harnesses` if given (e.g. ["codex"]).
 */
export function setup(project, { name, wake = true, register = true, harnesses = null } = {}) {
  project = resolve(project);
  const want = new Set(harnesses ?? Object.entries(harnessesHere()).filter(([, here]) => here).map(([h]) => h));
  const done = [];
  let result = { name: name || defaultName(project), settings: null, registered: false, manual: null };
  if (want.has("claude")) {
    name ||= defaultName(project);
    const path = writeHooks(project, hooksFor(name, { wake }));
    keepOutOfGit(project);
    remember(name, project);
    const add = ["mcp", "add", "-s", "local", "chamber", "--", others.NODE, ...others.mcpArgs(name)];
    let registered = false;
    if (register) {
      claude(["mcp", "remove", "-s", "local", "chamber"], project);
      registered = claude(add, project) === true;
    }
    result = { name, settings: path, registered, manual: registered ? null : `claude ${add.map((a) => (a.includes(" ") ? `"${a}"` : a)).join(" ")}` };
    done.push({ harness: "claude", agent: name, path });
  }
  if (want.has("codex")) {
    const agent = defaultName(project, "codex");
    done.push({ harness: "codex", agent, path: others.codex(project, agent) });
    remember(agent, project);
    keepOut(project, [".codex/config.toml"]);
  }
  if (want.has("antigravity")) {
    const agent = defaultName(project, "antigravity");
    done.push({ harness: "antigravity", agent, path: others.antigravity(project, agent) });
    remember(agent, project);
    keepOut(project, [".agents/mcp_config.json", ".agents/hooks.json"]);
  }
  if (want.has("dsh")) done.push({ harness: "dsh", agent: "dsh", path: others.dsh("dsh") });
  return { ...result, harnesses: done };
}

export function teardown(project, { register = true, name } = {}) {
  project = resolve(project);
  writeHooks(project, {});
  for (const agent of [name || defaultName(project), defaultName(project, "codex"), defaultName(project, "antigravity")]) {
    const marker = projectFile(agent);
    if (marker) rmSync(marker, { force: true });
  }
  if (register) claude(["mcp", "remove", "-s", "local", "chamber"], project);
  if (existsSync(join(project, ".codex", "config.toml"))) others.codex(project, "", { remove: true });
  if (existsSync(join(project, ".agents", "mcp_config.json")) || existsSync(join(project, ".agents", "hooks.json"))) others.antigravity(project, "", { remove: true });
}
