"""The registry: tools and roles created at runtime, as data, under deterministic rules.

``create_tool`` and ``create_role`` let a session (usually a job) extend Root without editing source. What they
create is DECLARATIVE: a tool is a fixed pipeline of existing host tools with templated arguments; a role is a named
tool scope composed from existing tools plus existing guides. No generated code ever runs in the host: a spec carrying
``code``, ``command``, ``shell`` or ``url`` is rejected outright, because this host cannot validate arbitrary
generated code. A genuinely new capability is a reviewed plugin, not a registry entry.

The rules are enforced by ordinary code, not by asking the model to behave:

* Validation is static and deterministic (names, schemas, templates, references, size limits). An invalid spec is
  refused with a list of reasons; nothing half-exists.
* Lifecycle: a created tool is ACTIVE IN THE CREATING SESSION at once (static validation has passed) and the session
  is expected to tell the owner what it created. It is visible nowhere else. The owner promotes it with
  ``POST /api/registry/tools/{name}/promote`` to make it DURABLE - part of the catalogue offered to future sessions.
  That promotion is the owner's explicit choice; creation never implies it. Declared tests are a stronger gate the
  creator can choose: run them and a failure takes the tool back to DRAFT even in its own session.
* Execution re-checks everything, every call: each pipeline step passes the session's tool scope
  (``catalogue.allowed``) and the owning toolset's own approvals, exactly like a directly called tool. A composite
  tool grants nothing by existing, and validation at creation time is not treated as a permanent fact.
* Composites cannot call composites, the discovery gateway, or the registry's own tools: recursion and self-modifying
  pipelines are impossible by construction, not by audit.
* Roles are versioned and never edited in place; a job pins the version it started with. Using a role still requires
  an owner-authenticated ``POST /api/jobs``, so ``create_role`` can never grant the calling session anything.
* What a created role bounds is reported with the same honesty as the built-in roles: a role whose scope includes
  Shell, process or run_python bounds nothing (``catalogue.guarantee``).
"""

from __future__ import annotations

import functools
import json
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    import fcntl
except ImportError:  # Windows development machines: no inter-process lock; single-writer deployments only.
    fcntl = None

from . import catalogue
from .sessions.host_tools import tool_error

if TYPE_CHECKING:
    from .sessions.runtime import SessionRuntime
    from .tenancy import Tenant

CREATE_TOOL = "create_tool"
CREATE_ROLE = "create_role"
LIST_TOOL = "registry_list"
ADOPT_ROLE = "adopt_role"
RECOMMEND_JOB = "recommend_job"
MANAGEMENT_TOOLS = (CREATE_TOOL, CREATE_ROLE, LIST_TOOL, "propose_tool", ADOPT_ROLE, RECOMMEND_JOB)  # propose_tool lives in genplugins.py

MAX_TOOLS = 64
MAX_ROLES = 32
MAX_VERSIONS_KEPT = 20
MAX_STEPS = 8
MAX_TESTS = 5
MAX_STEP_ARGS_CHARS = 2000
MAX_OUTPUT_CHARS = 4000
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
TEMPLATE_RE = re.compile(r"\$\{([a-z][a-z0-9_]*)\}")

#: Native runtime tool names a role scope may name (``Task`` is excluded: its subagents run outside the host's names).
NATIVE_TOOLS = {"ReadFile", "ReadMediaFile", "WriteFile", "StrReplaceFile", "Glob", "Grep", "Shell", "run_python"}
#: Fields that would make a tool spec executable code or network access. Rejected, not sanitized: this host cannot
#: validate them, so a spec carrying one is not a registry tool at all.
EXECUTABLE_FIELDS = ("code", "command", "shell", "script", "url", "endpoint")


class RegistryError(ValueError):
    pass


# ----- a JSON Schema subset, checked by hand --------------------------------------------------------------------------

SCHEMA_TYPES = {"string", "integer", "number", "boolean", "array", "object"}


def validate_schema(schema: Any, path: str = "parameters") -> list[str]:
    """Structural problems in a parameter schema. Only a small, deterministic subset of JSON Schema is accepted:
    an object with typed properties, ``required``, ``enum`` and ``additionalProperties: false``."""
    problems: list[str] = []
    if not isinstance(schema, dict):
        return [f"{path}: must be an object schema"]
    if schema.get("type") != "object":
        problems.append(f"{path}: `type` must be \"object\"")
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        problems.append(f"{path}: `properties` must be an object")
        props = {}
    for name, sub in props.items():
        if not NAME_RE.match(name):
            problems.append(f"{path}.{name}: parameter names must match {NAME_RE.pattern}")
            continue
        problems += validate_property(sub, f"{path}.{name}")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(r, str) for r in required):
        problems.append(f"{path}: `required` must be a list of parameter names")
    else:
        for r in required:
            if r not in props:
                problems.append(f"{path}: `required` names `{r}`, which is not a property")
    if schema.get("additionalProperties") is not False:
        problems.append(f"{path}: `additionalProperties` must be false (a registry tool's interface is closed; missing means open)")
    unknown = set(schema) - {"type", "properties", "required", "additionalProperties", "description"}
    if unknown:
        problems.append(f"{path}: unsupported schema keys: {', '.join(sorted(unknown))}")
    return problems


def validate_property(sub: Any, path: str) -> list[str]:
    if not isinstance(sub, dict):
        return [f"{path}: must be an object"]
    problems = []
    stype = sub.get("type")
    if stype not in SCHEMA_TYPES:
        problems.append(f"{path}: `type` must be one of {', '.join(sorted(SCHEMA_TYPES))}")
    if "enum" in sub:
        enum = sub["enum"]
        if not isinstance(enum, list) or not enum:
            problems.append(f"{path}: `enum` must be a non-empty list")
    if stype == "array" and "items" in sub:
        problems += validate_property(sub["items"], f"{path}[]")
    unknown = set(sub) - {"type", "description", "enum", "items", "default"}
    if unknown:
        problems.append(f"{path}: unsupported schema keys: {', '.join(sorted(unknown))}")
    return problems


