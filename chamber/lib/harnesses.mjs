// Coding agents other than Claude Code: where each keeps its MCP servers and hooks, and how
// chamber goes in (and back out) without touching the rest of their settings.
//
//   codex        <project>/.codex/config.toml   [mcp_servers.chamber] + [[hooks.*]] in a marked block
//   antigravity  <project>/.agents/mcp_config.json + .agents/hooks.json ("chamber" entries)
//   dsh          ~/.dsh/cordis.patch.yml (DeepSeek Harness home patch: every profile), one
//                agent per person; hooks run through its Claude Code hooks bridge
//
// Messages reach these agents the way their hooks allow: at session start, during work
// (after tool calls, or before model calls), and when they are about to stop with a
// message waiting (they keep going). None of them can be woken while idle (Claude Code can).

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { HOME, ROOT } from "./inbox.mjs";
import { LineError } from "./protocol.mjs";

const BIN = resolve(fileURLToPath(import.meta.url), "../../bin/chamber.mjs");
/** Marks our hook commands so `chamber remove` can find them. A POSIX shell takes an env prefix; cmd and
 *  PowerShell do not, so on Windows the mark is a flag the command ignores. */
export const MARKS = ["CHAMBER=1", "--chamber-hook"];
export const withMark = (command) => (process.platform === "win32" ? `${command} --chamber-hook` : `CHAMBER=1 ${command}`);
/** Inside the downloaded app there is its own node; agents must not need one installed. */
export const NODE = process.env.CHAMBER_BUNDLED ? process.execPath : "node";
const BEGIN = "# chamber:begin (added by `chamber setup`; `chamber remove` takes it out)";
const END = "# chamber:end";

/** Which chamber an agent's commands belong to: the first one needs no flag (configs from before stay valid). */
export const homeArgs = () => (HOME === ROOT ? [] : ["--home", HOME]);
const homeFlag = () => (HOME === ROOT ? "" : ` --home "${HOME}"`);
export const mcpArgs = (agent) => [BIN, "mcp", "--agent", agent, ...homeArgs()];
const hookCommand = (agent, format, event = "") => withMark(`"${NODE}" "${BIN}" hook --agent ${agent} --format ${format}${event ? ` --event ${event}` : ""}${homeFlag()}`);
export const agentCommand = (verb, agent) => withMark(`"${NODE}" "${BIN}" ${verb} --agent ${agent}${homeFlag()}`);

/** Which harnesses this machine has (by their config folders, so desktop apps count too). */
export function detected() {
  const home = homedir();
  return {
    codex: existsSync(join(home, ".codex")),
    antigravity: existsSync(join(home, ".gemini", "antigravity")) || existsSync(join(home, ".gemini", "antigravity-cli")),
    dsh: existsSync(process.env.DSH_HOME || join(home, ".dsh")),
  };
}

function readJson(path, fallback) {
  if (!existsSync(path)) return fallback;
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    throw new LineError(`${path} is not valid JSON; fix it first`);
  }
}

function writeJson(path, data) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${JSON.stringify(data, null, 2)}\n`);
}

/** Replace (or remove, with block "") our marked block in a text file. */
function withBlock(path, block) {
  const text = existsSync(path) ? readFileSync(path, "utf8") : "";
  const start = text.indexOf(BEGIN);
  const end = text.indexOf(END, start);
  const rest = start >= 0 && end >= 0 ? text.slice(0, start) + text.slice(end + END.length) : text;
  const trimmed = rest.replace(/\n{3,}/g, "\n\n").replace(/\s+$/, "");
  const next = block ? `${trimmed}${trimmed ? "\n\n" : ""}${BEGIN}\n${block}\n${END}\n` : trimmed ? `${trimmed}\n` : "";
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, next);
}

const toml = (s) => JSON.stringify(s); // a TOML basic string is a JSON string

// ----- Codex ----------------------------------------------------------------------------

export function codex(project, agent, { remove = false } = {}) {
  const path = join(project, ".codex", "config.toml");
  if (remove) return withBlock(path, ""), path;
  const hook = (event, matcher) => [`[[hooks.${event}]]`, ...(matcher ? [`matcher = ${toml(matcher)}`] : []), `[[hooks.${event}.hooks]]`, `type = "command"`, `command = ${toml(hookCommand(agent, "codex"))}`, `timeout = 10`, ""].join("\n");
  withBlock(path, [
    "[mcp_servers.chamber]",
    `command = ${toml(NODE)}`,
    `args = ${JSON.stringify(mcpArgs(agent))}`,
    "",
    hook("SessionStart"),
    hook("UserPromptSubmit"),
    hook("PostToolUse", "*"),
    hook("Stop"),
  ].join("\n").trimEnd());
  return path;
}

// ----- Antigravity -----------------------------------------------------------------------

export function antigravity(project, agent, { remove = false } = {}) {
  const mcpPath = join(project, ".agents", "mcp_config.json");
  const hooksPath = join(project, ".agents", "hooks.json");
  const mcp = readJson(mcpPath, {});
  const hooks = readJson(hooksPath, {});
  mcp.mcpServers ??= {};
  delete mcp.mcpServers.chamber;
  delete hooks.chamber;
  if (!remove) {
    mcp.mcpServers.chamber = { command: NODE, args: mcpArgs(agent) };
    const handler = (event) => [{ type: "command", command: hookCommand(agent, "antigravity", event), timeout: 10 }];
    hooks.chamber = { SessionStart: handler("SessionStart"), PreInvocation: handler("PreInvocation"), Stop: handler("Stop") };
  }
  if (remove && !Object.keys(mcp.mcpServers).length) delete mcp.mcpServers;
  writeJson(mcpPath, mcp);
  writeJson(hooksPath, hooks);
  return mcpPath;
}

// ----- DeepSeek Harness ------------------------------------------------------------------

export function dsh(agent, { remove = false } = {}) {
  const home = process.env.DSH_HOME || join(homedir(), ".dsh");
  const patch = join(home, "cordis.patch.yml");
  const hooksFile = join(HOME, "dsh-hooks.json");
  if (remove) return withBlock(patch, ""), patch;
  // The bridge runs Claude Code-format hooks; ours, for this one agent.
  const group = (event, matcher) => [{ ...(matcher ? { matcher } : {}), hooks: [{ type: "command", command: hookCommand(agent, "claude"), timeout: 10 }] }];
  writeJson(hooksFile, { hooks: { SessionStart: group(), UserPromptSubmit: group(), PostToolUse: group(null, "*"), Stop: group() } });
  const q = (s) => `'${String(s).replace(/'/g, "''")}'`; // YAML single-quoted
  withBlock(patch, [
    "- id: chamber-mcp",
    `  name: '@deepseek-ai/dsh-mcp-client'`,
    "  config:",
    "    serverName: chamber",
    "    transport: stdio",
    "    command: node",
    `    args: [${mcpArgs(agent).map(q).join(", ")}]`,
    "- id: chamber-hooks",
    `  name: '@deepseek-ai/dsh-hooks-claude-code'`,
    "  config:",
    `    configPath: ${q(hooksFile)}`,
  ].join("\n"));
  return patch;
}
