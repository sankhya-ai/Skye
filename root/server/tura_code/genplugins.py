"""Generated plugins: a genuinely MISSING capability as new code, reviewed and jailed - never trusted.

``create_tool`` composes existing tools declaratively. When no combination of existing tools can do the job, a session
may PROPOSE a generated plugin: a small Python module it writes itself. The governing rule, enforced by ordinary code:

    Model-written code is a CANDIDATE. It runs nowhere - not even once - except inside a throwaway bubblewrap jail,
    until the owner activates one exact content hash through the owner-authenticated API.

* Process isolation from day one: every execution (verification AND later calls) is a fresh ``bwrap`` process with
  ``--unshare-net`` (no network at all - not even the host's loopback), a read-only system, a private scratch
  workspace, and a scrubbed environment (no broker credentials, no API keys, no owner token). Python in-process
  sandboxing is not attempted; the process and its namespaces are the boundary. Shared kernel, no seccomp profile of
  our own - the same honest caveat the jobs isolation carries.
* No packages, no secrets, no network in v1. The static screen refuses imports outside a small stdlib whitelist and
  the jail cannot reach a network anyway. A capability that needs those is a hand-reviewed built-in plugin, not
  generated code.
* The static screen is a FILTER: passing means "nothing obviously forbidden", never "safe". Verification is the
  candidate's own declared tests passing in the jail: evidence, not proof. The trust decision is the owner's,
  made with the diff, the screen result and the test report in front of them.
* Activation is SESSION-LOCAL: the owner activates an exact hash for the creating session; making the plugin callable
  by future sessions is the owner's separate promotion. Revocation ends it everywhere on the next call.
* Resource limits, stated exactly: aggregate memory 256MB and 64 tasks via a PROBED user-scope cgroup (this is the
  gate - no proven cgroup, no execution), plus in-jail rlimits (CPU 10s, address space 512MB, 16MB per file, 256
  fds), a wall-clock kill, and no network. RLIMIT_NPROC is per-uid defense-in-depth only. Shared host kernel; a
  microVM tier is the step up if generated code ever runs without per-hash owner activation.

FAIL CLOSED: generated code runs only inside the namespace jail AND a user-scope cgroup whose memory/task limits
are probed to be applied (aggregate memory - including tmpfs scratch writes - and task count are bounded; rlimits
inside bound CPU, address space, file size and fds). Where that enforcement is unavailable, verification, owner
activation and invocation all refuse. There is no unbounded or "experimental" execution mode, and nothing ever
activates without the owner's explicit hash call.
* Identity is content: a version is the SHA-256 of its files. Activation names the hash and re-verifies in the jail
  before flipping the switch, so a tampered file or a stale review cannot activate. Revocation takes effect on the
  next call; each call re-resolves the active version and re-checks the hash before spending a process on it.

Lifecycle: draft -> verified (jailed tests passed; still callable by NO session) -> active (owner activated the exact
hash) -> revoked (terminal). A changed file is a new version with a new hash.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import catalogue
from .registry import NAME_RE, RegistryError, _atomic_write, validate_schema
from .sandbox import Bubblewrap
from .sessions.host_tools import tool_error

if TYPE_CHECKING:
    from .sessions.runtime import SessionRuntime
    from .tenancy import Tenant

PROPOSE_TOOL = "propose_tool"

MAX_PLUGINS = 16
MAX_PLUGIN_TOOLS = 8
MAX_SOURCE_CHARS = 64_000
MAX_TESTS_CHARS = 32_000
MAX_RESULT_CHARS = 8_000
VERIFY_TIMEOUT = 30.0
CALL_TIMEOUT = 20.0

#: The entire import surface a candidate may use. Pure computation only: nothing that touches the OS, the network,
#: or the interpreter's own machinery. The jail is the real boundary; this list keeps the reviewed surface small.
ALLOWED_IMPORTS = {
    "base64", "collections", "csv", "dataclasses", "datetime", "decimal", "enum", "fractions", "functools",
    "hashlib", "itertools", "json", "math", "random", "re", "statistics", "string", "textwrap", "typing",
}

#: Names a candidate may never call, however it reaches them. ``getattr``/``globals`` and friends are refused because
#: they defeat the attribute rules below (``getattr(x, "__globals__")``).
FORBIDDEN_CALLS = {
    "eval", "exec", "compile", "__import__", "breakpoint", "input", "exit", "quit",
    "getattr", "setattr", "delattr", "globals", "locals", "vars",
}

#: Dunder attribute reads that escape the module sandbox Python doesn't have. Refused on sight; the ordinary dunders
#: a module legitimately writes (``__name__`` etc. as bare names) are unaffected.
FORBIDDEN_ATTRIBUTES = {
    "__globals__", "__builtins__", "__subclasses__", "__mro__", "__class__", "__dict__", "__code__", "__closure__",
    "__import__", "__loader__", "__spec__", "__bases__", "__base__", "__qualname__", "__getattribute__",
}

FILES = ("plugin.py", "plugin.json", "tests.py")


class PluginError(RegistryError):
    pass


def screen_source(source: str, filename: str, *, extra_allowed: frozenset[str] = frozenset()) -> list[str]:
    """The static screen: a deterministic AST filter. A pass means "nothing obviously forbidden", NOT "safe" -
    only the jail plus the owner's review stand between a candidate and the host."""
    allowed = ALLOWED_IMPORTS | extra_allowed
    problems: list[str] = []
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as error:
        return [f"{filename}: does not parse: {error}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in allowed:
                    problems.append(f"{filename}:{node.lineno}: import `{alias.name}` is outside the whitelist (pure stdlib computation only; no OS, network or interpreter access)")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.level or root not in allowed:
                problems.append(f"{filename}:{node.lineno}: import from `{node.module}` is outside the whitelist")
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in FORBIDDEN_CALLS:
                problems.append(f"{filename}:{node.lineno}: `{fn.id}(...)` is refused (it defeats review and the interpreter rules)")
            elif isinstance(fn, ast.Attribute) and fn.attr in FORBIDDEN_CALLS:
                problems.append(f"{filename}:{node.lineno}: `.{fn.attr}(...)` is refused")
            elif isinstance(fn, ast.Attribute) and fn.attr in ("format", "format_map"):
                # "{0.__globals__}".format(x) hides attribute access inside a string the attribute check never sees.
                # The template is the RECEIVER of .format (fn.value); format_map's mapping can carry one too.
                candidates = [fn.value, *node.args]
                if any(isinstance(c, ast.Constant) and isinstance(c.value, str) and "__" in c.value for c in candidates):
                    problems.append(f"{filename}:{node.lineno}: a format template carries a dunder access path; dynamic attribute access is not review surface")
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES:
            problems.append(f"{filename}:{node.lineno}: attribute `{node.attr}` is refused (interpreter internals are not review surface)")
    return problems


def validate_manifest(manifest: Any, *, existing_names: set[str]) -> list[str]:
    problems: list[str] = []
    if not isinstance(manifest, dict):
        return ["plugin.json must be an object"]
    unknown = set(manifest) - {"tools"}
    if unknown:
        problems.append(f"plugin.json: unsupported keys: {', '.join(sorted(unknown))}")
    tools = manifest.get("tools")
    if not isinstance(tools, list) or not tools:
        return problems + ["plugin.json: `tools` must be a non-empty list"]
    if len(tools) > MAX_PLUGIN_TOOLS:
        problems.append(f"plugin.json: {len(tools)} tools; the limit is {MAX_PLUGIN_TOOLS}")
    seen: set[str] = set()
    for i, tool in enumerate(tools):
        if not isinstance(tool, dict):
            problems.append(f"tools[{i}]: must be an object")
            continue
        unknown = set(tool) - {"name", "description", "parameters"}
        if unknown:
            problems.append(f"tools[{i}]: unsupported keys: {', '.join(sorted(unknown))}")
        name = str(tool.get("name") or "")
        if not NAME_RE.match(name):
            problems.append(f"tools[{i}]: `name` must match {NAME_RE.pattern}")
        elif name in seen:
            problems.append(f"tools[{i}]: duplicate tool name `{name}`")
        elif name in existing_names:
            problems.append(f"tools[{i}]: `{name}` collides with an existing tool; a generated plugin cannot shadow or replace one")
        seen.add(name)
        description = str(tool.get("description") or "").strip()
        if not description:
            problems.append(f"tools[{i}]: `description` is required")
        elif len(description) > 500:
            problems.append(f"tools[{i}]: `description` is over 500 characters")
        problems += validate_schema(tool.get("parameters", {"type": "object", "properties": {}, "additionalProperties": False}),
                                    f"tools[{i}].parameters")
    return problems


def content_hash(files: dict[str, str]) -> str:
    """The identity of a version: SHA-256 over its exact files. Activation and calls name this, never just a name."""
    digest = hashlib.sha256()
    for name in FILES:
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(files.get(name, "").encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def read_files(plugin_dir: Path) -> dict[str, str]:
    return {name: (plugin_dir / name).read_text("utf-8") if (plugin_dir / name).exists() else "" for name in FILES}


class PluginStore:
    """Per-tenant store of generated plugin candidates. Owns plugins.json under the registry dir, with the same
    atomic-write + flock discipline as the registry itself."""

    def __init__(self, root: Path) -> None:
        self.dir = Path(root) / "registry" / "plugins"
        self.state_path = Path(root) / "registry" / "plugins.json"
        self.state: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.state_path.read_text("utf-8"))
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            self.state = data

    def _save(self) -> None:
        _atomic_write(self.state_path, self.state)

    def _plugin_lock(self):
        import fcntl
        from contextlib import contextmanager

        @contextmanager
        def held():
            self.dir.mkdir(parents=True, exist_ok=True)
            with open(self.dir / ".lock", "a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    self._load()
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return held()

    # ----- lookups ---------------------------------------------------------------

    def active_version(self, name: str, session: str | None = None) -> dict[str, Any] | None:
        """The version a session may call. Lifetime mirrors the registry: activation makes the plugin ACTIVE IN THE
        CREATING SESSION only; only the owner's separate promotion makes it durable (any session)."""
        self._load()
        entry = self.state.get(name)
        if not entry:
            return None
        version = entry.get("active_version")
        record = next((v for v in entry["versions"] if v["version"] == version and v["status"] == "active"), None)
        if record is None:
            return None
        if record.get("lifetime") == "durable":
            return {**record, "name": name}
        if session is not None and record.get("session") == session:
            return {**record, "name": name}
        return None

    def tool_owner(self, tool_name: str, session: str | None = None) -> dict[str, Any] | None:
        """The active plugin version exposing `tool_name` TO THIS SESSION, if any."""
        self._load()
        for name in self.state:
            record = self.active_version(name, session)
            if record and any(t["name"] == tool_name for t in record["manifest"].get("tools", [])):
                return record
        return None

    def listing(self) -> dict[str, Any]:
        self._load()
        out = {}
        for name, entry in sorted(self.state.items()):
            out[name] = {
                "active_version": entry.get("active_version"),
                "versions": [{k: v.get(k) for k in ("version", "status", "lifetime", "session", "sha256", "created_by", "created_at", "verified", "activated_at")}
                             for v in entry["versions"]],
            }
        return out

    # ----- mutations ---------------------------------------------------------------

    def propose(self, name: str, files: dict[str, str], *, created_by: str, session: str) -> tuple[dict[str, Any] | None, list[str]]:
        """Store a candidate as DRAFT after static screening. Nothing runs here; nothing half-exists on a refusal."""
        problems: list[str] = []
        if not NAME_RE.match(name or ""):
            problems.append(f"`name` must match {NAME_RE.pattern}")
        source = files.get("plugin.py", "")
        tests = files.get("tests.py", "")
        if not source.strip():
            problems.append("plugin.py is required and must define handle(tool, arguments)")
        if len(source) > MAX_SOURCE_CHARS:
            problems.append(f"plugin.py is over {MAX_SOURCE_CHARS} characters")
        if len(tests) > MAX_TESTS_CHARS:
            problems.append(f"tests.py is over {MAX_TESTS_CHARS} characters")
        if not tests.strip():
            problems.append("tests.py is required: a candidate must carry the tests its verification will run in the jail")
        if problems:
            return None, problems
        problems += screen_source(source, "plugin.py")
        problems += screen_source(tests, "tests.py", extra_allowed=frozenset({"plugin"}))  # the tests import the candidate itself
        if problems:
            return None, problems
        with self._plugin_lock():
            entry = self.state.setdefault(name, {"versions": [], "active_version": None})
            if len(entry["versions"]) >= 20:
                return None, [f"`{name}` has 20 versions; the history is full"]
            version = (entry["versions"][-1]["version"] + 1) if entry["versions"] else 1
            plugin_dir = self.dir / name / str(version)
            if plugin_dir.exists():
                return None, [f"`{name}` v{version} already exists on disk; versions are immutable"]
            plugin_dir.mkdir(parents=True)
            for filename in FILES:
                if files.get(filename):
                    (plugin_dir / filename).write_text(files[filename], "utf-8")
            record = {
                "version": version,
                "sha256": content_hash(files),
                "status": "draft",
                "created_by": created_by,
                "session": session,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "manifest": json.loads(files["plugin.json"]),
                "verified": None,
            }
            entry["versions"].append(record)
            self._save()
        return record, []

    def record_verification(self, name: str, version: int, report: dict[str, Any]) -> dict[str, Any]:
        with self._plugin_lock():
            record = self._version(name, version)
            record["verified"] = report
            if report.get("ok") and record["status"] == "draft":
                record["status"] = "verified"
            self._save()
        return {**record, "name": name}

    def review(self, name: str, version: int | None = None) -> dict[str, Any]:
        """Everything the owner needs BEFORE activation: the exact files, their hash, the manifest, and the honest
        runtime facts. The hash is recomputed from the bytes on disk at review time - a mismatch with the stored
        record means the candidate was tampered with or corrupted, and the UI must not offer activation."""
        with self._plugin_lock():
            entry = self.state.get(name)
            if not entry:
                raise PluginError(f"unknown plugin `{name}`")
            record = self._version(name, version if version is not None else max(v["version"] for v in entry["versions"]))
            files = read_files(self.dir / name / str(record["version"]))
            on_disk = content_hash(files)
            available, reason = execution_policy()
            return {
                "name": name,
                **{k: record.get(k) for k in ("version", "status", "lifetime", "session", "created_by", "created_at", "verified", "activated_at")},
                "sha256": record["sha256"],
                "on_disk_sha256": on_disk,
                "hash_matches_record": on_disk == record["sha256"],
                "files": files,
                "tools": record["manifest"].get("tools", []),
                "execution": {
                    "available": available is not None,
                    "reason": reason,
                    "isolation": ("each run is a fresh bubblewrap jail: no network, scrubbed environment, minimal mounts "
                                  "(no /etc, /opt, home or tenant paths), rlimit ceilings, exact-hash snapshot execution; "
                                  "aggregate memory/task limits apply only where the cgroup probe proves them"),
                },
                "activation": (f"POST /api/registry/plugins/{name}/activate with {{\"version\": {record['version']}, "
                               f"\"sha256\": \"{record['sha256']}\"}} - the exact hash of what you reviewed; anything else is refused"),
            }

    def activate(self, name: str, version: int, sha256: str) -> dict[str, Any]:
        """The owner's act: make EXACTLY the reviewed, re-verified hash callable. Refuses anything else."""
        with self._plugin_lock():
            record = self._version(name, version)
            if record["status"] == "revoked":
                raise PluginError(f"`{name}` v{version} is revoked; a revoked version is terminal - propose a new version instead.")
            if record["status"] != "verified":
                raise PluginError(f"`{name}` v{version} is {record['status']}; only a jail-verified version can be activated.")
            if record["sha256"] != sha256:
                raise PluginError(f"hash mismatch: the owner reviewed {sha256[:12]}... but `{name}` v{version} is {record['sha256'][:12]}... . Activation names the exact hash; nothing else activates.")
            on_disk = content_hash(read_files(self.dir / name / str(version)))
            if on_disk != sha256:
                raise PluginError(f"the files on disk no longer match the reviewed hash (now {on_disk[:12]}...); the candidate was tampered with or corrupted. Activation refused.")
            entry = self.state[name]
            for other in entry["versions"]:
                if other["status"] == "active":
                    other["status"] = "superseded"
            record["status"] = "active"
            record["activated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            entry["active_version"] = version
            # Activation is session-lifetime: the creating session may call it at once; future sessions need the
            # owner's separate promotion. record["session"] was fixed at proposal time.
            record["lifetime"] = "session"
            self._save()
        return {**record, "name": name}

    def promote(self, name: str, version: int | None = None) -> dict[str, Any]:
        """The owner's second, separate act: make an ACTIVATED version durable for future sessions."""
        with self._plugin_lock():
            entry = self.state.get(name)
            if not entry:
                raise PluginError(f"unknown plugin `{name}`")
            version = version or entry.get("active_version")
            record = next((v for v in entry["versions"] if v["version"] == version and v["status"] == "active"), None)
            if record is None:
                raise PluginError(f"`{name}` has no active v{version}; only an activated version can be promoted (activation is the hash gate, promotion is the lifetime gate).")
            record["lifetime"] = "durable"
            record["promoted_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._save()
        return {**record, "name": name}

    def revoke(self, name: str) -> dict[str, Any]:
        with self._plugin_lock():
            entry = self.state.get(name)
            if not entry:
                raise PluginError(f"unknown plugin `{name}`")
            found = None
            for record in entry["versions"]:
                if record["status"] == "active":
                    record["status"] = "revoked"
                    found = record
            entry["active_version"] = None
            self._save()
            if found is None:
                raise PluginError(f"`{name}` has no active version")
        return {**found, "name": name}

    def _version(self, name: str, version: int) -> dict[str, Any]:
        entry = self.state.get(name)
        if not entry:
            raise PluginError(f"unknown plugin `{name}`")
        record = next((v for v in entry["versions"] if v["version"] == version), None)
        if record is None:
            raise PluginError(f"unknown version `{name}` v{version}")
        return record


# ----- the jail ----------------------------------------------------------------------------------------------------


def sandbox_problem() -> str:
    """Why generated plugins cannot run here, or "". Fail-closed: a problem means NO verification and NO activation."""
    return Bubblewrap(network=False).problem()


#: The jail mounts ONLY what running Python needs. The agent sandbox's usual read-only system set includes /etc,
#: /opt, /nix and /snap - fine for the owner's own agent, wrong for candidate code: config files under those trees
#: can carry host secrets. Candidate jails get no /etc, no /opt, no home, no other users' files, no network.
JAIL_SYSTEM_DIRS = ("/usr", "/lib", "/lib64", "/lib32", "/libx32", "/bin", "/sbin")


MEMORY_MAX_BYTES = 256 * 1024 * 1024
TASKS_MAX = 64


def execution_policy() -> "tuple[list[str] | None, str]":
    """The ONLY execution mode for generated code: a user-scope cgroup whose memory AND pids limits are PROBED to be
    applied. Returns (argv_prefix, "") or (None, reason). No cache and no fallback: every verification, activation
    and invocation probes again, and the in-jail runner independently re-reads its OWN cgroup limits before any
    candidate code is imported (see genplugin_runner.check_cgroup), so a probe-then-degrade race cannot run anything
    unbounded. Binary presence is not evidence at either layer."""
    return _probe_cgroup()


def _probe_cgroup() -> "tuple[list[str] | None, str]":
    if not shutil.which("systemd-run"):
        return None, "no user-scope cgroup runner (systemd-run --user) on this host"
    prefix = ["systemd-run", "--user", "--scope", "--quiet",
              "-p", f"MemoryMax={MEMORY_MAX_BYTES}", "-p", "MemoryHigh=192M", "-p", f"TasksMax={TASKS_MAX}", "--"]
    try:
        probe = subprocess.run(
            [*prefix, "/bin/sh", "-c",
             "cg=$(cut -d: -f3 /proc/self/cgroup); echo $(cat /sys/fs/cgroup$cg/memory.max) $(cat /sys/fs/cgroup$cg/pids.max)"],
            capture_output=True, timeout=15, text=True,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, f"the cgroup probe itself failed: {error}"
    if probe.returncode != 0:
        return None, f"could not start a resource-limited scope: {probe.stderr.strip()[:200] or 'no detail'}"
    applied = probe.stdout.split()
    if applied != [str(MEMORY_MAX_BYTES), str(TASKS_MAX)]:
        return None, (f"the cgroup limits were not applied (read back memory.max/pids.max = {applied or 'nothing'}, "
                      f"expected {[str(MEMORY_MAX_BYTES), str(TASKS_MAX)]}); refusing to run generated code without a proven bound")
    return prefix, ""


def _jail_argv(python: str, runner: Path, snapshot: Path) -> list[str]:
    args = ["bwrap", "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
            "--unshare-cgroup-try", "--unshare-net"]
    for path in JAIL_SYSTEM_DIRS:
        if os.path.islink(path):
            args += ["--symlink", os.readlink(path), path]
        elif os.path.isdir(path):
            args += ["--ro-bind", path, path]
    args += ["--dev", "/dev", "--tmpfs", "/tmp"]
    # The runner verifies its own cgroup limits before loading candidate code; it needs both, read-only.
    args += ["--ro-bind", "/sys/fs/cgroup", "/sys/fs/cgroup"]
    args += ["--ro-bind", str(runner), str(runner), "--ro-bind", str(snapshot), str(snapshot)]
    # The scratch workspace is a jail-PRIVATE tmpfs: it dies with the namespace and no host path is ever writable.
    args += ["--tmpfs", "/scratch", "--chdir", "/scratch"]
    return [*args, "--", python, str(runner), str(snapshot), str(MEMORY_MAX_BYTES), str(TASKS_MAX)]


async def run_jailed(store: PluginStore, name: str, version: int, payload: dict[str, Any], *, timeout: float,
                     expected_sha256: str | None = None) -> dict[str, Any]:
    """One execution of candidate code: a fresh process in a fresh minimal jail. No network, no /etc or /opt, a
    private scratch workspace deleted afterwards, a scrubbed environment, rlimit ceilings. The process is the
    boundary. The plugin files are SNAPSHOTTED and re-hashed at mount time: the bytes the owner reviewed are the
    bytes that execute, with no check-to-mount window for a swapped file."""
    problem = sandbox_problem()
    if problem:
        return {"ok": False, "error": f"generated plugins need bubblewrap isolation, which is unavailable here, so nothing ran: {problem}"}
    prefix, reason = execution_policy()
    if prefix is None:
        return {"ok": False, "error": f"generated code runs only under proven aggregate resource limits, so nothing ran: {reason}"}
    runner = Path(__file__).with_name("genplugin_runner.py")
    python = shutil.which("python3")
    if not python:
        return {"ok": False, "error": "no python3 on PATH for the jail; nothing ran"}
    snapshot = Path(tempfile.mkdtemp(prefix="genplugin-src-"))
    try:
        source_dir = store.dir / name / str(version)
        files = read_files(source_dir)
        actual = content_hash(files)
        if expected_sha256 is not None and actual != expected_sha256:
            return {"ok": False, "error": f"the files on disk hash to {actual[:12]}..., not the activated {expected_sha256[:12]}...; nothing ran"}
        for filename, content in files.items():
            if content:
                (snapshot / filename).write_text(content, "utf-8")
        argv = [*prefix, *_jail_argv(python, runner, snapshot)]
        env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "TMPDIR": "/tmp"}
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(json.dumps(payload).encode()), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            proc.kill()
            await proc.wait()
            return {"ok": False, "error": f"the candidate did not finish within {timeout:.0f}s and was killed"}
        # Read enough for a legitimately large result, then parse BEFORE capping: truncating first corrupts the JSON.
        text = stdout.decode(errors="replace")[:1_000_000]
        line = text.strip().splitlines()[-1] if text.strip() else ""
        try:
            reply = json.loads(line)
        except ValueError:
            killed = proc.returncode in (-9, -11, -6, -24, -25, 137)  # SIGKILL/SIGSEGV/SIGABRT/SIGXCPU/SIGXFSZ
            why = "it hit a resource limit and was killed" if killed else f"exit {proc.returncode}"
            return {"ok": False, "error": f"the candidate produced no protocol reply ({why}); stderr tail: {stderr.decode(errors='replace')[-300:]}"}
        if isinstance(reply, dict):
            reply["isolation"] = f"cgroup-bounded (memory.max {MEMORY_MAX_BYTES}, pids.max {TASKS_MAX}, re-verified in-jail per execution) + namespace jail"
        return reply
    finally:
        shutil.rmtree(snapshot, ignore_errors=True)


# ----- the host toolsets -------------------------------------------------------------------------------------------


class GeneratedToolset:
    """Exposes the tools of ACTIVE generated plugins and runs each call in a fresh jail.

    Every call re-resolves the active version and re-checks the content hash on disk before spending a process:
    revocation takes effect on the next call, and a tampered file simply refuses to run."""

    plugin = "generated"

    def __init__(self, store: PluginStore) -> None:
        self.store = store

    def _active_tools(self, session: str | None) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        out = []
        for name in sorted(self.store.state.keys()):
            record = self.store.active_version(name, session)
            if record:
                for tool in record["manifest"].get("tools", []):
                    out.append((record, tool))
        return out

    def definitions(self, runtime: "SessionRuntime") -> list[dict[str, Any]]:
        session = getattr(runtime.record, "id", None)
        return [{
            "name": tool["name"],
            "description": tool["description"] + f" (generated plugin `{record['name']}` v{record['version']}, hash {record['sha256'][:12]}...: "
                                                 "model-written code, owner-activated, every call runs in a fresh networkless bubblewrap jail.)",
            "parameters": tool["parameters"],
        } for record, tool in self._active_tools(session)]

    def handles(self, name: str) -> bool:
        # ``handles`` sees no runtime; ``call`` enforces the session gate.
        return any(self.store.tool_owner(name, s) for s in (None,)) or any(
            t["name"] == name
            for entry in self.store.state.values()
            for record in entry["versions"]
            if record["status"] == "active"
            for t in record["manifest"].get("tools", []))

    async def call(self, runtime: "SessionRuntime", call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        record = self.store.tool_owner(name, getattr(runtime.record, "id", None))
        if record is None:
            return tool_error(f"`{name}` is not available to this session (unknown, not yet owner-activated, revoked, or session-lifetime in its creating session only).",
                              "Unavailable")
        _, reason = execution_policy()
        if reason:
            return tool_error(f"`{name}` did not run: generated code executes only under proven aggregate resource limits: {reason}",
                              "Execution policy unmet")
        # TOCTOU, closed: run_jailed snapshots the files, re-hashes the snapshot against the activated hash, and
        # only then mounts the snapshot into the jail. A swapped file never executes.
        reply = await run_jailed(self.store, record["name"], record["version"], {"mode": "call", "tool": name, "arguments": arguments},
                                 timeout=CALL_TIMEOUT, expected_sha256=record["sha256"])
        if not reply.get("ok"):
            return tool_error(str(reply.get("error", "the generated tool failed")), "Generated tool failed")
        result = reply.get("result")
        text = result if isinstance(result, str) else json.dumps(result, indent=2, default=str)
        return {"is_error": False, "output": text[:MAX_RESULT_CHARS], "message": "",
                "display": [{"type": "brief", "text": f"{name} (jailed)"}]}


class ProposeToolset:
    """``propose_tool``: write a candidate, screen it, verify it in the jail. NEVER activates - that is the owner's
    API call with the exact hash, and this toolset cannot make it."""

    plugin = "registry"

    def __init__(self, store: PluginStore) -> None:
        self.store = store

    def definitions(self, runtime: "SessionRuntime") -> list[dict[str, Any]]:
        return [{
            "name": PROPOSE_TOOL,
            "description": (
                "Propose a genuinely NEW capability as generated Python, when no pipeline of existing tools can do it. "
                "The code is a CANDIDATE: it is statically screened (a filter, not a safety judgment) and its own declared tests run once "
                "inside a networkless bubblewrap jail. It then stays INERT - callable by no session - until the owner activates the exact "
                "content hash through the owner API. Tell the owner what you proposed, its hash, and that activation is their call. "
                "No network, packages or secrets are available to generated code; needing those means this is the wrong path."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "snake_case plugin name"},
                    "tools": {"type": "array", "description": "Manifest: [{name, description, parameters}] the plugin exposes"},
                    "source": {"type": "string", "description": "plugin.py: must define handle(tool, arguments); whitelisted stdlib imports only"},
                    "tests": {"type": "string", "description": "tests.py: test_*() functions; run in the jail as verification"},
                },
                "required": ["name", "tools", "source", "tests"],
                "additionalProperties": False,
            },
        }]

    def handles(self, name: str) -> bool:
        return name == PROPOSE_TOOL

    async def call(self, runtime: "SessionRuntime", call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        from .registry import existing_tool_names  # local import: registry must not depend on this module

        tenant = runtime.tenant
        plugin_name = str(arguments.get("name") or "")
        existing = existing_tool_names(tenant, runtime, tenant.registry)
        for entry_name in self.store.state:
            for record in self.store.state[entry_name]["versions"]:
                existing |= {t["name"] for t in record["manifest"].get("tools", [])}
        manifest = {"tools": arguments.get("tools")}
        problems = validate_manifest(manifest, existing_names=existing)
        if problems:
            return tool_error("Candidate refused by manifest validation:\n- " + "\n- ".join(problems), "Invalid candidate")
        _, reason = execution_policy()
        if reason:
            return tool_error(
                "Generated code runs only under proven aggregate resource limits, so this candidate was not even stored: "
                + reason, "Execution policy unmet")
        if len(self.store.state) >= MAX_PLUGINS and plugin_name not in self.store.state:
            return tool_error(f"The generated-plugin store is full ({MAX_PLUGINS}); the owner must revoke one first.", "Store full")
        files = {"plugin.py": str(arguments.get("source") or ""), "plugin.json": json.dumps(manifest, indent=2), "tests.py": str(arguments.get("tests") or "")}
        created_by = getattr(runtime.record, "job_role", None) or f"session:{getattr(runtime.record, 'id', '?')}"
        record, problems = self.store.propose(plugin_name, files, created_by=created_by, session=getattr(runtime.record, "id", "") or "")
        if problems:
            return tool_error("Candidate refused by the static screen (a filter; passing it would not have meant safe):\n- " + "\n- ".join(problems), "Invalid candidate")
        report = await run_jailed(self.store, plugin_name, record["version"], {"mode": "test"}, timeout=VERIFY_TIMEOUT)
        record = self.store.record_verification(plugin_name, record["version"], report)
        emit = getattr(runtime, "emit", None)
        if callable(emit):
            emit("RegistryChanged", {"kind": "plugin", "name": plugin_name, "version": record["version"], "status": record["status"],
                                     "lifetime": f"inert until the owner activates hash {record['sha256'][:12]}...",
                                     "description": "; ".join(t["name"] for t in record["manifest"]["tools"])})
        payload = {
            **{k: record.get(k) for k in ("version", "sha256", "status", "created_by")},
            "name": plugin_name,
            "verification": report,
            "what_this_means": ("the candidate's declared tests passed in a networkless jail: evidence, not proof of correctness or safety. "
                                "The tool is callable by NO session until the owner activates this exact hash - and activation makes it "
                                "callable IN THIS SESSION only. Future sessions get it only if the owner then promotes it."),
            "owner_activation": f"POST /api/registry/plugins/{plugin_name}/activate with {{\"version\": {record['version']}, \"sha256\": \"{record['sha256']}\"}} (this session only); POST /api/registry/plugins/{plugin_name}/promote makes it durable",
            "tell_the_owner": (f"Say what you proposed ({plugin_name}: {', '.join(t['name'] for t in record['manifest']['tools'])}), "
                               "show the verification report, and leave activation - and any promotion to future sessions - to them."),
        }
        return {"is_error": False, "output": json.dumps(payload, indent=2, default=str), "message": "",
                "display": [{"type": "brief", "text": f"Proposed {plugin_name} v{record['version']} ({record['status']})"}]}


# ----- wiring --------------------------------------------------------------------------------------------------------


def install(server) -> None:
    def extend(tenant) -> None:
        tenant.plugin_store = PluginStore(Path(tenant.root))
        tenant.toolsets.append(GeneratedToolset(tenant.plugin_store))
        tenant.toolsets.append(ProposeToolset(tenant.plugin_store))

    server.extensions.append(extend)


def router(app_state):
    from fastapi import APIRouter, Body, Depends, HTTPException

    from .tenancy import Tenant

    api = APIRouter(prefix="/api")

    @api.get("/registry/plugins")
    async def listing(tenant: Tenant = Depends(app_state.auth.tenant)) -> dict:
        return tenant.plugin_store.listing()

    @api.get("/registry/plugins/{name}/review")
    async def review_plugin(name: str, version: int | None = None, tenant: Tenant = Depends(app_state.auth.tenant)) -> dict:
        try:
            return tenant.plugin_store.review(name, version)
        except PluginError as error:
            raise HTTPException(404, str(error)) from error

    @api.post("/registry/plugins/{name}/activate")
    async def activate(name: str, tenant: Tenant = Depends(app_state.auth.tenant), body: dict = Body(...)) -> dict:
        try:
            version = int(body.get("version"))
            sha256 = str(body.get("sha256") or "")
        except (TypeError, ValueError) as error:
            raise HTTPException(422, "activation needs {version, sha256}: the exact reviewed hash, nothing else") from error
        problem = sandbox_problem()
        if problem:
            raise HTTPException(409, f"generated plugins need bubblewrap isolation, which is unavailable here, so nothing was activated: {problem}")
        _, reason = execution_policy()
        if reason:
            raise HTTPException(409, f"generated code runs only under proven aggregate resource limits, so nothing was activated: {reason}")
        store = tenant.plugin_store
        # Re-verify the exact candidate in the jail at activation time: review plus a fresh pass, never a stale one.
        report = await run_jailed(store, name, version, {"mode": "test"}, timeout=VERIFY_TIMEOUT)
        store.record_verification(name, version, report)
        if not report.get("ok"):
            raise HTTPException(409, f"re-verification failed at activation, so nothing was activated: {json.dumps(report)[:500]}")
        try:
            return store.activate(name, version, sha256)
        except PluginError as error:
            raise HTTPException(409, str(error)) from error

    @api.post("/registry/plugins/{name}/promote")
    async def promote(name: str, tenant: Tenant = Depends(app_state.auth.tenant), body: dict = Body(default={})) -> dict:
        """The owner's separate lifetime gate: an ACTIVATED version becomes durable for future sessions."""
        try:
            return tenant.plugin_store.promote(name, body.get("version"))
        except PluginError as error:
            raise HTTPException(409, str(error)) from error

    @api.post("/registry/plugins/{name}/revoke")
    async def revoke(name: str, tenant: Tenant = Depends(app_state.auth.tenant)) -> dict:
        try:
            return tenant.plugin_store.revoke(name)
        except PluginError as error:
            raise HTTPException(404, str(error)) from error

    return api