def validate_arguments(schema: dict[str, Any], args: Any) -> list[str]:
    """Call-time check of arguments against the same subset. Deterministic; no coercion."""
    if not isinstance(args, dict):
        return ["arguments must be an object"]
    problems = []
    props = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in args:
            problems.append(f"missing required `{name}`")
    for name, value in args.items():
        if name not in props:
            problems.append(f"unexpected argument `{name}`")
            continue
        problems += check_value(props[name], value, name)
    return problems


def check_value(sub: dict[str, Any], value: Any, path: str) -> list[str]:
    stype = sub.get("type")
    ok = {
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }.get(stype, False)
    if not ok:
        return [f"`{path}` must be {stype}"]
    problems = []
    if "enum" in sub and value not in sub["enum"]:
        problems.append(f"`{path}` must be one of {sub['enum']}")
    if stype == "array" and isinstance(sub.get("items"), dict):
        for i, item in enumerate(value):
            problems += check_value(sub["items"], item, f"{path}[{i}]")
    return problems


# ----- templates: ``${param}`` substitution, nothing else -------------------------------------------------------------


def validate_templates(value: Any, params: set[str], path: str = "arguments") -> list[str]:
    problems = []
    if isinstance(value, str):
        for ref in TEMPLATE_RE.findall(value):
            if ref not in params:
                problems.append(f"{path}: template `${{{ref}}}` is not a declared parameter")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            problems += validate_templates(item, params, f"{path}[{i}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            problems += validate_templates(item, params, f"{path}.{key}")
    return problems


def render_templates(value: Any, args: dict[str, Any]) -> Any:
    """A whole-value ``${param}`` keeps the parameter's type; inside a longer string it interpolates as text."""
    if isinstance(value, str):
        whole = TEMPLATE_RE.fullmatch(value)
        if whole:
            return args[whole.group(1)]
        return TEMPLATE_RE.sub(lambda m: str(args[m.group(1)]), value)
    if isinstance(value, list):
        return [render_templates(v, args) for v in value]
    if isinstance(value, dict):
        return {k: render_templates(v, args) for k, v in value.items()}
    return value


# ----- the store -------------------------------------------------------------------------------------------------------


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str), "utf-8")
        tmp.replace(path)
    except OSError:
        pass  # in-memory state still answers; a failed write must not fail the call


