"""Plugins: everything root can do beyond its core tools, in one place to see and switch.

* **Built in** — documents, the Python toolbox, session search, schedules,
  coding flows, web search, memory. On unless the owner turns one off; a plugin
  that is off gives the agent none of its tools.
* **Connections** — GitHub and Gmail (``connections``): connect, disconnect.
* **MCP servers** — the owner's own (``<data>/mcp.json``, the usual
  ``mcpServers`` shape). A server switched off moves to ``mcp.disabled.json``,
  so the runtime never starts it.

A host toolset names its plugin in a ``plugin`` attribute; ``Tenant`` leaves
out the tools of plugins that are off.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .tenancy import Tenant

CATALOG: list[dict[str, Any]] = [
    {"id": "documents", "name": "Documents", "kind": "built-in", "icon": "file",
     "summary": "Read PDF, Word, Excel, PowerPoint and CSV files; edit spreadsheets and PDFs in place.",
     "capabilities": ["read_document", "edit_spreadsheet", "edit_pdf"]},
    {"id": "toolbox", "name": "Python toolbox", "kind": "built-in", "icon": "terminal",
     "summary": "root's own Python: it installs the packages a task needs (pandas, reportlab, matplotlib…) and runs code, handing back the files it makes.",
     "capabilities": ["run_python", "install on demand", "scratch folder"]},
    {"id": "github", "name": "GitHub", "kind": "connection", "icon": "github",
     "summary": "Issues, pull requests, reviews and checks for the workspace's repository. Writes need your approval.",
     "capabilities": ["read issues and PRs", "comment", "open PRs", "review"]},
    {"id": "gmail", "name": "Gmail", "kind": "connection", "icon": "mail",
     "summary": "Search and read your mail, and leave drafts for you to send. root never sends mail.",
     "capabilities": ["search", "read", "draft replies"]},
    {"id": "slack", "name": "Slack", "kind": "connection", "icon": "slack",
     "summary": "@mention root in a channel or thread, or message it, and it answers there. It reads the thread only when called, and is read-only in Slack.",
     "capabilities": ["answers @mentions and DMs", "reads the thread around the call", "replies in the thread"]},
    {"id": "keiki", "name": "Keiki", "kind": "connection", "icon": "message",
     "summary": "Text root on WhatsApp, iMessage, Telegram, SMS or email through Keiki (onkeiki.com). It answers when spoken to, read-only.",
     "capabilities": ["WhatsApp", "iMessage", "Telegram", "SMS and email", "answers in groups when @mentioned"]},
    {"id": "driver", "name": "Mac apps", "kind": "built-in", "icon": "monitor",
     "summary": "root reads and operates your Mac's apps through root-driver, in the background — your cursor and focus stay put. Clicking, typing and quitting apps ask first.",
     "capabilities": ["read windows", "click and type", "fill forms", "screenshots"]},
    {"id": "inspect", "name": "Page inspection", "kind": "built-in", "icon": "monitor",
     "summary": "Look at a locally running page in headless Chromium: screenshot, visible text, console errors, layout facts. Needs Playwright. Localhost only; granted to the designer and pm job roles, not to reviewers.",
     "capabilities": ["inspect_page"]},
    {"id": "guides", "name": "Review guides", "kind": "built-in", "icon": "monitor",
     "summary": "Guidance and a source-clue scan for the designer and pm job roles, loaded on demand. They steer judgment; they guarantee nothing about taste or quality.",
     "capabilities": ["role_guide", "design_scan"]},
    {"id": "history", "name": "Session search", "kind": "built-in", "icon": "search",
     "summary": "root searches your past sessions for earlier decisions, fixes and commands.",
     "capabilities": ["session_search"]},
    {"id": "schedules", "name": "Schedules", "kind": "built-in", "icon": "clock",
     "summary": "Prompts root runs by itself on a timetable — nightly tests, a morning PR digest.",
     "capabilities": ["schedule"]},
    {"id": "flows", "name": "Coding flows", "kind": "built-in", "icon": "branch",
     "summary": "On request, a change built and tested in an isolated worktree, reviewed before it is applied.",
     "capabilities": ["coding_flow"]},
    {"id": "web", "name": "Web search", "kind": "built-in", "icon": "globe",
     "summary": "Search the web and read pages, keyless by default.", "capabilities": ["SearchWeb", "FetchURL"], "setting": "web_search"},
    {"id": "memory", "name": "Memory (Dhee)", "kind": "built-in", "icon": "brain",
     "summary": "What root learns about your projects and preferences, recalled in later sessions.",
     "capabilities": ["memory_recall", "memory_remember", "memory_checkpoint"], "setting": "memory_mode"},
]
IDS = {p["id"] for p in CATALOG}
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def enabled(tenant: Tenant, plugin: str) -> bool:
    settings = tenant.settings.get()
    if plugin == "web":
        return settings.web_search
    if plugin == "memory":
        return settings.memory_mode != "off"
    return plugin not in settings.disabled_plugins


# ----- MCP servers ---------------------------------------------------------------------


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return servers if isinstance(servers, dict) else {}


def _write(path: Path, servers: dict[str, Any]) -> None:
    if not servers:
        path.unlink(missing_ok=True)
        return
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # an MCP server's env may hold a token
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"mcpServers": servers}, handle, indent=2)
    os.replace(tmp, path)


def mcp_servers(root: Path) -> list[dict[str, Any]]:
    """Every server, on and off; env values are never sent back, only their names."""
    out = []
    for path, on in ((root / "mcp.json", True), (root / "mcp.disabled.json", False)):
        for name, server in _read(path).items():
            server = server if isinstance(server, dict) else {}
            out.append(
                {
                    "name": name,
                    "enabled": on,
                    "command": server.get("command", ""),
                    "args": server.get("args", []),
                    "url": server.get("url", ""),
                    "env": sorted((server.get("env") or {}).keys()),
                }
            )
    return sorted(out, key=lambda s: s["name"])


class McpServer(BaseModel):
    command: str = ""
    args: list[str] = []
    url: str = ""
    #: New or changed variables; a name with an empty value keeps the stored one.
    env: dict[str, str] = {}
    enabled: bool = True


class PluginChange(BaseModel):
    enabled: bool


def router(app_state) -> APIRouter:
    api = APIRouter(prefix="/api/plugins")
    tenant_of = app_state.auth.tenant

    @api.get("")
    async def catalog(tenant: Tenant = Depends(tenant_of)) -> dict:
        plugins = [{**p, "enabled": enabled(tenant, p["id"])} for p in CATALOG]
        toolbox = getattr(tenant, "toolbox", None)
        return {
            "plugins": plugins,
            "mcp": mcp_servers(tenant.root),
            "toolbox": {"path": str(toolbox.root) if toolbox else "", "ready": bool(toolbox and toolbox.ready)},
        }

    @api.put("/{plugin_id}")
    async def switch(plugin_id: str, body: PluginChange, tenant: Tenant = Depends(tenant_of)) -> dict:
        if plugin_id not in IDS:
            raise HTTPException(404, "No such plugin.")
        if plugin_id == "web":
            tenant.settings.update({"web_search": body.enabled})
        elif plugin_id == "memory":
            tenant.settings.update({"memory_mode": "openrouter" if body.enabled else "off"})
        else:
            off = [p for p in tenant.settings.get().disabled_plugins if p != plugin_id]
            tenant.settings.update({"disabled_plugins": off if body.enabled else [*off, plugin_id]})
        return {"id": plugin_id, "enabled": enabled(tenant, plugin_id)}

    @api.get("/toolbox/packages")
    async def toolbox_packages(tenant: Tenant = Depends(tenant_of)) -> dict:
        toolbox = tenant.toolbox
        from .launcher import scrubbed_environment

        return {"packages": await toolbox.packages(env=scrubbed_environment(), wrap=list)}

    @api.put("/mcp/{name}")
    async def put_mcp(name: str, body: McpServer, tenant: Tenant = Depends(tenant_of)) -> dict:
        if not _NAME.match(name):
            raise HTTPException(400, "A server name is letters, digits, `.`, `_` or `-`.")
        if bool(body.command.strip()) == bool(body.url.strip()):
            raise HTTPException(400, "Give either a command (a local server) or a URL (a remote one).")
        on, off = tenant.root / "mcp.json", tenant.root / "mcp.disabled.json"
        current = _read(on).get(name) or _read(off).get(name) or {}
        env = dict(current.get("env") or {})
        for key, value in body.env.items():
            if value:
                env[key] = value
        server: dict[str, Any] = {"url": body.url.strip()} if body.url.strip() else {"command": body.command.strip(), "args": body.args}
        if env:
            server["env"] = env
        for path in (on, off):
            servers = _read(path)
            servers.pop(name, None)
            if (path == on) == body.enabled:
                servers[name] = server
            _write(path, servers)
        return {"mcp": mcp_servers(tenant.root)}

    @api.delete("/mcp/{name}")
    async def delete_mcp(name: str, tenant: Tenant = Depends(tenant_of)) -> dict:
        for path in (tenant.root / "mcp.json", tenant.root / "mcp.disabled.json"):
            servers = _read(path)
            if servers.pop(name, None) is not None:
                _write(path, servers)
        return {"mcp": mcp_servers(tenant.root)}

    return api
