"""A delegated worker reaches this workspace's project memory only: no owner reads, writes, digest facts or first-turn lessons."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from tura_code.memory import MemoryScopeError, MemoryView, tool_definitions
from tura_code.memory.digest import digest_turn


class Fake:
    """Records what the gateway was asked; no model, no Dhee."""

    def __init__(self):
        self.calls = []

    async def recall(self, query, workspace, *, scope, limit, owner):
        self.calls.append(("recall", scope))
        return [{"id": "1", "memory": "m", "scope": scope}]

    async def remember(self, content, workspace, *, scope, kind, session_id, owner):
        self.calls.append(("remember", scope))
        return {"id": "x"}

    async def compile_packet(self, prompt, workspace, *, seen, first_turn, owner, project_only):
        self.calls.append(("packet", project_only))

    async def list_memories(self, workspace, limit, owner, project_only):
        self.calls.append(("list", project_only))
        return []


def view(delegated):
    v = MemoryView(Fake(), "owner:u")
    return (v.restricted() if delegated else v), v.gateway


def run(c):
    return asyncio.run(c)


def test_owner_view_is_unchanged():
    v, g = view(False)
    run(v.recall("q", Path("/w"), scope="owner"))
    run(v.remember("x", Path("/w"), scope="owner"))
    run(v.compile_packet("q", Path("/w"), seen=set(), first_turn=True))
    assert g.calls == [("recall", "owner"), ("remember", "owner"), ("packet", False)]


@pytest.mark.parametrize("scope", ["all", "project", "", "bogus"])
def test_delegated_recall_is_project_only(scope):
    v, g = view(True)
    run(v.recall("q", Path("/w"), scope=scope))
    assert g.calls == [("recall", "project")]


def test_delegated_refuses_owner_read_and_write():
    v, g = view(True)
    with pytest.raises(MemoryScopeError):
        run(v.recall("q", Path("/w"), scope="owner"))
    with pytest.raises(MemoryScopeError):
        run(v.remember("x", Path("/w"), scope="owner"))
    assert g.calls == []
    run(v.remember("x", Path("/w"), scope="project"))
    assert g.calls == [("remember", "project")]


def test_tool_layer_turns_owner_into_a_tool_error_and_hides_it():
    v, g = view(True)
    out = run(v.handle_tool("memory_remember", {"content": "x", "scope": "owner"}, workspace=Path("/w"), session_id="s"))
    assert out["is_error"] and g.calls == []
    out = run(v.handle_tool("memory_recall", {"query": "q", "scope": "owner"}, workspace=Path("/w"), session_id="s"))
    assert out["is_error"] and g.calls == []
    for d in tool_definitions(project_only=True):
        enum = d["parameters"]["properties"].get("scope", {}).get("enum")
        assert enum in (None, ["project"])
    assert any("owner" in str(d) for d in tool_definitions())


def test_packet_and_list_are_project_only():
    v, g = view(True)
    run(v.compile_packet("q", Path("/w"), seen=set(), first_turn=True))
    run(v.list_memories(Path("/w")))
    assert g.calls == [("packet", True), ("list", True)]


def test_digest_drops_owner_facts_for_a_delegated_worker(monkeypatch):
    from tura_code.memory import digest

    fact = {"kind": "user_preference", "content": "owner likes tabs", "replaces": ""}
    proj = {"kind": "project_fact", "content": "tests run with pytest", "replaces": ""}
    parsed = {"summary": "s", "task_type": "", "what_worked": "", "what_failed": "", "decisions": [], "todos": [], "facts": [fact, proj]}

    async def complete(*a, **k):
        return "{}"

    monkeypatch.setenv(digest.llm.SCRIPTED_LLM_ENV, "1")
    monkeypatch.setattr(digest.llm, "complete", complete)
    monkeypatch.setattr(digest, "parse", lambda *a, **k: parsed)
    for delegated, expected in ((True, [("remember", "project")]), (False, [("remember", "owner"), ("remember", "project")])):
        v, g = view(delegated)
        g.checkpoint = None

        async def checkpoint(workspace, *, summary, status, **f):
            return {}

        v.checkpoint = checkpoint
        turn = SimpleNamespace(prompt="p", files_touched=[], verified=True, rejections=[], reply=lambda: "r", tool_count=3, checks=[], errors=[], trace=lambda n: [])
        run(digest_turn(v, Path("/w"), turn, "finished", settings=SimpleNamespace(base_url="", learning_model="", model=""), api_key="k"))
        assert [c for c in g.calls if c[0] == "remember"] == expected


def test_host_wiring_follows_the_session_record_only():
    """The host stamps SessionRecord.delegated; tenant.memory_for and the memory toolset read nothing else."""
    from tura_code.sessions.host_tools import MemoryToolset
    from tura_code.sessions.record import SessionRecord
    from tura_code.tenancy import Tenant

    class On(MemoryView):
        enabled = True

        def restricted(self):
            return On(self.gateway, self.owner, project_only=True)

    class T:
        memory = On(Fake(), "owner:u")
        memory_for = Tenant.memory_for

    t = T()
    delegated = SessionRecord(id="a", workspace="/w", job="j1", delegated=True)
    top_level = SessionRecord(id="b", workspace="/w", job="j2", delegated=False)
    assert t.memory_for(delegated).project_only and not t.memory_for(top_level).project_only
    assert not t.memory_for(SessionRecord(id="c", workspace="/w")).project_only
    for record, owner_visible in ((delegated, False), (top_level, True)):
        rt = SimpleNamespace(tenant=t, record=record)
        text = str(MemoryToolset().definitions(rt))
        assert ("owner" in text.lower()) is owner_visible
    # a model cannot set it: a truthy non-bool or a tool argument does nothing
    assert not t.memory_for(SimpleNamespace(delegated="true")).project_only
