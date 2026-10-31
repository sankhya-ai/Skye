"""Dynamic tool catalogue: discovery is presentation; scope and approvals still decide every call."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from tura_code import catalogue
from tura_code.settings import Settings


class FakeToolset:
    def __init__(self, plugin, names):
        self.plugin = plugin
        self.names = names
        self.calls = []

    def definitions(self, runtime):
        return [{"name": n, "description": f"{n.replace('_', ' ')} for {self.plugin}", "parameters": {"type": "object"}} for n in self.names]

    def handles(self, name):
        return name in self.names

    async def call(self, runtime, call_id, name, arguments):
        self.calls.append(name)
        return {"is_error": False, "output": "ok", "message": "", "display": []}


def make(**settings):
    toolsets = [FakeToolset(None, ["process"]), FakeToolset("documents", ["read_pdf", "edit_sheet"]), FakeToolset("github", ["github_comment"])]
    holder = SimpleNamespace(value=Settings(**settings))
    tenant = SimpleNamespace(settings=SimpleNamespace(get=lambda: holder.value), toolsets=toolsets)
    tenant.active_toolsets = lambda: tenant.toolsets
    tenant.toolsets.append(catalogue.GatewayToolset())
    runtime = SimpleNamespace(tenant=tenant)
    return tenant, runtime, toolsets


def names(tenant, runtime):
    return sorted(d["name"] for d in catalogue.listed_definitions(tenant, runtime))


def run(coro):
    return asyncio.run(coro)


def test_off_by_default_lists_everything_and_no_gateway():
    tenant, runtime, _ = make()
    assert names(tenant, runtime) == ["edit_sheet", "github_comment", "process", "read_pdf"]


def test_on_hides_deferred_and_lists_gateway():
    tenant, runtime, _ = make(dynamic_tools=True)
    assert names(tenant, runtime) == ["find_tools", "process", "use_tool"]
    assert catalogue.stats(tenant, runtime)["deferred_chars"] > 0


def test_find_returns_schema_of_relevant_tool_only():
    tenant, runtime, _ = make(dynamic_tools=True)
    gateway = tenant.toolsets[-1]
    out = run(gateway.call(runtime, "c", "find_tools", {"query": "read a pdf"}))
    found = json.loads(out["output"])
    assert [d["name"] for d in found][0] == "read_pdf" and "github_comment" not in [d["name"] for d in found]


def test_use_tool_runs_owner_toolset():
    tenant, runtime, toolsets = make(dynamic_tools=True)
    out = run(tenant.toolsets[-1].call(runtime, "c", "use_tool", {"name": "read_pdf", "arguments": {}}))
    assert not out["is_error"] and toolsets[1].calls == ["read_pdf"]


def test_use_tool_rejects_unknown_and_core_names():
    tenant, runtime, toolsets = make(dynamic_tools=True)
    for target in ("process", "nope", "use_tool"):
        out = run(tenant.toolsets[-1].call(runtime, "c", "use_tool", {"name": target}))
        assert out["is_error"]
    assert toolsets[0].calls == []


def test_scope_blocks_even_after_discovery():
    tenant, runtime, toolsets = make(dynamic_tools=True, tool_scope=["read_*", "process"])
    gateway = tenant.toolsets[-1]
    found = json.loads(run(gateway.call(runtime, "c", "find_tools", {"query": "github comment sheet pdf"}))["output"])
    assert [d["name"] for d in found] == ["read_pdf"]
    out = run(gateway.call(runtime, "c", "use_tool", {"name": "github_comment"}))
    assert out["is_error"] and toolsets[2].calls == []


def test_scope_also_filters_without_dynamic_catalogue():
    tenant, runtime, _ = make(tool_scope=["process"])
    assert names(tenant, runtime) == ["process"]


def test_gateway_off_refuses():
    tenant, runtime, _ = make()
    assert run(tenant.toolsets[-1].call(runtime, "c", "find_tools", {"query": "pdf"}))["is_error"]


def test_empty_query_finds_nothing():
    assert catalogue.rank("", [{"name": "a", "description": "b"}]) == []


def test_session_scope_intersects_tenant_scope():
    tenant, runtime, _ = make(tool_scope=["read_*", "process", "edit_*"])
    runtime.record = SimpleNamespace(tool_scope=["read_*", "process"])
    assert catalogue.allowed(tenant, "read_pdf", runtime)
    assert not catalogue.allowed(tenant, "edit_sheet", runtime)  # tenant allows, session does not
    assert not catalogue.allowed(tenant, "github_comment", runtime)  # session would allow nothing here
    assert catalogue.allowed(tenant, "find_tools", runtime)


def test_session_scope_applies_through_gateway():
    tenant, runtime, toolsets = make(dynamic_tools=True)
    runtime.record = SimpleNamespace(tool_scope=["read_pdf", "process"])
    out = run(tenant.toolsets[-1].call(runtime, "c", "use_tool", {"name": "edit_sheet"}))
    assert out["is_error"] and toolsets[1].calls == []


def test_native_exclusions_drop_unscoped_tools_and_always_subagents():
    from tura_code.launcher import BASE_NATIVE_TOOLS

    excluded = {catalogue.short(n) for n in catalogue.native_exclusions(["ReadFile", "Grep", "Task"], BASE_NATIVE_TOOLS)}
    assert "Shell" in excluded and "WriteFile" in excluded and "Task" in excluded
    assert "ReadFile" not in excluded and "Grep" not in excluded


def test_overlay_excludes_out_of_scope_native_tools(tmp_path):
    from tura_code import launcher

    tenant, runtime, _ = make()
    spec = launcher.LaunchSpec(session_id="s", workspace=tmp_path, settings=Settings(), permission_mode="default", memory_enabled=False, tool_scope=("ReadFile", "Glob"))
    text = launcher.write_agent_overlay(spec, tmp_path).read_text()
    assert '"kimi_cli.tools.shell:Shell"' in text and '"kimi_cli.tools.file:WriteFile"' in text
    assert '"kimi_cli.tools.file:ReadFile"' not in text


def test_guarantee_is_none_while_a_code_runner_is_in_scope():
    tenant, _, _ = make()
    assert catalogue.guarantee(tenant, SimpleNamespace(tool_scope=[]))["scoped"] is False
    for scope in (["Shell", "ReadFile"], ["run_*"], ["process"]):
        g = catalogue.guarantee(tenant, SimpleNamespace(tool_scope=scope))
        assert g["level"] == "none" and g["scoped"] and g["runners"]
    g = catalogue.guarantee(tenant, SimpleNamespace(tool_scope=["ReadFile", "Grep"]))
    assert g["level"] == "tool-names" and "not OS-level" in g["note"]
