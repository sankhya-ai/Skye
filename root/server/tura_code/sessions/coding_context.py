"""Detecting workspace project facts and verification capabilities.

Inspired by Hermes Agent's coding context engine: detects project root,
package managers, manifest scripts, git status, and canonical verification commands
so the agent operates with senior engineer context from step 1.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("tura_code.coding_context")

_CACHE_TTL = 30.0  # seconds
_FACTS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

# Markers indicating project roots
_PROJECT_MARKERS = (
    "pyproject.toml",
    "setup.py",
    "requirements.txt",
    "package.json",
    "tsconfig.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "Gemfile",
    "composer.json",
    "Makefile",
    "CMakeLists.txt",
    "AGENTS.md",
    "CLAUDE.md",
    ".cursorrules",
)

_VERIFY_KEYWORDS = ("test", "lint", "typecheck", "check", "build")


def _find_project_root(workspace: Path) -> Path:
    """Find closest root containing any known project marker, bounded by 5 parents."""
    resolved = workspace.resolve()
    for candidate in [resolved, *resolved.parents[:5]]:
        if candidate == Path.home() or str(candidate) == "/":
            break
        if (candidate / ".git").exists() or any((candidate / marker).exists() for marker in _PROJECT_MARKERS):
            return candidate
    return resolved


def _detect_python_facts(root: Path) -> tuple[str | None, list[str]]:
    """Detect Python package manager and test runner."""
    pm = None
    commands: list[str] = []
    if (root / "uv.lock").exists():
        pm = "uv"
        commands.append("uv run pytest")
    elif (root / "poetry.lock").exists():
        pm = "poetry"
        commands.append("poetry run pytest")
    elif (root / "Pipfile.lock").exists():
        pm = "pipenv"
        commands.append("pipenv run pytest")
    elif (root / "pyproject.toml").exists() or (root / "setup.py").exists() or (root / "requirements.txt").exists():
        pm = "python"
        commands.append("pytest")

    # Linters / typecheckers
    if (root / "ruff.toml").exists() or (root / ".ruff.toml").exists():
        commands.append(f"{pm} run ruff check" if pm in ("uv", "poetry") else "ruff check")
    if (root / "mypy.ini").exists() or (root / ".mypy.ini").exists():
        commands.append(f"{pm} run mypy ." if pm in ("uv", "poetry") else "mypy .")

    return pm, commands


def _detect_node_facts(root: Path) -> tuple[str | None, list[str]]:
    """Detect Node / TypeScript package manager and scripts from package.json."""
    pkg_json = root / "package.json"
    if not pkg_json.is_file():
        return None, []

    pm = "npm"
    if (root / "pnpm-lock.yaml").exists():
        pm = "pnpm"
    elif (root / "bun.lockb").exists() or (root / "bun.lock").exists():
        pm = "bun"
    elif (root / "yarn.lock").exists():
        pm = "yarn"

    commands: list[str] = []
    try:
        data = json.loads(pkg_json.read_text(encoding="utf-8"))
        scripts = data.get("scripts") if isinstance(data, dict) else {}
        if isinstance(scripts, dict):
            # Prioritize test, then typecheck, then lint, then build
            for target in ("test", "typecheck", "lint", "check", "build"):
                if target in scripts:
                    commands.append(f"{pm} test" if target == "test" else f"{pm} run {target}")
            # Also look for any script matching verify keywords
            for name in scripts:
                if any(kw in name.lower() for kw in _VERIFY_KEYWORDS):
                    cmd = f"{pm} test" if name == "test" else f"{pm} run {name}"
                    if cmd not in commands and len(commands) < 5:
                        commands.append(cmd)
    except Exception:
        pass

    if not commands:
        commands.append(f"{pm} test")
    return pm, commands


def _detect_rust_facts(root: Path) -> tuple[str | None, list[str]]:
    if not (root / "Cargo.toml").is_file():
        return None, []
    return "cargo", ["cargo test", "cargo check"]


def _detect_go_facts(root: Path) -> tuple[str | None, list[str]]:
    if not (root / "go.mod").is_file():
        return None, []
    return "go", ["go test ./...", "go vet ./..."]


def _detect_git_facts(root: Path) -> dict[str, Any]:
    """Inspect git branch and status safely with a 1.0s timeout."""
    git_dir = root / ".git"
    if not git_dir.exists() and not (root.parent / ".git").exists():
        return {}
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(root),
            stderr=subprocess.DEVNULL,
            timeout=1.0,
            encoding="utf-8",
            errors="replace",
        ).strip()
        status_out = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=str(root),
            stderr=subprocess.DEVNULL,
            timeout=1.0,
            encoding="utf-8",
            errors="replace",
        ).strip()
        dirty_count = len(status_out.splitlines()) if status_out else 0
        return {"branch": branch, "dirty_count": dirty_count}
    except Exception:
        return {}


def detect_project_facts(workspace: Path) -> dict[str, Any]:
    """Analyze a workspace directory and return high-signal project facts."""
    cache_key = str(workspace.resolve())
    now = time.time()
    if cache_key in _FACTS_CACHE:
        ts, cached = _FACTS_CACHE[cache_key]
        if now - ts < _CACHE_TTL:
            return cached

    root = _find_project_root(workspace)
    stack = "unknown"
    pm = None
    verify_commands: list[str] = []

    # 1. Rust
    rust_pm, rust_cmds = _detect_rust_facts(root)
    if rust_pm:
        stack, pm = "Rust", rust_pm
        verify_commands.extend(rust_cmds)

    # 2. Node / TS
    node_pm, node_cmds = _detect_node_facts(root)
    if node_pm:
        is_ts = (root / "tsconfig.json").is_file()
        stack, pm = ("TypeScript" if is_ts else "Node.js"), node_pm
        verify_commands.extend(node_cmds)

    # 3. Python
    py_pm, py_cmds = _detect_python_facts(root)
    if py_pm:
        stack, pm = "Python", py_pm
        verify_commands.extend(py_cmds)

    # 4. Go
    go_pm, go_cmds = _detect_go_facts(root)
    if go_pm:
        stack, pm = "Go", go_pm
        verify_commands.extend(go_cmds)

    # 5. Makefile fallback
    if (root / "Makefile").is_file() and not verify_commands:
        verify_commands.append("make test")

    git_info = _detect_git_facts(root)

    # Instructions detected
    instructions = [f for f in ("AGENTS.md", "CLAUDE.md", ".cursorrules") if (root / f).is_file()]

    facts = {
        "root": str(root),
        "stack": stack,
        "package_manager": pm,
        "verify_commands": verify_commands[:4],
        "git": git_info,
        "instructions": instructions,
    }
    _FACTS_CACHE[cache_key] = (now, facts)
    return facts


def format_context_block(facts: dict[str, Any]) -> str:
    """Format project facts as a concise block for the system envelope."""
    if not facts or facts.get("stack") == "unknown" and not facts.get("git") and not facts.get("verify_commands"):
        return ""

    lines = ["Workspace Facts:"]
    stack_part = facts.get("stack", "")
    pm = facts.get("package_manager")
    if pm:
        stack_part = f"{stack_part} ({pm})" if stack_part != "unknown" else pm

    git = facts.get("git") or {}
    git_part = ""
    if git.get("branch"):
        dirty = git.get("dirty_count", 0)
        status = f"{dirty} modified" if dirty > 0 else "clean"
        git_part = f" | Git: `{git['branch']}` ({status})"

    if stack_part or git_part:
        lines.append(f"- Stack: {stack_part or 'General'}{git_part}")

    cmds = facts.get("verify_commands") or []
    if cmds:
        cmd_str = ", ".join(f"`{c}`" for c in cmds)
        lines.append(f"- Verification commands: {cmd_str}")

    instructions = facts.get("instructions") or []
    if instructions:
        inst_str = ", ".join(f"`{i}`" for i in instructions)
        lines.append(f"- Project guidelines: {inst_str}")

    return "\n".join(lines)
