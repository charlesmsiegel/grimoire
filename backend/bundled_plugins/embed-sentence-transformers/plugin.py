"""Local embedding provider using the `sentence-transformers` library.

The model is loaded lazily on the first call to `embed` (or `health_check`)
so importing the plugin doesn't pull torch into memory. The blocking
`SentenceTransformer.encode` call is dispatched to the asyncio default
executor so the event loop stays responsive.

Vector dimensions are reported from the model's own
`get_sentence_embedding_dimension()` once it's been loaded; before that
the provider returns 0 so callers know the model hasn't initialised yet
(the embedding cache and routing tables key on (model_id, dimensions),
so they shouldn't ask for the dimension count before warm-up).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from grimoire.types.common import HealthLevel, HealthStatus

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "sentence-transformers/all-mpnet-base-v2"


class SentenceTransformersEmbeddingProvider:
    id = "embed-sentence-transformers"
    name = "Sentence-Transformers Embeddings"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        self.model_id: str = str(cfg.get("model") or DEFAULT_MODEL)
        self._device: str | None = cfg.get("device") or None
        self._normalize: bool = bool(cfg.get("normalize", True))
        self._batch_size: int = int(cfg.get("batch_size", 32))
        self._cache_folder: str | None = cfg.get("cache_folder") or None
        self._model: Any | None = None
        self._load_lock = asyncio.Lock()
        # Reported as 0 until the model has been loaded; we don't know the
        # dimension count without inspecting the loaded model.
        self.dimensions: int = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = await self._ensure_model()
        loop = asyncio.get_running_loop()
        # `encode` is CPU/GPU bound and blocking; push it to the default
        # executor so we don't stall the event loop.
        vectors = await loop.run_in_executor(
            None,
            lambda: model.encode(
                texts,
                batch_size=self._batch_size,
                normalize_embeddings=self._normalize,
                convert_to_numpy=True,
                show_progress_bar=False,
            ),
        )
        return [[float(v) for v in row] for row in vectors]

    async def health_check(self) -> HealthStatus:
        try:
            await self._ensure_model()
        except ModuleNotFoundError as exc:
            return HealthStatus(
                level=HealthLevel.UNCONFIGURED,
                target_id=self.id,
                message=(
                    "sentence-transformers is not installed; "
                    f"install plugin requirements ({exc.name})"
                ),
            )
        except Exception as exc:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message=f"failed to load model {self.model_id!r}: {exc!r}",
            )
        return HealthStatus(
            level=HealthLevel.HEALTHY,
            target_id=self.id,
            message=f"model {self.model_id!r} loaded ({self.dimensions} dims)",
        )

    async def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is not None:
                return self._model
            loop = asyncio.get_running_loop()
            model = await loop.run_in_executor(None, self._load_model_blocking)
            dims = model.get_sentence_embedding_dimension()
            self.dimensions = int(dims) if dims is not None else 0
            self._model = model
            return model

    def _load_model_blocking(self) -> Any:
        try:
            from sentence_transformers import SentenceTransformer
        except ModuleNotFoundError:
            raise
        kwargs: dict[str, Any] = {}
        if self._device:
            kwargs["device"] = self._device
        if self._cache_folder:
            kwargs["cache_folder"] = self._cache_folder
        logger.info("loading SentenceTransformer model %s", self.model_id)
        return SentenceTransformer(self.model_id, **kwargs)


__all__ = ["SentenceTransformersEmbeddingProvider"]
