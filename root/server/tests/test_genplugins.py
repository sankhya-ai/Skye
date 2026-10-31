"""Generated plugins: candidate code, static screen, jailed verification, owner activation of the exact hash.

The jail tests run real bubblewrap processes (no network namespace, read-only system) and skip where bwrap cannot
run - the same fail-closed rule the feature itself follows.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from tura_code import catalogue, genplugins, registry
from tura_code.genplugins import GeneratedToolset, PluginStore, ProposeToolset


def run(coro):
    return asyncio.run(coro)


def make(tmp_path):
    reg = registry.Registry(tmp_path)
    store = PluginStore(tmp_path)
    holder = SimpleNamespace(value=SimpleNamespace(tool_scope=[], dynamic_tools=False, disabled_plugins=[]))
    tenant = SimpleNamespace(settings=SimpleNamespace(get=lambda: holder.value), registry=reg, plugin_store=store)
    tenant.toolsets = [catalogue.GatewayToolset(), registry.RegistryToolset(), registry.CompositeToolset(reg),
                       GeneratedToolset(store), ProposeToolset(store)]
    tenant.active_toolsets = lambda: tenant.toolsets
    runtime = SimpleNamespace(tenant=tenant, record=SimpleNamespace(job_role="", id="s1", tool_scope=[]), emit=None)
    return tenant, runtime, store


MATH_SOURCE = '''
import math
import statistics


def handle(tool, arguments):
    if tool == "stats_summary":
        numbers = arguments["numbers"]
        return {"mean": statistics.mean(numbers), "stdev": statistics.pstdev(numbers), "root_mean": math.sqrt(statistics.mean(n * n for n in numbers))}
    raise ValueError(f"unknown tool {tool}")
'''

MATH_TESTS = '''
import math

import plugin


def test_stats_summary():
    out = plugin.handle("stats_summary", {"numbers": [2.0, 4.0]})
    assert out["mean"] == 3.0
    assert abs(out["stdev"] - 1.0) < 1e-9


def test_unknown_tool_rejected():
    try:
        plugin.handle("nope", {})
    except ValueError:
        return
    raise AssertionError("expected ValueError")
'''

MANIFEST = [{"name": "stats_summary", "description": "Mean and spread of a number list.",
             "parameters": {"type": "object", "properties": {"numbers": {"type": "array", "items": {"type": "number"}}},
                            "required": ["numbers"], "additionalProperties": False}}]

_, _POLICY_REASON = genplugins.execution_policy()
needs_bwrap = pytest.mark.skipif(
    bool(genplugins.sandbox_problem()) or bool(_POLICY_REASON),
    reason=(genplugins.sandbox_problem() or _POLICY_REASON or "ok")
    + " - cgroup-bounded execution is UNTESTED on this host; only refusal paths are proven here",
)


def propose(runtime, source=MATH_SOURCE, tests=MATH_TESTS, tools=MANIFEST, name="numstats"):
    toolset = next(t for t in runtime.tenant.toolsets if isinstance(t, ProposeToolset))
    return run(toolset.call(runtime, "c1", "propose_tool", {"name": name, "tools": tools, "source": source, "tests": tests}))


# ----- the static screen: a filter, never a safety pass ---------------------------------------------------------


def test_screen_refuses_os_network_and_interpreter_access():
    assert genplugins.screen_source("import os\nprint(os.getcwd())", "plugin.py")
    assert genplugins.screen_source("import socket", "plugin.py")
    assert genplugins.screen_source("import subprocess", "plugin.py")
    assert genplugins.screen_source("from urllib import request", "plugin.py")
    assert genplugins.screen_source("eval('1+1')", "plugin.py")
    assert genplugins.screen_source("getattr(x, '__globals__')", "plugin.py")
    assert genplugins.screen_source("().__class__", "plugin.py")
    assert genplugins.screen_source("x.__globals__", "plugin.py")
    assert genplugins.screen_source("compile('1', 'f', 'eval')", "plugin.py")
    assert genplugins.screen_source("not python at all {", "plugin.py")
    # Attribute access hidden in a format TEMPLATE string escapes the attribute check; the screen must catch it.
    assert genplugins.screen_source("'{0.__globals__}'.format(x)", "plugin.py")
    assert genplugins.screen_source("'{0.__class__}'.format(())", "plugin.py")
    # An ordinary f-string carries real AST nodes, and ordinary prose strings pass.
    assert genplugins.screen_source("f'{x.__class__}'", "plugin.py")  # an Attribute node, caught as such
    assert genplugins.screen_source("note = 'a __double__ underscore in prose'", "plugin.py") == []


def test_screen_allows_whitelisted_stdlib():
    assert genplugins.screen_source(MATH_SOURCE, "plugin.py") == []
    assert genplugins.screen_source("import json\nimport re\nfrom collections import Counter", "plugin.py") == []


def test_screen_is_not_claimed_as_safety():
    doc = genplugins.__doc__ + genplugins.screen_source.__doc__
    assert "filter" in doc and "not" in doc.lower()


# ----- proposal and lifecycle ------------------------------------------------------------------------------------


def test_refused_candidate_leaves_nothing_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(genplugins, "execution_policy", lambda: (["true"], ""))  # the screen, not the policy, is on test
    tenant, runtime, store = make(tmp_path)
    result = propose(runtime, source="import os\n" + MATH_SOURCE)
    assert result["is_error"]
    assert "whitelist" in result["output"] + result["message"]
    assert store.state == {}
    assert not (store.dir / "numstats").exists()


def test_manifest_collisions_and_schema_errors_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(genplugins, "execution_policy", lambda: (["true"], ""))
    tenant, runtime, store = make(tmp_path)
    result = propose(runtime, tools=[{"name": "create_tool", "description": "shadow", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}])
    assert result["is_error"] and "collides" in result["output"] + result["message"]
    result = propose(runtime, tools=[{"name": "bad name!", "description": "x", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}])
    assert result["is_error"]
    assert store.state == {}


@needs_bwrap
def test_proposal_verifies_in_jail_but_stays_inert(tmp_path):
    tenant, runtime, store = make(tmp_path)
    result = propose(runtime)
    assert not result["is_error"], result["output"]
    payload = json.loads(result["output"])
    assert payload["status"] == "verified"
    assert payload["verification"]["ok"] is True
    assert len(payload["verification"]["results"]) == 2
    # Verified is NOT callable: activation is the owner's API act, not a side effect of proposing.
    generated = next(t for t in tenant.toolsets if isinstance(t, GeneratedToolset))
    assert generated.definitions(runtime) == []
    call = run(generated.call(runtime, "c2", "stats_summary", {"numbers": [1.0]}))
    assert call["is_error"] and "not available to this session" in call["output"] + call["message"]


@needs_bwrap
def test_failing_tests_keep_the_candidate_a_draft(tmp_path):
    tenant, runtime, store = make(tmp_path)
    result = propose(runtime, tests="def test_wrong():\n    assert 1 == 2\n")
    payload = json.loads(result["output"])
    assert payload["status"] == "draft"
    assert payload["verification"]["ok"] is False
    with pytest.raises(genplugins.PluginError, match="verified"):
        store.activate("numstats", 1, payload["sha256"])


@needs_bwrap
def test_activation_requires_the_exact_hash_and_owner_api(tmp_path):
    tenant, runtime, store = make(tmp_path)
    payload = json.loads(propose(runtime)["output"])
    with pytest.raises(genplugins.PluginError, match="hash mismatch"):
        store.activate("numstats", 1, "0" * 64)
    record = store.activate("numstats", 1, payload["sha256"])
    assert record["status"] == "active"
    # Now the tool exists and every call runs jailed.
    generated = next(t for t in tenant.toolsets if isinstance(t, GeneratedToolset))
    defs = generated.definitions(runtime)
    assert [d["name"] for d in defs] == ["stats_summary"]
    assert "owner-activated" in defs[0]["description"] and "jail" in defs[0]["description"]
    call = run(generated.call(runtime, "c3", "stats_summary", {"numbers": [2.0, 4.0]}))
    assert not call["is_error"], call
    assert json.loads(call["output"])["mean"] == 3.0


@needs_bwrap
def test_tampered_files_refuse_activation_and_calls(tmp_path):
    tenant, runtime, store = make(tmp_path)
    payload = json.loads(propose(runtime)["output"])
    plugin_py = store.dir / "numstats" / "1" / "plugin.py"
    plugin_py.write_text(plugin_py.read_text() + "\n# tampered\n")
    with pytest.raises(genplugins.PluginError, match="no longer match"):
        store.activate("numstats", 1, payload["sha256"])
    # Restore and activate, then tamper again: calls refuse too.
    plugin_py.write_text(MATH_SOURCE)
    store.activate("numstats", 1, payload["sha256"])
    plugin_py.write_text(plugin_py.read_text() + "\n# tampered\n")
    generated = next(t for t in tenant.toolsets if isinstance(t, GeneratedToolset))
    call = run(generated.call(runtime, "c4", "stats_summary", {"numbers": [1.0]}))
    assert call["is_error"] and "not the activated" in call["message"] + call["output"]


@needs_bwrap
def test_revocation_is_immediate_and_terminal(tmp_path):
    tenant, runtime, store = make(tmp_path)
    payload = json.loads(propose(runtime)["output"])
    store.activate("numstats", 1, payload["sha256"])
    store.revoke("numstats")
    generated = next(t for t in tenant.toolsets if isinstance(t, GeneratedToolset))
    assert generated.definitions(runtime) == []
    call = run(generated.call(runtime, "c5", "stats_summary", {"numbers": [1.0]}))
    assert call["is_error"]
    with pytest.raises(genplugins.PluginError, match="terminal"):
        store.activate("numstats", 1, payload["sha256"])


# ----- lifecycle: activation is session-local; promotion is the owner's separate act --------------------------------


def _other_runtime(tenant):
    return SimpleNamespace(tenant=tenant, record=SimpleNamespace(job_role="", id="s2", tool_scope=[]), emit=None)


@needs_bwrap
def test_activation_is_session_local_until_promoted(tmp_path):
    tenant, runtime, store = make(tmp_path)
    payload = json.loads(propose(runtime)["output"])  # proposed by session s1
    store.activate("numstats", 1, payload["sha256"])
    generated = next(t for t in tenant.toolsets if isinstance(t, GeneratedToolset))
    # The creating session can call it at once.
    assert any(d["name"] == "stats_summary" for d in generated.definitions(runtime))
    ok = run(generated.call(runtime, "c11", "stats_summary", {"numbers": [2.0, 4.0]}))
    assert not ok["is_error"]
    # An unrelated session cannot: no definitions, and a direct call is refused.
    other = _other_runtime(tenant)
    assert generated.definitions(other) == []
    refused = run(generated.call(other, "c12", "stats_summary", {"numbers": [1.0]}))
    assert refused["is_error"] and "not available to this session" in refused["message"]
    # The owner's separate promotion opens it to future sessions.
    store.promote("numstats")
    assert any(d["name"] == "stats_summary" for d in generated.definitions(other))
    now_ok = run(generated.call(other, "c13", "stats_summary", {"numbers": [1.0, 3.0]}))
    assert not now_ok["is_error"] and json.loads(now_ok["output"])["mean"] == 2.0
    # Revocation still ends it for everyone, immediately.
    store.revoke("numstats")
    assert generated.definitions(runtime) == [] and generated.definitions(other) == []


@needs_bwrap
def test_promotion_requires_activation_first(tmp_path):
    tenant, runtime, store = make(tmp_path)
    payload = json.loads(propose(runtime)["output"])
    with pytest.raises(genplugins.PluginError, match="no active"):
        store.promote("numstats")  # verified-but-never-activated cannot be promoted
    store.activate("numstats", 1, payload["sha256"])
    record = store.promote("numstats")
    assert record["lifetime"] == "durable" and record["status"] == "active"


# ----- the jail is the boundary ----------------------------------------------------------------------------------


@needs_bwrap
def test_the_jail_has_no_network_at_all(tmp_path):
    """Even code that somehow reached the socket module cannot get a packet out: no network namespace."""
    tenant, runtime, store = make(tmp_path)
    reply = run(genplugins.run_jailed(store, ".", 0, {"mode": "probe"}, timeout=10))  # placeholder store paths unused below
    # run_jailed needs a real plugin dir; probe the sandbox directly instead:
    from tura_code.sandbox import Bubblewrap, Mounts
    import shutil, subprocess, tempfile
    scratch = tempfile.mkdtemp()
    sandbox = Bubblewrap(network=False)
    argv = sandbox.wrap([shutil.which("python3"), "-c",
                         "import socket\ntry:\n    socket.create_connection(('127.0.0.1', 9), timeout=2)\n    print('NETWORK_REACHABLE')\nexcept OSError as e:\n    print('NO_NETWORK', type(e).__name__)"],
                        Mounts(workspace=__import__("pathlib").Path(scratch)))
    out = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    assert "NO_NETWORK" in out.stdout and "NETWORK_REACHABLE" not in out.stdout


@needs_bwrap
def test_a_runaway_candidate_is_killed(tmp_path):
    tenant, runtime, store = make(tmp_path)
    payload = json.loads(propose(runtime, tests="def test_ok():\n    pass\n")["output"])
    store.activate("numstats", 1, payload["sha256"])
    plugin_py = store.dir / "numstats" / "1" / "plugin.py"
    original = plugin_py.read_text()
    store.state["numstats"]["versions"][0]["sha256"]  # keep honest state; rewrite files + hash together below
    loop_source = original + "\n"
    loop_source = loop_source.replace('raise ValueError(f"unknown tool {tool}")',
                                      'raise ValueError(f"unknown tool {tool}")\n\n\ndef handle_loop(tool, arguments):\n    while True:\n        pass')
    # rewrite manifest + source and recompute the hash, as a new version would
    plugin_py.write_text(loop_source)
    manifest = json.loads((store.dir / "numstats" / "1" / "plugin.json").read_text())
    manifest["tools"].append({"name": "spin_forever", "description": "never returns", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}})
    (store.dir / "numstats" / "1" / "plugin.json").write_text(json.dumps(manifest))
    files = genplugins.read_files(store.dir / "numstats" / "1")
    store.state["numstats"]["versions"][0]["manifest"] = manifest
    store.state["numstats"]["versions"][0]["sha256"] = genplugins.content_hash(files)
    store._save()
    # replace handle to spin: the candidate maps the new tool to the loop
    plugin_py.write_text(loop_source.replace("def handle(tool, arguments):", "def handle(tool, arguments):\n    if tool == 'spin_forever':\n        return handle_loop(tool, arguments)"))
    files = genplugins.read_files(store.dir / "numstats" / "1")
    store.state["numstats"]["versions"][0]["sha256"] = genplugins.content_hash(files)
    store._save()
    generated = next(t for t in tenant.toolsets if isinstance(t, GeneratedToolset))
    call = run(generated.call(runtime, "c6", "spin_forever", {}))
    # The CPU rlimit kills the spin (SIGKILL) before the wall-clock timeout needs to.
    assert call["is_error"] and ("killed" in call["output"] + call["message"] or "limit" in call["output"] + call["message"])


@needs_bwrap
def test_oversized_output_is_capped(tmp_path):
    tenant, runtime, store = make(tmp_path)
    big = MATH_SOURCE.replace('return {"mean": statistics.mean(numbers), "stdev": statistics.pstdev(numbers), "root_mean": math.sqrt(statistics.mean(n * n for n in numbers))}',
                              'return "x" * 100000')
    payload = json.loads(propose(runtime, source=big, tests="def test_ok():\n    pass\n")["output"])
    store.activate("numstats", 1, payload["sha256"])
    generated = next(t for t in tenant.toolsets if isinstance(t, GeneratedToolset))
    call = run(generated.call(runtime, "c7", "stats_summary", {"numbers": [1.0]}))
    assert not call["is_error"]
    assert len(call["output"]) <= genplugins.MAX_RESULT_CHARS


# ----- fail closed -----------------------------------------------------------------------------------------------


def test_policy_probe_outcomes(monkeypatch):
    """Policy selection is mocked here: binary presence must never be enough, a failed probe or an unapplied limit
    refuses, and only a probe that reads back the exact applied memory.max AND pids.max yields the cgroup prefix.
    There is no policy cache: every execution probes again, and the runner re-checks its own limits per exec."""
    monkeypatch.setattr(genplugins.shutil, "which", lambda name: None)
    prefix, reason = genplugins.execution_policy()
    assert prefix is None and "systemd-run" in reason

    monkeypatch.setattr(genplugins.shutil, "which", lambda name: "/usr/bin/systemd-run")

    class Fake:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

    monkeypatch.setattr(genplugins.subprocess, "run", lambda *a, **k: Fake(1, stderr="dbus gone"))
    prefix, reason = genplugins.execution_policy()
    assert prefix is None and "could not start" in reason

    monkeypatch.setattr(genplugins.subprocess, "run", lambda *a, **k: Fake(0, stdout="max max\n"))  # limits silently not applied
    prefix, reason = genplugins.execution_policy()
    assert prefix is None and "not applied" in reason

    monkeypatch.setattr(genplugins.subprocess, "run", lambda *a, **k: Fake(0, stdout=f"{genplugins.MEMORY_MAX_BYTES} max\n"))  # pids unbounded!
    prefix, reason = genplugins.execution_policy()
    assert prefix is None and "not applied" in reason

    monkeypatch.setattr(genplugins.subprocess, "run", lambda *a, **k: Fake(0, stdout=f"{genplugins.MEMORY_MAX_BYTES} {genplugins.TASKS_MAX}\n"))
    prefix, reason = genplugins.execution_policy()
    assert reason == "" and prefix and "MemoryMax=268435456" in " ".join(prefix) and "TasksMax=64" in " ".join(prefix)


def test_propose_and_invoke_refuse_without_proven_limits(tmp_path, monkeypatch):
    """On a host where bounded execution is unavailable (THIS host included), the whole path refuses:
    nothing is stored, nothing verifies, nothing activates, nothing invokes. Composition tools are unaffected."""
    monkeypatch.setattr(genplugins, "execution_policy", lambda: (None, "no cgroup here (test double)"))
    tenant, runtime, store = make(tmp_path)
    result = propose(runtime)
    assert result["is_error"] and "no cgroup here" in result["output"] + result["message"]
    assert store.state == {} and not store.dir.exists()  # refused before anything was stored
    # An already-active plugin (activated when enforcement existed) refuses to invoke once it is gone.
    store.state["numstats"] = {"active_version": 1, "versions": [{
        "version": 1, "status": "active", "lifetime": "durable", "session": "s1", "sha256": "ab" * 32,
        "manifest": {"tools": MANIFEST}, "created_by": "t", "created_at": "", "verified": None}]}
    store._save()  # tool_owner re-reads from disk
    generated = next(t for t in tenant.toolsets if isinstance(t, GeneratedToolset))
    call = run(generated.call(runtime, "c20", "stats_summary", {"numbers": [1.0]}))
    assert call["is_error"] and "proven aggregate resource limits" in call["message"]


def test_no_bwrap_means_no_verification_no_activation(tmp_path, monkeypatch):
    monkeypatch.setattr(genplugins, "sandbox_problem", lambda: "bubblewrap (`bwrap`) is not installed.")
    tenant, runtime, store = make(tmp_path)
    reply = run(genplugins.run_jailed(store, "numstats", 1, {"mode": "test"}, timeout=5))
    assert reply["ok"] is False and "unavailable" in reply["error"]


@needs_bwrap
def test_listing_reports_status_and_hashes(tmp_path):
    tenant, runtime, store = make(tmp_path)
    propose(runtime)
    listing = store.listing()
    assert listing["numstats"]["versions"][0]["status"] == "verified"
    assert len(listing["numstats"]["versions"][0]["sha256"]) == 64


# ----- the jail's reach: canaries, environment, resource ceilings -------------------------------------------------

CANARY = "canary-7f3e9c1d-host-secret-do-not-read"


@needs_bwrap
def test_host_files_are_invisible_canary(tmp_path):
    """A secret-shaped file on the host (even a world-readable one) is not inside the jail's filesystem: no /etc,
    no /opt, no home, no tenant root, no other temp files - only the read-only source snapshot and the scratch dir."""
    tenant, runtime, store = make(tmp_path)
    canary = tmp_path.parent / (CANARY + ".txt")  # a sibling of the workspace, outside every jail mount
    canary.write_text(CANARY)
    probe = (
        "def handle(tool, arguments):\n"
        "    if tool == 'stats_summary':\n"
        "        targets = ['/etc/hostname', '/etc/passwd', '/opt', '/root', '" + str(canary) + "']\n"
        "        out = {}\n"
        "        for t in targets:\n"
        "            try:\n"
        "                with open(t, 'rb') as fh:\n"
        "                    out[t] = fh.read(64).decode('utf-8', 'replace')\n"
        "            except (OSError, IOError) as e:\n"
        "                out[t] = type(e).__name__\n"
        "        return out\n"
        "    raise ValueError('nope')\n"
    )
    payload = json.loads(propose(runtime, source=probe, tests="def test_ok():\n    pass\n")["output"])
    store.activate("numstats", 1, payload["sha256"])
    generated = next(t for t in tenant.toolsets if isinstance(t, GeneratedToolset))
    call = run(generated.call(runtime, "c8", "stats_summary", {}))
    assert not call["is_error"], call
    result = json.loads(call["output"])
    for target, outcome in result.items():
        assert CANARY not in outcome, (target, outcome)
        assert outcome in ("FileNotFoundError", "PermissionError", "IsADirectoryError"), (target, outcome)


@needs_bwrap
def test_the_jail_environment_carries_no_host_secrets(tmp_path, monkeypatch):
    """The exec environment is exactly PATH/HOME/TMPDIR - asserted at the exec boundary, not by trusting the screen."""
    captured = {}
    original = asyncio.create_subprocess_exec

    async def spy(*argv, **kwargs):
        captured["env"] = kwargs.get("env", {})
        return await original(*argv, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-never-leak")
    monkeypatch.setenv("BROKER_TOKEN", "should-never-leak")
    tenant, runtime, store = make(tmp_path)
    propose(runtime, tests="def test_ok():\n    pass\n")
    env = captured["env"]
    assert set(env) == {"PATH", "HOME", "TMPDIR"}, env
    assert all("leak" not in str(v) for v in env.values())


@needs_bwrap
def test_resource_ceilings_memory_and_filesize(tmp_path):
    """The runner's rlimits, not goodwill, stop exhaustion: a 2GB allocation dies and a 100MB file write dies."""
    tenant, runtime, store = make(tmp_path)
    source = (
        "def handle(tool, arguments):\n"
        "    kind = arguments['kind']\n"
        "    if kind == 'memory':\n"
        "        blob = bytearray(2 * 1024 * 1024 * 1024)\n"
        "        return len(blob)\n"
        "    if kind == 'bigfile':\n"
        "        with open('/tmp/big', 'wb') as fh:\n"
        "            fh.write(b'x' * 100 * 1024 * 1024)\n"
        "        return 'wrote'\n"
        "    raise ValueError(kind)\n"
    )
    payload = json.loads(propose(runtime, source=source, tests="def test_ok():\n    pass\n")["output"])
    store.activate("numstats", 1, payload["sha256"])
    generated = next(t for t in tenant.toolsets if isinstance(t, GeneratedToolset))
    mem = run(generated.call(runtime, "c9", "stats_summary", {"kind": "memory"}))
    assert mem["is_error"], mem  # MemoryError inside the jail, or killed: either way it did not succeed
    big = run(generated.call(runtime, "c10", "stats_summary", {"kind": "bigfile"}))
    assert big["is_error"] and "wrote" not in big["output"]


