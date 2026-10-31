"""Persistent host settings and the data-directory layout.

Everything root writes lives under one directory (``TURA_CODE_HOME``,
default ``~/.tura-code``):

    settings.json        this module (0600: it holds the API keys, unless the
                         Keychain does: see ``vault``)
    sessions/<id>/       session.json + events.jsonl, see ``sessions``
    runtime/             per-session agent overlays and runtime config files
    kimi/                the runtime's own share dir (context, wire logs)
    dhee/                Dhee's memory store
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from . import vault as vaults

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"

PermissionMode = Literal["default", "accept_edits", "plan", "yolo"]
DirectorMode = Literal["on", "shadow", "off"]
MemoryMode = Literal["local", "openrouter", "off"]
LearningMode = Literal["review", "auto", "off"]
PlanningMode = Literal["default", "scaffold", "cost_saver", "off"]
ActionSpaceMode = Literal["predefined", "bash_only", "auto"]
ReasoningEffort = Literal["", "none", "low", "medium", "high", "xhigh"]

#: Credentials: never sent to the UI, and a blank field in a form means "unchanged".
SECRET_FIELDS = ("openrouter_api_key", "parallel_api_key", "firecrawl_api_key")


def data_dir() -> Path:
    raw = os.environ.get("TURA_CODE_HOME", "").strip()
    path = Path(raw).expanduser() if raw else Path.home() / ".tura-code"
    path.mkdir(parents=True, exist_ok=True)
    return path


class Settings(BaseModel):
    """What the owner chooses in the settings panel."""

    openrouter_api_key: str = ""
    base_url: str = OPENROUTER_BASE_URL
    # Cheap and fast; the harness (Jev, Dhee, tolerant edits, argument repair)
    # is built so a model of this class can do real multi-file work.
    model: str = "deepseek/deepseek-v4.1-flash"
    # Tried in order when the model fails or its providers are down (OpenRouter's `models`).
    fallback_models: list[str] = Field(default_factory=list, max_length=3)
    max_context_size: int = Field(default=200_000, ge=8_000, le=4_000_000)
    reasoning_effort: ReasoningEffort = ""
    # Which of the model's OpenRouter providers to prefer. Speed and price
    # differ a lot between providers of the same model: "" keeps OpenRouter's
    # own balancing.
    provider_sort: Literal["", "throughput", "price", "latency"] = ""
    max_steps_per_turn: int = Field(default=100, ge=1, le=1_000)
    # Jev (`typesafe/jev-1.13`) decides, before each step, whether the model
    # should reply or which tool it should call. See runtime/.../director.rs.
    director_mode: DirectorMode = "on"
    # Real memory once there is a key (local, lexical memory until then): see ``memory.gateway``.
    memory_mode: MemoryMode = "openrouter"
    memory_llm_model: str = "openai/gpt-4o-mini"
    memory_embedding_model: str = "openai/text-embedding-3-small"
    memory_embedding_dims: int = Field(default=1536, ge=64, le=8192)
    # Web search is keyless by default (Parallel, then Firecrawl). Keys lift
    # their per-IP limits; the paid OpenRouter plugin is an opt-in last resort.
    web_search: bool = True
    parallel_api_key: str = ""
    firecrawl_api_key: str = ""
    web_search_paid_fallback: bool = False
    search_model: str = "openai/gpt-4o-mini"
    # The Hermes-style learning loop: after substantive turns a reviewer
    # proposes skills and memories. `review` queues them for the owner,
    # `auto` promotes what passes the overfit filter, `off` skips the review.
    learning_mode: LearningMode = "review"
    # The most parallel candidates the agent may give a coding flow. Each one
    # repeats the whole job (and its tokens); the owner raises this, not the agent.
    flow_max_candidates: int = Field(default=1, ge=1, le=3)
    # Plugins the owner switched off (``plugins``): the agent gets none of their tools.
    disabled_plugins: list[str] = Field(default_factory=list)
    # Dynamic tool catalogue (``catalogue``): documents, GitHub, Gmail, schedules, history and Mac-app
    # tools are found on demand instead of listed at start. Off by default; savings are unmeasured.
    dynamic_tools: bool = False
    # Host-set allow-list of tool name patterns (fnmatch) for every call, listed or discovered. Empty = no limit.
    tool_scope: list[str] = Field(default_factory=list)
    learning_model: str = ""
    default_permission_mode: PermissionMode = "default"
    recent_workspaces: list[str] = Field(default_factory=list)
    planning_mode: PlanningMode = "default"
    action_space_mode: ActionSpaceMode = "predefined"

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.strip().rstrip("/") or OPENROUTER_BASE_URL

    def api_key(self) -> str:
        """The key in effect: the environment wins over the stored one."""
        return os.environ.get(OPENROUTER_KEY_ENV, "").strip() or self.openrouter_api_key.strip()

    def key_source(self) -> str:
        if os.environ.get(OPENROUTER_KEY_ENV, "").strip():
            return "environment"
        if self.openrouter_api_key.strip():
            return "settings"
        return "missing"

    def public(self) -> dict:
        """Settings as the UI may see them: never a credential itself."""
        data = self.model_dump()
        key = self.api_key()
        for name in SECRET_FIELDS:
            data[f"{name.removesuffix('_api_key')}_key_set"] = bool(data[name])
            data[name] = ""
        data["api_key_set"] = bool(key)
        data["api_key_hint"] = f"…{key[-4:]}" if len(key) >= 8 else ""
        data["api_key_source"] = self.key_source()
        return data


class SettingsStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or data_dir()
        self.path = self.root / "settings.json"
        self.vault = vaults.for_root(self.root)
        self._lock = threading.Lock()
        #: The secrets as last written to the vault, so a save writes only what changed.
        self._vaulted: dict[str, str] = {}
        self._settings = self._load()

    def _load(self) -> Settings:
        try:
            data = json.loads(self.path.read_text("utf-8")) if self.path.exists() else {}
            settings = Settings.model_validate(data)
        except (OSError, ValueError):
            data, settings = {}, Settings()
        if self.vault.inline:
            return settings
        migrated = False
        for name in SECRET_FIELDS:
            if data.get(name):
                self.vault.set(name, str(data[name]))  # a key the file still holds moves to the Keychain
                migrated = True
            self._vaulted[name] = self.vault.get(name)
            setattr(settings, name, self._vaulted[name])
        if migrated:
            self._settings = settings
            self._write()
        return settings

    def get(self) -> Settings:
        with self._lock:
            return self._settings.model_copy(deep=True)

    def update(self, changes: dict) -> Settings:
        with self._lock:
            data = self._settings.model_dump()
            for key, value in changes.items():
                if key not in data:
                    continue
                # An empty credential in the form means "unchanged", not
                # "erase"; erasing is an explicit `clear_<field>`.
                if key in SECRET_FIELDS and not str(value or "").strip():
                    continue
                data[key] = value
            if changes.get("clear_api_key"):
                data["openrouter_api_key"] = ""
            for name in SECRET_FIELDS:
                if changes.get(f"clear_{name}"):
                    data[name] = ""
            self._settings = Settings.model_validate(data)
            self._write()
            return self._settings.model_copy(deep=True)

    def remember_workspace(self, workspace: str) -> None:
        with self._lock:
            recent = [w for w in self._settings.recent_workspaces if w != workspace]
            self._settings.recent_workspaces = [workspace, *recent][:12]
            self._write()

    def forget_workspace(self, workspace: str) -> None:
        with self._lock:
            self._settings.recent_workspaces = [w for w in self._settings.recent_workspaces if w != workspace]
            self._write()

    def _write(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        stored = self._settings
        if not self.vault.inline:
            for name in SECRET_FIELDS:
                value = getattr(self._settings, name)
                if self._vaulted.get(name, "") != value:
                    self.vault.set(name, value)
                    self._vaulted[name] = value
            stored = self._settings.model_copy(update={name: "" for name in SECRET_FIELDS})
        data = stored.model_dump()
        if not self.vault.inline:
            data[vaults.MARKER] = self.vault.name  # every root on this directory reads the Keychain now
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2))
        os.replace(tmp, self.path)
