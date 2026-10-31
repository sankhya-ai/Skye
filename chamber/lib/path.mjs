// An app opened from the Finder (or the Dock, or at login) gets the system's bare PATH, not the
// one your terminal has, so `claude` and `codex` (in ~/.local/bin, Homebrew, nvm, …) are not
// found. Before it looks for them, the app's daemon takes your login shell's PATH, plus the
// places these CLIs install to.

import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir, userInfo } from "node:os";
import { delimiter, join } from "node:path";

const MARK = "__chamber_path__";

/** The PATH your login shell sets up, or "" (Windows, no shell, or it took too long). */
function loginShellPath() {
  if (process.platform === "win32") return "";
  const shell = process.env.SHELL || (process.platform === "darwin" ? "/bin/zsh" : "/bin/bash");
  try {
    const out = execFileSync(shell, ["-ilc", `printf '${MARK}%s${MARK}' "$PATH"`], { encoding: "utf8", timeout: 4000, stdio: ["ignore", "pipe", "ignore"] });
    return out.split(MARK)[1] || "";
  } catch {
    return "";
  }
}

/** Where Claude Code, Codex and Node's global installs usually live. */
function usualPlaces() {
  const home = homedir();
  return [
    join(home, ".local", "bin"), // Claude Code's own installer
    join(home, ".claude", "local"),
    "/opt/homebrew/bin",
    "/usr/local/bin",
    join(home, ".npm-global", "bin"),
    join(home, ".bun", "bin"),
    join(home, ".volta", "bin"),
    ...(process.platform === "win32" && process.env.APPDATA ? [join(process.env.APPDATA, "npm")] : []),
  ];
}

/** PATH with the login shell's and the usual places added after what is already there. Idempotent. */
export function fullPath(current = process.env.PATH || "", { shell = loginShellPath() } = {}) {
  const parts = current.split(delimiter).filter(Boolean);
  for (const dir of [...shell.split(delimiter), ...usualPlaces()]) {
    if (dir && !parts.includes(dir) && existsSync(dir)) parts.push(dir);
  }
  return parts.join(delimiter);
}

let done = false;
/** Widen this process's PATH once (children inherit it), and make sure USER is set: Claude Code
 * finds its sign-in in the macOS Keychain by it. */
export function useFullPath() {
  if (done) return;
  done = true;
  process.env.PATH = fullPath();
  try {
    process.env.USER ||= userInfo().username;
    process.env.LOGNAME ||= process.env.USER;
  } catch {}
}
