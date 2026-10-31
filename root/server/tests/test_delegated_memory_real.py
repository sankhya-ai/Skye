"""Real Dhee (local EmbeddingGemma): a delegated worker never sees or writes owner memory, whatever the arguments."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tura_code.memory.gateway import MemoryGateway, MemoryView
from tura_code.settings import Settings

pytest.importorskip("dhee")
from tests.test_memory_modes import _one_embedder_for_the_module  # noqa: E402,F401  (one shared ONNX session)

OWNER_FACT = "The owner prefers purple dashboards and tabs for indentation."
PROJECT_FACT = "This project runs its tests with pytest -x from the server directory."


def test_delegated_worker_is_project_only_on_real_dhee(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    gateway = MemoryGateway(tmp_path, lambda: Settings())
    work = tmp_path / "ws"
    work.mkdir()
    owner = MemoryView(gateway, "owner:u1")
    worker = owner.restricted()

    async def go():
        await owner.remember(OWNER_FACT, work, scope="owner", kind="preference")
        await owner.remember(PROJECT_FACT, work, scope="project", kind="convention")
        for scope in ("all", "owner", "project"):  # the owner sees both
            pass
        owner_all = {h["scope"] for h in await owner.recall("purple dashboards indentation", work, scope="all")}
        assert "owner" in owner_all
        # explicit bypass attempts
        for scope in ("all", "project", "bogus", "", None):
            hits = await worker.recall("purple dashboards tabs indentation", work, scope=scope or "all")
            assert all(h["scope"] == "project" and "purple" not in h["memory"] for h in hits), (scope, hits)
        out = await worker.handle_tool("memory_recall", {"query": "purple dashboards", "scope": "owner"}, workspace=work, session_id="s")
        assert out["is_error"] and "purple" not in out["output"]
        out = await worker.handle_tool("memory_remember", {"content": "worker plants an owner fact", "scope": "owner"}, workspace=work, session_id="s")
        assert out["is_error"]
        assert not [h for h in await owner.recall("worker plants an owner fact", work, scope="owner") if "plants" in h["memory"]]
        listed = await worker.list_memories(work)
        assert all(i["scope"] == "project" for i in listed)
        # automatic packet: no owner line, no lessons
        packet = await worker.compile_packet("purple dashboards tabs indentation pytest", work, seen=set(), first_turn=True)
        text = packet.text if packet else ""
        assert "purple" not in text
        assert packet is None or all(i.get("scope", "project") == "project" for i in packet.items)
        # project memory is shared by design
        shared = await worker.recall("how are tests run", work)
        assert any("pytest" in h["memory"] for h in shared)
        # owner packet unchanged
        op = await owner.compile_packet("purple dashboards tabs indentation", work, seen=set(), first_turn=True)
        assert op is not None and "purple" in op.text

    asyncio.run(go())
    gateway._close_current()
