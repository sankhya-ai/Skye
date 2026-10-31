"""EmbeddingGemma 2: the embedder behind local memory.

Dhee 7.5 runs it on the CPU (provider ``gemma``), so local memory ranks by
meaning with no key. Until its 314 MB model is on disk, local memory keeps
Dhee's hash embedder, and the first use starts the download on a thread of its
own: no turn ever waits for it. When it lands, the gateway rebuilds Dhee on it
and embeds what was stored meanwhile again. ``TURA_CODE_OFFLINE`` never
downloads; neither does an install without ``onnxruntime`` or an older Dhee.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

MODEL = "embeddinggemma-2"


def _gemma() -> Any:
    try:
        from dhee.embeddings import embeddinggemma
    except Exception:  # noqa: BLE001 - an older Dhee simply has no local embedder
        return None
    return embeddinggemma


class LocalEmbedder:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._error = ""

    def supported(self) -> bool:
        gemma = _gemma()
        return gemma is not None and gemma.dependencies_installed()

    def ready(self) -> bool:
        gemma = _gemma()
        return gemma is not None and gemma.is_available()

    def state(self) -> str:
        """``ready``, ``downloading``, ``waiting`` (to start), or why it cannot run."""
        if self.ready():
            return "ready"
        if not self.supported():
            return "unavailable: install Dhee 7.5+ with its `gemma` extra"
        if self._thread is not None and self._thread.is_alive():
            return "downloading"
        if self._error:
            return f"failed: {self._error}"
        if os.environ.get("TURA_CODE_OFFLINE"):
            return "unavailable: offline"
        return "waiting"

    def fetch(self) -> None:
        """Start the download once, in the background, if it can and must happen."""
        if os.environ.get("TURA_CODE_OFFLINE") or self.ready() or not self.supported():
            return
        if self._thread is not None and (self._thread.is_alive() or self._error):
            return  # running, or failed this run: a restart tries again
        gemma = _gemma()

        def download() -> None:
            try:
                gemma.download()
                logger.info("EmbeddingGemma 2 is ready; local memory now ranks by meaning")
            except Exception as error:  # noqa: BLE001 - memory keeps working without it
                self._error = str(error)
                logger.warning("EmbeddingGemma 2 download failed: %s", error)

        self._thread = threading.Thread(target=download, name="dhee-gemma-download", daemon=True)
        self._thread.start()