# ----- end to end through the real app: propose in a session, owner activates over HTTP, session invokes ---------------

import time as _time


@needs_bwrap
def test_e2e_propose_activate_invoke_through_the_real_app(client, workspace, script):
    """The whole governed path through the real HTTP API and runtime, not direct calls: a session proposes
    model-written code (verified in the jail, INERT), the owner activates the exact hash over the owner API, and only
    then can a session invoke it - every invocation a fresh networkless jail."""
    from conftest import of_type, run_turn, text, tool_call

    script([tool_call("p", "propose_tool", {
        "name": "numstats",
        "tools": MANIFEST,
        "source": MATH_SOURCE,
        "tests": MATH_TESTS,
    }), text("proposed")])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        results = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(run_turn(ws, "build the stats tool"), "ToolResult")}
    proposal = json.loads(results["p"]["output"])
    assert proposal["status"] == "verified" and proposal["verification"]["ok"] is True
    # Inert before the owner's act: the registry shows it, but no session can call it.
    listing = client.get("/api/registry/plugins").json()
    assert listing["numstats"]["active_version"] is None
    # Wrong hash refuses; the exact reviewed hash activates (re-verified in the jail at activation time).
    bad = client.post("/api/registry/plugins/numstats/activate", json={"version": 1, "sha256": "0" * 64})
    assert bad.status_code == 409
    ok = client.post("/api/registry/plugins/numstats/activate", json={"version": 1, "sha256": proposal["sha256"]})
    assert ok.status_code == 200, ok.text
    # Activation is session-local: the SAME session can invoke it on its next turn...
    script([tool_call("u", "stats_summary", {"numbers": [2.0, 4.0]}), text("done")])
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        results = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(run_turn(ws, "summarize 2 and 4"), "ToolResult")}
    assert results["u"]["is_error"] is False
    assert json.loads(results["u"]["output"])["mean"] == 3.0
    # ...but a NEW session cannot, until the owner promotes it.
    script([text("no tools needed")])
    session2 = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{session2}/ws") as ws:
        ws.receive_json()
        events2 = run_turn(ws, "summarize 1 and 3")
    assert not any(e["payload"].get("name") == "stats_summary" for e in events2 if e.get("type") == "ToolCall"), events2
    assert client.post("/api/registry/plugins/numstats/promote", json={}).status_code == 200
    script([tool_call("u2", "stats_summary", {"numbers": [1.0, 3.0]}), text("done")])
    with client.websocket_connect(f"/api/sessions/{session2}/ws") as ws:
        ws.receive_json()
        results2 = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(run_turn(ws, "summarize 1 and 3"), "ToolResult")}
    assert results2["u2"]["is_error"] is False
    assert json.loads(results2["u2"]["output"])["mean"] == 2.0
    # Revocation over the owner API takes the tool away immediately.
    assert client.post("/api/registry/plugins/numstats/revoke").status_code == 200
    assert client.get("/api/registry/plugins").json()["numstats"]["active_version"] is None


