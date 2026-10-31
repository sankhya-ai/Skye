"""Which Dhee memory root builds: real (OpenRouter, with decisions) once there is a key, local before."""

from __future__ import annotations

import pytest

from tura_code.memory.gateway import MemoryGateway
from tura_code.settings import Settings

pytest.importorskip("dhee")


@pytest.fixture(autouse=True, scope="module")
def _one_embedder_for_the_module():
    """Every gateway builds its own Dhee, and Dhee its own EmbeddingGemma ONNX session (~250 MB that
    Python does not give back): share one session across the module so a small CI box is not exhausted."""
    try:
        from dhee.embeddings.embeddinggemma import EmbeddingGemmaEmbedder
    except Exception:  # noqa: BLE001 - no Dhee gemma: nothing to share
        yield
        return
    original, shared = EmbeddingGemmaEmbedder._ensure, {}

    def ensure(self):
        if shared.get("variant") == (self.variant, self.dims):
            self._session, self._tokenizer, self._media = shared["session"], shared["tokenizer"], shared["media"]
            return
        original(self)
        shared.update(variant=(self.variant, self.dims), session=self._session, tokenizer=self._tokenizer, media=self._media)

    EmbeddingGemmaEmbedder._ensure = ensure
    yield
    EmbeddingGemmaEmbedder._ensure = original


def _gateway(tmp_path, **settings) -> MemoryGateway:
    return MemoryGateway(tmp_path, lambda: Settings(**settings))


