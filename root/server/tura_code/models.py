"""OpenRouter's model catalogue, for the model picker and capability detection.

The catalogue endpoint is public, so the picker works before a key is set.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

CACHE_SECONDS = 3600


class ModelCatalog:
    def __init__(self) -> None:
        self._models: list[dict[str, Any]] = []
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()
        self.error = ""

    async def models(self, base_url: str, *, refresh: bool = False) -> list[dict[str, Any]]:
        async with self._lock:
            fresh = time.time() - self._fetched_at < CACHE_SECONDS
            if self._models and fresh and not refresh:
                return self._models
            try:
                raw = await asyncio.to_thread(_fetch, f"{base_url.rstrip('/')}/models")
                self._models = [_summarize(item) for item in raw.get("data", []) if isinstance(item, dict)]
                self._models.sort(key=lambda m: m["id"])
                self._fetched_at = time.time()
                self.error = ""
            except Exception as error:  # noqa: BLE001 - the picker still takes free text
                logger.info("model catalogue unavailable: %s", error)
                self.error = str(error)
            return self._models

    def capabilities(self, model_id: str) -> list[str]:
        for model in self._models:
            if model["id"] == model_id:
                return model["capabilities"]
        return []

    def context_length(self, model_id: str) -> int | None:
        for model in self._models:
            if model["id"] == model_id:
                return model.get("context_length")
        return None


def _fetch(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "tura-code/0.1", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - fixed https URL
        return json.loads(response.read().decode("utf-8"))


def _summarize(item: dict) -> dict[str, Any]:
    architecture = item.get("architecture") or {}
    inputs = architecture.get("input_modalities") or []
    params = item.get("supported_parameters") or []
    pricing = item.get("pricing") or {}
    capabilities = []
    if "image" in inputs:
        capabilities.append("image_in")
    return {
        "id": item.get("id") or "",
        "name": item.get("name") or item.get("id") or "",
        "context_length": item.get("context_length"),
        "tools": "tools" in params,
        "reasoning": "reasoning" in params or "include_reasoning" in params,
        "capabilities": capabilities,
        "prompt_price": pricing.get("prompt"),
        "completion_price": pricing.get("completion"),
    }
