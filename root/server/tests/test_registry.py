"""The registry: create_tool / create_role as declarative data under deterministic rules. No provider, no cost."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from tura_code import catalogue, guides, jobs, registry


class FakeToolset:
    def __init__(self, plugin, names, fail_on=None):
        self.plugin = plugin
        self.names = names
        self.calls = []
        self.fail_on = fail_on or set()

    def definitions(self, runtime):
        return [{"name": n, "description": f"{n.replace('_', ' ')}", "parameters": {"type": "object"}} for n in self.names]

    def handles(self, name):
        return name in self.names

    async def call(self, runtime, call_id, name, arguments):
        self.calls.append((name, arguments))
        if name in self.fail_on:
            return {"is_error": True, "output": "", "message": f"{name} failed", "display": []}
        return {"is_error": False, "output": f"{name} got {json.dumps(arguments, sort_keys=True)}", "message": "", "display": []}


def make(tmp_path, scope=(), dynamic=False):
    """A fake tenant with two host toolsets, the gateway and a real registry on disk."""
    reg = registry.Registry(tmp_path)
    toolsets = [FakeToolset(None, ["process"]), FakeToolset("documents", ["read_pdf"]), FakeToolset("github", ["github_comment"])]
    holder = SimpleNamespace(value=SimpleNamespace(tool_scope=list(scope), dynamic_tools=dynamic, disabled_plugins=[]))
    tenant = SimpleNamespace(settings=SimpleNamespace(get=lambda: holder.value), toolsets=toolsets, registry=reg)
    tenant.active_toolsets = lambda: tenant.toolsets
    tenant.toolsets += [catalogue.GatewayToolset(), registry.RegistryToolset(), registry.CompositeToolset(reg)]
    runtime = SimpleNamespace(tenant=tenant, record=SimpleNamespace(job_role="", id="s1", tool_scope=list(scope)))
    return tenant, runtime, reg, toolsets


def run(coro):
    return asyncio.run(coro)


def spec(**over):
    base = {
        "name": "pdf_digest",
        "description": "Read a PDF and file a digest comment.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "pr": {"type": "integer"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "steps": [
            {"tool": "read_pdf", "arguments": {"path": "${path}"}},
            {"tool": "github_comment", "arguments": {"pr": "${pr}", "body": "digest of ${path}"}},
        ],
    }
    base.update(over)
    return base


# ----- static validation -------------------------------------------------------------------------------------


def test_schema_subset_is_enforced():
    assert registry.validate_schema({"type": "object", "properties": {}, "additionalProperties": False}) == []
    assert registry.validate_schema({"type": "object", "properties": {}})  # open schema refused
    assert registry.validate_schema({"type": "array"})
    assert registry.validate_schema({"type": "object", "properties": {"X": {"type": "string"}}})
    assert registry.validate_schema({"type": "object", "properties": {"a": {"type": "string"}}, "required": ["b"], "additionalProperties": False})
    assert registry.validate_schema({"type": "object", "properties": {"a": {"type": "frob"}}, "additionalProperties": False})
    assert registry.validate_schema({"type": "object", "properties": {}, "additionalProperties": False, "$schema": "x"})


def test_argument_validation_is_deterministic():
    schema = spec()["parameters"]
    assert registry.validate_arguments(schema, {"path": "a.pdf", "pr": 3}) == []
    assert registry.validate_arguments(schema, {"pr": 3}) == ["missing required `path`"]
    assert "unexpected argument `x`" in registry.validate_arguments(schema, {"path": "a", "x": 1})[0]
    assert registry.validate_arguments(schema, {"path": 3})
    assert registry.validate_arguments(schema, {"path": "a", "pr": True})  # bool is not int


def test_templates(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    problems = registry.validate_templates({"path": "${nope}"}, {"path"})
    assert problems and "not a declared parameter" in problems[0]
    assert registry.render_templates({"pr": "${pr}", "body": "digest of ${path}"}, {"pr": 7, "path": "a.pdf"}) == {"pr": 7, "body": "digest of a.pdf"}


def test_executable_code_and_network_specs_are_refused(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    for field in ("code", "command", "shell", "script", "url"):
        record, problems = reg.create_tool(spec(**{field: "x = 1"}), existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
        assert record is None and any(field in p for p in problems)


def test_name_collisions_and_reserved_names_refused(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    for bad in ("Shell", "find_tools", "create_tool", "read_pdf", "BadName", "x"):
        record, problems = reg.create_tool(spec(name=bad), existing_names={"read_pdf"}, host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
        assert record is None, bad


def test_step_rules(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    hosts = {"read_pdf", "github_comment", "process"}
    record, problems = reg.create_tool(spec(steps=[{"tool": "Shell", "arguments": {}}]), existing_names=set(), host_tool_names=hosts, created_by="t", session="s1")
    assert record is None and "not an existing host tool" in problems[0]  # native tools are not pipeline steps
    for bad in ("find_tools", "create_tool"):
        record, problems = reg.create_tool(spec(steps=[{"tool": bad, "arguments": {}}]), existing_names=set(), host_tool_names=hosts | {bad}, created_by="t", session="s1")
        assert record is None and "cannot be a pipeline step" in problems[0], bad
    record, problems = reg.create_tool(spec(steps=[{"tool": "read_pdf", "arguments": {"p": "${missing}"}}]), existing_names=set(), host_tool_names=hosts, created_by="t", session="s1")
    assert record is None and "not a declared parameter" in problems[0]
    record, problems = reg.create_tool(spec(steps=[{"tool": "read_pdf", "arguments": {}}] * 9), existing_names=set(), host_tool_names=hosts, created_by="t", session="s1")
    assert record is None and any("limit" in p for p in problems)
    reg.create_tool(spec(), existing_names=set(), host_tool_names=hosts, created_by="t", session="s1")
    record, problems = reg.create_tool(spec(name="self_eater", steps=[{"tool": "pdf_digest", "arguments": {}}]), existing_names=set(), host_tool_names=hosts, created_by="t", session="s1")
    assert record is None and "cannot be a pipeline step" in problems[0]  # a composite is never a step, even itself


# ----- lifecycle: draft -> tested/activated -> active -> revoked ---------------------------------------------


def test_created_tool_is_active_in_its_session_and_nowhere_else(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    record, problems = reg.create_tool(spec(), existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
    assert not problems and record["status"] == "active" and record["lifetime"] == "session"
    composite = registry.CompositeToolset(reg)
    [definition] = composite.definitions(runtime)  # visible to the creating session at once
    assert definition["name"] == "pdf_digest" and "v1" in definition["description"]
    other = SimpleNamespace(tenant=tenant, record=SimpleNamespace(job_role="", id="s2", tool_scope=[]))
    assert composite.definitions(other) == []  # future/other sessions do not see it
    result = run(composite.call(other, "c1", "pdf_digest", {"path": "a.pdf"}))
    assert result["is_error"] and "not available to this session" in result["message"]
    reg.promote_tool("pdf_digest")  # the owner's explicit choice
    assert composite.definitions(other) and not run(composite.call(other, "c2", "pdf_digest", {"path": "a.pdf", "pr": 7}))["is_error"]


def test_run_tests_activates_only_when_they_pass(tmp_path):
    tenant, runtime, reg, toolsets = make(tmp_path)
    documents = toolsets[1]
    with_tests = spec(tests=[{"arguments": {"path": "a.pdf", "pr": 1}}])
    record, _ = reg.create_tool(with_tests, existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
    assert record["status"] == "active" and record["activation"] == "session"  # session-active before any test runs
    report = run(registry.run_tests(tenant, runtime, "t1", {**record, "name": "pdf_digest"}, reg))
    assert report["ok"] and len(documents.calls) == 1  # the pipeline really ran
    updated = reg.record_test_report("pdf_digest", record["version"], report, activate=report["ok"])
    assert updated["status"] == "active" and updated["activation"] == "tests"

    reg2_tools = registry.Registry(tmp_path / "other")
    failing = FakeToolset("documents", ["read_pdf"], fail_on={"read_pdf"})
    tenant2 = SimpleNamespace(settings=tenant.settings, toolsets=[failing], registry=reg2_tools)
    tenant2.active_toolsets = lambda: tenant2.toolsets
    runtime2 = SimpleNamespace(tenant=tenant2, record=runtime.record)
    record2, _ = reg2_tools.create_tool(spec(steps=[{"tool": "read_pdf", "arguments": {}}], tests=[{"arguments": {"path": "a.pdf", "pr": 1}}]),
                                        existing_names=set(), host_tool_names={"read_pdf"}, created_by="t", session="s1")
    report2 = run(registry.run_tests(tenant2, runtime2, "t2", {**record2, "name": "pdf_digest"}, reg2_tools))
    assert not report2["ok"]
    updated2 = reg2_tools.record_test_report("pdf_digest", record2["version"], report2, activate=report2["ok"])
    assert updated2["status"] == "draft"  # failing tests take it back to draft even in its own session, report kept
    assert reg2_tools.active_tool("pdf_digest", "s1") is None
    with pytest.raises(registry.RegistryError, match="only an active tool"):
        reg2_tools.promote_tool("pdf_digest")  # promotion never launders a known failure


def test_create_tool_through_the_management_tool(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    toolset = registry.RegistryToolset()
    bad = run(toolset.call(runtime, "c1", "create_tool", {**spec(), "code": "print(1)"}))
    assert bad["is_error"] and "executable code" in bad["message"]
    good = run(toolset.call(runtime, "c2", "create_tool", spec()))
    assert not good["is_error"]
    created = json.loads(good["output"])
    assert created["status"] == "active" and created["lifetime"] == "session" and created["session"] == "s1"
    listing = run(toolset.call(runtime, "c3", "registry_list", {}))
    assert "pdf_digest" in listing["output"]


# ----- execution ------------------------------------------------------------------------------------------------


def test_pipeline_runs_steps_in_order_with_rendered_arguments(tmp_path):
    tenant, runtime, reg, toolsets = make(tmp_path)
    record, _ = reg.create_tool(spec(), existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
    composite = registry.CompositeToolset(reg)
    result = run(composite.call(runtime, "c1", "pdf_digest", {"path": "a.pdf", "pr": 7}))
    assert not result["is_error"], result
    assert toolsets[1].calls == [("read_pdf", {"path": "a.pdf"})]
    assert toolsets[2].calls == [("github_comment", {"pr": 7, "body": "digest of a.pdf"})]  # ${pr} kept its int type
    assert "step 0" in result["output"] and "step 1" in result["output"]


def test_pipeline_stops_on_step_error_and_reports_the_step(tmp_path):
    tenant, runtime, reg, toolsets = make(tmp_path)
    toolsets[1].fail_on.add("read_pdf")
    record, _ = reg.create_tool(spec(), existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
    result = run(registry.CompositeToolset(reg).call(runtime, "c1", "pdf_digest", {"path": "a.pdf", "pr": 7}))
    assert result["is_error"] and "step 0" in result["message"]
    assert toolsets[2].calls == []  # the second step never ran
    assert json.loads(result["output"])["failed_at_step"] == 0


def test_every_step_repasses_the_sessions_scope(tmp_path):
    tenant, runtime, reg, toolsets = make(tmp_path, scope=["read_pdf"])  # github_comment NOT in scope
    record, _ = reg.create_tool(spec(), existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
    result = run(registry.CompositeToolset(reg).call(runtime, "c1", "pdf_digest", {"path": "a.pdf", "pr": 7}))
    assert result["is_error"] and "outside this session's tool scope" in result["message"]
    assert toolsets[1].calls == [("read_pdf", {"path": "a.pdf"})]  # in-scope step ran
    assert toolsets[2].calls == []  # out-of-scope step refused by ordinary code, not by asking the model


def test_call_arguments_are_validated_against_the_schema(tmp_path):
    tenant, runtime, reg, toolsets = make(tmp_path)
    record, _ = reg.create_tool(spec(), existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
    result = run(registry.CompositeToolset(reg).call(runtime, "c1", "pdf_digest", {"pr": 7}))
    assert result["is_error"] and "missing required `path`" in result["message"]
    assert toolsets[1].calls == []  # nothing ran on bad input


def test_persistence_roundtrip(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    reg.create_tool(spec(), existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
    reg.create_role({"name": "docs_reviewer", "description": "reviews docs", "tools": ["ReadFile", "read_pdf"]},
                    universe={"ReadFile", "read_pdf"}, guide_names=set(), builtin_roles=set(jobs.ROLES), created_by="t", session="s1")
    reloaded = registry.Registry(tmp_path)
    assert reloaded.active_tool("pdf_digest", "s1")["version"] == 1
    assert reloaded.active_role("docs_reviewer")["tools"] == ["ReadFile", "read_pdf"]


def test_revoke_takes_a_tool_out_of_dispatch(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    reg.create_tool(spec(), existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
    composite = registry.CompositeToolset(reg)
    assert composite.handles("pdf_digest")
    reg.set_tool_status("pdf_digest", "revoked")
    assert not composite.handles("pdf_digest") and composite.definitions(runtime) == []


def test_versions_accumulate_and_a_fresh_session_version_never_hides_a_promoted_one(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    reg.create_tool(spec(), existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
    reg.promote_tool("pdf_digest", version=1)  # v1 durable
    v2, _ = reg.create_tool(spec(description="v2"), existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
    assert v2["version"] == 2 and v2["lifetime"] == "session"
    assert reg.active_tool("pdf_digest", "s1")["description"] == "v2"  # the creating session gets its new version
    assert reg.active_tool("pdf_digest", "s2")["description"] == "Read a PDF and file a digest comment."  # everyone else keeps the durable one


# ----- roles -------------------------------------------------------------------------------------------------------


def role_spec(**over):
    base = {"name": "docs_reviewer", "description": "Reads documents and reviews them with evidence.", "tools": ["ReadFile", "Glob", "read_pdf"], "guides": ["evidence"]}
    base.update(over)
    return base


def test_create_role_validation(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    universe = registry.role_universe(tenant, runtime, reg)
    guide_names = set(guides.GUIDES)
    record, problems = reg.create_role(role_spec(), universe=universe, guide_names=guide_names, builtin_roles=set(jobs.ROLES), created_by="t", session="s1")
    assert not problems and record["status"] == "active" and record["version"] == 1
    record, problems = reg.create_role(role_spec(name="developer"), universe=universe, guide_names=guide_names, builtin_roles=set(jobs.ROLES), created_by="t", session="s1")
    assert record is None and "built-in role" in problems[0]
    record, problems = reg.create_role(role_spec(name="bad_scope", tools=["ReadFile", "nope_tool"]), universe=universe, guide_names=guide_names, builtin_roles=set(jobs.ROLES), created_by="t", session="s1")
    assert record is None and "not a known tool name" in problems[0]
    record, problems = reg.create_role(role_spec(name="bad_guides", guides=["made_up_guide"]), universe=universe, guide_names=guide_names, builtin_roles=set(jobs.ROLES), created_by="t", session="s1")
    assert record is None and "existing guides" in problems[0]


def test_create_role_flags_code_runners_honestly(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    universe = registry.role_universe(tenant, runtime, reg)
    record, _ = reg.create_role(role_spec(tools=["ReadFile", "Shell"], guides=[]), universe=universe, guide_names=set(), builtin_roles=set(jobs.ROLES), created_by="t", session="s1")
    assert record["code_runners"] == ["Shell"] and "bounds nothing" in record["scope_note"]


def test_a_role_cannot_name_a_draft_composite(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    made, _ = reg.create_tool(spec(), existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
    reg.record_test_report("pdf_digest", made["version"], {"ok": False, "results": []}, activate=False)  # demoted to draft
    universe = registry.role_universe(tenant, runtime, reg)
    record, problems = reg.create_role(role_spec(tools=["ReadFile", "pdf_digest"], guides=[]), universe=universe, guide_names=set(), builtin_roles=set(jobs.ROLES), created_by="t", session="s1")
    assert record is None and "not active" in problems[0]
    reg.set_tool_status("pdf_digest", "active", activation="owner")
    universe = registry.role_universe(tenant, runtime, reg)
    record, problems = reg.create_role(role_spec(tools=["ReadFile", "pdf_digest"], guides=[]), universe=universe, guide_names=set(), builtin_roles=set(jobs.ROLES), created_by="t", session="s1")
    assert not problems


def test_role_versions_and_revoke(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    universe = registry.role_universe(tenant, runtime, reg)
    reg.create_role(role_spec(), universe=universe, guide_names=set(guides.GUIDES), builtin_roles=set(jobs.ROLES), created_by="t", session="s1")
    v2, _ = reg.create_role(role_spec(tools=["ReadFile"]), universe=universe, guide_names=set(guides.GUIDES), builtin_roles=set(jobs.ROLES), created_by="t", session="s1")
    assert v2["version"] == 2 and reg.active_role("docs_reviewer")["version"] == 2  # newest version is active
    reg.set_role_status("docs_reviewer", "revoked")
    assert reg.active_role("docs_reviewer") is None


# ----- jobs integration ---------------------------------------------------------------------------------------------


def test_resolve_role_builtin_registry_and_unknown(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    resolved = jobs.resolve_role(tenant, "reviewer")
    assert resolved["scope"] == jobs.ROLES["reviewer"] and resolved["version"] is None
    universe = registry.role_universe(tenant, runtime, reg)
    reg.create_role(role_spec(), universe=universe, guide_names=set(guides.GUIDES), builtin_roles=set(jobs.ROLES), created_by="t", session="s1")
    resolved = jobs.resolve_role(tenant, "docs_reviewer", source_session="s1")
    assert resolved["version"] == 1 and "read_pdf" in resolved["scope"]
    with pytest.raises(jobs.JobError):
        jobs.resolve_role(tenant, "wizard")


def test_resolve_role_fails_when_a_named_composite_was_revoked(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    reg.create_tool(spec(), existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
    reg.promote_tool("pdf_digest")
    universe = registry.role_universe(tenant, runtime, reg)
    reg.create_role(role_spec(tools=["ReadFile", "pdf_digest"]), universe=universe, guide_names=set(guides.GUIDES), builtin_roles=set(jobs.ROLES), created_by="t", session="s1")
    assert jobs.resolve_role(tenant, "docs_reviewer", source_session="s1")["scope"]
    reg.set_tool_status("pdf_digest", "revoked")
    with pytest.raises(jobs.JobError, match="no longer active"):
        jobs.resolve_role(tenant, "docs_reviewer", source_session="s1")  # fails at job start, not mid-job


def test_guides_for_registry_role(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    universe = registry.role_universe(tenant, runtime, reg)
    reg.create_role(role_spec(), universe=universe, guide_names=set(guides.GUIDES), builtin_roles=set(jobs.ROLES), created_by="t", session="s1")
    assert guides.guides_for("docs_reviewer", tenant) == ["evidence"]
    assert "evidence" in guides.index_for("docs_reviewer", tenant)
    assert guides.guides_for("docs_reviewer") == []  # without the tenant, nothing leaks


# ----- the owner API ---------------------------------------------------------------------------------------------------


def test_owner_api_lists_activates_and_revokes(client, tmp_path):
    server = client.app.state.root.server
    tenant = server.tenant()
    reg = tenant.registry
    reg.create_tool(spec(), existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
    reg.create_role(role_spec(), universe={"ReadFile", "Glob", "read_pdf"}, guide_names=set(guides.GUIDES), builtin_roles=set(jobs.ROLES), created_by="t", session="s1")

    listing = client.get("/api/registry").json()
    created = listing["tools"]["pdf_digest"]["versions"][0]
    assert created["status"] == "active" and created["lifetime"] == "session"  # active for its session, not the catalogue
    assert listing["roles"]["docs_reviewer"]["active_version"] == 1

    roles = client.get("/api/jobs/roles").json()
    assert "reviewer" in roles["roles"] and roles["registry_roles"] == {}  # nothing joins the standing catalogue by default

    promoted = client.post("/api/registry/tools/pdf_digest/promote").json()
    assert promoted["lifetime"] == "durable"
    assert client.post("/api/registry/roles/docs_reviewer/promote").status_code == 200
    assert client.get("/api/jobs/roles").json()["registry_roles"]["docs_reviewer"]["version"] == 1

    revoked = client.post("/api/registry/tools/pdf_digest/revoke").json()
    assert revoked["status"] == "revoked"
    assert client.post("/api/registry/roles/docs_reviewer/revoke").status_code == 200
    assert client.get("/api/jobs/roles").json()["registry_roles"] == {}
    assert client.post("/api/registry/tools/nope/promote").status_code == 409


# ----- through the real runtime with the scripted model (no provider, no cost) ---------------------------------------

from conftest import of_type, requires_runtime, run_turn, text, tool_call  # noqa: E402


@requires_runtime
def test_create_then_use_a_tool_in_one_real_session(client, workspace, script):
    """The scripted model creates a composite and calls it in the same session: real wire, real dispatch, real steps."""
    create_args = {
        "name": "process_digest",
        "description": "List background processes, then list them again as a digest.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        "steps": [{"tool": "process", "arguments": {"action": "list"}}],
    }
    script([
        tool_call("c", "create_tool", create_args),
        tool_call("u", "process_digest", {}),
        text("done"),
    ])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        results = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(run_turn(ws, "make and use a tool"), "ToolResult")}
    created = results["c"]
    assert created["is_error"] is False and '"status": "active"' in created["output"] and '"lifetime": "session"' in created["output"]
    # The runtime's tool list is fixed at initialize, so a mid-turn call to a tool created THIS turn is refused by the
    # runtime itself; from the next turn the host re-initializes with it registered (same as connecting a plugin).
    assert results["u"]["is_error"] is True
    # A new script for turn two: a re-initialized runtime process reads the script from its start.
    script([tool_call("u2same", "process_digest", {}), text("done")])
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        results = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(run_turn(ws, "use it now"), "ToolResult")}
    used = results["u2same"]
    assert used["is_error"] is False and "step 0" in used["output"]  # the pipeline really ran through dispatch
    # A second session must not see the session-scoped tool.
    script([tool_call("u2", "process_digest", {}), text("done")])
    other_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{other_id}/ws") as ws:
        ws.receive_json()
        results2 = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(run_turn(ws, "use the tool"), "ToolResult")}
    assert results2["u2"]["is_error"] is True
    # The owner promotes it; a THIRD session can now call it.
    assert client.post("/api/registry/tools/process_digest/promote").status_code == 200
    script([tool_call("u3", "process_digest", {}), text("done")])
    third_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{third_id}/ws") as ws:
        ws.receive_json()
        results3 = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(run_turn(ws, "use the tool"), "ToolResult")}
    assert results3["u3"]["is_error"] is False


@requires_runtime
def test_create_role_then_start_a_job_with_it(client, workspace, script):
    """A scripted session creates a role; the owner starts a job with it; the job runs under that exact scope."""
    script([
        tool_call("r", "create_role", {"name": "process_watcher", "description": "Watches background processes and reports.", "tools": ["ReadFile", "Glob", "process"], "contract": ["summary"]}),
        text("done"),
    ])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        results = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(run_turn(ws, "make a role"), "ToolResult")}
    assert results["r"]["is_error"] is False and '"lifetime": "session"' in results["r"]["output"]
    # Session-lifetime roles do not join the standing catalogue, but the owner can start a job with one at once.
    assert client.get("/api/jobs/roles").json()["registry_roles"] == {}
    script([text('{"summary": "no processes"}')])
    # ...but only when the owner names the creating session explicitly: an ambiguous bare name refuses.
    refused = client.post("/api/jobs", json={"goal": "report processes", "workspace": str(workspace), "role": "process_watcher"})
    assert refused.status_code != 200 and "source_session" in refused.text
    created = client.post("/api/jobs", json={"goal": "report processes", "workspace": str(workspace), "role": "process_watcher", "source_session": session_id})
    assert created.status_code == 200, created.text
    item = created.json()
    assert item["role_version"] == 1 and item["scope_guarantee"]["level"] == "none"  # `process` is a code runner: honest guarantee
    end = __import__("time").time() + 30
    while __import__("time").time() < end and client.get(f"/api/jobs/{item['id']}").json()["status"] == "running":
        __import__("time").sleep(0.2)
    item = client.get(f"/api/jobs/{item['id']}").json()
    assert item["status"] != "running" and item["result"]["contract"]["ok"]
    assert item["result"]["contract"]["data"]["summary"] == "no processes"


# ----- adversarial and boundary cases (review-driven) ----------------------------------------------------------


def test_template_substitution_is_not_recursive(tmp_path):
    # A call argument that LOOKS like a template is data, not a template: it is inserted, never re-scanned.
    rendered = registry.render_templates({"body": "${path}"}, {"path": "${pr}", "pr": 1})
    assert rendered == {"body": "${pr}"}


def test_oversized_step_output_is_truncated(tmp_path):
    tenant, runtime, reg, toolsets = make(tmp_path)

    class Loud(FakeToolset):
        async def call(self, runtime, call_id, name, arguments):
            return {"is_error": False, "output": "x" * 100_000, "message": "", "display": []}

    toolsets[1] = Loud("documents", ["read_pdf"])
    record, _ = reg.create_tool(spec(steps=[{"tool": "read_pdf", "arguments": {}}, {"tool": "github_comment", "arguments": {"body": "b"}}]),
                                existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
    result = run(registry.CompositeToolset(reg).call(runtime, "c1", "pdf_digest", {"path": "a"}))
    assert not result["is_error"] and len(result["output"]) <= 2 * registry.MAX_OUTPUT_CHARS + 200


def test_a_spec_cannot_smuggle_lifecycle_or_session_fields(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    for field in ("lifetime", "session", "status", "activation", "active_version"):
        record, problems = reg.create_tool(spec(**{field: "durable"}), existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
        assert record is None and any("unsupported spec fields" in p for p in problems), field
    # ...and the owner API takes names, not session ids: a caller cannot steer another session's entries.


def test_a_scoped_session_cannot_author_a_role_more_powerful_than_itself(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path, scope=["ReadFile", "Glob"])
    ceiling = registry.role_creator_ceiling(tenant, runtime, reg)
    assert ceiling is not None and "Shell" not in ceiling and "github_comment" not in ceiling
    record, problems = reg.create_role(role_spec(tools=["ReadFile", "Shell"], guides=[]), universe=registry.role_universe(tenant, runtime, reg),
                                       guide_names=set(), builtin_roles=set(jobs.ROLES), created_by="t", session="s1", ceiling=ceiling)
    assert record is None and "more powerful than itself" in problems[0]
    record, problems = reg.create_role(role_spec(tools=["ReadFile", "Glob"], guides=[]), universe=registry.role_universe(tenant, runtime, reg),
                                       guide_names=set(), builtin_roles=set(jobs.ROLES), created_by="t", session="s1", ceiling=ceiling)
    assert not problems
    # An owner session (empty scope) is unrestricted.
    tenant2, runtime2, reg2, _ = make(tmp_path / "other")
    assert registry.role_creator_ceiling(tenant2, runtime2, reg2) is None


def test_creation_emits_a_deterministic_owner_visible_event(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    events = []
    runtime.emit = lambda kind, payload, persist=True: events.append((kind, payload))
    run(registry.RegistryToolset().call(runtime, "c1", "create_tool", spec()))
    run(registry.RegistryToolset().call(runtime, "c2", "create_role", role_spec()))
    kinds = [k for k, _ in events]
    assert kinds == ["RegistryChanged", "RegistryChanged"]
    assert events[0][1]["name"] == "pdf_digest" and events[0][1]["lifetime"] == "session"
    assert events[1][1]["kind"] == "role" and events[1][1]["name"] == "docs_reviewer"


def test_revocation_mid_pipeline_and_version_pinning(tmp_path):
    tenant, runtime, reg, toolsets = make(tmp_path)
    record, _ = reg.create_tool(spec(), existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
    composite = registry.CompositeToolset(reg)

    original = registry.Registry.active_tool

    def revoke_midway(self, name, session=None):  # the revoke lands between step resolution and execution
        reg.set_tool_status("pdf_digest", "revoked")
        return original(self, name, session)

    # An in-flight call finishes its already-resolved record; the NEXT call is refused. Documented semantics.
    result = run(composite.call(runtime, "c1", "pdf_digest", {"path": "a", "pr": 1}))
    assert not result["is_error"]
    reg.set_tool_status("pdf_digest", "revoked") if reg.active_tool("pdf_digest", "s1") else None
    assert run(composite.call(runtime, "c2", "pdf_digest", {"path": "a", "pr": 1}))["is_error"] is True


# ----- the gateway reaches a tool created THIS turn (no runtime change) ---------------------------------------------


def test_use_tool_dispatches_a_composite_created_this_turn(tmp_path):
    tenant, runtime, reg, toolsets = make(tmp_path, dynamic=True)
    run(registry.RegistryToolset().call(runtime, "c1", "create_tool", spec()))
    gateway = catalogue.GatewayToolset()
    found = run(gateway.call(runtime, "g1", "use_tool", {"name": "pdf_digest", "arguments": {"path": "a.pdf", "pr": 3}}))
    assert not found["is_error"], found
    assert toolsets[1].calls and toolsets[2].calls  # the pipeline really ran, mid-turn, through the gateway
    hits = run(gateway.call(runtime, "g2", "find_tools", {"query": "pdf digest"}))
    assert "pdf_digest" in hits["output"]
    # A scoped session cannot even AUTHOR a tool whose steps it could not run itself.
    tenant_scoped, runtime_scoped, reg_scoped, _ = make(tmp_path / "scoped", scope=["ReadFile"], dynamic=True)
    refused_create = run(registry.RegistryToolset().call(runtime_scoped, "c1", "create_tool", spec()))
    assert refused_create["is_error"] and "could not itself run" in refused_create["message"]


@requires_runtime
def test_gateway_reaches_a_tool_created_this_turn_real_runtime(client, workspace, script):
    """Mid-turn use without a runtime change: create_tool, then use_tool in the SAME turn (catalogue on)."""
    client.put("/api/settings", json={"dynamic_tools": True})
    script([
        tool_call("c", "create_tool", {
            "name": "process_digest",
            "description": "List background processes.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            "steps": [{"tool": "process", "arguments": {"action": "list"}}],
        }),
        tool_call("u", "use_tool", {"name": "process_digest", "arguments": {}}),
        text("done"),
    ])
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        results = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(run_turn(ws, "make and immediately use a tool"), "ToolResult")}
    assert results["c"]["is_error"] is False
    assert results["u"]["is_error"] is False and "step 0" in results["u"]["output"]  # same-turn use through the gateway


# ----- concurrency / TOCTOU / version semantics ------------------------------------------------------------------


def test_two_sessions_can_own_same_named_tools_without_shadowing(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    reg.create_tool(spec(), existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
    v2, _ = reg.create_tool(spec(description="s2's version"), existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s2")
    assert v2["version"] == 2
    assert reg.active_tool("pdf_digest", "s1")["description"] == "Read a PDF and file a digest comment."  # s1 keeps its own
    assert reg.active_tool("pdf_digest", "s2")["description"] == "s2's version"
    assert reg.active_tool("pdf_digest", "s3") is None  # neither is durable
    reg.promote_tool("pdf_digest", version=1)
    assert reg.active_tool("pdf_digest", "s3")["description"] == "Read a PDF and file a digest comment."  # everyone else gets the durable one


def test_duplicate_creation_versions_and_double_promotion_is_stable(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    v1, _ = reg.create_tool(spec(), existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
    v2, _ = reg.create_tool(spec(), existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
    assert (v1["version"], v2["version"]) == (1, 2)  # never two live drafts of the same version
    reg.promote_tool("pdf_digest")
    again = reg.promote_tool("pdf_digest")  # a second promotion is a no-op, not a new state
    assert again["lifetime"] == "durable" and again["version"] == 2
    with pytest.raises(registry.RegistryError):
        reg.promote_tool("pdf_digest", version=99)


def test_revocation_mid_pipeline_stops_before_the_next_step(tmp_path):
    tenant, runtime, reg, toolsets = make(tmp_path)
    composite = registry.CompositeToolset(reg)

    class Revoking(FakeToolset):
        async def call(self, runtime, call_id, name, arguments):
            reg.set_tool_status("pdf_digest", "revoked")  # lands between step 0 and step 1
            return await super().call(runtime, call_id, name, arguments)

    toolsets[1] = Revoking("documents", ["read_pdf"])
    reg.create_tool(spec(), existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
    result = run(composite.call(runtime, "c1", "pdf_digest", {"path": "a", "pr": 1}))
    assert result["is_error"] and "revoked or superseded" in result["message"]
    assert json.loads(result["output"])["failed_at_step"] == 1  # step 0 ran, step 1 never did
    assert toolsets[2].calls == []


def test_superseded_mid_pipeline_stops_instead_of_running_a_stale_version(tmp_path):
    tenant, runtime, reg, toolsets = make(tmp_path)
    composite = registry.CompositeToolset(reg)

    class Superseding(FakeToolset):
        async def call(self, runtime, call_id, name, arguments):
            reg.create_tool(spec(description="v2"), existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
            return await super().call(runtime, call_id, name, arguments)

    toolsets[1] = Superseding("documents", ["read_pdf"])
    reg.create_tool(spec(), existing_names=set(), host_tool_names={"read_pdf", "github_comment"}, created_by="t", session="s1")
    result = run(composite.call(runtime, "c1", "pdf_digest", {"path": "a", "pr": 1}))
    assert result["is_error"] and "revoked or superseded" in result["message"]  # v1's remaining steps did not run under v2's existence


def test_role_version_is_pinned_at_job_creation_and_latest_wins(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    universe = registry.role_universe(tenant, runtime, reg)
    reg.create_role(role_spec(), universe=universe, guide_names=set(guides.GUIDES), builtin_roles=set(jobs.ROLES), created_by="t", session="s1")
    assert jobs.resolve_role(tenant, "docs_reviewer", source_session="s1")["version"] == 1
    reg.create_role(role_spec(tools=["ReadFile"]), universe=universe, guide_names=set(guides.GUIDES), builtin_roles=set(jobs.ROLES), created_by="t", session="s1")
    resolved = jobs.resolve_role(tenant, "docs_reviewer", source_session="s1")
    assert resolved["version"] == 2 and resolved["scope"] == ["ReadFile"]  # a job start binds the version active at that moment
    # Cross-session: owner-authenticated, but the private role must be named together with its creating session.
    assert jobs.resolve_role(SimpleNamespace(registry=reg), "docs_reviewer", source_session="s1")["version"] == 2


def test_a_private_role_needs_an_explicit_source_session(tmp_path):
    """Owner authentication proves WHO asks; source_session proves WHICH private role they mean. No silent pickup."""
    tenant, runtime, reg, _ = make(tmp_path)
    universe = registry.role_universe(tenant, runtime, reg)
    reg.create_role(role_spec(), universe=universe, guide_names=set(guides.GUIDES), builtin_roles=set(jobs.ROLES), created_by="t", session="s1")
    with pytest.raises(jobs.JobError, match="source_session"):
        jobs.resolve_role(tenant, "docs_reviewer")  # bare name: refused
    with pytest.raises(jobs.JobError, match="source_session"):
        jobs.resolve_role(tenant, "docs_reviewer", source_session="someone-else")
    assert jobs.resolve_role(tenant, "docs_reviewer", source_session="s1")["version"] == 1
    reg.promote_role("docs_reviewer")
    assert jobs.resolve_role(tenant, "docs_reviewer")["version"] == 1  # durable roles need no source_session


def test_event_payloads_and_outputs_carry_no_hidden_data(tmp_path):
    # RegistryChanged payloads are the entry's public metadata only (no steps, no arguments, no test output).
    tenant, runtime, reg, _ = make(tmp_path)
    events = []
    runtime.emit = lambda kind, payload, persist=True: events.append(payload)
    run(registry.RegistryToolset().call(runtime, "c1", "create_tool", spec()))
    assert set(events[0]) == {"kind", "name", "version", "status", "lifetime", "description"}


def test_a_job_can_pin_a_role_version(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    universe = registry.role_universe(tenant, runtime, reg)
    reg.create_role(role_spec(), universe=universe, guide_names=set(guides.GUIDES), builtin_roles=set(jobs.ROLES), created_by="t", session="s1")
    reg.create_role(role_spec(tools=["ReadFile"]), universe=universe, guide_names=set(guides.GUIDES), builtin_roles=set(jobs.ROLES), created_by="t", session="s1")
    assert jobs.resolve_role(tenant, "docs_reviewer", source_session="s1")["version"] == 2  # latest active by default
    pinned = jobs.resolve_role(tenant, "docs_reviewer", version=1, source_session="s1")
    assert pinned["version"] == 1 and "read_pdf" in pinned["scope"]
    with pytest.raises(jobs.JobError, match="v9"):
        jobs.resolve_role(tenant, "docs_reviewer", version=9, source_session="s1")


# ----- multi-process safety: the flock must make concurrent read-modify-write lose nothing ------------------------------


def _parallel_create_worker(args):
    """A separate process with its OWN Registry instance on the shared dir, creating versions of the same tool."""
    import sys
    path, count = args
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    from tura_code import registry as reg_mod

    reg = reg_mod.Registry(__import__("pathlib").Path(path))
    base = {
        "name": "shared_tool",
        "description": "Created concurrently by several processes.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        "steps": [{"tool": "read_pdf", "arguments": {}}],
    }
    for _ in range(count):
        record, problems = reg.create_tool(dict(base), existing_names=set(), host_tool_names={"read_pdf"}, created_by="race", session=f"proc-{__import__('os').getpid()}")
        assert not problems, problems
    return True


def test_concurrent_processes_lose_no_versions(tmp_path):
    """Truly simultaneous mutation: 6 OS processes x 4 creates of the SAME tool name against one store.

    Without the flock the interleaved read-modify-writes clobber each other and versions vanish; with it every
    create lands and version numbers are exactly 1..24."""
    import multiprocessing

    reg = registry.Registry(tmp_path)
    reg.create_tool({  # v1 exists before the race
        "name": "shared_tool", "description": "First.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        "steps": [{"tool": "read_pdf", "arguments": {}}],
    }, existing_names=set(), host_tool_names={"read_pdf"}, created_by="setup", session="s0")
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(6) as pool:
        pool.map(_parallel_create_worker, [(str(tmp_path), 4)] * 6)
    final = registry.Registry(tmp_path)
    versions = [v["version"] for v in final.tools["shared_tool"]["versions"]]
    # MAX_VERSIONS_KEPT prunes to the last 20; what proves the lock is that the kept tail is CONTIGUOUS and every
    # process's creates landed: 6..25 with no gaps and no duplicates (lost updates would collide or truncate early).
    assert sorted(versions) == list(range(6, 26)), versions
    # And the file on disk is intact JSON, not a torn write.
    assert json.loads((tmp_path / "registry" / "tools.json").read_text())["shared_tool"]["versions"][-1]["version"] == 25


def _parallel_lifecycle_worker(args):
    path, kind = args
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    from tura_code import registry as reg_mod

    reg2 = reg_mod.Registry(__import__("pathlib").Path(path))
    for _ in range(10):
        try:
            if kind == "promote":
                reg2.promote_tool("raced_tool")
            else:
                reg2.set_tool_status("raced_tool", "active", activation="owner")
        except reg_mod.RegistryError:
            pass


def test_concurrent_promotion_and_revocation_stay_consistent(tmp_path):
    """Two processes race the SAME version's lifecycle; the file must always parse and end in one clean state."""
    import multiprocessing

    reg = registry.Registry(tmp_path)
    record, problems = reg.create_tool({
        "name": "raced_tool", "description": "Raced.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        "steps": [{"tool": "read_pdf", "arguments": {}}],
    }, existing_names=set(), host_tool_names={"read_pdf"}, created_by="setup", session="s0")
    assert not problems

    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_parallel_lifecycle_worker, args=((str(tmp_path), k),)) for k in ("promote", "activate")]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    final = registry.Registry(tmp_path)  # parses, so no torn write
    entry = final.tools["raced_tool"]
    assert entry["active_version"] in {v["version"] for v in entry["versions"]}


