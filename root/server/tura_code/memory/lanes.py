"""Rebuilding Dhee's memory on another embedder: OpenRouter's, or EmbeddingGemma's.

Dhee's facade picks its providers itself, so these reach past its public API
(``_engram``, ``_memory``) — the one place root does.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..settings import Settings

logger = logging.getLogger(__name__)


def reembed(dhee: Any, marker: Path) -> None:
    """Embed what the hash embedder stored again, into EmbeddingGemma's collection. Once."""
    try:
        report = dhee._engram.repair_memory_vectors(user_id=None, limit=100_000, dry_run=False, reembed=True)
    except Exception:  # noqa: BLE001 - memory still works; a restart tries again
        logger.warning("Re-embedding memories for EmbeddingGemma 2 failed", exc_info=True)
        return
    if report.get("reembedded"):
        logger.info("Re-embedded %d memories with EmbeddingGemma 2", report["reembedded"])
    marker.write_text("embeddinggemma-2\n", encoding="utf-8")


def use_openrouter(dhee: Any, settings: Settings) -> None:
    """Point Dhee's embedder and enrichment LLM at OpenRouter.

    Dhee's facade picks providers from its own environment detection and
    takes no config, so — as Tura's hub does — the memory is rebuilt here
    with explicit OpenRouter lanes: the one place root reaches past Dhee's
    public API (``_engram``, ``_memory``). A separate vector collection per
    embedding width keeps vectors from two models from ever being compared.

    Decisions (Dhee 7.4, Jev through OpenRouter) gate the write path: a fact
    that restates a stored one reaffirms it instead of adding a duplicate.
    No ``fact_subjects``: project memory is about the code, not a person.
    """
    key = settings.api_key()
    if not key:
        raise RuntimeError("no OpenRouter API key is configured")
    from dhee.configs.base import DecisionConfig, EmbedderConfig, LLMConfig, VectorStoreConfig
    from dhee.memory.main import FullMemory

    engram = dhee._engram
    base = engram.memory.config
    dims = int(settings.memory_embedding_dims)
    config = base.model_copy(deep=True)
    config.llm = LLMConfig(
        provider="openai",
        config={
            "model": settings.memory_llm_model,
            "api_key": key,
            "base_url": settings.base_url,
            "temperature": 0.2,
            "max_tokens": 2048,
        },
    )
    config.embedder = EmbedderConfig(
        provider="openai",
        config={
            "model": settings.memory_embedding_model,
            "api_key": key,
            "base_url": settings.base_url,
            "embedding_dims": dims,
        },
    )
    config.vector_store = VectorStoreConfig(
        provider="zvec",
        config={
            "collection_name": f"tura_openrouter_{dims}",
            "path": str(Path(engram.data_dir) / "zvec"),
            "embedding_model_dims": dims,
        },
    )
    config.embedding_model_dims = dims
    if "openrouter.ai" in settings.base_url:
        config.decisions = DecisionConfig(enabled=True, provider="openrouter", api_key=key)
    engram._memory = FullMemory(config)
    engram._provider = "openai"
