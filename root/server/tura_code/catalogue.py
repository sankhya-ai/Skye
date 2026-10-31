"""Dynamic tool catalogue: a small standing toolset, the rest found on demand.

With ``dynamic_tools`` on, the toolsets in ``DEFERRED_PLUGINS`` are not listed to
the model at start. Two always-listed tools replace them: ``find_tools`` ranks
the deferred tools for a task and returns their schemas, ``use_tool`` runs one.

Discovery is presentation, not permission. Every call, listed or discovered,
passes ``allowed`` (the owner's ``tool_scope``) and then the owning toolset's own
approvals, so finding a tool grants nothing. The tools a session sees never
depend on what the model asks for: ``tool_scope`` is host configuration.

Whether this saves tokens is unmeasured; ``stats`` only counts definition size.
"""

from __future__ import annotations

import fnmatch
import json
import re
import time
from typing import TYPE_CHECKING, Any

from .sessions.host_tools import tool_error

if TYPE_CHECKING:
    from .sessions.runtime import SessionRuntime
    from .tenancy import Tenant

FIND_TOOL = "find_tools"
USE_TOOL = "use_tool"
GATEWAY_NAMES = (FIND_TOOL, USE_TOOL)
#: Plugins whose tools are found on demand. The coding core (files, shell, process, run_python, memory, flows) stays listed.
DEFERRED_PLUGINS = frozenset({"driver", "documents", "history", "schedules", "github", "gmail", "inspect"})
MAX_RESULTS = 8


def enabled(tenant: "Tenant") -> bool:
    return bool(tenant.settings.get().dynamic_tools)


def _matches(scope: Any, name: str) -> bool:
    return not scope or any(fnmatch.fnmatchcase(name, pattern) for pattern in scope)


def allowed(tenant: "Tenant", name: str, runtime: "SessionRuntime | None" = None) -> bool:
    """Both scopes must allow a tool name: the tenant's ``tool_scope`` and the session's own.

    Empty scope allows everything; the gateway itself is always allowed.
    """
    if name in GATEWAY_NAMES:
        return True
    record = getattr(runtime, "record", None)
    return _matches(tenant.settings.get().tool_scope, name) and _matches(getattr(record, "tool_scope", None), name)


def short(full_name: str) -> str:
    return full_name.rsplit(":", 1)[-1]


def native_exclusions(scope: Any, native: tuple[str, ...]) -> list[str]:
    """Runtime-native tools (``Shell``, ``WriteFile``...) outside the scope, by full name.

    ``Task`` is always excluded under a scope: its subagents run their own tool lists, outside the host's names.
    """
    return [full for full in native if short(full) == "Task" or not _matches(scope, short(full))]


#: Tools that run arbitrary code. While one is in scope, the scope bounds nothing that code can reach.
CODE_RUNNERS = ("Shell", "process", "run_python")


def guarantee(tenant: "Tenant", record: Any) -> dict[str, Any]:
    """What this session's scope does and does not bound. Never claim more than this."""
    tenant_scope = list(tenant.settings.get().tool_scope)
    session_scope = list(getattr(record, "tool_scope", None) or [])
    if not tenant_scope and not session_scope:
        return {"level": "none", "scoped": False, "runners": [], "note": "No scope set."}
    both = lambda n: _matches(tenant_scope, n) and _matches(session_scope, n)  # noqa: E731
    runners = [n for n in CODE_RUNNERS if both(n)]
    if runners:
        return {
            "level": "none",
            "scoped": True,
            "runners": runners,
            "note": (
                f"{', '.join(runners)} can run arbitrary code, so this scope does not stop reaching files, credentials "
                "or network APIs directly. It only shapes which named tools are offered. Remove the runners or run the "
                "session in a sandbox with no credentials to get a real boundary."
            ),
        }
    return {
        "level": "tool-names",
        "scoped": True,
        "runners": [],
        "note": (
            "No code runner is in scope: calls to host and built-in tools outside the scope are refused by name, and "
            "MCP servers and subagents are not loaded. This is not OS-level isolation: file tools still read what the "
            "runtime's sandbox lets them, and approvals still apply."
        ),
    }