# --- adopt_role: one-way narrowing role adoption inside a session ------------------------------------------


def adoptable_runtime(tenant, scope=(), session="s1"):
    """A fake runtime whose record is mutable like the real SessionRecord, with an emit collector."""
    events = []
    runtime = SimpleNamespace(
        tenant=tenant,
        record=SimpleNamespace(job_role="", id=session, tool_scope=list(scope), role="", role_version=0),
        emit=lambda kind, payload, persist=True: events.append((kind, payload)),
    )
    return runtime, events


def make_role(reg, name="docs_reviewer", tools=("find_tools", "use_tool", "read_pdf"), description="Review docs only.", session="s1"):
    record, problems = reg.create_role(
        {"name": name, "description": description, "tools": list(tools), "guides": []},
        universe={"find_tools", "use_tool", "read_pdf", "process", "github_comment", "pdf_digest"},
        guide_names=set(), builtin_roles=set(jobs.ROLES), created_by="model", session=session,
    )
    assert not problems, problems
    return record


def test_adopt_durable_role_narrows_and_pins(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    make_role(reg)
    reg.promote_role("docs_reviewer")
    runtime, events = adoptable_runtime(tenant)
    result = run(registry.RegistryToolset().call(runtime, "c1", "adopt_role", {"name": "docs_reviewer"}))
    assert result["is_error"] is False
    record = runtime.record
    assert record.role == "docs_reviewer" and record.role_version == 1
    assert sorted(record.tool_scope) == ["find_tools", "read_pdf", "use_tool"]
    payload = json.loads(result["output"])
    assert payload["one_way"] is True and payload["pinned"] is True
    assert any(kind == "RoleAdopted" and body["role"] == "docs_reviewer" for kind, body in events)


def test_adopt_own_session_role_but_not_another_sessions(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    make_role(reg, name="mine")  # created by session s1, session-lifetime
    runtime, _ = adoptable_runtime(tenant, session="s1")
    assert run(registry.RegistryToolset().call(runtime, "c1", "adopt_role", {"name": "mine"}))["is_error"] is False

    other, _ = adoptable_runtime(tenant, session="s2")  # a different session cannot adopt s1's private role
    refused = run(registry.RegistryToolset().call(other, "c2", "adopt_role", {"name": "mine"}))
    assert refused["is_error"] is True and "private to the session" in refused["message"]


def test_adopt_never_widens_past_the_session_ceiling(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    make_role(reg, tools=["find_tools", "use_tool", "read_pdf"])
    reg.promote_role("docs_reviewer")
    scoped, _ = adoptable_runtime(tenant, scope=["find_tools", "use_tool"])  # read_pdf is outside this ceiling
    refused = run(registry.RegistryToolset().call(scoped, "c1", "adopt_role", {"name": "docs_reviewer"}))
    assert refused["is_error"] is True and "outside its current ceiling" in refused["message"]
    assert scoped.record.role == "" and scoped.record.tool_scope == ["find_tools", "use_tool"]  # unchanged

    fits, _ = adoptable_runtime(tenant, scope=["read_pdf", "find*"])  # fnmatch ceiling that DOES cover it
    assert run(registry.RegistryToolset().call(fits, "c2", "adopt_role", {"name": "docs_reviewer"}))["is_error"] is False


def test_adopt_refuses_a_role_whose_composite_steps_are_outside_the_ceiling(tmp_path):
    """Naming a composite is not permission for its steps: a role that leans on an un-runnable composite is refused up front."""
    tenant, runtime, reg, _ = make(tmp_path, dynamic=True)
    run(registry.RegistryToolset().call(runtime, "c1", "create_tool", spec()))  # composite pdf_digest -> read_pdf + github_comment
    reg.promote_tool("pdf_digest")
    record, problems = reg.create_role(
        {"name": "digest_writer", "description": "Write digests.", "tools": ["find_tools", "use_tool", "pdf_digest"], "guides": []},
        universe={"find_tools", "use_tool", "pdf_digest"}, guide_names=set(), builtin_roles=set(jobs.ROLES), created_by="model", session="s1",
    )
    assert not problems
    reg.promote_role("digest_writer")
    scoped, _ = adoptable_runtime(tenant, scope=["pdf_digest", "read_pdf", "find_tools", "use_tool"])  # github_comment missing
    refused = run(registry.RegistryToolset().call(scoped, "c2", "adopt_role", {"name": "digest_writer"}))
    assert refused["is_error"] is True and "github_comment" in refused["message"]


def test_adopt_builtin_role_and_unknown_role(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    runtime, _ = adoptable_runtime(tenant)
    unknown = run(registry.RegistryToolset().call(runtime, "c1", "adopt_role", {"name": "nope"}))
    assert unknown["is_error"] is True and "unknown or inactive" in unknown["message"]
    builtin = run(registry.RegistryToolset().call(runtime, "c2", "adopt_role", {"name": "developer"}))
    assert builtin["is_error"] is False and runtime.record.role == "developer" and runtime.record.role_version == 0


def test_adopt_pins_the_version_and_refuses_a_wrong_pin(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path)
    make_role(reg)
    reg.promote_role("docs_reviewer")
    make_role(reg, tools=["find_tools", "use_tool"])  # v2, narrower
    reg.promote_role("docs_reviewer")
    runtime, _ = adoptable_runtime(tenant)
    wrong = run(registry.RegistryToolset().call(runtime, "c1", "adopt_role", {"name": "docs_reviewer", "version": 9}))
    assert wrong["is_error"] is True and "unknown or inactive" in wrong["message"]
    ok = run(registry.RegistryToolset().call(runtime, "c2", "adopt_role", {"name": "docs_reviewer", "version": 1}))
    assert ok["is_error"] is False and runtime.record.role_version == 1


def test_gateway_honors_the_narrowed_scope_in_the_same_turn(tmp_path):
    """Adoption rewrites the session record the gateway reads per call: no restart needed for host/gateway tools."""
    tenant, runtime, reg, _ = make(tmp_path, dynamic=True)
    make_role(reg)
    reg.promote_role("docs_reviewer")
    runtime, _ = adoptable_runtime(tenant)
    gateway = next(t for t in tenant.toolsets if isinstance(t, catalogue.GatewayToolset))
    # The gateway dispatches deferred-plugin tools ("documents", "github") and registry composites.
    before = run(gateway.call(runtime, "g1", "use_tool", {"name": "github_comment", "arguments": {}}))
    assert before["is_error"] is False
    run(registry.RegistryToolset().call(runtime, "c1", "adopt_role", {"name": "docs_reviewer"}))
    after = run(gateway.call(runtime, "g2", "use_tool", {"name": "github_comment", "arguments": {}}))
    assert after["is_error"] is True and "not an available tool" in after["message"]
    kept = run(gateway.call(runtime, "g3", "use_tool", {"name": "read_pdf", "arguments": {}}))
    assert kept["is_error"] is False


def test_revocation_after_adoption_keeps_the_narrowed_scope(tmp_path):
    """The adopted scope is a snapshot: revoking the registry role does not widen the session back; its guides just stop."""
    tenant, runtime, reg, _ = make(tmp_path, dynamic=True)
    make_role(reg)
    reg.promote_role("docs_reviewer")
    runtime, events = adoptable_runtime(tenant)
    run(registry.RegistryToolset().call(runtime, "c1", "adopt_role", {"name": "docs_reviewer"}))
    reg.set_role_status("docs_reviewer", "revoked")
    assert sorted(runtime.record.tool_scope) == ["find_tools", "read_pdf", "use_tool"]  # still narrowed
    gateway = next(t for t in tenant.toolsets if isinstance(t, catalogue.GatewayToolset))
    still = run(gateway.call(runtime, "g1", "use_tool", {"name": "github_comment", "arguments": {}}))
    assert still["is_error"] is True


@requires_runtime
def test_adopt_role_in_a_real_session(client, workspace, script):
    """End to end with the real runtime: create + owner-promote a role, adopt it in another session, and the
    session record and the live gateway both reflect the narrowing in the same turn."""
    client.put("/api/settings", json={"dynamic_tools": True})
    first = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    script([tool_call("r", "create_role", {"name": "reader", "description": "Read-only helper.", "tools": ["find_tools", "use_tool", "ReadFile"]}), text("done")])
    with client.websocket_connect(f"/api/sessions/{first}/ws") as ws:
        ws.receive_json()
        run_turn(ws, "make a role")
    assert client.post("/api/registry/roles/reader/promote").status_code == 200

    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    script([
        tool_call("a", "adopt_role", {"name": "reader"}),
        tool_call("u", "use_tool", {"name": "github_comment", "arguments": {"body": "x"}}),  # outside the adopted scope, same turn
        text("done"),
    ])
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "adopt the reader role")
    results = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(events, "ToolResult")}
    assert results["a"]["is_error"] is False
    adopted = json.loads(results["a"]["output"])
    assert adopted["one_way"] is True and adopted["version"] == 1
    # The same-turn gateway dispatch already honors the narrowing - no restart, no cached scope.
    assert results["u"]["is_error"] is True and "not an available tool" in results["u"]["message"]
    notice = next(e for e in of_type(events, "RoleAdopted"))
    assert notice["payload"]["role"] == "reader" and notice["payload"]["reversible"] is False
    record = client.get(f"/api/sessions/{session_id}").json()
    assert record["role"] == "reader" and record["role_version"] == 1
    assert sorted(record["tool_scope"]) == ["ReadFile", "find_tools", "use_tool"]


@requires_runtime
def test_adopt_role_that_widens_is_refused_in_a_scoped_session(client, workspace, script):
    """A scoped session cannot use adoption to escape its ceiling; the refusal leaves the record untouched."""
    client.put("/api/settings", json={"dynamic_tools": True})
    first = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    script([tool_call("r", "create_role", {"name": "heavy", "description": "Runs processes.", "tools": ["find_tools", "use_tool", "process"]}), text("done")])
    with client.websocket_connect(f"/api/sessions/{first}/ws") as ws:
        ws.receive_json()
        run_turn(ws, "make a role")
    assert client.post("/api/registry/roles/heavy/promote").status_code == 200

    session_id = client.post("/api/sessions", json={"workspace": str(workspace), "tool_scope": ["find_tools", "use_tool", "adopt_role"]}).json()["id"]
    script([tool_call("a", "adopt_role", {"name": "heavy"}), text("done")])
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "adopt heavy")
    results = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(events, "ToolResult")}
    assert results["a"]["is_error"] is True and "outside its current ceiling" in results["a"]["message"]
    record = client.get(f"/api/sessions/{session_id}").json()
    assert record["role"] == "" and record["tool_scope"] == ["find_tools", "use_tool", "adopt_role"]


