"""The skills root learned: ``<tenant>/skills/<name>/SKILL.md``.

The runtime loads this directory as an extra skill root
(``KIMI_EXTRA_SKILLS_DIRS``), so a promoted skill is in the agent's system
prompt from the next launch on — the launch fingerprint includes the skills,
so the next prompt restarts the runtime when one changes. An owner's own
skills (``~/.claude/skills``, the project's ``.agents/skills``…) load after
these and win on a name clash.

Updating a skill keeps the previous text in ``.history/``, so an improvement
that turns out worse can be rolled back.
"""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Any

_FRONT = re.compile(r"^---\n(.*?)\n---\n", re.S)


def render(skill: dict[str, Any], *, source: str) -> str:
    title = skill["name"].replace("-", " ").capitalize()
    description = skill["trigger"].replace("\n", " ").replace('"', "'")
    lines = ["---", f"name: {skill['name']}", f'description: "{description}"', "---", "", f"# {title}", "", skill["lesson"], ""]
    for heading, key in (("Steps", "steps"), ("Verify", "verification"), ("Pitfalls", "pitfalls")):
        if skill.get(key):
            lines += [f"## {heading}", "", *[f"- {item}" for item in skill[key]], ""]
    lines += [f"<!-- learned by root: {source} -->", ""]
    return "\n".join(lines)


def _frontmatter(text: str) -> dict[str, str]:
    match = _FRONT.match(text)
    fields: dict[str, str] = {}
    for line in (match.group(1) if match else "").splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"')
    return fields


class SkillLibrary:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, name: str) -> Path:
        path = (self.root / name / "SKILL.md").resolve()
        if not path.is_relative_to(self.root.resolve()) or "/" in name or name.startswith("."):
            raise ValueError("invalid skill name")
        return path

    def list(self) -> list[dict[str, Any]]:
        items = []
        for path in sorted(self.root.glob("*/SKILL.md")):
            text = path.read_text("utf-8", errors="replace")
            fields = _frontmatter(text)
            items.append(
                {
                    "name": fields.get("name") or path.parent.name,
                    "description": fields.get("description", ""),
                    "updated_at": path.stat().st_mtime,
                    "versions": len(list((self.root / ".history").glob(f"{path.parent.name}--*.md"))),
                }
            )
        return items

    def read(self, name: str) -> str:
        return self.path(name).read_text("utf-8")

    def write(self, skill: dict[str, Any], *, source: str) -> dict[str, Any]:
        path = self.path(skill["name"])
        updated = path.exists()
        if updated:
            history = self.root / ".history"
            history.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, history / f"{skill['name']}--{time.time_ns()}.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(skill, source=source), "utf-8")
        return {"name": skill["name"], "path": str(path), "updated": updated}

    def rollback(self, name: str) -> bool:
        versions = sorted((self.root / ".history").glob(f"{name}--*.md"))
        if not versions:
            return False
        shutil.copy2(versions[-1], self.path(name))
        versions[-1].unlink()
        return True

    def remove(self, name: str) -> bool:
        path = self.path(name)
        if not path.exists():
            return False
        shutil.rmtree(path.parent)
        return True