def is_deferred(toolset: Any) -> bool:
    return getattr(toolset, "plugin", None) in DEFERRED_PLUGINS


def listed_definitions(tenant: "Tenant", runtime: "SessionRuntime") -> list[dict[str, Any]]:
    """What the model sees at start: scoped, minus deferred toolsets when the catalogue is on."""
    dynamic = enabled(tenant)
    out: list[dict[str, Any]] = []
    for toolset in tenant.active_toolsets():
        if dynamic and is_deferred(toolset):
            continue
        out += [d for d in toolset.definitions(runtime) if allowed(tenant, d["name"], runtime)]
    return out


def deferred_definitions(tenant: "Tenant", runtime: "SessionRuntime") -> list[tuple[Any, dict[str, Any]]]:
    return [
        (toolset, d)
        for toolset in tenant.active_toolsets()
        if is_deferred(toolset)
        for d in toolset.definitions(runtime)
        if allowed(tenant, d["name"], runtime)
    ]


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower().replace("_", " ")) if len(t) > 1]


def rank(query: str, entries: list[dict[str, Any]], limit: int = MAX_RESULTS) -> list[dict[str, Any]]:
    """Token overlap with name weighted over description; ties keep catalogue order. No model call."""
    words = set(_tokens(query))
    if not words:
        return []
    scored = []
    for index, entry in enumerate(entries):
        name = set(_tokens(entry["name"]))
        body = set(_tokens(entry.get("description", "")))
        score = 3 * len(words & name) + len(words & body)
        if score:
            scored.append((-score, index, entry))
    scored.sort(key=lambda row: row[:2])
    return [entry for _, _, entry in scored[: max(1, min(limit, MAX_RESULTS))]]


def stats(tenant: "Tenant", runtime: "SessionRuntime") -> dict[str, int]:
    """Definition size only (characters), listed vs deferred. Not a token or cost measurement."""
    size = lambda defs: sum(len(json.dumps(d)) for d in defs)  # noqa: E731
    return {
        "listed_chars": size(listed_definitions(tenant, runtime)),
        "deferred_chars": size(d for _, d in deferred_definitions(tenant, runtime)),
    }


def _composite_definitions(runtime: "SessionRuntime") -> list[dict[str, Any]]:
    """ACTIVE registry tools this session may call (created by it, or promoted durable). Empty without a registry."""
    registry = getattr(runtime.tenant, "registry", None)
    if registry is None:
        return []
    from .registry import CompositeToolset

    toolset = next((t for t in runtime.tenant.toolsets if isinstance(t, CompositeToolset)), None)
    return toolset.definitions(runtime) if toolset is not None else []


def _composite_record(runtime: "SessionRuntime", name: str) -> dict[str, Any] | None:
    registry = getattr(runtime.tenant, "registry", None)
    if registry is None:
        return None
    record = registry.active_tool(name, getattr(runtime.record, "id", None))
    return {**record, "name": name} if record is not None else None


def gateway_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": FIND_TOOL,
            "description": (
                "Find extra tools for the task at hand (documents, GitHub, Gmail, schedules, session history, Mac apps). "
                "Describe what you need in a few words; returns matching tool names with their schemas. "
                "Finding a tool does not allow its use: it still asks the owner where it writes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What you need, e.g. `read a PDF` or `comment on a pull request`."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": USE_TOOL,
            "description": f"Run a tool that `{FIND_TOOL}` returned, by name, with arguments matching its schema. Also the way to call a registry tool created earlier in this turn.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "arguments": {"type": "object"}},
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    ]


