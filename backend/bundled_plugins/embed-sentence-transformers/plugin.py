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

`list_models()` returns a curated list of popular HuggingFace embedding
models with their published dimensions. The picker's "not in catalog"
fallback lets users type any HF model id (or local checkpoint path) that
isn't in the curated set.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.llm import ModelInfo

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "sentence-transformers/all-mpnet-base-v2"


# Curated list of popular HuggingFace embedding models that work with
# `sentence-transformers` out of the box. Dimensions are the published
# native counts. Not authoritative — the loaded model is trusted over
# this table for the actual `dimensions` attribute.
CURATED_MODELS: tuple[tuple[str, int, str], ...] = (
    ("sentence-transformers/all-MiniLM-L6-v2", 384, "MiniLM-L6 (fast, small)"),
    ("sentence-transformers/all-MiniLM-L12-v2", 384, "MiniLM-L12 (balanced)"),
    ("sentence-transformers/all-mpnet-base-v2", 768, "MPNet base (default, accurate)"),
    ("sentence-transformers/multi-qa-mpnet-base-dot-v1", 768, "MPNet for QA/search"),
    ("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", 384, "MiniLM multilingual"),
    ("sentence-transformers/paraphrase-multilingual-mpnet-base-v2", 768, "MPNet multilingual"),
    ("BAAI/bge-small-en-v1.5", 384, "BGE small (English)"),
    ("BAAI/bge-base-en-v1.5", 768, "BGE base (English)"),
    ("BAAI/bge-large-en-v1.5", 1024, "BGE large (English)"),
    ("BAAI/bge-m3", 1024, "BGE-M3 (multilingual, multi-vector)"),
    ("intfloat/e5-small-v2", 384, "E5 small"),
    ("intfloat/e5-base-v2", 768, "E5 base"),
    ("intfloat/e5-large-v2", 1024, "E5 large"),
    ("intfloat/multilingual-e5-large", 1024, "E5 large multilingual"),
    ("nomic-ai/nomic-embed-text-v1", 768, "Nomic Embed Text v1"),
    ("nomic-ai/nomic-embed-text-v1.5", 768, "Nomic Embed Text v1.5"),
    ("mixedbread-ai/mxbai-embed-large-v1", 1024, "Mixedbread MXBAI large"),
    ("Alibaba-NLP/gte-large-en-v1.5", 1024, "GTE large v1.5"),
    ("jinaai/jina-embeddings-v2-base-en", 768, "Jina v2 base (8k context)"),
)


class SentenceTransformersEmbeddingProvider:
    id = "embed-sentence-transformers"
    name = "Sentence-Transformers Embeddings"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        active = cfg.get("active_model") or cfg.get("model") or DEFAULT_MODEL
        self.model_id: str = str(active)
        self._device: str | None = cfg.get("device") or None
        self._normalize: bool = bool(cfg.get("normalize", True))
        self._batch_size: int = int(cfg.get("batch_size", 32))
        self._cache_folder: str | None = cfg.get("cache_folder") or None
        self._model: Any | None = None
        self._load_lock = asyncio.Lock()
        # Reported as 0 until the model has been loaded; we don't know the
        # dimension count without inspecting the loaded model. The curated
        # catalog populates this for known ids so the picker has a sensible
        # value even pre-load.
        self.dimensions: int = _curated_dimensions(self.model_id) or 0

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

    async def list_models(self) -> list[ModelInfo]:
        models = [
            ModelInfo(id=mid, name=label, dimensions=dims)
            for (mid, dims, label) in CURATED_MODELS
        ]
        if self.model_id and not any(m.id == self.model_id for m in models):
            models.append(
                ModelInfo(
                    id=self.model_id,
                    name=self.model_id,
                    dimensions=self.dimensions or None,
                )
            )
        return models

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


def _curated_dimensions(model_id: str) -> int | None:
    for mid, dims, _label in CURATED_MODELS:
        if mid == model_id:
            return dims
    return None


__all__ = ["SentenceTransformersEmbeddingProvider"]
