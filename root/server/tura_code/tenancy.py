"""Who the server serves: one owner (the local app) or many people (hosted).

The **server** owns what is shared and expensive — one Dhee instance, the
model catalog, the LLM proxy, the runtime pool, the sandbox. A **tenant** is
one person's world: their settings, sessions, trust ledger, learned skills,
memory scope and (hosted) workspaces. Tenants are built on first use.

    local:   <home>/                    the owner's data, workspaces anywhere
    hosted:  <home>/users/<id>/         one directory per person
             <home>/users/<id>/workspaces/   the only place their sessions may work
             <home>/users/<id>/home/    $HOME inside their sandbox
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import launcher
from .llm_proxy import Restriction, LLMProxy
from .memory import OWNER_SCOPE, MemoryGateway, MemoryView
from .models import ModelCatalog
from .pool import RuntimePool
from .sandbox import Sandbox
from .sessions import SessionManager
from .sessions.host_tools import MemoryToolset, ProcessToolset, Toolset
from .settings import SettingsStore
from .trust import TrustLedger
from .users import UserStore

if TYPE_CHECKING:
    from .sessions import SessionRuntime, TurnState

logger = logging.getLogger(__name__)
LOCAL = "local"


class WorkspaceError(ValueError):
    pass


class Tenant:
    def __init__(self, server: "Server", tenant_id: str, root: Path, *, settings: SettingsStore, owner_scope: str, workspace_root: Path | None, home: Path | None) -> None:
        self.server = server
        self.id = tenant_id
        self.root = root
        self.settings = settings
        self.sessions_dir = root / "sessions"
        self.skills_dir = root / "skills"
        self.workspace_root = workspace_root
        self.home = home
        self.trust = TrustLedger(root / "trust.json")
        self.memory = MemoryView(server.gateway, owner_scope)
        self.toolsets: list[Toolset] = [MemoryToolset(), ProcessToolset()]
        #: Awaited when the tenant closes (a service's background work).
        self.closers: list[Any] = [self._close_digests]
        #: Turn digests still writing to memory (``_checkpoint``).
        self._digests: set[asyncio.Task] = set()
        self.sessions = SessionManager(self)
        for extension in server.extensions:
            extension(self)

    # ----- shared resources --------------------------------------------------

    @property
    def pool(self) -> RuntimePool:
        return self.server.pool

    @property
    def proxy(self) -> LLMProxy:
        return self.server.proxy

    @property
    def sandbox(self) -> Sandbox:
        return self.server.sandbox

    def sandbox_for(self, record) -> Sandbox:
        """The jail a session runs in: its own ``isolation`` if the host set one, else the server's sandbox."""
        mode = getattr(record, "isolation", "")
        if not mode:
            return self.server.sandbox
        from .sandbox import Bubblewrap

        if mode == "bwrap":
            return Bubblewrap()
        if mode == "bwrap-model-only":
            return Bubblewrap(network=False, bridge=self.server.llm_bridge())
        raise ValueError(f"Unknown isolation {mode!r}.")

    def api_key(self) -> str:
        """This person's key, else the server's (hosted: the operator's)."""
        own = self.settings.get().openrouter_api_key.strip()
        if self.server.mode == "hosted":
            return own or self.server.settings.get().api_key()
        return self.settings.get().api_key()

    # ----- workspaces ----------------------------------------------------------

    def check_workspace(self, workspace: str | Path) -> Path:
        raw_str = str(workspace).strip()
        path = Path(raw_str).expanduser()
        if self.workspace_root is not None and not path.is_absolute():
            if raw_str.startswith("shared:"):
                clean = raw_str.removeprefix("shared:").strip()
                path = self.server.shared_workspaces / clean
            elif (self.server.shared_workspaces / raw_str).is_dir():
                path = self.server.shared_workspaces / raw_str
            else:
                path = self.workspace_root / path
        path = path.resolve()
        if not path.is_dir():
            raise WorkspaceError(f"Workspace does not exist or is not a directory: {path}")
        if self.workspace_root is not None:
            is_personal = path.is_relative_to(self.workspace_root.resolve())
            is_shared = path.is_relative_to(self.server.shared_workspaces.resolve())
            if not (is_personal or is_shared):
                raise WorkspaceError("Workspaces must be inside your personal or shared workspace folder.")
        return path

    def touch(self, record) -> None:
        if record.kind == "session":
            self.settings.remember_workspace(record.workspace)

    # ----- launching -----------------------------------------------------------

    def launch_spec(self, runtime: "SessionRuntime") -> launcher.LaunchSpec:
        settings = self.settings.get()
        endpoint = None
        if self.proxy.enabled and not launcher.scripted():
            base = settings.base_url
            rule = None
            if runtime.record.isolation:
                steps = runtime.record.max_steps
                rule = Restriction(
                    models=lambda: {self.settings.get().model, *self.settings.get().fallback_models},
                    max_requests=max(60, 3 * steps) if steps else 300,
                )
                self.proxy.budget(runtime.record.id, runtime.record.max_tokens)
            endpoint = self.proxy.issue(self.id, runtime.record.id, lambda: (base, self.api_key()), fallbacks=lambda: self.settings.get().fallback_models, restrict=rule)
        return launcher.LaunchSpec(
            session_id=runtime.record.id,
            workspace=Path(runtime.record.workspace),
            settings=settings,
            permission_mode=runtime.record.permission_mode,  # type: ignore[arg-type]
            memory_enabled=self.memory.enabled,
            capabilities=list(self.server.model_capabilities.get(settings.model, [])),
            mcp_config=launcher.user_mcp_config(self.root),
            api_key="" if endpoint else self.api_key(),
            endpoint=endpoint,
            skills_dirs=(self.skills_dir,) if self.skills_dir.is_dir() else (),
            script_role=runtime.record.role or "main",
            sandbox=self.sandbox_for(runtime.record),
            session_dir=runtime.dir,
            home=self.home,
            extra_writable=self._git_dirs(runtime),
            planning_mode=settings.planning_mode,
            answering=runtime.record.kind if runtime.record.answering else "",
            toolbox=getattr(getattr(self, "toolbox", None), "root", None),
            tool_scope=tuple(runtime.record.tool_scope),
            max_steps=runtime.record.max_steps,
            max_tokens=runtime.record.max_tokens,
            max_wall_seconds=runtime.record.max_wall_seconds,
            max_cost_usd=runtime.record.max_cost_usd,
        )

    @staticmethod
    def _git_dirs(runtime: "SessionRuntime") -> tuple[Path, ...]:
        """A worktree's Git metadata lives in its main repository: it must be writable too."""
        from .flows.git import common_dir

        found = common_dir(Path(runtime.record.workspace))
        return (found,) if found else ()

    def director_state(self, spec: launcher.LaunchSpec) -> str:
        """Whether Jev can actually steer: it needs an OpenRouter-shaped endpoint and a key."""
        settings = spec.settings
        if settings.director_mode == "off" or launcher.scripted():
            return "off"
        base = spec.endpoint[0] if spec.endpoint else settings.base_url
        if not (spec.endpoint or spec.key()) or "/v1" not in base:
            return "off"
        return settings.director_mode

    def active_toolsets(self) -> list[Toolset]:
        """The host toolsets of every plugin that is on."""
        off = set(self.settings.get().disabled_plugins)
        return [t for t in self.toolsets if getattr(t, "plugin", None) not in off]

    def tool_definitions(self, runtime: "SessionRuntime") -> list[dict[str, Any]]:
        from . import catalogue

        return catalogue.listed_definitions(self, runtime)

    def command_environment(self, runtime: "SessionRuntime") -> dict[str, str]:
        from .sandbox import Mounts

        env = launcher.scrubbed_environment()
        return self.sandbox_for(runtime.record).environment(env, Mounts(workspace=Path(runtime.record.workspace), home=self.home))

    def wrap_command(self, runtime: "SessionRuntime", argv: list[str]) -> list[str]:
        from .sandbox import Mounts

        mounts = Mounts(workspace=Path(runtime.record.workspace), writable=(runtime.dir, *self._git_dirs(runtime)), home=self.home)
        return self.sandbox_for(runtime.record).wrap(argv, mounts)

    # ----- after a turn --------------------------------------------------------

    async def after_turn(self, runtime: "SessionRuntime", turn: "TurnState", status: str) -> None:
        await self._checkpoint(runtime, turn, status)
        for hook in self.server.after_turn_hooks:
            try:
                await hook(self, runtime, turn, status)
            except Exception:  # noqa: BLE001 - nothing after a turn may fail it
                logger.exception("after-turn hook failed")

    def memory_for(self, record: Any) -> MemoryView:
        """The memory this session may reach: a delegated worker (stamped by the host on the record) gets project scope only."""
        return self.memory.restricted() if getattr(record, "delegated", False) is True else self.memory

    async def _checkpoint(self, runtime: "SessionRuntime", turn: "TurnState", status: str) -> None:
        """Checkpoint turns that did real work; a quick answer is not an episode.

        The digest is a model call, so it runs beside the session: the next
        (queued) message never waits for memory.
        """
        if not self.memory.enabled or turn.prompt.startswith("/") or runtime.record.kind != "session":
            return
        if not turn.files_touched and turn.tool_count < 3:
            return
        task = asyncio.create_task(self._digest(runtime, turn, status))
        self._digests.add(task)
        task.add_done_callback(self._digests.discard)

    async def _digest(self, runtime: "SessionRuntime", turn: "TurnState", status: str) -> None:
        from .memory.digest import digest_turn

        try:
            result = await digest_turn(self.memory_for(runtime.record), Path(runtime.record.workspace), turn, status, settings=self.settings.get(), api_key=self.api_key())
            runtime.emit("MemoryCheckpoint", {"saved": True, "files_touched": turn.files_touched, **result})
        except Exception as error:  # noqa: BLE001
            logger.info("turn checkpoint skipped: %s", error)

    async def _close_digests(self) -> None:
        for task in list(self._digests):
            task.cancel()
        await asyncio.gather(*self._digests, return_exceptions=True)

    async def close(self) -> None:
        for closer in self.closers:
            await closer()
        await self.sessions.close()