def test_approval_path_rejects_out_of_scope_native_senders_same_turn(tmp_path):
    """Native tools don't re-read the session record mid-turn, but every command, write, web fetch and
    outside-workspace read asks the host: the approval path rejects out-of-scope senders BEFORE trust policy,
    so no grant or ledger entry can launder them. This is adopt_role's same-turn native enforcement."""
    from tura_code.sessions.approvals import Approvals
    from tura_code.trust.ledger import TrustLedger

    class FakeRt(Approvals):
        def __init__(self, tenant, record):
            self.tenant = tenant
            self.record = record
            self.pending = {}
            self.events = []

        def emit(self, kind, payload, persist=True):
            self.events.append((kind, payload))

        def own_roots(self):
            return ()

    tenant, runtime, reg, _ = make(tmp_path)
    tenant.trust = TrustLedger(tmp_path / "trust.json")
    tenant.server = SimpleNamespace(mode="local")
    record = SimpleNamespace(permission_mode="yolo", kind="session", unattended=False, grants=[], workspace=str(tmp_path),
                             tool_scope=["find_tools", "use_tool", "ReadFile"])
    rt = FakeRt(tenant, record)

    # Even in yolo mode with everything auto-approved, an out-of-scope sender is rejected.
    rejected = run(rt._approve({"id": "r1", "sender": "Shell", "action": "run command", "description": "ls", "display": []}))
    assert rejected == "reject"
    resolved = [p for k, p in rt.events if k == "ApprovalResolved"]
    assert resolved and resolved[-1]["auto"] is True and "outside this session's tool scope" in resolved[-1]["reason"]

    # An in-scope sender falls through to the normal trust policy (yolo auto-approves reads of the workspace).
    allowed = run(rt._approve({"id": "r2", "sender": "ReadFile", "action": "read file", "description": "Read `x.py`", "display": []}))
    assert allowed == "approve"


