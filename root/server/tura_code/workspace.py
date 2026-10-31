"""Read-only views of a workspace for the UI: files, git changes, folders.

Nothing here writes. Edits belong to the agent, behind its approvals.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import hostos

IGNORED_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", "target", "dist", "build",
    ".next", ".turbo", ".cache", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea",
}
MAX_FILES = 5_000
MAX_READ_BYTES = 1_000_000
MAX_DIFF_BYTES = 400_000
#: The macOS folder dialog, run by the local host for a browser page, which
#: cannot learn a folder's path itself. The start folder comes in as an argument.
PICK_FOLDER = [
    "osascript",
    "-e", "on run argv",
    "-e", "activate",
    "-e", 'POSIX path of (choose folder with prompt "Choose a workspace folder" default location (POSIX file (item 1 of argv)))',
    "-e", "end run",
]
#: The same on Windows: the WinForms folder dialog, on top of the browser. The
#: start folder comes in through the environment, never as code.
PICK_FOLDER_WINDOWS = (
    "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
    "Add-Type -AssemblyName System.Windows.Forms; "
    "$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true}; "
    "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
    "$dialog.Description = 'Choose a workspace folder'; "
    "$dialog.SelectedPath = $env:ROOT_PICK_START; "
    "if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { $dialog.SelectedPath } else { exit 1 }"
)
PICK_TIMEOUT = 600


class WorkspaceError(ValueError):
    pass


def resolve_inside(workspace: Path, relative: str) -> Path:
    root = workspace.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise WorkspaceError("Path is outside the workspace.")
    return target


def _git(workspace: Path, *args: str, limit: int = MAX_DIFF_BYTES) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace), *args],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout[:limit].decode("utf-8", errors="replace")


def is_git_repo(workspace: Path) -> bool:
    return _git(workspace, "rev-parse", "--is-inside-work-tree") is not None


def list_files(workspace: Path) -> dict:
    root = workspace.resolve()
    if is_git_repo(root):
        out = _git(root, "ls-files", "--cached", "--others", "--exclude-standard", limit=20_000_000) or ""
        files = sorted({line for line in out.splitlines() if line})
        return {"files": files[:MAX_FILES], "truncated": len(files) > MAX_FILES, "git": True}
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            files.append((Path(dirpath) / name).relative_to(root).as_posix())
            if len(files) >= MAX_FILES:
                return {"files": files, "truncated": True, "git": False}
    return {"files": files, "truncated": False, "git": False}


def read_file(workspace: Path, relative: str) -> dict:
    path = resolve_inside(workspace, relative)
    if not path.is_file():
        raise WorkspaceError("Not a file.")
    raw = path.read_bytes()[: MAX_READ_BYTES + 1]
    binary = b"\x00" in raw[:8192]
    return {
        "path": relative,
        "binary": binary,
        "truncated": len(raw) > MAX_READ_BYTES,
        "content": "" if binary else raw[:MAX_READ_BYTES].decode("utf-8", errors="replace"),
    }


def git_changes(workspace: Path) -> dict:
    root = workspace.resolve()
    if not is_git_repo(root):
        return {"git": False, "branch": "", "files": [], "diff": ""}
    branch = (_git(root, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all") or ""
    files = []
    for line in status.splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.append({"path": path.strip('"'), "status": code.strip() or "M"})
    diff = _git(root, "diff", "HEAD", "--no-color", "--no-ext-diff") or _git(root, "diff", "--no-color") or ""
    # Untracked files have no diff against HEAD; show them as additions.
    for item in files:
        if item["status"] != "??":
            continue
        try:
            path = resolve_inside(root, item["path"])
            raw = path.read_bytes()[:20_000]
        except (OSError, WorkspaceError):
            continue
        if b"\x00" in raw:
            continue
        body = raw.decode("utf-8", errors="replace").splitlines()
        diff += (
            f"diff --git a/{item['path']} b/{item['path']}\nnew file mode 100644\n--- /dev/null\n+++ b/{item['path']}\n"
            f"@@ -0,0 +1,{len(body)} @@\n" + "".join(f"+{line}\n" for line in body)
        )
        if len(diff) > MAX_DIFF_BYTES:
            break
    return {"git": True, "branch": branch, "files": files, "diff": diff[:MAX_DIFF_BYTES]}


def list_dirs(path: str | None, root: Path | None = None) -> dict:
    """Subdirectories of ``path`` (default: home, or ``root``), for the workspace picker.

    With ``root`` (a hosted person's workspace folder) nothing outside it is listed.
    """
    base = Path(path).expanduser() if path else (root or Path.home())
    base = base.resolve()
    if root is not None and not base.is_relative_to(root.resolve()):
        base = root.resolve()
    if not base.is_dir():
        raise WorkspaceError("Not a directory.")
    entries = []
    try:
        for child in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if child.name.startswith(".") or not child.is_dir():
                continue
            entries.append({"name": child.name, "path": str(child), "git": (child / ".git").exists()})
    except PermissionError as error:
        raise WorkspaceError("Permission denied.") from error
    return {
        "path": str(base),
        "parent": str(base.parent) if base.parent != base and (root is None or base != root.resolve()) else None,
        "git": (base / ".git").exists(),
        "entries": entries[:500],
    }


def folder_picker() -> bool:
    """Whether this machine can show the native folder dialog."""
    if hostos.WINDOWS:
        return True
    return sys.platform == "darwin" and shutil.which(PICK_FOLDER[0]) is not None


def picker_command(begin: Path) -> tuple[list[str], dict[str, str] | None]:
    """The native dialog's argv and environment: osascript on a Mac, WinForms through PowerShell on Windows."""
    if hostos.WINDOWS:
        argv = [hostos.powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-STA", "-Command", PICK_FOLDER_WINDOWS]
        return argv, {**os.environ, "ROOT_PICK_START": str(begin)}
    return [*PICK_FOLDER, str(begin)], None


async def pick_folder(start: str | None = None) -> str | None:
    """The folder the owner chooses in the native dialog, or None when they cancel."""
    begin = Path(start).expanduser() if start else Path.home()
    if not begin.is_dir():
        begin = Path.home()
    argv, env = picker_command(begin)
    process = await asyncio.create_subprocess_exec(*argv, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), PICK_TIMEOUT)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return None
    # A cancelled dialog exits non-zero ("User canceled", -128).
    path = output.decode(errors="replace").strip()
    if process.returncode != 0 or not path:
        return None
    if hostos.WINDOWS:
        return path if path.endswith(":\\") else path.rstrip("\\")
    return path.rstrip("/") or "/"
