"""Replaying the owner-configured journey command, with honest limits.

The journey command is a plain shell command the OWNER writes into the team policy
(Settings) - often just a smoke test. Exit 0 means the replay passed; anything else means
the product still fails that check. This module never invents a pass: no command, a
timeout, or a non-zero exit are all recorded as failures with evidence.

Evidence discipline: only a bounded, redacted output tail is recorded - never the full
output, and secret-looking assignments (TOKEN=..., Bearer ...) are masked before storage,
because journey output ends up in the backlog record and the UI.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

EVIDENCE_LIMIT = 2000
DEFAULT_TIMEOUT_SECONDS = 600

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)[A-Z0-9_]*)"
    r"\s*[:=]\s*('[^']*'|\"[^\"]*\"|\S+)")
_BEARER = re.compile(r"(?i)\bBearer\s+\S+")


def redact(text: str) -> str:
    """Mask secret-looking values so recorded evidence cannot leak credentials."""
    text = _BEARER.sub("Bearer [redacted]", text)
    return _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}=[redacted]", text)


def repo_revision(cwd: str) -> str:
    """The repo revision a replay ran against: the git HEAD when the repo is git, else a
    digest manifest of the files - so recorded evidence is bound to an actual tree, never
    to a bare claim."""
    import hashlib
    root = Path(cwd) if cwd else Path.cwd()
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                              text=True, timeout=10)
        if head.returncode == 0:
            return "git:" + head.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return "tree:" + digest.hexdigest()[:16]


MANIFEST_FILE_LIMIT = 2000


def _manifest_paths(root: Path) -> list[Path]:
    """The files a change record is about. In a git repository: tracked files plus untracked
    files git does not ignore - so dependency installs and build output (node_modules/,
    dist/) never flood the record or push it past its limit into fake deletions. Outside
    git, every file."""
    import subprocess
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True, timeout=60)
        top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        proc = top = None
    if proc is not None and proc.returncode == 0 and top is not None and top.returncode == 0 \
            and Path(top.stdout.strip()).resolve() == root.resolve():
        names = sorted({n for n in proc.stdout.decode("utf-8", "surrogateescape").split("\0") if n})
        return [p for p in (root / n for n in names) if p.is_file()]
    return sorted(p for p in root.rglob("*") if p.is_file())


def repo_manifest(cwd: str, *, limit: int = MANIFEST_FILE_LIMIT) -> tuple[dict[str, str], bool]:
    """A path -> content-hash map of the repo's files (bounded; .git internals skipped).

    This is the diffable form of repo_revision's non-git fallback: two manifests taken at
    different times yield real added/modified/deleted file lists for ANY repo, git or not -
    so 'what the mission actually changed' is derived from the tree, never from a worker's
    own claims. Returns (manifest, truncated): truncated means files beyond the limit were
    not hashed and the diff is known-incomplete (surfaced, never hidden)."""
    import hashlib
    root = Path(cwd) if cwd else Path.cwd()
    manifest: dict[str, str] = {}
    truncated = False
    if not root.is_dir():
        return manifest, truncated
    for path in _manifest_paths(root):
        rel = str(path.relative_to(root))
        if rel == ".git" or rel.startswith(".git/") or "/.git/" in rel:
            continue
        if len(manifest) >= limit:
            truncated = True
            break
        try:
            manifest[rel] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        except OSError:
            continue
    return manifest, truncated


def diff_manifests(start: dict[str, str], end: dict[str, str]) -> dict[str, list[str]]:
    """added/modified/deleted path lists between two repo_manifest snapshots, sorted."""
    # A worktree's ".git" is a pointer FILE, which older records hashed; it is never work.
    start = {p: h for p, h in start.items() if p != ".git"}
    end = {p: h for p, h in end.items() if p != ".git"}
    added = sorted(p for p in end if p not in start)
    deleted = sorted(p for p in start if p not in end)
    modified = sorted(p for p in end if p in start and end[p] != start[p])
    return {"added": added, "modified": modified, "deleted": deleted}


def journey_digest(command: str, cwd: str) -> str:
    """A digest of the journey CHECK itself: every token of the command that names an existing
    file is hashed, so a worker quietly rewriting the check inside its writable repo shows up
    as a changed digest on the next replay. DETECTION, not enforcement - the hard guarantee is
    keeping the journey script outside the team's writable repo (the command may point anywhere)."""
    import hashlib
    root = Path(cwd) if cwd else Path.cwd()
    digest = hashlib.sha256()
    found = False
    for token in command.split():
        candidate = (root / token).resolve()
        try:
            if candidate.is_file():
                digest.update(token.encode())
                digest.update(candidate.read_bytes())
                found = True
        except OSError:
            continue
    return digest.hexdigest()[:16] if found else ""


def run_journey(command: str, cwd: str = "",
                timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Run the journey command once and return a bounded, redacted verdict."""
    try:
        proc = subprocess.run(command, cwd=cwd or None, shell=True, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"passed": False, "timed_out": True, "returncode": None,
                "evidence": f"journey replay timed out after {timeout}s"}
    evidence = redact((proc.stdout + proc.stderr))[-EVIDENCE_LIMIT:]
    return {"passed": proc.returncode == 0, "timed_out": False,
            "returncode": proc.returncode, "evidence": evidence}
