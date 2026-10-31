"""``run_python``: run Python in root's toolbox, installing what it needs first.

For the work a coding agent does around code — analyse a CSV with pandas, plot
with matplotlib, make a PDF with reportlab — the agent names the packages and
the code. Missing packages are installed into the toolbox (the owner approves
an install like any ``pip install``); the code runs like any command (approved
like Shell's), in the session's scratch folder or the workspace. Files it makes
come back in the result, so the owner can open or download them.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..sessions.host_tools import tool_error
from .env import ToolboxError, check_requirements, run

if TYPE_CHECKING:
    from ..sessions import SessionRuntime

TOOL = "run_python"
OUTPUT_LIMIT = 20_000
RUNS = ".root-runs"
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", RUNS, ".pytest_cache", "target", "dist", "build"}

DEFINITION = {
    "name": TOOL,
    "description": (
        "Run Python 3 in root's own toolbox environment — for data work, charts, documents and one-off scripts, not the "
        "project's own code (run that with the project's tools in Shell). List any packages the code needs in "
        "`packages` (PyPI names, e.g. [\"pandas\", \"reportlab\"]): missing ones are installed into the toolbox first, "
        "never into the system or the project. `cwd` is `scratch` (default: the session's own folder, $ROOT_SCRATCH — "
        "put deliverables the owner did not ask to keep in the project here) or `workspace`. Files the run creates are "
        "listed in the result and the owner can open them. Empty `code` only installs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "packages": {"type": "array", "items": {"type": "string"}},
            "cwd": {"type": "string", "enum": ["scratch", "workspace"]},
            "timeout": {"type": "integer", "minimum": 5, "maximum": 900, "description": "Seconds; default 120."},
        },
        "required": ["code"],
        "additionalProperties": False,
    },
}


def _clip(text: str) -> str:
    return text if len(text) <= OUTPUT_LIMIT else text[:OUTPUT_LIMIT] + f"\n[truncated: {len(text) - OUTPUT_LIMIT} more characters]"


def changed_files(root: Path, since: float, *, limit: int = 50, scan: int = 20_000) -> list[dict[str, Any]]:
    """Files under ``root`` written since ``since`` (skipping dependency and build folders), newest first."""
    found, seen = [], 0
    for directory, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            seen += 1
            if seen > scan:
                break
            path = Path(directory) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime >= since:
                found.append({"path": str(path.relative_to(root)), "size": stat.st_size, "mtime": stat.st_mtime})
    found.sort(key=lambda f: f["mtime"], reverse=True)
    return found[:limit]


class RunPythonToolset:
    plugin = "toolbox"

    def definitions(self, runtime: "SessionRuntime") -> list[dict[str, Any]]:
        if runtime.record.kind not in ("session", "candidate") or runtime.record.permission_mode == "plan":
            return []
        return [DEFINITION]

    def handles(self, name: str) -> bool:
        return name == TOOL

    async def call(self, runtime: "SessionRuntime", call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        toolbox = runtime.tenant.toolbox
        env = runtime.tenant.command_environment(runtime)
        env["ROOT_SCRATCH"] = str(runtime.scratch_dir)

        def wrap(argv: list[str]) -> list[str]:
            return runtime.tenant.wrap_command(runtime, argv)

        code = str(arguments.get("code") or "")
        try:
            packages = check_requirements(list(arguments.get("packages") or []))
            await toolbox.ensure(env=env, wrap=wrap)
            missing = await toolbox.missing(packages, env=env, wrap=wrap)
            installed = ""
            if missing:
                command = f"pip install {' '.join(missing)}"
                if not await runtime.approve_host_command(call_id, command, "Install into root's Python toolbox", sender="root"):
                    return tool_error(f"The owner did not approve installing {', '.join(missing)}.", "Install rejected")
                installed = f"Installed into the toolbox: {', '.join(missing)}.\n"
                await toolbox.install(missing, env=env, wrap=wrap)
            if not code.strip():
                return {"is_error": False, "output": installed or "Already installed.", "message": "", "display": [{"type": "brief", "text": installed.strip() or "Already installed"}]}

            where = runtime.scratch_dir if arguments.get("cwd", "scratch") != "workspace" else Path(runtime.record.workspace)
            runtime.scratch_dir.mkdir(parents=True, exist_ok=True)
            scripts = runtime.scratch_dir / RUNS
            scripts.mkdir(exist_ok=True)
            script = scripts / f"run-{int(time.time() * 1000)}.py"
            script.write_text(code, "utf-8")
            display = [{"type": "shell", "language": "python", "command": code[:4000]}]
            if not await runtime.approve_host_command(call_id, f"python3 {script.name}", "Run Python", sender="root", display=display):
                return tool_error("The owner did not approve running this code.", "Rejected")
            started = time.time() - 1
            status, out, err = await run(
                wrap([str(toolbox.python), str(script)]), cwd=where, env=toolbox.environment(env), timeout=float(arguments.get("timeout") or 120)
            )
        except ToolboxError as error:
            return tool_error(str(error), "Toolbox error")

        produced = [{**f, "scope": "scratch"} for f in changed_files(runtime.scratch_dir, started) if not f["path"].startswith(RUNS)]
        if where != runtime.scratch_dir:
            produced += [{**f, "scope": "workspace"} for f in changed_files(where, started)]
        parts = [installed + f"exit code {status}"]
        if out.strip():
            parts.append("--- stdout ---\n" + _clip(out.rstrip()))
        if err.strip():
            parts.append("--- stderr ---\n" + _clip(err.rstrip()))
        if produced:
            parts.append("files written:\n" + "\n".join(f"  {f['scope']}: {f['path']} ({f['size']} bytes)" for f in produced))
        display: list[dict[str, Any]] = [{"type": "brief", "text": f"exit {status}" + (f" · {len(produced)} file{'s' if len(produced) != 1 else ''}" if produced else "")}]
        if produced:
            display.append({"type": "files", "files": [{"path": f["path"], "scope": f["scope"], "size": f["size"]} for f in produced]})
        return {"is_error": status != 0, "output": "\n\n".join(parts), "message": "" if status == 0 else f"Python exited with {status}.", "display": display}