class Server:
    """What every tenant shares."""

    def __init__(self, home: Path, *, mode: str = "local", sandbox: Sandbox | None = None, pool: RuntimePool | None = None) -> None:
        if mode not in ("local", "hosted"):
            raise ValueError("mode must be `local` or `hosted`")
        self.home = home
        self.mode = mode
        self.settings = SettingsStore(home)
        self.gateway = MemoryGateway(home, self.settings.get)
        self.catalog = ModelCatalog()
        self.proxy = LLMProxy()
        self.pool = pool or RuntimePool()
        self.sandbox = sandbox or Sandbox()
        self._bridge = None

        self.users = UserStore(home / "users.json")
        self.shared_workspaces = home / "shared_workspaces"
        self.shared_workspaces.mkdir(parents=True, exist_ok=True)
        self.model_capabilities: dict[str, list[str]] = {}
        #: Called with each new tenant, to add toolsets and services (learning, flows).
        self.extensions: list[Any] = []
        #: Awaited after every turn: (tenant, runtime, turn, status).
        self.after_turn_hooks: list[Any] = []
        #: Server-wide services with ``start()`` and ``async close()`` (the line).
        self.background: list[Any] = []
        self.tenants: dict[str, Tenant] = {}

    def llm_bridge(self):
        """(socket path, loopback port) of the unix-socket bridge to the LLM proxy, started on first use; None without a proxy."""
        if not self.proxy.enabled:
            return None
        from urllib.parse import urlparse

        from .netbridge import Bridge

        target = urlparse(self.proxy.base or "")
        if self._bridge is None:
            self._bridge = Bridge(self.home / "run" / "llm.sock", target.hostname or "127.0.0.1", target.port or 80).start()
        return (self._bridge.path, self._bridge.port)

    def tenant(self, tenant_id: str = LOCAL) -> Tenant:
        tenant = self.tenants.get(tenant_id)
        if tenant is not None:
            return tenant
        if self.mode == "local":
            if tenant_id != LOCAL:
                raise KeyError(tenant_id)
            tenant = Tenant(self, LOCAL, self.home, settings=self.settings, owner_scope=OWNER_SCOPE, workspace_root=None, home=None)
        else:
            root = self.home / "users" / tenant_id
            workspaces = root / "workspaces"
            workspaces.mkdir(parents=True, exist_ok=True)
            tenant = Tenant(
                self,
                tenant_id,
                root,
                settings=SettingsStore(root),
                owner_scope=f"owner:{tenant_id}",
                workspace_root=workspaces,
                home=root / "home",
            )
        self.tenants[tenant_id] = tenant
        return tenant

    def start(self) -> None:
        self.pool.start()
        for service in self.background:
            service.start()

    async def refresh_capabilities(self) -> None:
        if launcher.scripted() or os.environ.get("TURA_CODE_OFFLINE"):
            return
        items = await self.catalog.models(self.settings.get().base_url)
        self.model_capabilities = {m["id"]: m["capabilities"] for m in items}

    async def close(self) -> None:
        for service in self.background:
            await service.close()
        for tenant in list(self.tenants.values()):
            await tenant.close()
        await self.pool.close()
        await self.proxy.close()
        if self._bridge is not None:
            self._bridge.stop()
        await self.gateway.close()