@requires_runtime
def test_adopt_role_blocks_a_native_command_in_the_same_turn(client, workspace, script):
    """Real runtime, same turn: after adopt_role narrows the scope, a native Shell call is auto-rejected by the
    approval path - the runtime's initialized tool list no longer decides what may run."""
    client.put("/api/settings", json={"dynamic_tools": True})
    first = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    script([tool_call("r", "create_role", {"name": "reader", "description": "Read-only helper.", "tools": ["find_tools", "use_tool", "ReadFile"]}), text("done")])
    with client.websocket_connect(f"/api/sessions/{first}/ws") as ws:
        ws.receive_json()
        run_turn(ws, "make a role")
    assert client.post("/api/registry/roles/reader/promote").status_code == 200

    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    script([
        tool_call("a", "adopt_role", {"name": "reader"}),
        tool_call("s", "Shell", {"command": "echo should-not-run"}),
        text("done"),
    ])
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "adopt then try a command")
    rejections = [e["payload"] for e in of_type(events, "ApprovalResolved")
                  if e["payload"].get("response") == "reject" and e["payload"].get("sender") == "Shell"]
    assert rejections and "outside this session's tool scope" in rejections[0]["reason"]
    results = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(events, "ToolResult")}
    assert results["a"]["is_error"] is False
    assert "should-not-run" not in str(results.get("s", ""))  # the command never executed


