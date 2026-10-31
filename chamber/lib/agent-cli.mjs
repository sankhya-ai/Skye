// Getting Claude Code and Codex ready from the app: are they installed, are you signed in, and
// install or sign in with one click. Installing uses each tool's own installer; signing in runs
// the tool's own browser sign-in. Each runs as a job the board polls (status, last output, a
// sign-in link in case the browser did not open).

import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const win = process.platform === "win32";
const which = (bin) => spawnSync(win ? "where" : "which", [bin], { encoding: "utf8" }).stdout?.split("\n")[0]?.trim() || null;
const run = (bin, args) => spawnSync(bin, args, { encoding: "utf8", timeout: 15_000, stdio: ["ignore", "pipe", "pipe"] });

/** What each tool needs: how to install it, sign in, and tell whether you are signed in. */
const TOOLS = {
  claude: {
    label: "Claude Code",
    bin: "claude",
    install: () => (win
      ? ["powershell", ["-NoProfile", "-Command", "irm https://claude.ai/install.ps1 | iex"]]
      : ["bash", ["-c", "curl -fsSL https://claude.ai/install.sh | bash"]]),
    login: ["auth", "login"],
    signedIn: (bin) => {
      try {
        return Boolean(JSON.parse(run(bin, ["auth", "status", "--json"]).stdout).loggedIn);
      } catch {
        return false;
      }
    },
  },
  codex: {
    label: "Codex",
    bin: "codex",
    install: () => {
      if (which("npm")) return [win ? "npm.cmd" : "npm", ["install", "-g", "@openai/codex"]];
      if (which("brew")) return ["brew", ["install", "codex"]];
      return null;
    },
    login: ["login"],
    signedIn: (bin) => run(bin, ["login", "status"]).status === 0 || existsSync(join(homedir(), ".codex", "auth.json")),
  },
};

const jobs = new Map(); // "claude:install" → { running, ok, output, link, at }

/** Is each tool installed (and which version), are you signed in, and what is running for it. */
export function agentTools() {
  return Object.entries(TOOLS).map(([id, tool]) => {
    const bin = which(tool.bin);
    const version = bin ? (run(bin, ["--version"]).stdout || "").trim().split("\n")[0] || null : null;
    const job = jobs.get(`${id}:install`)?.running ? jobs.get(`${id}:install`) : jobs.get(`${id}:login`);
    return {
      id,
      label: tool.label,
      installed: Boolean(bin),
      version,
      signedIn: bin ? tool.signedIn(bin) : false,
      job: job ? { action: job.action, running: job.running, ok: job.ok, output: job.output.slice(-400), link: job.link } : null,
    };
  });
}

/** Start installing a tool, or signing in to it. Resolves at once; the job runs on. */
export function startAgentTool(id, action) {
  const tool = TOOLS[id];
  if (!tool) throw new Error(`no tool ${id}`);
  if (action !== "install" && action !== "login") throw new Error("install or login");
  const key = `${id}:${action}`;
  if (jobs.get(key)?.running) return jobs.get(key);
  let command;
  if (action === "install") {
    command = tool.install();
    if (!command) throw new Error(`${tool.label} installs with npm or Homebrew; install Node.js (nodejs.org) first, then try again.`);
  } else {
    const bin = which(tool.bin);
    if (!bin) throw new Error(`Install ${tool.label} first.`);
    command = [bin, tool.login];
  }
  const job = { action, running: true, ok: null, output: "", link: null, at: Date.now() };
  jobs.set(key, job);
  const child = spawn(command[0], command[1], { stdio: ["ignore", "pipe", "pipe"], env: process.env });
  const take = (chunk) => {
    const text = String(chunk).replace(/\x1b\[[0-9;?]*[A-Za-z]/g, "");
    job.output = (job.output + text).slice(-4000);
    job.link ||= text.match(/https:\/\/[^\s"'<>]+/)?.[0] || null; // the sign-in page, if the browser did not open
  };
  child.stdout.on("data", take);
  child.stderr.on("data", take);
  child.on("error", (err) => Object.assign(job, { running: false, ok: false, output: job.output + `\n${err.message}` }));
  child.on("close", (code) => {
    job.running = false;
    job.ok = code === 0;
    // A new install lands in a folder this process may not have had on its PATH.
    if (action === "install") import("./path.mjs").then(({ fullPath }) => (process.env.PATH = fullPath()));
  });
  return job;
}