# --- synthetic-cgroupfs verification of the in-jail self-check (no-cost route on hosts WITHOUT cgroup support) ------
#
# Honest label: these tests verify the self-check CODE PATH end-to-end inside a real bubblewrap namespace jail -
# the real genplugin_runner.check_cgroup, reading a real /proc/self/cgroup (0::/ under a cgroup namespace) and a
# synthetic /sys/fs/cgroup bind-mounted read-only. They prove the runner refuses wrong/absent limits and accepts
# exact ones. They do NOT prove any host enforces those limits: that still requires a cgroup-capable host, and the
# bounded-execution suite stays skip-labeled here.

import os as _os
import shutil as _shutil
from pathlib import Path as _Path
import subprocess as _subprocess

import pytest as _pytest


def _synthetic_cgroup_run(tmp_path, memory_max, pids_max, expected):
    fake = tmp_path / "fake-cgroupfs"
    fake.mkdir()
    (fake / "memory.max").write_text(memory_max)
    (fake / "pids.max").write_text(pids_max)
    runner = _Path(genplugins.__file__).with_name("genplugin_runner.py")
    argv = ["bwrap", "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-cgroup-try", "--unshare-net"]
    for path in genplugins.JAIL_SYSTEM_DIRS:
        if _os.path.islink(path):
            argv += ["--symlink", _os.readlink(path), path]
        elif _os.path.isdir(path):
            argv += ["--ro-bind", path, path]
    argv += ["--proc", "/proc", "--dir", "/sys", "--dir", "/sys/fs",
             "--ro-bind", str(fake), "/sys/fs/cgroup", "--ro-bind", str(runner), str(runner), "--",
             _shutil.which("python3"), "-c",
             "import sys; sys.path.insert(0, %r); from genplugin_runner import check_cgroup; check_cgroup(*sys.argv[1:])" % str(runner.parent),
             *expected]
    return _subprocess.run(argv, capture_output=True, text=True, timeout=30)


