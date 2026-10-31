"""Mac apps: root operates the owner's apps through root-driver (its own repository and app).

root-driver is its own signed app: a background driver that reads windows through
the accessibility tree and clicks, types and fills fields without taking the
cursor or focus. root calls it as `root-driver call <tool> -`; the driver keeps
its snapshots between calls, so a `snapshot_id` from one look is good in the
next action.

Each driver tool reaches the agent as ``mac_<tool>``. Looking (reads) needs no
approval; operating an app asks as "operate apps" and closing windows or
quitting apps as "close windows or quit apps" — so "Allow for this session"
covers each class, and auto-approve-all approves both. Plan mode and line
sessions only look. Only a local root on a Mac offers these tools: a hosted
root must never drive the server's own desktop.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..sessions.host_tools import tool_error

if TYPE_CHECKING:
    from ..sessions import SessionRuntime

PREFIX = "mac_"
ACTION_OPERATE = "operate apps"
ACTION_CLOSE = "close windows or quit apps"
BIN_ENV = "ROOT_DRIVER_BIN"
#: Without a driver, look again this often: installing it needs no restart of root.
RECHECK_SECONDS = 30.0


def find_driver() -> Path | None:
    """`ROOT_DRIVER_BIN`, then `root-driver` on PATH, then the usual app locations."""
    raw = os.environ.get(BIN_ENV, "").strip()
    if raw:
        path = Path(raw).expanduser()
        return path if path.is_file() else None
    found = shutil.which("root-driver")
    candidates = [Path(found)] if found else []
    candidates += [
        Path.home() / ".local" / "bin" / "root-driver",
        Path.home() / "Applications" / "RootDriver.app" / "Contents" / "MacOS" / "root-driver",
        Path("/Applications/RootDriver.app/Contents/MacOS/root-driver"),
    ]
    return next((path for path in candidates if path.is_file()), None)


class DriverToolset:
    plugin = "driver"

    def __init__(self, binary: Path | None = None) -> None:
        self._binary = binary
        self._catalog: dict[str, dict[str, Any]] = {}
        self._checked = float("-inf")

    @property
    def binary(self) -> Path | None:
        return self._binary if self._binary is not None else find_driver()

    def catalog(self) -> dict[str, dict[str, Any]]:
        """The driver's own tool list, read once from `root-driver list-tools`."""
        if not self._catalog and time.monotonic() - self._checked >= RECHECK_SECONDS:
            self._checked = time.monotonic()
            binary = self.binary
            tools: list[dict[str, Any]] = []
            if binary is not None:
                try:
                    out = subprocess.run([str(binary), "list-tools"], capture_output=True, text=True, timeout=10)
                    tools = json.loads(out.stdout).get("tools", []) if out.returncode == 0 else []
                except (OSError, ValueError, subprocess.SubprocessError):
                    tools = []
            self._catalog = {tool["name"]: tool for tool in tools if isinstance(tool, dict) and tool.get("name")}
        return self._catalog

    def available(self, runtime: "SessionRuntime") -> bool:
        return sys.platform == "darwin" and runtime.tenant.server.mode == "local" and bool(self.catalog())

    def definitions(self, runtime: "SessionRuntime") -> list[dict[str, Any]]:
        if not self.available(runtime):
            return []
        look_only = runtime.record.permission_mode == "plan" or bool(runtime.record.answering)
        return [
            {
                "name": PREFIX + name,
                "description": _description(tool),
                "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
            }
            for name, tool in self.catalog().items()
            if not look_only or tool.get("risk") == "read"
        ]

    def handles(self, name: str) -> bool:
        return name.startswith(PREFIX) and name[len(PREFIX):] in self.catalog()

    async def call(self, runtime: "SessionRuntime", call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool_name = name[len(PREFIX):]
        tool = self.catalog().get(tool_name)
        binary = self.binary
        if tool is None or binary is None or not self.available(runtime):
            return tool_error("root-driver is not installed on this Mac.", "Not installed")
        risk = tool.get("risk")
        if risk != "read":
            if runtime.record.permission_mode == "plan" or runtime.record.answering:
                return tool_error("This session only looks at apps; it does not operate them.", "Read-only")
            action = ACTION_CLOSE if risk == "destructive" else ACTION_OPERATE
            if not await runtime.approve_host_action(call_id, action, _ask(tool_name, arguments)):
                return tool_error(f"`{tool_name}` was not allowed.", "Rejected")
        timeout = float(tool.get("default_timeout") or 15) + 20  # the driver may first have to start
        try:
            process = await asyncio.create_subprocess_exec(
                str(binary), "call", tool_name, "-",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(process.communicate(json.dumps(arguments).encode()), timeout)
        except asyncio.TimeoutError:
            process.kill()
            return tool_error(f"root-driver did not answer `{tool_name}` within {int(timeout)}s.", "Timed out")
        except OSError as error:
            return tool_error(f"root-driver could not start: {error}", "Failed")
        try:
            result = json.loads(out.decode() or "{}")
        except ValueError:
            return tool_error("root-driver answered with something that is not JSON.", "Failed")
        return _result(tool_name, result, exit_code=process.returncode or 0)


def _description(tool: dict[str, Any]) -> str:
    risk = {"read": "Looks only.", "write": "Operates the app (asks first).", "destructive": "Closes or quits (asks first)."}
    return f"{tool.get('description', '')} {risk.get(tool.get('risk', ''), '')} (root-driver, the owner's Mac apps.)".strip()


def _ask(tool_name: str, arguments: dict[str, Any]) -> str:
    shown = {k: v for k, v in arguments.items() if k in ("app", "app_name", "bundle_id", "pid", "text", "value", "chord", "key", "menu_path", "urls")}
    detail = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)[:80]}" for k, v in shown.items())
    return f"{tool_name.replace('_', ' ')}" + (f": {detail}" if detail else "")


def _result(tool_name: str, result: dict[str, Any], *, exit_code: int) -> dict[str, Any]:
    images: list[str] = []

    def strip(value: Any) -> Any:
        if isinstance(value, str) and value.startswith("data:image/"):
            images.append(value)
            return f"<image {len(images)} attached>"
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items()}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value

    text = json.dumps(strip(result), ensure_ascii=False, sort_keys=True)
    failed = result.get("ok") is False or result.get("status") == "failed"
    if exit_code == 87:
        text += "\n\nroot-driver needs Accessibility: ask the owner to allow Root Driver in System Settings → Privacy & Security (or run `root-driver permissions grant`)."
    output: Any = text if not images else [{"type": "text", "text": text}, *({"type": "image_url", "image_url": {"url": url}} for url in images)]
    brief = f"{tool_name.replace('_', ' ')}: " + ("failed" if failed else "done")
    return {
        "is_error": bool(failed),
        "output": output,
        "message": str(result.get("error") or "") if failed else "",
        "display": [{"type": "brief", "text": brief}],
    }


def install(server) -> None:
    toolset = DriverToolset()

    def extend(tenant) -> None:
        tenant.toolsets.append(toolset)

    server.extensions.append(extend)
