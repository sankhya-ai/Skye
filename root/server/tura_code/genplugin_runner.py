"""The in-jail runner for a generated plugin candidate. Host-written, mounted read-only into the bubblewrap jail.

This is the ONLY code that touches the candidate inside the jail, and it treats the candidate as data with a narrow
contract: ``plugin.py`` provides ``handle(tool: str, arguments: dict)`` and ``tests.py`` provides ``test_*()``
functions. The candidate's own stdout is redirected to stderr so the protocol on real stdout stays one JSON object.

Protocol: one JSON request on stdin ({"mode": "test"} or {"mode": "call", "tool": ..., "arguments": {...}}), one JSON
response on stdout. The jail itself (no network, read-only system, private scratch workspace, scrubbed environment)
is the boundary; this script does not try to be one.
"""

import contextlib
import importlib.util
import json
import os
import resource
import sys
import traceback
from pathlib import Path

# Hard resource ceilings, set by the host before any candidate code loads. The namespace jail is the boundary for
# what the candidate can REACH; these bound what it can CONSUME. The wall-clock timeout on the host is the backstop.
resource.setrlimit(resource.RLIMIT_CPU, (10, 10))            # 10s of CPU
resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))   # 512 MB address space
resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))          # no fork bombs
resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))  # 16 MB files
resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reply(payload) -> None:
    sys.stdout.write("\n" + json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()


def check_cgroup(expected_memory_max: str, expected_pids_max: str) -> None:
    """Per-exec ground truth: THIS process must already be inside a cgroup with the exact memory and pids limits.
    Reads its own /proc/self/cgroup and the (read-only) /sys/fs/cgroup mount. Refuses before any candidate code is
    imported, so a probe-then-degrade race cannot run anything unbounded."""
    try:
        lines = Path("/proc/self/cgroup").read_text().splitlines()
    except OSError as error:
        raise RuntimeError(f"cannot read my own cgroup membership: {error}")
    v2 = next((line.split("::", 1)[1] for line in lines if line.startswith("0::")), None)
    if not v2:
        raise RuntimeError("this process is not in a cgroup v2 scope; refusing to run candidate code")
    base = Path("/sys/fs/cgroup") / v2.lstrip("/")
    try:
        memory_max = (base / "memory.max").read_text().strip()
        pids_max = (base / "pids.max").read_text().strip()
    except OSError as error:
        raise RuntimeError(f"cgroup limits unreadable at {base}: {error}")
    if memory_max != expected_memory_max or pids_max != expected_pids_max:
        raise RuntimeError(f"cgroup limits are memory.max={memory_max} pids.max={pids_max}, "
                           f"expected {expected_memory_max}/{expected_pids_max}; refusing to run candidate code")


def main() -> None:
    plugin_dir = sys.argv[1]
    check_cgroup(sys.argv[2], sys.argv[3])
    # ``import plugin`` inside tests.py must resolve to the candidate (mounted read-only); the jail bounds the rest.
    sys.path.insert(0, plugin_dir)
    request = json.loads(sys.stdin.read())
    try:
        with contextlib.redirect_stdout(sys.stderr):
            plugin = _load("candidate_plugin", os.path.join(plugin_dir, "plugin.py"))
        if request.get("mode") == "test":
            tests_path = os.path.join(plugin_dir, "tests.py")
            results = []
            if os.path.exists(tests_path):
                with contextlib.redirect_stdout(sys.stderr):
                    tests = _load("candidate_tests", tests_path)
                for name in sorted(n for n in dir(tests) if n.startswith("test_") and callable(getattr(tests, n))):
                    try:
                        with contextlib.redirect_stdout(sys.stderr):
                            getattr(tests, name)()
                        results.append({"test": name, "ok": True})
                    except Exception as error:  # noqa: BLE001 - the candidate is untrusted; any failure is data
                        results.append({"test": name, "ok": False, "error": f"{type(error).__name__}: {error}"[:400]})
            _reply({"ok": bool(results) and all(r["ok"] for r in results), "results": results,
                    "note": "ran the declared tests in the jail; passing here is evidence, not proof of correctness or safety"})
            return
        tool = str(request.get("tool") or "")
        arguments = request.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise TypeError("arguments must be an object")
        handle = getattr(plugin, "handle", None)
        if not callable(handle):
            raise AttributeError("plugin.py must define handle(tool, arguments)")
        with contextlib.redirect_stdout(sys.stderr):
            result = handle(tool, arguments)
        json.dumps(result, default=str)  # the result must be JSON-shaped before it crosses the boundary
        _reply({"ok": True, "result": result})
    except Exception as error:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        _reply({"ok": False, "error": f"{type(error).__name__}: {error}"[:600]})


if __name__ == "__main__":
    main()
