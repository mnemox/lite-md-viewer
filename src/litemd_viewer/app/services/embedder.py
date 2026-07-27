"""Local CPU embeddings via fastembed (ONNX Runtime, no PyTorch).

The model is loaded lazily and off the event loop: the first call downloads roughly 130 MB
of ONNX weights into the huggingface cache, and every call afterwards is offline. Loading is
allowed to fail without taking the app down -- search then reports itself as unavailable
while the rest of the application keeps working.
"""

from __future__ import annotations

import logging
import threading

from .. import config

log = logging.getLogger(__name__)


class Embedder:
    """Thread-safe lazy wrapper around a fastembed TextEmbedding model."""

    def __init__(self, model_name: str | None = None, dimension: int | None = None) -> None:
        self.model_name = model_name or config.EMBED_MODEL
        self.dimension = dimension or config.EMBED_DIM
        self._model = None
        self._lock = threading.Lock()
        self._load_failed = False
        self.last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return config.EMBEDDING_ENABLED and not self._load_failed

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self) -> bool:
        """Load the model if needed. Returns whether it is usable."""
        if not config.EMBEDDING_ENABLED:
            return False
        if self._model is not None:
            return True
        if self._load_failed:
            return False

        with self._lock:
            if self._model is not None:
                return True
            if self._load_failed:
                return False
            try:
                from fastembed import TextEmbedding

                log.info("loading embedding model %s", self.model_name)
                self._model = TextEmbedding(model_name=self.model_name)
                log.info("embedding model ready")
                self.last_error = None
                return True
            except Exception as exc:  # noqa: BLE001 - degrade instead of crashing
                self._load_failed = True
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("embedding model unavailable: %s", self.last_error)
                return False

    def embed_documents(self, texts: list[str]) -> list[list[float]] | None:
        """Embed passages. None means the model is unavailable."""
        if not texts:
            return []
        if not self.load():
            return None
        try:
            return [list(map(float, v)) for v in self._model.embed(texts)]
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("embedding failed: %s", self.last_error)
            return None

    def embed_query(self, text: str) -> list[float] | None:
        """Embed a search query.

        bge is trained asymmetrically: passages are embedded bare, while a query may carry an
        instruction prefix. fastembed's `query_embed` does *not* apply one for this model --
        measured, its output is identical to `embed` -- so the prefix is applied here instead,
        and is configurable because it is model-specific.
        """
        if not self.load():
            return None
        try:
            prompt = config.EMBED_QUERY_PREFIX + text
            vectors = list(self._model.embed([prompt]))
            if not vectors:
                return None
            return list(map(float, vectors[0]))
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("query embedding failed: %s", self.last_error)
            return None