class GatewayToolset:
    plugin = None

    def definitions(self, runtime: "SessionRuntime") -> list[dict[str, Any]]:
        return gateway_definitions() if enabled(runtime.tenant) else []

    def handles(self, name: str) -> bool:
        return name in GATEWAY_NAMES

    async def call(self, runtime: "SessionRuntime", call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tenant = runtime.tenant
        if not enabled(tenant):
            return tool_error("The dynamic tool catalogue is off.", "Off")
        found = deferred_definitions(tenant, runtime)
        if name == FIND_TOOL:
            limit = arguments.get("limit") if isinstance(arguments.get("limit"), int) else MAX_RESULTS
            hits = rank(str(arguments.get("query") or ""), [d for _, d in found] + _composite_definitions(runtime), limit)
            if not hits:
                return capability_miss(runtime, str(arguments.get("query") or ""))
            return {"is_error": False, "output": json.dumps(hits, ensure_ascii=False), "message": "", "display": [{"type": "brief", "text": f"Found {len(hits)} tool(s)"}]}
        target = str(arguments.get("name") or "")
        args = arguments.get("arguments") or {}
        if not isinstance(args, dict):
            return tool_error("`arguments` must be an object.", "Invalid arguments")
        owner = next((t for t, d in found if d["name"] == target), None)
        # A registry tool created this turn is not in the runtime's initialized tool list; the gateway still reaches it,
        # through the same scope check as any call. No runtime change, no bypass: each pipeline step re-checks scope and approvals.
        if owner is None:
            composite = _composite_record(runtime, target)
            if composite is not None and allowed(tenant, target, runtime):
                from .registry import run_composite

                return await run_composite(tenant, runtime, call_id, composite, args, tenant.registry)
        # Re-checked at call time: the model may name a tool it never found, or one outside the scope.
        if owner is None or not allowed(tenant, target, runtime):
            return tool_error(f"`{target}` is not an available tool. Use `{FIND_TOOL}` to see what is.", "Unavailable")
        return await owner.call(runtime, call_id, target, args)


#: Every this many find_tools misses in a session, the owner sees a CapabilityGap notice.
MISS_NOTIFY_EVERY = 3
#: The session record keeps at most this many recent misses.
MISS_KEEP = 20


def capability_miss(runtime: "SessionRuntime | None", query: str) -> dict[str, Any]:
    """A find_tools miss is a structured capability-gap signal, not just prose.

    Deterministic detection only: the session record tallies the miss, the result tells the model how a genuinely
    missing capability can be added (compose with ``create_tool``, or generate code with ``propose_tool`` - owner
    activates the exact hash), and every MISS_NOTIFY_EVERY misses the owner sees a CapabilityGap notice. A miss is
    NOT proof that a capability is missing or that building one will succeed; the model still decides what, if
    anything, to build."""
    misses: list[dict[str, Any]] = []
    if runtime is not None:
        record = getattr(runtime, "record", None)
        if record is not None:
            misses = list(getattr(record, "capability_misses", None) or [])
            misses.append({"query": query[:200], "at": time.time()})
            misses = misses[-MISS_KEEP:]
            record.capability_misses = misses
            save = getattr(runtime, "save_record", None)
            if callable(save):
                save()
            emit = getattr(runtime, "emit", None)
            if callable(emit) and len(misses) % MISS_NOTIFY_EVERY == 0:
                emit("CapabilityGap", {"queries": [m["query"] for m in misses[-MISS_NOTIFY_EVERY:]], "misses": len(misses)})
    hint = ("No matching tool. If the capability is genuinely missing, `create_tool` composes existing tools into a new one and "
            "`propose_tool` generates new code (the owner activates the exact reviewed hash). "
            "Do the task with the tools already listed when one fits.")
    if misses:
        hint += f" ({len(misses)} miss(es) this session; the owner is shown a CapabilityGap notice every {MISS_NOTIFY_EVERY}.)"
    return {"is_error": False, "output": hint, "message": "", "display": [{"type": "brief", "text": "No tools found"}]}


def install(server) -> None:
    def extend(tenant) -> None:
        tenant.toolsets.append(GatewayToolset())

    server.extensions.append(extend)