# --- capability-miss signal: deterministic detection, never proof -------------------------------------------------


def test_find_tools_miss_tallies_the_record_and_hints_how_to_extend(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path, dynamic=True)
    runtime, events = adoptable_runtime(tenant)
    gateway = next(t for t in tenant.toolsets if isinstance(t, catalogue.GatewayToolset))
    result = run(gateway.call(runtime, "g1", "find_tools", {"query": "translate speech in real time"}))
    assert result["is_error"] is False
    assert "create_tool" in result["output"] and "propose_tool" in result["output"]
    assert runtime.record.capability_misses[0]["query"] == "translate speech in real time"
    assert not [e for e in events if e[0] == "CapabilityGap"]  # first miss: no owner notice yet


def test_every_third_miss_shows_the_owner_a_capability_gap(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path, dynamic=True)
    runtime, events = adoptable_runtime(tenant)
    gateway = next(t for t in tenant.toolsets if isinstance(t, catalogue.GatewayToolset))
    for i in range(7):
        run(gateway.call(runtime, f"g{i}", "find_tools", {"query": f"missing thing {i}"}))
    gaps = [p for k, p in events if k == "CapabilityGap"]
    assert [g["misses"] for g in gaps] == [3, 6]
    assert gaps[0]["queries"] == ["missing thing 0", "missing thing 1", "missing thing 2"]
    assert len(runtime.record.capability_misses) == 7


