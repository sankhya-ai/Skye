#!/usr/bin/env node
import { spawn } from "node:child_process";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(fileURLToPath(import.meta.url), "../..");
const BACKEND_SCRIPT = resolve(ROOT, "scripts/subagent_backend.mjs");
const FRONTEND_SCRIPT = resolve(ROOT, "scripts/subagent_frontend.mjs");

const CYAN = "\x1b[36m";
const GREEN = "\x1b[32m";
const YELLOW = "\x1b[33m";
const RED = "\x1b[31m";
const BOLD = "\x1b[1m";
const RESET = "\x1b[0m";

console.log(`${BOLD}================================================================${RESET}`);
console.log(`${BOLD}    CHAMBER INTER-AGENT COMMUNICATION TEST RUNNER              ${RESET}`);
console.log(`${BOLD}================================================================${RESET}`);
console.log(`Spawning 2 autonomous subagents talking over Chamber's encrypted relay:\n` +
            `  Subagent 1: ${CYAN}Frontend UI Agent${RESET}\n` +
            `  Subagent 2: ${GREEN}Backend & Protocol Agent${RESET}\n`);

// 1. Start Backend Subagent first so it is listening
const backendProc = spawn(process.execPath, [BACKEND_SCRIPT], {
  cwd: ROOT,
  env: process.env,
  stdio: ["ignore", "pipe", "pipe"],
});

backendProc.stdout.on("data", (chunk) => {
  const lines = chunk.toString().trim().split("\n");
  for (const l of lines) {
    console.log(`${GREEN}[Backend Agent]${RESET} ${l}`);
  }
});

backendProc.stderr.on("data", (chunk) => {
  const lines = chunk.toString().trim().split("\n");
  for (const l of lines) {
    console.error(`${RED}[Backend Agent ERR]${RESET} ${l}`);
  }
});

// Give backend subagent 2 seconds to connect to the relay before launching frontend
await new Promise((r) => setTimeout(r, 2000));

// 2. Start Frontend Subagent
const frontendProc = spawn(process.execPath, [FRONTEND_SCRIPT], {
  cwd: ROOT,
  env: process.env,
  stdio: ["ignore", "pipe", "pipe"],
});

frontendProc.stdout.on("data", (chunk) => {
  const lines = chunk.toString().trim().split("\n");
  for (const l of lines) {
    console.log(`${CYAN}[Frontend Agent]${RESET} ${l}`);
  }
});

frontendProc.stderr.on("data", (chunk) => {
  const lines = chunk.toString().trim().split("\n");
  for (const l of lines) {
    console.error(`${RED}[Frontend Agent ERR]${RESET} ${l}`);
  }
});

let frontendExited = false;
let backendExited = false;
let exitCode = 0;

const timeoutTimer = setTimeout(() => {
  console.error(`\n${RED}❌ TIMEOUT: Inter-agent conversation did not conclude within 30 seconds.${RESET}`);
  try { frontendProc.kill("SIGTERM"); } catch {}
  try { backendProc.kill("SIGTERM"); } catch {}
  process.exit(1);
}, 30000);

frontendProc.on("exit", (code) => {
  frontendExited = true;
  console.log(`\n${CYAN}ℹ [Frontend Agent] exited with code ${code}${RESET}`);
  if (code !== 0) exitCode = code || 1;
  checkFinished();
});

backendProc.on("exit", (code) => {
  backendExited = true;
  console.log(`\n${GREEN}ℹ [Backend Agent] exited with code ${code}${RESET}`);
  if (code !== 0) exitCode = code || 1;
  checkFinished();
});

function checkFinished() {
  if (frontendExited && backendExited) {
    clearTimeout(timeoutTimer);
    console.log(`\n${BOLD}================================================================${RESET}`);
    if (exitCode === 0) {
      console.log(`${BOLD}${GREEN}✔ TEST PASSED: Subagents successfully coordinated and resolved the task!${RESET}`);
    } else {
      console.log(`${BOLD}${RED}❌ TEST FAILED: One or more subagents exited with error code ${exitCode}${RESET}`);
    }
    console.log(`${BOLD}================================================================${RESET}\n`);
    process.exit(exitCode);
  }
}
