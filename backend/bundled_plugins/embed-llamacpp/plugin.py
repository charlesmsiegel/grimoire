"""Local GGUF embedding provider using llama-cpp-python.

Loads a GGUF embedding model lazily on the first call. The blocking
``Llama.embed`` call is dispatched to the asyncio default executor so
the event loop stays responsive. Supports any GGUF embedding model
(nomic-embed, BGE, E5, etc.).
"""

from __future__ import annotations

import asyncio
import math
import sys
import threading
from pathlib import Path
from typing import Any

from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.llm import ModelInfo

DEFAULT_N_CTX = 2048
DEFAULT_N_BATCH = 32


def _ensure_llama_cpp_importable(plugin: Any) -> None:
    """Make sure ``llama_cpp`` is importable, restoring the plugin venv path if needed."""
    try:
        import llama_cpp
    except ImportError:
        extra = getattr(plugin, "_plugin_sys_path", None)
        if not extra:
            raise
        if extra not in sys.path:
            sys.path.insert(0, extra)
        import llama_cpp  # noqa: F401


class LlamaCppEmbeddingProvider:
    id = "embed-llamacpp"
    name = "llama.cpp Embeddings"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        self.config = cfg
        self._model_path: str | None = cfg.get("model_path") or None
        self._n_ctx: int = int(cfg.get("n_ctx") or DEFAULT_N_CTX)
        self._n_threads: int | None = cfg.get("n_threads")
        self._n_gpu_layers: int = int(cfg.get("n_gpu_layers") or 0)
        self._n_batch: int = int(cfg.get("n_batch") or DEFAULT_N_BATCH)
        self._normalize: bool = bool(cfg.get("normalize", True))
        self.model_id: str = cfg.get("model_id") or (
            Path(self._model_path).stem if self._model_path else "local-gguf"
        )
        self.dimensions: int = 0
        self.max_batch_size: int = self._n_batch
        self._llama: Any = None
        self._load_lock = threading.Lock()
        self._inference_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # EmbeddingProvider protocol
    # ------------------------------------------------------------------ #

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        llama = self._get_llama()
        loop = asyncio.get_running_loop()
        def _embed() -> Any:
            with self._inference_lock:
                return llama.embed(texts)

        raw = await loop.run_in_executor(None, _embed)
        vectors: list[list[float]] = []
        for row in raw:
            vec = [float(v) for v in row]
            if self._normalize:
                vec = _l2_normalize(vec)
            vectors.append(vec)
        if vectors and self.dimensions != len(vectors[0]):
            self.dimensions = len(vectors[0])
        return vectors

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id=self.model_id,
                name=self.model_id,
                dimensions=self.dimensions or None,
            )
        ]

    async def health_check(self) -> HealthStatus:
        if not self._model_path:
            return HealthStatus(
                level=HealthLevel.UNCONFIGURED,
                target_id=self.id,
                message="model_path not configured",
            )
        path = Path(self._model_path)
        if not path.exists():
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message=f"model file not found at {self._model_path}",
            )
        try:
            _ensure_llama_cpp_importable(self)
        except ImportError:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message="llama-cpp-python not installed",
            )
        loaded = self._llama is not None
        return HealthStatus(
            level=HealthLevel.HEALTHY,
            target_id=self.id,
            message=(
                f"model loaded ({self.dimensions} dims)"
                if loaded
                else "model_path resolved (lazy load)"
            ),
        )

    # ------------------------------------------------------------------ #
    # Lazy model load
    # ------------------------------------------------------------------ #

    def _get_llama(self) -> Any:
        if self._llama is not None:
            return self._llama
        if not self._model_path:
            raise RuntimeError("embed-llamacpp: model_path not configured")
        if not Path(self._model_path).exists():
            raise RuntimeError(f"embed-llamacpp: model file not found at {self._model_path}")
        _ensure_llama_cpp_importable(self)
        from llama_cpp import Llama

        with self._load_lock:
            if self._llama is None:
                kwargs: dict[str, Any] = {
                    "model_path": self._model_path,
                    "n_ctx": self._n_ctx,
                    "n_gpu_layers": self._n_gpu_layers,
                    "embedding": True,
                    "verbose": False,
                }
                if self._n_threads is not None:
                    kwargs["n_threads"] = int(self._n_threads)
                self._llama = Llama(**kwargs)
        return self._llama


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


__all__ = ["LlamaCppEmbeddingProvider"]
