"""Projects: the workspaces a team groups its missions under, owner-managed like the policy.

A project is a named repo path. Missions carry a project id so the sidebar can group
conversations (missions) under their workspace - berd's Projects/Chats information
architecture, backed by Skye's persisted engine. The registry file (skye.projects.json,
override with SKYE_PROJECTS) is owner-managed configuration, exactly like the roles registry.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path
from typing import Any

DEFAULT_PATH = "skye.projects.json"
_NAME = re.compile(r"^.{1,60}$")


class ProjectError(ValueError):
    pass


def default_path() -> str:
    return os.environ.get("SKYE_PROJECTS", "").strip() or DEFAULT_PATH


def _read(path: str | Path | None = None) -> dict[str, Any]:
    try:
        data = json.loads(Path(path or default_path()).read_text("utf-8"))
    except (OSError, ValueError):
        return {"projects": []}
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
        return {"projects": []}
    return data


def list_projects(path: str | Path | None = None) -> list[dict[str, Any]]:
    return [p for p in _read(path)["projects"] if p.get("id") and p.get("name")]


def get(project_id: str, path: str | Path | None = None) -> dict[str, Any] | None:
    for project in list_projects(path):
        if project["id"] == project_id:
            return project
    return None


def add(entry: dict[str, Any], path: str | Path | None = None) -> dict[str, Any]:
    """Record a project: a name and the repo path its missions operate on. The path must
    exist - a project pointing nowhere would fail every mission dispatched under it."""
    name = str(entry.get("name") or "").strip()
    if not _NAME.match(name):
        raise ProjectError("a project needs a name (1-60 chars)")
    repo = str(entry.get("repo") or "").strip()
    if not repo or not Path(repo).is_dir():
        raise ProjectError(f"repo must be an existing directory, got {repo!r}")
    record = {"id": str(entry.get("id") or "").strip() or f"proj-{secrets.token_hex(4)}",
              "name": name, "repo": repo}
    target = Path(path or default_path())
    data = _read(target)
    data["projects"] = [p for p in data["projects"] if p.get("id") != record["id"]]
    data["projects"].append(record)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    tmp.replace(target)
    return record