def test_default_without_a_key_is_local_and_quiet(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    gateway = _gateway(tmp_path)
    assert gateway._settings().memory_mode == "openrouter"
    assert gateway._ensure() is not None
    assert gateway.status()["active_mode"] == "local" and gateway.status()["error"] == ""
    gateway._close_current()


def test_with_a_key_memory_is_real_and_decides_before_storing(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    gateway = _gateway(tmp_path, openrouter_api_key="sk-or-test")
    dhee = gateway._ensure()
    assert gateway.status()["active_mode"] == "openrouter", gateway.status()
    config = dhee._engram.memory.config
    assert config.decisions.enabled and config.decisions.provider == "openrouter"
    assert config.decisions.fact_subjects == [], "project memory is not filtered to facts about a person"
    gateway._close_current()


def test_a_non_openrouter_endpoint_gets_no_decisions(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    gateway = _gateway(tmp_path, openrouter_api_key="k", base_url="https://llm.internal/v1")
    dhee = gateway._ensure()
    assert gateway.status()["active_mode"] == "openrouter"
    assert not dhee._engram.memory.config.decisions.enabled
    gateway._close_current()


# ----- EmbeddingGemma 2: local memory that ranks by meaning ------------------

from tura_code.memory.local_embedder import LocalEmbedder  # noqa: E402

gemma_ready = pytest.mark.skipif(not LocalEmbedder().ready(), reason="EmbeddingGemma 2 is not installed and downloaded (dhee models download gemma)")


def _run(coro):
    import asyncio

    return asyncio.new_event_loop().run_until_complete(coro)


@gemma_ready
def test_local_memory_finds_by_meaning_and_leaves_out_the_unrelated(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    gateway, ws = _gateway(tmp_path), tmp_path / "ws"
    for fact in ("The project runs its tests with `make test`.", "Deploys go out from the release branch every Thursday."):
        _run(gateway.remember(fact, ws))
    assert gateway.status()["embedder"] == "embeddinggemma-2"
    hits = _run(gateway.recall("when do we ship to production", ws))
    assert hits and "Thursday" in hits[0]["memory"], hits  # no word in common with the memory
    assert _run(gateway.recall("a recipe for banana bread", ws)) == []
    gateway._close_current()


@gemma_ready
def test_what_was_stored_before_the_model_arrived_moves_to_it(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    gateway, ws = _gateway(tmp_path), tmp_path / "ws"
    monkeypatch.setattr(LocalEmbedder, "ready", lambda self: False)
    monkeypatch.setattr(LocalEmbedder, "fetch", lambda self: None)
    _run(gateway.remember("Deploys go out from the release branch every Thursday.", ws))
    assert gateway.status()["embedder"] == "hash"

    monkeypatch.setattr(LocalEmbedder, "ready", lambda self: True)  # the download finished
    hits = _run(gateway.recall("when do we ship to production", ws))
    assert gateway.status()["embedder"] == "embeddinggemma-2"
    assert hits and "Thursday" in hits[0]["memory"], hits
    assert (tmp_path / "dhee" / ".gemma-reembedded").exists()
    gateway._close_current()


def test_offline_local_memory_never_downloads(monkeypatch):
    monkeypatch.setenv("TURA_CODE_OFFLINE", "1")
    local = LocalEmbedder()
    monkeypatch.setattr(LocalEmbedder, "ready", lambda self: False)
    local.fetch()
    assert local._thread is None
    if local.supported():
        assert local.state() == "unavailable: offline"



# ----- the vector store behind local memory: scale, persistence, and saying so ------

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

from tura_code.memory.gateway import raw_cosine  # noqa: E402


def test_raw_cosine_undoes_only_the_halved_scale():
    assert raw_cosine(0.8, "SqliteVecStore") == pytest.approx(0.6)
    assert raw_cosine(0.8, "ZvecStore") == 0.8
    assert raw_cosine(0.8, "InMemoryVectorStore") == 0.8
    assert raw_cosine(0.8, "") == 0.8  # an unfamiliar Dhee: assume a raw cosine, as before


def _without(monkeypatch, *modules):
    """Make these Dhee vector stores un-importable, as on Python < 3.11 (no zvec) or without sqlite-vec."""
    for name in modules:
        monkeypatch.setitem(sys.modules, name, None)


def _recall_in_a_new_process(tmp_path, ws, query, *, block=()):
    """What a restart sees: a separate Python process opens the same memory folder and recalls."""
    import json
    import subprocess

    code = (
        "import sys, json, asyncio\n"
        f"for name in {list(block)!r}: sys.modules[name] = None\n"
        "from pathlib import Path\n"
        "from tura_code.memory.gateway import MemoryGateway\n"
        "from tura_code.settings import Settings\n"
        f"g = MemoryGateway(Path({str(tmp_path)!r}), lambda: Settings())\n"
        f"hits = asyncio.new_event_loop().run_until_complete(g.recall({query!r}, Path({str(ws)!r})))\n"
        "print('HITS' + json.dumps([h['memory'] for h in hits]), json.dumps(g.status()['vector_store']))\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, cwd=Path(__file__).resolve().parent.parent)
    line = next((ln for ln in done.stdout.splitlines() if ln.startswith("HITS")), "")
    assert line, done.stderr[-2000:]
    hits, store = line[4:].rsplit(" ", 1)
    return json.loads(hits), json.loads(store)


def _facts(gateway, ws):
    for fact in ("The project runs its tests with `make test`.", "Deploys go out from the release branch every Thursday."):
        _run(gateway.remember(fact, ws))


@gemma_ready
def test_the_supported_store_keeps_memory_across_a_restart(tmp_path, monkeypatch):
    pytest.importorskip("zvec")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    ws = tmp_path / "ws"
    first = _gateway(tmp_path)
    _facts(first, ws)
    assert first.status()["vector_store"] == "ZvecStore" and first.status()["warning"] == ""
    first._close_current()
    hits, store = _recall_in_a_new_process(tmp_path, ws, "when do we ship to production")
    assert store == "ZvecStore" and any("Thursday" in memory for memory in hits), hits
    assert _recall_in_a_new_process(tmp_path, ws, "a recipe for banana bread")[0] == []


@gemma_ready
def test_sqlite_fallback_still_leaves_out_the_unrelated_and_persists(tmp_path, monkeypatch):
    pytest.importorskip("sqlite_vec")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    _without(monkeypatch, "dhee.vector_stores.zvec_store")
    ws = tmp_path / "ws"
    first = _gateway(tmp_path)
    _facts(first, ws)
    status = first.status()
    assert status["vector_store"] == "SqliteVecStore" and "zvec" in status["warning"]
    hits = _run(first.recall("when do we ship to production", ws))
    assert hits and "Thursday" in hits[0]["memory"], hits
    assert _run(first.recall("a recipe for banana bread", ws)) == [], "sqlite-vec scores (1+cos)/2: the floor must be applied on the cosine"
    first._close_current()
    again, store = _recall_in_a_new_process(tmp_path, ws, "when do we ship to production", block=["dhee.vector_stores.zvec_store"])
    assert store == "SqliteVecStore" and any("Thursday" in memory for memory in again), again
    assert _recall_in_a_new_process(tmp_path, ws, "a recipe for banana bread", block=["dhee.vector_stores.zvec_store"])[0] == []


@gemma_ready
def test_in_memory_fallback_is_reported_not_silent(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    _without(monkeypatch, "dhee.vector_stores.zvec_store", "dhee.vector_stores.sqlite_vec")
    ws = tmp_path / "ws"
    gateway = _gateway(tmp_path)
    _facts(gateway, ws)
    status = gateway.status()
    assert status["vector_store"] == "InMemoryVectorStore"
    assert "restart" in status["warning"]
    hits = _run(gateway.recall("when do we ship to production", ws))
    assert hits and "Thursday" in hits[0]["memory"], hits
    assert _run(gateway.recall("a recipe for banana bread", ws)) == []
    gateway._close_current()
    gone, store = _recall_in_a_new_process(tmp_path, ws, "when do we ship to production", block=["dhee.vector_stores.zvec_store", "dhee.vector_stores.sqlite_vec"])
    assert store == "InMemoryVectorStore" and gone == [], "the in-memory store keeps nothing across a restart, which is what the warning says"