class Registry:
    """Per-tenant store of created tools and roles. Two JSON files; versions kept per name."""

    def __init__(self, root: Path) -> None:
        self.dir = Path(root) / "registry"
        self.tools: dict[str, dict[str, Any]] = {}
        self.roles: dict[str, dict[str, Any]] = {}
        self._load("tools.json", self.tools)
        self._load("roles.json", self.roles)

    def _load(self, filename: str, into: dict[str, Any]) -> None:
        try:
            data = json.loads((self.dir / filename).read_text("utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            into.update(data)

    def _save_tools(self) -> None:
        _atomic_write(self.dir / "tools.json", self.tools)

    def _save_roles(self) -> None:
        _atomic_write(self.dir / "roles.json", self.roles)

    # ----- multi-process safety ---------------------------------------------------
    #
    # Several server processes (and job runners) can share one tenant root. Every write is already atomic (tmp file +
    # rename), but a read-modify-write across two processes could still lose the other process's update. Mutations
    # therefore run under an exclusive flock on ``.lock`` and RE-READ the store after acquiring it; plain reads take a
    # lock-free fresh snapshot, which is consistent because writes are atomic.

    def _refresh(self) -> None:
        tools: dict[str, Any] = {}
        roles: dict[str, Any] = {}
        self._load("tools.json", tools)
        self._load("roles.json", roles)
        self.tools, self.roles = tools, roles

    @contextmanager
    def _exclusive(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / ".lock", "a+b") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                self._refresh()
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _mutates(fn):
        @functools.wraps(fn)
        def wrapper(self: "Registry", *args, **kwargs):
            with self._exclusive():
                return fn(self, *args, **kwargs)
        return wrapper

    # ----- lookups ---------------------------------------------------------------

    def active_tool(self, name: str, session: str | None = None) -> dict[str, Any] | None:
        """The version a session may call: active, and either durable or created by THIS session."""
        self._refresh()
        entry = self.tools.get(name)
        if not entry:
            return None
        # 1) The caller's OWN latest session version: another session creating the same name must never shadow it.
        if session is not None:
            own = [v for v in entry["versions"] if v["status"] == "active" and v.get("lifetime") == "session" and v.get("session") == session]
            if own:
                return own[-1]
        # 2) The pinned version, if this caller may use it.
        version = entry.get("active_version")
        record = next((v for v in entry["versions"] if v["version"] == version and v["status"] == "active"), None)
        if record is not None and (record.get("lifetime") == "durable" or (session is not None and record.get("session") == session)):
            return record
        # 3) The newest durable version, so one session's fresh draft never takes a promoted tool away from everyone else.
        durable = [v for v in entry["versions"] if v["status"] == "active" and v.get("lifetime") == "durable"]
        return durable[-1] if durable else None

    def tool_names(self) -> set[str]:
        return set(self.tools)

    def active_role(self, name: str, version: int | None = None) -> dict[str, Any] | None:
        """The version a job may start with. Both lifetimes resolve: naming the role in POST /api/jobs is the owner's act."""
        self._refresh()
        entry = self.roles.get(name)
        if not entry:
            return None
        version = version or entry.get("active_version")
        found = next((v for v in entry["versions"] if v["version"] == version and v["status"] == "active"), None)
        return {**found, "name": name} if found else None

    def catalogue_roles(self) -> dict[str, dict[str, Any]]:
        """Durable roles only: the standing catalogue future sessions pick from."""
        return {name: self.active_role(name) for name in sorted(self.roles)
                if self.active_role(name) and self.active_role(name).get("lifetime") == "durable"}

    # ----- tools -----------------------------------------------------------------

    @_mutates
    def create_tool(self, spec: dict[str, Any], *, existing_names: set[str], host_tool_names: set[str], created_by: str, session: str, ceiling: set[str] | None = None) -> tuple[dict[str, Any] | None, list[str]]:
        """Validate a tool spec and store it as a new version, ACTIVE in the creating session only. Returns (record, problems)."""
        problems: list[str] = []
        if not isinstance(spec, dict):
            return None, ["the tool spec must be an object"]
        for field in EXECUTABLE_FIELDS:
            if field in spec:
                problems.append(
                    f"`{field}` makes a tool executable code or network access, which this host cannot validate. "
                    "A registry tool is a pipeline of existing tools only; a genuinely new capability needs a reviewed plugin."
                )
        unknown = set(spec) - {"name", "description", "parameters", "steps", "tests"}
        if unknown:
            problems.append(f"unsupported spec fields: {', '.join(sorted(unknown))}")
        name = str(spec.get("name") or "")
        if not NAME_RE.match(name):
            problems.append(f"`name` must match {NAME_RE.pattern}")
        elif name in catalogue.GATEWAY_NAMES or name in MANAGEMENT_TOOLS or name in NATIVE_TOOLS or name in existing_names:
            problems.append(f"`{name}` is already a tool name; a registry tool cannot shadow or replace an existing one")
        description = str(spec.get("description") or "").strip()
        if not description:
            problems.append("`description` is required (one or two sentences: what it does and when to use it)")
        elif len(description) > 500:
            problems.append("`description` is over 500 characters")
        parameters = spec.get("parameters", {"type": "object", "properties": {}, "additionalProperties": False})
        problems += validate_schema(parameters)
        params = set(parameters.get("properties", {})) if isinstance(parameters, dict) else set()

        steps = spec.get("steps")
        if not isinstance(steps, list) or not steps:
            problems.append("`steps` must be a non-empty list of {tool, arguments}")
            steps = []
        elif len(steps) > MAX_STEPS:
            problems.append(f"`steps` has {len(steps)} entries; the limit is {MAX_STEPS}")
        requirements: list[str] = []
        for i, step in enumerate(steps[:MAX_STEPS]):
            if not isinstance(step, dict):
                problems.append(f"steps[{i}]: must be an object")
                continue
            tool = step.get("tool")
            if tool in self.tool_names() or tool in MANAGEMENT_TOOLS or tool in catalogue.GATEWAY_NAMES:
                problems.append(f"steps[{i}]: `{tool}` cannot be a pipeline step (no composites, gateway or registry tools: recursion is impossible by construction)")
            elif tool not in host_tool_names:
                problems.append(f"steps[{i}]: `{tool}` is not an existing host tool. Composite steps orchestrate host tools; "
                                "native runtime tools (files, shell) are not callable from a pipeline.")
            elif ceiling is not None and tool not in ceiling:
                problems.append(f"steps[{i}]: `{tool}` is outside this session's own tool scope: a session cannot author a tool it could not itself run")
            else:
                requirements.append(tool)
            arguments = step.get("arguments", {})
            if len(json.dumps(arguments)) > MAX_STEP_ARGS_CHARS:
                problems.append(f"steps[{i}]: arguments are over {MAX_STEP_ARGS_CHARS} characters")
            problems += validate_templates(arguments, params, f"steps[{i}].arguments")

        tests = spec.get("tests", [])
        if not isinstance(tests, list) or len(tests) > MAX_TESTS:
            problems.append(f"`tests` must be a list of at most {MAX_TESTS} cases")
            tests = []
        for i, case in enumerate(tests):
            if not isinstance(case, dict) or not isinstance(case.get("arguments"), dict):
                problems.append(f"tests[{i}]: must be an object with an `arguments` object")
                continue
            problems += [f"tests[{i}]: {p}" for p in validate_arguments(parameters, case["arguments"])]

        if problems:
            return None, problems
        if len(self.tools) >= MAX_TOOLS and name not in self.tools:
            return None, [f"the registry already holds {MAX_TOOLS} tools; revoke one first"]

        entry = self.tools.setdefault(name, {"name": name, "active_version": None, "versions": []})
        version = (entry["versions"][-1]["version"] + 1) if entry["versions"] else 1
        record = {
            "version": version, "status": "active", "lifetime": "session", "session": session,
            "created": time.time(), "created_by": created_by,
            "description": description, "parameters": parameters, "steps": steps,
            "requirements": sorted(set(requirements)),
            "code_runners": sorted(set(requirements) & set(catalogue.CODE_RUNNERS)),
            "tests": tests, "test_report": None, "activation": "session",
            "note": "Active in the creating session only; the owner promotes it to the durable catalogue for future sessions. "
                    "Each step still passes the calling session's tool scope and approvals when the tool runs. This record grants nothing by existing.",
        }
        entry["versions"].append(record)
        del entry["versions"][:-MAX_VERSIONS_KEPT]
        entry["active_version"] = version
        self._save_tools()
        return {**record, "name": name}, []

    @_mutates
    def record_test_report(self, name: str, version: int, report: dict[str, Any], activate: bool) -> dict[str, Any] | None:
        """Attach a test report. Passing tests keep the tool active (activation upgrades to ``tests``); failing tests
        take it back to DRAFT even in its own session: declared tests that failed are a stronger signal than the default."""
        entry = self.tools.get(name)
        record = next((v for v in (entry or {}).get("versions", []) if v["version"] == version), None)
        if record is None:
            return None
        record["test_report"] = report
        if activate:
            record["status"] = "active"
            record["activation"] = "tests"
            entry["active_version"] = version
        else:
            record["status"] = "draft"
            if entry.get("active_version") == version:
                entry["active_version"] = None
        self._save_tools()
        return {**record, "name": name}

    @_mutates
    def promote_tool(self, name: str, version: int | None = None) -> dict[str, Any]:
        """The owner's explicit choice: offer this tool to future sessions. Only an active tool can be promoted;
        a draft (failed tests) must be re-activated first, so promotion never launders a known failure."""
        entry = self.tools.get(name)
        if entry is None:
            raise RegistryError(f"No registered tool `{name}`.")
        target = version if version is not None else (entry.get("active_version") or (entry["versions"][-1]["version"] if entry["versions"] else None))
        record = next((v for v in entry["versions"] if v["version"] == target), None)
        if record is None:
            raise RegistryError(f"No version {target} of `{name}`.")
        if record["status"] != "active":
            raise RegistryError(f"`{name}` v{record['version']} is {record['status']}; only an active tool can join the durable catalogue.")
        record["lifetime"] = "durable"
        record["promoted"] = True
        self._save_tools()
        return {**record, "name": name}

    @_mutates
    def promote_role(self, name: str) -> dict[str, Any]:
        entry = self.roles.get(name)
        if entry is None:
            raise RegistryError(f"No registered role `{name}`.")
        record = next((v for v in entry["versions"] if v["version"] == entry.get("active_version") and v["status"] == "active"), None)
        if record is None:
            raise RegistryError(f"Role `{name}` has no active version to promote.")
        record["lifetime"] = "durable"
        record["promoted"] = True
        self._save_roles()
        return {**record, "name": name}

    @_mutates
    def set_tool_status(self, name: str, status: str, activation: str | None = None, version: int | None = None) -> dict[str, Any]:
        entry = self.tools.get(name)
        if entry is None:
            raise RegistryError(f"No registered tool `{name}`.")
        target = version if version is not None else (entry.get("active_version") or (entry["versions"][-1]["version"] if entry["versions"] else None))
        record = next((v for v in entry["versions"] if v["version"] == target), None)
        if record is None:
            raise RegistryError(f"No version {target} of `{name}`.")
        if status == "active":
            record["status"] = "active"
            record["activation"] = activation or "owner"
            entry["active_version"] = record["version"]
        elif status == "revoked":
            record["status"] = "revoked"
            if entry.get("active_version") == record["version"]:
                entry["active_version"] = None
        else:
            raise RegistryError(f"Unknown status `{status}`.")
        self._save_tools()
        return {**record, "name": name}

    # ----- roles -----------------------------------------------------------------

    @_mutates
    def create_role(self, spec: dict[str, Any], *, universe: set[str], guide_names: set[str], builtin_roles: set[str], created_by: str, session: str = "", ceiling: set[str] | None = None) -> tuple[dict[str, Any] | None, list[str]]:
        created_by_session = session
        problems: list[str] = []
        if not isinstance(spec, dict):
            return None, ["the role spec must be an object"]
        unknown = set(spec) - {"name", "description", "tools", "guides", "contract", "verdict"}
        if unknown:
            problems.append(f"unsupported spec fields: {', '.join(sorted(unknown))}")
        name = str(spec.get("name") or "")
        if not NAME_RE.match(name):
            problems.append(f"`name` must match {NAME_RE.pattern}")
        elif name in builtin_roles:
            problems.append(f"`{name}` is a built-in role; a registry role cannot shadow it")
        description = str(spec.get("description") or "").strip()
        if not description:
            problems.append("`description` is required: what this role is for, so whoever picks a role can choose")
        tools = spec.get("tools")
        if not isinstance(tools, list) or not tools or any(not isinstance(t, str) for t in tools):
            problems.append("`tools` must be a non-empty list of existing tool names")
            tools = []
        else:
            for tool in sorted(set(tools)):
                if tool in self.tool_names() and self.active_tool(tool, created_by_session or None) is None:
                    problems.append(f"`{tool}` is a registered tool that is not active for this session (draft, revoked or another session's); activate it first")
                elif tool not in universe:
                    problems.append(f"`{tool}` is not a known tool name")
                elif ceiling is not None and tool not in ceiling:
                    problems.append(f"`{tool}` is outside this session's own tool scope: a session cannot author a role more powerful than itself")
        guides = spec.get("guides", [])
        if not isinstance(guides, list) or any(g not in guide_names for g in guides if isinstance(g, str)) or any(not isinstance(g, str) for g in guides):
            problems.append(f"`guides` must be names of existing guides ({', '.join(sorted(guide_names))}); new prose is not a validated guide")
        contract = spec.get("contract", [])
        if not isinstance(contract, list) or any(not isinstance(k, str) for k in contract):
            problems.append("`contract` must be a list of required JSON keys (or empty)")
        verdict = bool(spec.get("verdict", False))
        if problems:
            return None, problems
        if len(self.roles) >= MAX_ROLES and name not in self.roles:
            return None, [f"the registry already holds {MAX_ROLES} roles; revoke one first"]

        entry = self.roles.setdefault(name, {"name": name, "active_version": None, "versions": []})
        version = (entry["versions"][-1]["version"] + 1) if entry["versions"] else 1
        scope = sorted(set(tools))
        record = {
            "version": version, "status": "active", "lifetime": "session", "session": created_by_session,
            "created": time.time(), "created_by": created_by,
            "description": description, "tools": scope, "guides": list(guides),
            "contract": (contract or (["verdict", "reasons"] if verdict else [])), "verdict": verdict,
            "code_runners": sorted(set(scope) & set(catalogue.CODE_RUNNERS)),
            "scope_note": (
                "This scope includes a code runner (Shell, process or run_python), so it bounds nothing: the role's code can reach "
                "files, credentials and the network directly."
                if set(scope) & set(catalogue.CODE_RUNNERS) else
                "No code runner in scope: tool names outside this list are refused by name. Still not OS-level isolation."
            ),
            "note": "Usable in an owner-started job at once; the owner promotes it to join the standing catalogue (GET /api/jobs/roles). "
                    "A role is data: using it still requires an owner-authenticated POST /api/jobs. Creating it granted the creator nothing.",
        }
        entry["versions"].append(record)
        del entry["versions"][:-MAX_VERSIONS_KEPT]
        entry["active_version"] = version
        self._save_roles()
        return {**record, "name": name}, []

    @_mutates
    def set_role_status(self, name: str, status: str) -> dict[str, Any]:
        entry = self.roles.get(name)
        if entry is None:
            raise RegistryError(f"No registered role `{name}`.")
        if status != "revoked":
            raise RegistryError("Roles can only be revoked (a new version is created by create_role, not by editing).")
        if entry.get("active_version") is not None:
            for record in entry["versions"]:
                if record["version"] == entry["active_version"]:
                    record["status"] = "revoked"
            entry["active_version"] = None
        self._save_roles()
        return entry

    # ----- listings ----------------------------------------------------------------

    def listing(self) -> dict[str, Any]:
        self._refresh()
        return {
            "tools": {
                name: {
                    "active_version": entry.get("active_version"),
                    "versions": [
                        {"version": v["version"], "status": v["status"], "lifetime": v.get("lifetime", "session"),
                         "description": v["description"],
                         "requirements": v["requirements"], "code_runners": v["code_runners"],
                         "tested": v["test_report"] is not None, "tests_ok": bool((v.get("test_report") or {}).get("ok")),
                         "activation": v.get("activation")}
                        for v in entry["versions"]
                    ],
                }
                for name, entry in sorted(self.tools.items())
            },
            "roles": {
                name: {
                    "active_version": entry.get("active_version"),
                    "versions": [
                        {"version": v["version"], "status": v["status"], "lifetime": v.get("lifetime", "session"),
                         "description": v["description"],
                         "tools": v["tools"], "guides": v["guides"], "verdict": v["verdict"], "code_runners": v["code_runners"]}
                        for v in entry["versions"]
                    ],
                }
                for name, entry in sorted(self.roles.items())
            },
            "note": "Session-lifetime tools work in their creating session only; durable ones are offered to every session. "
                    "Only ACTIVE role versions can start a job. Drafts and revoked entries are inert data.",
        }


# ----- name universes ---------------------------------------------------------------------------------------------------


def host_tool_names(tenant: "Tenant", runtime: "SessionRuntime | None", registry: Registry) -> set[str]:
    """Names a pipeline step or role scope may name: every host toolset's tools, minus composites/gateway/registry tools."""
    names: set[str] = set()
    for toolset in tenant.active_toolsets():
        if isinstance(toolset, (CompositeToolset, RegistryToolset, catalogue.GatewayToolset)):
            continue
        for definition in toolset.definitions(runtime):
            names.add(definition["name"])
    return names - set(MANAGEMENT_TOOLS) - set(catalogue.GATEWAY_NAMES) - registry.tool_names()


def role_universe(tenant: "Tenant", runtime: "SessionRuntime | None", registry: Registry) -> set[str]:
    """Everything a role scope may name: native tools, host tools, gateway names, and ACTIVE composites.

    ``recommend_job`` is the ONE management tool a role may name, deliberately: proposing a job executes nothing
    (the owner's accept is the only start path) and grants the proposer no tools, so letting a job's role include
    it enables governed sub-job proposals - with lineage, depth/fanout bounds and an escalation flag, see
    ``jobs.request_job``. The other management tools stay unnameable: creating tools or roles is chat-session work."""
    session = getattr(getattr(runtime, "record", None), "id", None)
    active = {name for name in registry.tool_names() if registry.active_tool(name, session)}
    return NATIVE_TOOLS | host_tool_names(tenant, runtime, registry) | set(catalogue.GATEWAY_NAMES) | active | {RECOMMEND_JOB}


def role_creator_ceiling(tenant: "Tenant", runtime: "SessionRuntime | None", registry: Registry) -> set[str] | None:
    """The most powerful role THIS session may author: nothing outside its own scope.

    ``None`` means unrestricted (an owner session with an empty scope). A scoped session (a job) can only compose
    roles from tools it could itself call, so create_role can never launder a capability the creator lacks. The
    gateway names are always in: finding and using are gated per call anyway.
    """
    scope = set(getattr(getattr(runtime, "record", None), "tool_scope", None) or [])
    tenant_scope = set(tenant.settings.get().tool_scope or [])
    if not scope and not tenant_scope:
        return None
    allowed = (scope or role_universe(tenant, runtime, registry)) & (tenant_scope or role_universe(tenant, runtime, registry))
    return set(allowed) | set(catalogue.GATEWAY_NAMES)


def adoptable_role(tenant: "Tenant", runtime: "SessionRuntime", registry: Registry, name: str, version: int = 0) -> tuple[dict[str, Any] | None, str]:
    """Resolve a role this session may ADOPT, and check the adoption grants nothing.

    Adoptable: built-in presets, durable registry roles, and session-lifetime roles THIS session created (never
    another session's). The role's actual tools - including every step of any composite it names - must each be
    inside the session's CURRENT effective ceiling (tenant scope AND session scope, via ``catalogue.allowed``), so
    adopting can only ever narrow what the session can do. Returns (resolved, problem)."""
    from .jobs import ROLES  # local import: jobs imports this module

    if name in ROLES:
        scope = list(ROLES[name])
        resolved = {"name": name, "version": None, "tools": scope, "description": "built-in preset", "guides": []}
    else:
        record = registry.active_role(name, version=version or None)
        if record is None:
            return None, f"unknown or inactive role `{name}`"
        if record.get("lifetime") != "durable" and record.get("session") != getattr(runtime.record, "id", None):
            return None, (f"role `{name}` v{record['version']} is private to the session that created it; this session can only adopt "
                          "durable roles or its own. The owner can start a job with a private role (source_session) instead.")
        resolved = record
    outside = [t for t in resolved["tools"] if t not in catalogue.GATEWAY_NAMES and not catalogue.allowed(tenant, t, runtime)]
    if outside:
        return None, (f"adopting `{name}` would give this session tool(s) outside its current ceiling: {', '.join(sorted(outside))}. "
                      "Adoption only narrows. To use that role, the owner can start a job with it (POST /api/jobs).")
    # Composite expansion: naming a composite is not permission for its steps. Pre-check them against the same
    # ceiling so an adopted role fails HERE, not mid-run (execution re-checks them per step regardless).
    session = getattr(runtime.record, "id", None)
    for tool in resolved["tools"]:
        composite = registry.active_tool(tool, session) if tool in registry.tool_names() else None
        if composite:
            bad_steps = [st["tool"] for st in composite["steps"] if not catalogue.allowed(tenant, st["tool"], runtime)]
            if bad_steps:
                return None, (f"role `{name}` names composite `{tool}`, whose step tool(s) {', '.join(sorted(set(bad_steps)))} are outside "
                              "this session's ceiling; adopting it would not grant them (steps are re-checked at execution), so the role is refused here.")
    return resolved, ""


def existing_tool_names(tenant: "Tenant", runtime: "SessionRuntime | None", registry: Registry) -> set[str]:
    """Names a new tool must not collide with: native, host, gateway, registry management and registered composites."""
    return NATIVE_TOOLS | host_tool_names(tenant, runtime, registry) | set(catalogue.GATEWAY_NAMES) | set(MANAGEMENT_TOOLS) | registry.tool_names()


# ----- executing a composite ---------------------------------------------------------------------------------------------


def _find_owner(tenant: "Tenant", runtime: "SessionRuntime", name: str, registry: Registry):
    for toolset in tenant.active_toolsets():
        if isinstance(toolset, (CompositeToolset, RegistryToolset)):
            continue
        if toolset.handles(name):
            return toolset
    return None


async def run_composite(tenant: "Tenant", runtime: "SessionRuntime", call_id: str, record: dict[str, Any], arguments: dict[str, Any], registry: Registry) -> dict[str, Any]:
    """Run a pipeline. Every step re-passes scope and the owning toolset's own approvals; the composite adds nothing."""
    problems = validate_arguments(record["parameters"], arguments)
    if problems:
        return tool_error("Invalid arguments: " + "; ".join(problems), "Invalid arguments")
    outputs: list[dict[str, Any]] = []
    for i, step in enumerate(record["steps"]):
        # TOCTOU: the version this call resolved must still be the active one before EACH step. A revocation or a
        # newer version landing mid-pipeline stops the run instead of silently executing a stale or replaced tool.
        current = registry.active_tool(record.get("name", ""), getattr(runtime.record, "id", None))
        if current is None or current["version"] != record["version"]:
            return _composite_result(record, outputs, "this tool was revoked or superseded while the pipeline was running; the pipeline stopped before step "
                                     f"{i}. Re-run to use the current version.", step=i)
        tool = step["tool"]
        if not catalogue.allowed(tenant, tool, runtime):
            return _composite_result(record, outputs, f"step {i} (`{tool}`) is outside this session's tool scope, so the pipeline stopped here. "
                                   "A created tool never widens the session it runs in.", step=i)
        owner = _find_owner(tenant, runtime, tool, registry)
        if owner is None:
            return _composite_result(record, outputs, f"step {i}: `{tool}` has no owning toolset in this session (plugin off or tool gone).", step=i)
        try:
            rendered = render_templates(step.get("arguments", {}), arguments)
        except KeyError as error:
            return _composite_result(record, outputs, f"step {i}: missing argument {error}.", step=i)
        result = await owner.call(runtime, f"{call_id}:{i}", tool, rendered)
        output = str(result.get("output") or result.get("message") or "")
        outputs.append({"step": i, "tool": tool, "is_error": bool(result.get("is_error")), "output": output[:MAX_OUTPUT_CHARS]})
        if result.get("is_error"):
            return _composite_result(record, outputs, f"step {i} (`{tool}`) failed, so the pipeline stopped. Step output:\n{output[:MAX_OUTPUT_CHARS]}", step=i, raw=result)
    text = "\n\n".join(f"[step {o['step']} · {o['tool']}]\n{o['output']}" for o in outputs)
    return {"is_error": False, "output": text, "message": "", "display": [{"type": "brief", "text": f"{record.get('name', 'composite')}: {len(outputs)} step(s)"}]}


def _composite_result(record: dict[str, Any], outputs: list[dict[str, Any]], message: str, step: int, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"is_error": True, "output": json.dumps({"failed_at_step": step, "completed": outputs}, indent=2), "message": message,
            "display": [{"type": "brief", "text": f"Failed at step {step}"}]}


# ----- the host toolsets -------------------------------------------------------------------------------------------------


class CompositeToolset:
    """Exposes ACTIVE registry tools as ordinary tool definitions and runs their pipelines."""

    plugin = "registry"

    def __init__(self, registry: Registry) -> None:
        self.registry = registry

    def definitions(self, runtime: "SessionRuntime") -> list[dict[str, Any]]:
        registry = self.registry
        out = []
        session = getattr(runtime.record, "id", None)
        for name in sorted(registry.tool_names()):
            record = registry.active_tool(name, session)
            if record is None:
                continue
            requirements = ", ".join(record["requirements"])
            out.append({
                "name": name,
                "description": record["description"] + f" (registry tool v{record['version']}: runs {requirements} in a fixed pipeline; each step still needs this session's scope and approvals.)",
                "parameters": record["parameters"],
            })
        return out

    def handles(self, name: str) -> bool:
        # ``handles`` sees no runtime, so any active version matches here; ``call`` enforces the session scope.
        entry = self.registry.tools.get(name)
        return bool(entry) and any(v["status"] == "active" for v in entry["versions"])

    async def call(self, runtime: "SessionRuntime", call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        registry = self.registry
        record = registry.active_tool(name, getattr(runtime.record, "id", None))
        if record is None:
            return tool_error(f"`{name}` is not available to this session (unknown, draft, revoked, or session-scoped to its creator).", "Unavailable")
        return await run_composite(runtime.tenant, runtime, call_id, {**record, "name": name}, arguments, registry)


class RegistryToolset:
    """``create_tool`` / ``create_role`` / ``registry_list``: proposal and inspection, never execution of new code."""

    plugin = "registry"

    def definitions(self, runtime: "SessionRuntime") -> list[dict[str, Any]]:
        return [
            {
                "name": CREATE_TOOL,
                "description": (
                    "Propose a NEW tool as a fixed pipeline of existing host tools, with an argument schema and optional tests. "
                    "Static validation runs now; if it passes, the tool is ACTIVE IN THIS SESSION at once - tell the owner what you created "
                    "and that it joins the durable catalogue for future sessions only if they choose to promote it. "
                    "With run_tests=true its declared tests execute for real first and a failure leaves it a draft. "
                    "You cannot create executable code, network access, or a tool that widens this session's scope."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "snake_case, unique across all tools"},
                        "description": {"type": "string"},
                        "parameters": {"type": "object", "description": "Closed JSON Schema object for the tool's arguments"},
                        "steps": {"type": "array", "description": "List of {tool, arguments}; arguments may use ${param} templates"},
                        "tests": {"type": "array", "description": "List of {arguments, expect_error?}; executed only when run_tests is true"},
                        "run_tests": {"type": "boolean", "description": "Run the declared tests now, for real, in this session"},
                    },
                    "required": ["name", "description", "parameters", "steps"],
                    "additionalProperties": False,
                },
            },
            {
                "name": CREATE_ROLE,
                "description": (
                    "Propose a NEW job role: a versioned, named tool scope composed from existing tools and existing guides. "
                    "The owner can start a job with it at once - tell the owner you created it; it joins the standing role catalogue "
                    "(GET /api/jobs/roles) only if the owner promotes it. Creating it grants this session nothing. "
                    "A scope containing Shell, process or run_python is not a boundary, and the registry says so on the record."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string", "description": "What this role is for, so whoever picks a role can choose"},
                        "tools": {"type": "array", "description": "Existing tool names (native, host, or active registry tools)"},
                        "guides": {"type": "array", "description": "Existing guide names only"},
                        "contract": {"type": "array", "description": "Default required JSON keys for the final reply"},
                        "verdict": {"type": "boolean", "description": "This role returns a pass/block verdict"},
                    },
                    "required": ["name", "description", "tools"],
                    "additionalProperties": False,
                },
            },
            {
                "name": ADOPT_ROLE,
                "description": (
                    "Take on an existing role for the rest of this session: its guides load and your tools NARROW to its scope. "
                    "One-way - the session cannot widen back, and only roles whose tools are all inside your current scope can be adopted "
                    "(durable roles, built-in presets, or roles this session itself created). The role version is pinned. "
                    "Output contracts are advisory in a chat session; they are enforced only for jobs. "
                    "To RUN work under a role outside your scope, ask the owner to start a job instead."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "A built-in role, a durable registry role, or one this session created"},
                        "version": {"type": "integer", "description": "Pin a registry role version (0 = the active version)"},
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
            {
                "name": RECOMMEND_JOB,
                "description": (
                    "Recommend that the OWNER start an unattended job (a goal under a role) - for work that needs tools outside this "
                    "session's scope, or that should keep running without you. This STORES a proposal and shows it to the owner; it starts "
                    "nothing. The owner's accept is the only way the job runs, and it runs with exactly the fields stored at proposal time "
                    "(the role version is pinned now). Never use this for work your own tools can do, and never present it as work underway."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "goal": {"type": "string", "description": "What the job should do"},
                        "role": {"type": "string", "description": "A built-in role, a durable registry role, or one this session created (default: developer)"},
                        "version": {"type": "integer", "description": "Pin a registry role version (0 = the active version)"},
                        "reason": {"type": "string", "description": "Why a job, not this session, should do it - the owner reads this"},
                    },
                    "required": ["goal"],
                    "additionalProperties": False,
                },
            },
            {
                "name": LIST_TOOL,
                "description": "List registered tools and roles with their status (draft/active/revoked), versions, requirements and test results.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        ]

    def handles(self, name: str) -> bool:
        # MANAGEMENT_TOOLS is also the name-reservation list (it contains genplugins' propose_tool); this toolset
        # only ANSWERS its own five.
        return name in (CREATE_TOOL, CREATE_ROLE, LIST_TOOL, ADOPT_ROLE, RECOMMEND_JOB)

    async def call(self, runtime: "SessionRuntime", call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tenant = runtime.tenant
        registry = tenant.registry
        if name == LIST_TOOL:
            return _ok(registry.listing(), "Registry listing")
        created_by = getattr(runtime.record, "job_role", None) or f"session:{getattr(runtime.record, 'id', '?')}"
        session = getattr(runtime.record, "id", "") or ""
        emit = getattr(runtime, "emit", None)
        if name == CREATE_TOOL:
            spec = {k: v for k, v in arguments.items() if k != "run_tests"}
            record, problems = registry.create_tool(
                spec,
                existing_names=existing_tool_names(tenant, runtime, registry),
                host_tool_names=host_tool_names(tenant, runtime, registry),
                created_by=created_by,
                session=session,
                ceiling=role_creator_ceiling(tenant, runtime, registry),
            )
            if problems:
                return tool_error("Tool refused by static validation:\n- " + "\n- ".join(problems), "Invalid tool spec")
            if arguments.get("run_tests") and record["tests"]:
                report = await run_tests(tenant, runtime, call_id, {**record, "name": record.get("name") or arguments["name"]}, registry)
                record = registry.record_test_report(arguments["name"], record["version"], report, activate=report["ok"])
            if callable(emit):
                emit("RegistryChanged", {"kind": "tool", "name": arguments["name"], "version": record["version"], "status": record["status"],
                                         "lifetime": record.get("lifetime", "session"), "description": record["description"]})
            payload = {**record, "name": arguments["name"]}
            if record["status"] == "active":
                payload["available_from"] = "your next turn (the runtime registers new host tools when its tool list refreshes); this session only, unless the owner promotes it"
                payload["tell_the_owner"] = f"Say what you created (`{arguments['name']}`: {record['description']}) and that joining the durable catalogue is their choice."
            return _ok(payload, f"Tool `{arguments['name']}` v{record['version']} ({record['status']})")
        if name == ADOPT_ROLE:
            resolved, problem = adoptable_role(tenant, runtime, registry, str(arguments.get("name") or ""), int(arguments.get("version") or 0))
            if problem:
                return tool_error(problem, "Cannot adopt role")
            record = runtime.record
            record.role = resolved["name"]
            record.role_version = resolved["version"] or 0
            record.tool_scope = list(resolved["tools"])  # one-way narrowing; the fingerprint change re-inits the runtime next turn
            save = getattr(runtime, "save_record", None)
            if callable(save):
                save()
            from .guides import guides_for

            loaded = guides_for(resolved["name"], tenant)
            if callable(emit):
                emit("RoleAdopted", {"role": resolved["name"], "version": resolved["version"], "tools": len(resolved["tools"]),
                                     "guides": loaded, "pinned": True, "reversible": False})
            payload = {"role": resolved["name"], "version": resolved["version"], "tools_now_in_scope": list(resolved["tools"]),
                       "guides_loaded": loaded, "pinned": True, "one_way": True,
                       "honest_limits": ("host and gateway tools narrow at once; native commands, writes, web and outside-workspace reads are "
                                         "rejected from this moment via the approval path; read-only native tools inside the workspace may not "
                                         "consult the host per call, so for them the narrowed set is guaranteed from the next turn (the runtime "
                                         "re-initializes); guides follow the role now; any output contract is advisory in a chat session "
                                         "(enforced for jobs only); if this role is revoked later, your narrowed tools stay narrowed and its "
                                         "guides stop loading.")}
            return _ok(payload, f"Adopted role `{resolved['name']}`" + (f" v{resolved['version']}" if resolved["version"] else ""))
        if name == CREATE_ROLE:
            from .guides import GUIDES  # local import: guides imports nothing from here
            from .jobs import ROLES

            record, problems = registry.create_role(
                arguments,
                universe=role_universe(tenant, runtime, registry),
                guide_names=set(GUIDES),
                builtin_roles=set(ROLES),
                created_by=created_by,
                session=session,
                ceiling=role_creator_ceiling(tenant, runtime, registry),
            )
            if problems:
                return tool_error("Role refused by static validation:\n- " + "\n- ".join(problems), "Invalid role spec")
            if callable(emit):
                emit("RegistryChanged", {"kind": "role", "name": arguments["name"], "version": record["version"], "status": record["status"],
                                         "lifetime": record.get("lifetime", "session"), "description": record["description"]})
            payload = {**record, "name": arguments["name"],
                       "tell_the_owner": f"Say what you created (role `{arguments['name']}`: {record['description']}); the owner can start jobs with it now, and promoting it into the standing catalogue is their choice."}
            return _ok(payload, f"Role `{arguments['name']}` v{record['version']} (active)")
        if name == RECOMMEND_JOB:
            from . import jobs as jobs_mod  # local import: jobs imports registry only inside functions

            payload, problem = jobs_mod.request_job(tenant, runtime, arguments)
            if problem:
                return tool_error(problem, "Cannot recommend job")
            if callable(emit):
                emit("JobRecommended", {"id": payload["id"], "goal": payload["goal"], "role": payload["role"],
                                        "version": payload["role_version"] or None, "reason": payload["reason"],
                                        "workspace": payload["workspace"]})
            return _ok(payload, f"Recommended a {payload['role']} job to the owner - nothing started")
        return tool_error(f"Unknown registry tool `{name}`.", "Unknown tool")


async def run_tests(tenant: "Tenant", runtime: "SessionRuntime", call_id: str, record: dict[str, Any], registry: Registry) -> dict[str, Any]:
    """Run a draft's declared tests FOR REAL in the creating session: real tools, real side effects, real scope checks."""
    results = []
    for i, case in enumerate(record["tests"]):
        expect_error = bool(case.get("expect_error", False))
        result = await run_composite(tenant, runtime, f"{call_id}:test{i}", record, case["arguments"], registry)
        passed = bool(result.get("is_error")) == expect_error
        results.append({"case": i, "ok": passed, "expect_error": expect_error, "is_error": bool(result.get("is_error")),
                        "detail": (str(result.get("message") or "")[:300] if not passed else "")})
    return {"ok": all(r["ok"] for r in results) and bool(results), "results": results,
            "note": "Tests executed the pipeline's real tools in the creating session, under that session's scope and approvals."}


def _ok(payload: dict[str, Any], brief: str) -> dict[str, Any]:
    return {"is_error": False, "output": json.dumps(payload, indent=2, default=str), "message": "", "display": [{"type": "brief", "text": brief}]}


# ----- wiring ------------------------------------------------------------------------------------------------------------


def install(server) -> None:
    def extend(tenant) -> None:
        tenant.registry = Registry(Path(tenant.root))
        tenant.toolsets.append(RegistryToolset())
        tenant.toolsets.append(CompositeToolset(tenant.registry))

    server.extensions.append(extend)


def router(app_state):
    from fastapi import APIRouter, Depends, HTTPException

    from .tenancy import Tenant

    api = APIRouter(prefix="/api")

    @api.get("/registry")
    async def listing(tenant: Tenant = Depends(app_state.auth.tenant)) -> dict:
        return tenant.registry.listing()

    @api.post("/registry/tools/{name}/activate")
    async def activate(name: str, tenant: Tenant = Depends(app_state.auth.tenant)) -> dict:
        try:
            return tenant.registry.set_tool_status(name, "active", activation="owner")
        except RegistryError as error:
            raise HTTPException(404, str(error)) from error

    @api.post("/registry/tools/{name}/promote")
    async def promote_tool(name: str, tenant: Tenant = Depends(app_state.auth.tenant)) -> dict:
        try:
            return tenant.registry.promote_tool(name)
        except RegistryError as error:
            raise HTTPException(409, str(error)) from error

    @api.post("/registry/tools/{name}/revoke")
    async def revoke_tool(name: str, tenant: Tenant = Depends(app_state.auth.tenant)) -> dict:
        try:
            return tenant.registry.set_tool_status(name, "revoked")
        except RegistryError as error:
            raise HTTPException(404, str(error)) from error

    @api.post("/registry/roles/{name}/promote")
    async def promote_role(name: str, tenant: Tenant = Depends(app_state.auth.tenant)) -> dict:
        try:
            return tenant.registry.promote_role(name)
        except RegistryError as error:
            raise HTTPException(409, str(error)) from error

    @api.post("/registry/roles/{name}/revoke")
    async def revoke_role(name: str, tenant: Tenant = Depends(app_state.auth.tenant)) -> dict:
        try:
            return tenant.registry.set_role_status(name, "revoked")
        except RegistryError as error:
            raise HTTPException(404, str(error)) from error

    return api