def test_miss_history_is_bounded(tmp_path):
    tenant, runtime, reg, _ = make(tmp_path, dynamic=True)
    runtime, events = adoptable_runtime(tenant)
    runtime.record.capability_misses = [{"query": f"old {i}", "at": 0} for i in range(catalogue.MISS_KEEP)]
    gateway = next(t for t in tenant.toolsets if isinstance(t, catalogue.GatewayToolset))
    run(gateway.call(runtime, "g1", "find_tools", {"query": "newest"}))
    assert len(runtime.record.capability_misses) == catalogue.MISS_KEEP
    assert runtime.record.capability_misses[-1]["query"] == "newest"


@requires_runtime
def test_capability_gap_surfaces_in_a_real_session(client, workspace, script):
    """Real runtime: three find_tools misses emit one CapabilityGap event the UI renders as a notice."""
    client.put("/api/settings", json={"dynamic_tools": True})
    session_id = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    script([
        tool_call("m1", "find_tools", {"query": "zzz nonexistent capability one"}),
        tool_call("m2", "find_tools", {"query": "zzz nonexistent capability two"}),
        tool_call("m3", "find_tools", {"query": "zzz nonexistent capability three"}),
        text("done"),
    ])
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "look for tools")
    gaps = [e["payload"] for e in of_type(events, "CapabilityGap")]
    assert len(gaps) == 1 and gaps[0]["misses"] == 3
    record = client.get(f"/api/sessions/{session_id}").json()
    assert len(record["capability_misses"]) == 3
