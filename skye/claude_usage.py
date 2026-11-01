"""What Skye knows about the owner's Claude Code account and usage limits - without spending
a single token to find out.

Two sources, both first-party and both free:

- ``claude auth status --json``: logged in or not, the auth method and the plan
  (pro / max / team / ...). It reads local auth state; no model call.
- ``rate_limit_event`` frames that Claude Code emits on its stream-json output during the
  runs Skye already makes. Each carries the subscription window it describes
  (``rateLimitType``: five_hour, seven_day, seven_day_opus, ...), its ``status``
  (allowed / allowed_warning / rejected), when it resets (``resetsAt``, epoch seconds) and,
  when the service reports it, ``utilization`` (0..1). Skye persists the latest frame per
  window so the UI can show "how much is left" between runs.

What this is NOT: a live meter. Between runs the numbers are as of the last run Skye saw;
the UI shows that timestamp. Skye never reads Claude Code's stored OAuth credentials to
call usage endpoints itself - the owner's login stays the CLI's business.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_ACCOUNT_CACHE: dict[str, Any] = {"at": 0.0, "value": None}
ACCOUNT_TTL = 120.0  # seconds; `claude auth status` is local but not free of process cost


def _state_dir() -> Path | None:
    """Where usage is recorded: the running Skye's state folder. Outside Skye (a bare
    library call, a test) nothing is recorded rather than littering the current directory."""
    value = os.environ.get("SKYE_STATE_DIR", "").strip()
    return Path(value) if value else None


def _store_path(state_dir: str | Path | None = None) -> Path | None:
    base = state_dir or _state_dir()
    return Path(base) / "claude-usage.json" if base else None


def _read(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", "utf-8")
    tmp.replace(path)


_INFO_KEYS = ("status", "resetsAt", "rateLimitType", "utilization", "isUsingOverage",
              "overageStatus", "overageResetsAt", "surpassedThreshold")


def record_rate_limit(info: dict[str, Any], state_dir: str | Path | None = None) -> None:
    """Persist the latest rate_limit_info for its window. Unknown shapes are ignored."""
    if not isinstance(info, dict) or not isinstance(info.get("status"), str):
        return
    window = str(info.get("rateLimitType") or "current")
    entry = {k: info[k] for k in _INFO_KEYS if k in info}
    entry["seen_at"] = time.time()
    path = _store_path(state_dir)
    if path is None:
        return
    with _LOCK:
        data = _read(path)
        windows = data.get("windows") if isinstance(data.get("windows"), dict) else {}
        windows[window] = entry
        data["windows"] = windows
        _write(path, data)


def record_run(usage: dict[str, Any], *, role: str = "", model: str = "",
               state_dir: str | Path | None = None) -> None:
    """Add one finished Claude Code run to Skye's own running totals (what Skye spent)."""
    if not isinstance(usage, dict):
        return
    path = _store_path(state_dir)
    if path is None:
        return
    with _LOCK:
        data = _read(path)
        totals = data.get("skye_totals") if isinstance(data.get("skye_totals"), dict) else {}
        totals["runs"] = int(totals.get("runs") or 0) + 1
        totals["turns"] = int(totals.get("turns") or 0) + int(usage.get("num_turns") or 0)
        try:
            totals["cost_usd"] = round(float(totals.get("cost_usd") or 0)
                                       + float(usage.get("total_cost_usd") or 0), 6)
        except (TypeError, ValueError):
            pass
        for key in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                    "cache_creation_input_tokens"):
            if usage.get(key) is not None:
                try:
                    totals[key] = int(totals.get(key) or 0) + int(usage[key])
                except (TypeError, ValueError):
                    pass
        totals["last_run_at"] = time.time()
        data["skye_totals"] = totals
        recent = data.get("recent_runs") if isinstance(data.get("recent_runs"), list) else []
        recent.append({"at": time.time(), "role": role, "model": model,
                       "turns": usage.get("num_turns"), "cost_usd": usage.get("total_cost_usd"),
                       "duration_ms": usage.get("duration_ms")})
        data["recent_runs"] = recent[-20:]
        _write(path, data)


def account(force: bool = False) -> dict[str, Any]:
    """The CLI's own view of the login: installed, logged in, plan. Cached briefly."""
    now = time.monotonic()
    with _LOCK:
        cached = _ACCOUNT_CACHE["value"]
        if cached is not None and not force and now - _ACCOUNT_CACHE["at"] < ACCOUNT_TTL:
            return dict(cached)
    binary = shutil.which("claude")
    if not binary:
        value: dict[str, Any] = {"installed": False, "logged_in": False,
                                 "hint": "Install Claude Code: https://claude.com/claude-code"}
    else:
        value = {"installed": True, "logged_in": False}
        try:
            proc = subprocess.run([binary, "auth", "status", "--json"], capture_output=True,
                                  text=True, timeout=20, env=worker_env())
            data = json.loads(proc.stdout or "{}")
            value.update({
                "logged_in": bool(data.get("loggedIn")),
                "auth_method": str(data.get("authMethod") or ""),
                "plan": str(data.get("subscriptionType") or ""),
                "provider": str(data.get("apiProvider") or ""),
            })
            if not value["logged_in"]:
                value["hint"] = "Run `claude` once in a terminal and log in."
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            value["error"] = f"could not read `claude auth status`: {error}"
        try:
            version = subprocess.run([binary, "--version"], capture_output=True, text=True,
                                     timeout=10, env=worker_env())
            value["version"] = (version.stdout or "").strip().split(" ")[0]
        except (OSError, subprocess.TimeoutExpired):
            pass
    with _LOCK:
        _ACCOUNT_CACHE.update({"at": now, "value": value})
    return dict(value)


def snapshot(state_dir: str | Path | None = None, force_account: bool = False) -> dict[str, Any]:
    """Everything the UI's usage card shows, in one payload."""
    data = _read(_store_path(state_dir))
    windows = data.get("windows") if isinstance(data.get("windows"), dict) else {}
    now = time.time()
    shaped = []
    for name, entry in windows.items():
        if not isinstance(entry, dict):
            continue
        resets = entry.get("resetsAt")
        expired = isinstance(resets, (int, float)) and resets < now
        shaped.append({"window": name, **entry, "expired": bool(expired)})
    order = {"five_hour": 0, "seven_day": 1, "seven_day_opus": 2, "seven_day_sonnet": 3}
    shaped.sort(key=lambda w: order.get(w["window"], 9))
    return {"account": account(force=force_account), "windows": shaped,
            "skye_totals": data.get("skye_totals") or {},
            "recent_runs": (data.get("recent_runs") or [])[-5:],
            "usage_page": "https://claude.ai/settings/usage"}


def worker_env() -> dict[str, str]:
    """The environment Skye launches Claude Code with. When Skye itself was started from
    inside a Claude Code session, the CLI's own nesting markers would leak into the worker
    and change its behavior; they are removed. Everything else (login, PATH) is inherited."""
    env = dict(os.environ)
    for key in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT"):
        env.pop(key, None)
    return env
