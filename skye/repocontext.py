"""Lightweight per-repo context for automatic repo routing.

When the owner declares several repos and asks Skye to start sessions, the PLANNER routes each
step to the repo the work belongs to (the owner's 2026-11-01 ask: no hand-flagging
backend/frontend per task). To choose, the planner needs a little truthful context per repo -
never a full tree dump, and never anything outside the owner-declared set. This module reads
ONLY the declared paths, with hard caps that are visible in what it returns, so the brief
stays small and nothing about the trimming is hidden.
"""

from __future__ import annotations

import json
from pathlib import Path

#: Well-known manifest files, detected by name only (plus package.json's "name" field, which
#: is plain JSON and cheap to read). Detection is reported as-is - a manifest says what the
#: repo likely is, it does not prove it.
MANIFESTS = ("package.json", "pyproject.toml", "go.mod", "Cargo.toml", "pom.xml",
             "build.gradle", "build.gradle.kts", "composer.json", "Gemfile",
             "Package.swift", "pubspec.yaml", "requirements.txt")


def repo_digests(repos: dict[str, str], *, max_entries: int = 40,
                 max_readme: int = 1000) -> dict[str, dict]:
    """One small digest per DECLARED repo. A missing/unreadable path is reported as itself,
    never faked as an empty repo. Directories sort before files; entries beyond max_entries
    are counted, not listed."""
    digests: dict[str, dict] = {}
    for name, raw_path in repos.items():
        path = Path(str(raw_path))
        digest: dict = {"path": str(path), "exists": path.is_dir()}
        if not path.is_dir():
            digest["error"] = "declared path does not exist or is not a directory"
            digests[str(name)] = digest
            continue
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError as error:
            digest["error"] = f"cannot list: {error}"
            digests[str(name)] = digest
            continue
        shown = [(p.name + "/" if p.is_dir() else p.name) for p in entries[:max_entries]]
        digest["top"] = shown
        digest["overflow"] = max(0, len(entries) - len(shown))
        found = [m for m in MANIFESTS if (path / m).is_file()]
        digest["manifests"] = found
        if "package.json" in found:
            try:
                package_name = json.loads((path / "package.json").read_text("utf-8")).get("name")
                if package_name:
                    digest["package_name"] = str(package_name)
            except (OSError, ValueError):
                digest["package_name_error"] = "package.json present but unreadable"
        readmes = sorted(p for p in path.glob("README*") if p.is_file())
        if readmes:
            try:
                text = readmes[0].read_text("utf-8", errors="replace")
                digest["readme"] = text[:max_readme]
                digest["readme_truncated"] = len(text) > max_readme
            except OSError:
                digest["readme_error"] = f"{readmes[0].name} present but unreadable"
        digests[str(name)] = digest
    return digests


def format_for_brief(digests: dict[str, dict], *, max_chars: int = 3000) -> str:
    """The repositories section for the planner's brief. Every declared repo gets an equal
    share of the budget, so a fat early repo can never starve a later one out of the brief:
    each line is trimmed to its own share with the trim marked, and the rare whole-section
    overflow says exactly what happened. This is the planner's ONLY per-repo context - it
    must be honest about what it leaves out."""
    names = list(digests)
    share = max(160, max_chars // max(1, len(names)))
    lines = []
    for name in names:
        digest = digests[name]
        if not digest.get("exists"):
            line = f"- {name}: {digest['path']} - {digest.get('error', 'unavailable')}"
        else:
            parts = [f"- {name}: {digest['path']}"]
            if digest.get("package_name"):
                parts.append(f"package '{digest['package_name']}'")
            if digest.get("manifests"):
                parts.append("manifests: " + ", ".join(digest["manifests"]))
            top = digest.get("top") or []
            listing = ", ".join(top[:12])
            if len(top) > 12 or digest.get("overflow"):
                listing += f", ... ({digest.get('overflow', 0) + max(0, len(top) - 12)} more)"
            if listing:
                parts.append("top level: " + listing)
            if digest.get("readme"):
                first_lines = " / ".join(
                    ln.strip() for ln in digest["readme"].splitlines()[:3] if ln.strip())[:300]
                if first_lines:
                    parts.append("readme: " + first_lines)
            line = "; ".join(parts)
        if len(line) > share:
            line = line[:share - 26] + f" ... (+{len(line) - share + 26} chars trimmed)"
        lines.append(line)
    text = "\n".join(lines)
    if len(text) > max_chars:
        # Second pass, name-level: every repo stays visible, detail goes.
        lines = [f"- {name}: {digests[name]['path']} "
                 f"({digests[name].get('error') or 'details trimmed to fit the budget'})"
                 for name in names]
        text = "\n".join(lines)
    if len(text) > max_chars:
        # Last resort: even names don't fit. Dropped repos are NAMED, never vanished.
        kept, dropped, used = [], [], 0
        for line in lines:
            if used + len(line) + 1 <= max(0, max_chars - 120):
                kept.append(line)
                used += len(line) + 1
            else:
                dropped.append(line.split(":", 1)[0].lstrip("- "))
        text = ("\n".join(kept)
                + f"\n(budget {max_chars} chars; detail cut to fit; repos left out of this "
                  f"briefing and needing the owner's own words: {', '.join(dropped)})")
    return text
def discover_repos(root: str | Path, *, max_repos: int = 24) -> dict:
    """Bounded repo discovery INSIDE one owner-declared folder.

    The declared folder is the whole scope, never the filesystem: when it is itself a git
    repository, that is the one repo; otherwise only its IMMEDIATE subfolders are checked
    for a .git entry (one level, no recursive scan, hidden folders skipped). Everything
    found AND everything passed over is reported, so discovery never quietly returns an
    empty or partial scope. Names come from the subfolder names - the owner declared the
    folder, the folder names the repos."""
    root_path = Path(str(root)).expanduser()
    result: dict = {"root": str(root_path), "root_exists": root_path.is_dir(),
                    "root_is_repo": False, "repos": {}, "skipped": 0, "overflow": []}
    if not root_path.is_dir():
        result["error"] = "declared path does not exist or is not a directory"
        return result
    if (root_path / ".git").exists():
        result["root_is_repo"] = True
        result["repos"] = {root_path.resolve().name or "repo": str(root_path)}
        return result
    try:
        children = sorted((p for p in root_path.iterdir()
                           if p.is_dir() and not p.name.startswith(".")),
                          key=lambda p: p.name.lower())
    except OSError as error:
        result["error"] = f"cannot list: {error}"
        return result
    repos: dict[str, str] = {}
    for child in children:
        if not (child / ".git").exists():
            result["skipped"] += 1
            continue
        if len(repos) >= max_repos:
            result["overflow"].append(child.name)
            continue
        name, suffix = child.name, 2
        while name in repos:
            name, suffix = f"{child.name}-{suffix}", suffix + 1
        repos[name] = str(child)
    result["repos"] = repos
    return result
