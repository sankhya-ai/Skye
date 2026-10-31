"""GitHub: who root is on GitHub for this person, and a small REST client.

Credentials, first found wins:

1. a token connected in root (pasted, or GitHub's device flow when a client
   id is configured) — the only source in hosted mode;
2. locally, the owner's ``gh`` CLI login (``gh auth token``);
3. locally, ``GH_TOKEN`` / ``GITHUB_TOKEN``.

The workspace's repository and branch come from its own ``.git`` (read as
files, no process), so the agent rarely has to name a repository.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

import httpx

from .store import ConnectionStore

NAME = "github"
API_ENV = "TURA_CODE_GITHUB_API"
WEB_ENV = "TURA_CODE_GITHUB_WEB"
CLIENT_ID_ENV = "TURA_CODE_GITHUB_CLIENT_ID"
SCOPES = "repo read:org"
TIMEOUT = 20.0

_gh_cache: tuple[float, str] = (0.0, "")


class GitHubError(RuntimeError):
    pass


def api_base() -> str:
    return os.environ.get(API_ENV, "").strip().rstrip("/") or "https://api.github.com"


def web_base() -> str:
    return os.environ.get(WEB_ENV, "").strip().rstrip("/") or "https://github.com"


def client_id() -> str:
    return os.environ.get(CLIENT_ID_ENV, "").strip()


async def _gh_cli_token() -> str:
    global _gh_cache
    if time.monotonic() - _gh_cache[0] < 60:
        return _gh_cache[1]
    token = ""
    if shutil.which("gh"):
        try:
            process = await asyncio.create_subprocess_exec("gh", "auth", "token", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(process.communicate(), 5)
            token = out.decode().strip() if process.returncode == 0 else ""
        except (OSError, asyncio.TimeoutError):
            token = ""
    _gh_cache = (time.monotonic(), token)
    return token


async def credentials(store: ConnectionStore, *, local: bool) -> tuple[str, str]:
    """``(token, source)``; ``("", "")`` when root has no way to act on GitHub."""
    entry = store.get(NAME)
    if entry and (entry.get("secret") or {}).get("token"):
        return entry["secret"]["token"], "connected"
    if not local:
        return "", ""
    token = await _gh_cli_token()
    if token:
        return token, "gh CLI"
    for env in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(env, "").strip():
            return os.environ[env].strip(), env
    return "", ""


# ----- the workspace's repository ---------------------------------------------------


def _git_dir(workspace: Path) -> Path | None:
    marker = workspace / ".git"
    if marker.is_dir():
        return marker
    if marker.is_file():
        text = marker.read_text("utf-8", errors="replace").strip()
        if text.startswith("gitdir:"):
            path = Path(text.split(":", 1)[1].strip())
            return path if path.is_absolute() else (workspace / path).resolve()
    return None


def _common_dir(git_dir: Path) -> Path:
    common = git_dir / "commondir"
    if common.is_file():
        return (git_dir / common.read_text("utf-8").strip()).resolve()
    return git_dir


def parse_remote(url: str) -> str:
    """``owner/name`` from an HTTPS or SSH remote URL; empty when it is not one."""
    match = re.search(r"(?:[:/])([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", url.strip())
    return f"{match.group(1)}/{match.group(2)}" if match else ""


def workspace_repo(workspace: Path) -> str:
    git_dir = _git_dir(workspace)
    if git_dir is None:
        return ""
    try:
        config = (_common_dir(git_dir) / "config").read_text("utf-8", errors="replace")
    except OSError:
        return ""
    section = re.search(r'\[remote "origin"\](.*?)(?:\n\[|\Z)', config, re.S)
    url = re.search(r"^\s*url\s*=\s*(\S+)", section.group(1), re.M) if section else None
    return parse_remote(url.group(1)) if url else ""


def workspace_branch(workspace: Path) -> str:
    git_dir = _git_dir(workspace)
    try:
        head = (git_dir / "HEAD").read_text("utf-8").strip() if git_dir else ""
    except OSError:
        return ""
    return head.removeprefix("ref: refs/heads/") if head.startswith("ref: refs/heads/") else ""


# ----- REST -----------------------------------------------------------------------


class GitHub:
    def __init__(self, token: str) -> None:
        self.token = token

    async def request(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None, accept: str = "application/vnd.github+json") -> Any:
        headers = {"Authorization": f"Bearer {self.token}", "Accept": accept, "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "root"}
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            try:
                response = await client.request(method, f"{api_base()}{path}", params=params, json=body, headers=headers)
            except httpx.HTTPError as error:
                raise GitHubError(f"Could not reach GitHub: {error}") from error
        if response.status_code == 401:
            raise GitHubError("GitHub rejected the token: reconnect GitHub in Settings → Connections.")
        if response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0":
            raise GitHubError("GitHub's rate limit is used up for now; try again later.")
        if response.status_code == 404:
            raise GitHubError(f"Not found on GitHub ({path}), or the token cannot see it.")
        if response.status_code >= 400:
            try:
                detail = response.json()
                message = detail.get("message", "") + "".join(f"; {e.get('message') or e.get('code')}" for e in detail.get("errors") or [] if isinstance(e, dict))
            except ValueError:
                message = response.text[:300]
            raise GitHubError(f"GitHub said {response.status_code}: {message}")
        if accept != "application/vnd.github+json":
            return response.text
        return response.json() if response.content else {}

    async def viewer(self) -> dict:
        return await self.request("GET", "/user")


# ----- the device flow ---------------------------------------------------------------


async def start_device_flow() -> dict:
    if not client_id():
        raise GitHubError(f"Sign-in with GitHub needs a GitHub OAuth app: set {CLIENT_ID_ENV}, or paste a token instead.")
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(f"{web_base()}/login/device/code", data={"client_id": client_id(), "scope": SCOPES}, headers={"Accept": "application/json"})
    data = response.json() if response.content else {}
    if response.status_code >= 400 or "device_code" not in data:
        raise GitHubError(f"GitHub did not start the sign-in: {data.get('error_description') or response.text[:200]}")
    return data


async def poll_device_flow(device_code: str) -> str:
    """The access token, or "" while the person has not finished on GitHub."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(
            f"{web_base()}/login/oauth/access_token",
            data={"client_id": client_id(), "device_code": device_code, "grant_type": "urn:ietf:params:oauth:grant-type:device_code"},
            headers={"Accept": "application/json"},
        )
    data = response.json() if response.content else {}
    if data.get("access_token"):
        return str(data["access_token"])
    if data.get("error") in ("authorization_pending", "slow_down"):
        return ""
    raise GitHubError(data.get("error_description") or data.get("error") or "GitHub sign-in failed.")