@_pytest.mark.skipif(bool(genplugins.sandbox_problem()), reason=genplugins.sandbox_problem() or "ok")
def test_in_jail_self_check_accepts_the_exact_probed_limits(tmp_path):
    result = _synthetic_cgroup_run(tmp_path, "268435456\n", "64\n", ["268435456", "64"])
    assert result.returncode == 0, result.stderr


@_pytest.mark.skipif(bool(genplugins.sandbox_problem()), reason=genplugins.sandbox_problem() or "ok")
def test_in_jail_self_check_refuses_an_unbounded_pids_limit(tmp_path):
    result = _synthetic_cgroup_run(tmp_path, "268435456\n", "max\n", ["268435456", "64"])
    assert result.returncode != 0 and "refusing to run candidate code" in result.stderr


@_pytest.mark.skipif(bool(genplugins.sandbox_problem()), reason=genplugins.sandbox_problem() or "ok")
def test_in_jail_self_check_refuses_a_wrong_memory_limit(tmp_path):
    result = _synthetic_cgroup_run(tmp_path, "536870912\n", "64\n", ["268435456", "64"])
    assert result.returncode != 0 and "refusing to run candidate code" in result.stderr


@_pytest.mark.skipif(bool(genplugins.sandbox_problem()), reason=genplugins.sandbox_problem() or "ok")
def test_in_jail_self_check_refuses_when_the_cgroupfs_is_absent(tmp_path):
    # No /sys/fs/cgroup mount at all: the runner must refuse, not proceed unbounded.
    runner = _Path(genplugins.__file__).with_name("genplugin_runner.py")
    argv = ["bwrap", "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-cgroup-try", "--unshare-net"]
    for path in genplugins.JAIL_SYSTEM_DIRS:
        if _os.path.islink(path):
            argv += ["--symlink", _os.readlink(path), path]
        elif _os.path.isdir(path):
            argv += ["--ro-bind", path, path]
    argv += ["--proc", "/proc", "--ro-bind", str(runner), str(runner), "--",
             _shutil.which("python3"), "-c",
             "import sys; sys.path.insert(0, %r); from genplugin_runner import check_cgroup; check_cgroup('268435456', '64')" % str(runner.parent)]
    result = _subprocess.run(argv, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0 and ("unreadable" in result.stderr or "refusing" in result.stderr)


# ----- the owner's review surface: see exactly what activation would run -------------------------------------------

from conftest import of_type, requires_runtime, run_turn, text, tool_call  # noqa: E402


def test_review_returns_the_exact_files_hash_and_execution_truth(tmp_path, monkeypatch):
    """Direct store level: review recomputes the on-disk hash, exposes every file, and reports execution
    availability honestly. Verification is stubbed here; the real jail path is covered (or skip-labeled) elsewhere."""
    monkeypatch.setattr(genplugins, "execution_policy", lambda: (["true"], ""))
    tenant, runtime, store = make(tmp_path)
    store.propose("numstats", {"plugin.py": MATH_SOURCE, "plugin.json": json.dumps({"tools": MANIFEST}), "tests.py": MATH_TESTS},
                  created_by="model", session="s1")
    store.record_verification("numstats", 1, {"ok": True, "results": [{"name": "test_mean", "ok": True}]})
    review = store.review("numstats")
    assert review["files"]["plugin.py"] == MATH_SOURCE and review["files"]["tests.py"] == MATH_TESTS
    assert review["sha256"] == review["on_disk_sha256"] and review["hash_matches_record"] is True
    assert review["tools"][0]["name"] == "stats_summary"
    assert review["execution"]["available"] is True
    assert review["sha256"] in review["activation"]
    with pytest.raises(genplugins.PluginError):
        store.review("numstats", 9)
    with pytest.raises(genplugins.PluginError):
        store.review("nope")


def test_review_exposes_on_disk_tampering_and_blocks_stale_activation(tmp_path, monkeypatch):
    """Stale review must never authorize changed code: after the files change on disk, review says so and the
    previously reviewed hash no longer activates."""
    monkeypatch.setattr(genplugins, "execution_policy", lambda: (["true"], ""))
    tenant, runtime, store = make(tmp_path)
    store.propose("numstats", {"plugin.py": MATH_SOURCE, "plugin.json": json.dumps({"tools": MANIFEST}), "tests.py": MATH_TESTS},
                  created_by="model", session="s1")
    store.record_verification("numstats", 1, {"ok": True, "results": [{"name": "test_mean", "ok": True}]})
    reviewed_hash = store.review("numstats")["sha256"]
    target = store.dir / "numstats" / "1" / "plugin.py"
    target.write_text(MATH_SOURCE + "\n# tampered after review\n", "utf-8")
    stale = store.review("numstats")
    assert stale["hash_matches_record"] is False and stale["on_disk_sha256"] != reviewed_hash
    with pytest.raises(genplugins.PluginError, match="no longer match"):
        store.activate("numstats", 1, reviewed_hash)


@requires_runtime
def test_review_endpoint_through_the_real_app(client, workspace, script, monkeypatch, home):
    """The owner flow over HTTP: propose in a session, GET the review, activate with the reviewed hash, and a
    404 for unknown plugins. Verification is stubbed (this host has no cgroup enforcement); the hash gates are real."""
    monkeypatch.setattr(genplugins, "execution_policy", lambda: (["true"], ""))

    async def fake_jailed(store, name, version, payload, *, timeout, expected_sha256=None):
        return {"ok": True, "results": [{"name": "test_mean", "ok": True}], "isolation": "verification stubbed in this test"}

    monkeypatch.setattr(genplugins, "run_jailed", fake_jailed)
    script([tool_call("p", "propose_tool", {"name": "numstats", "tools": MANIFEST, "source": MATH_SOURCE, "tests": MATH_TESTS}), text("proposed")])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        results = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(run_turn(ws, "build the stats tool"), "ToolResult")}
    assert json.loads(results["p"]["output"])["status"] == "verified"
    assert client.get("/api/registry/plugins/nope/review").status_code == 404
    review = client.get("/api/registry/plugins/numstats/review").json()
    assert review["status"] == "verified" and review["hash_matches_record"] is True
    assert review["files"]["plugin.py"] == MATH_SOURCE
    activated = client.post("/api/registry/plugins/numstats/activate", json={"version": 1, "sha256": review["sha256"]})
    assert activated.status_code == 200, activated.text
    # On-disk tamper after review: the next review reports it and the old hash can no longer activate a re-review cycle.
    target = home / "registry" / "plugins" / "numstats" / "1" / "plugin.py"
    target.write_text(MATH_SOURCE + "\n# tampered\n", "utf-8")
    stale = client.get("/api/registry/plugins/numstats/review").json()
    assert stale["hash_matches_record"] is False
