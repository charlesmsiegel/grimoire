"""Cloud embedding provider that calls the OpenAI embeddings API.

Uses an `httpx.AsyncClient` so requests share a single connection pool.
The client is created lazily on first call so importing the plugin
doesn't open network resources until the gateway actually routes through
it.

The active embedding model is configured via ``active_model`` and surfaced
to the UI by ``list_models()``, which hits OpenAI's ``/v1/models``
endpoint and keeps the rows whose id starts with one of the embedding
family prefixes. Dimensions come from the response payload, falling back
to a table of known counts when the catalog hasn't been fetched yet.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.llm import ModelInfo

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "text-embedding-3-small"
DEFAULT_BASE_URL = "https://api.openai.com/v1"

# Embedding model id prefixes published by OpenAI. Used to filter the
# `/v1/models` listing (which contains chat, audio, image, and embedding
# models in one bucket) without hard-coding the actual catalog.
EMBEDDING_PREFIXES: tuple[str, ...] = ("text-embedding-",)

# Known native dimension counts for the OpenAI embedding models. Used so
# the model picker can show a dimension count before the model has been
# called, and so `dimensions` is populated before the first call; not
# authoritative — the response is trusted over this table.
KNOWN_DIMENSIONS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}

# Published USD price per 1K input tokens for the OpenAI embedding models.
# OpenAI's `/v1/models` endpoint doesn't return pricing, so this table is
# the source of truth for what the picker shows. Update when OpenAI revises
# its embedding price list (https://openai.com/api/pricing/).
KNOWN_INPUT_COST_PER_1K: dict[str, float] = {
    "text-embedding-3-small": 0.00002,
    "text-embedding-3-large": 0.00013,
    "text-embedding-ada-002": 0.00010,
}


class OpenAIEmbeddingProvider:
    id = "embed-openai"
    name = "OpenAI Embeddings"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        self._api_key: str | None = cfg.get("api_key") or None
        self._base_url: str = str(cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        # Accept the legacy ``model`` key so saved configs from before the
        # rename keep working without manual migration.
        active = cfg.get("active_model") or cfg.get("model") or DEFAULT_MODEL
        self.model_id: str = str(active)
        self._configured_dimensions: int | None = (
            int(cfg["dimensions"]) if cfg.get("dimensions") is not None else None
        )
        self._organization: str | None = cfg.get("organization") or None
        self._timeout: float = float(cfg.get("timeout_seconds", 30))
        self.dimensions: int = self._configured_dimensions or KNOWN_DIMENSIONS.get(self.model_id, 0)
        self._client: Any | None = None
        self._client_lock = asyncio.Lock()
        self._models_cache: list[ModelInfo] | None = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self._api_key:
            raise RuntimeError("embed-openai: api_key is not configured")
        client = await self._ensure_client()
        payload: dict[str, Any] = {"model": self.model_id, "input": texts}
        if self._configured_dimensions is not None:
            payload["dimensions"] = self._configured_dimensions
        response = await client.post("/embeddings", json=payload)
        if response.status_code >= 400:
            text = response.text
            raise RuntimeError(f"embed-openai: request failed ({response.status_code}): {text}")
        data = response.json()
        rows = data.get("data") or []
        vectors: list[list[float]] = []
        for entry in rows:
            embedding = entry.get("embedding") or []
            vectors.append([float(v) for v in embedding])
        if vectors and self.dimensions != len(vectors[0]):
            self.dimensions = len(vectors[0])
        return vectors

    async def list_models(self) -> list[ModelInfo]:
        if self._models_cache is not None:
            return list(self._models_cache)
        if not self._api_key:
            raise RuntimeError("embed-openai: api_key is not configured")
        client = await self._ensure_client()
        try:
            response = await client.get("/models")
        except Exception as exc:
            raise RuntimeError(f"embed-openai: could not list models: {exc!r}") from exc
        if response.status_code >= 400:
            raise RuntimeError(
                f"embed-openai: /models returned HTTP {response.status_code}: {response.text}"
            )
        data = response.json()
        rows = data.get("data") or []
        models: list[ModelInfo] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            mid = str(row.get("id") or "")
            if not mid or not mid.startswith(EMBEDDING_PREFIXES):
                continue
            models.append(
                ModelInfo(
                    id=mid,
                    name=mid,
                    dimensions=KNOWN_DIMENSIONS.get(mid),
                    input_cost_per_1k=KNOWN_INPUT_COST_PER_1K.get(mid),
                )
            )
        # Always include the currently selected model so it shows up in
        # the picker even when /models prunes it (e.g. preview models).
        if self.model_id and not any(m.id == self.model_id for m in models):
            models.append(
                ModelInfo(
                    id=self.model_id,
                    name=self.model_id,
                    dimensions=KNOWN_DIMENSIONS.get(self.model_id),
                    input_cost_per_1k=KNOWN_INPUT_COST_PER_1K.get(self.model_id),
                )
            )
        models.sort(key=lambda m: m.id)
        self._models_cache = models
        return list(models)

    async def health_check(self) -> HealthStatus:
        if not self._api_key:
            return HealthStatus(
                level=HealthLevel.UNCONFIGURED,
                target_id=self.id,
                message="api_key is not configured",
            )
        try:
            await self.embed(["ping"])
        except ModuleNotFoundError as exc:
            return HealthStatus(
                level=HealthLevel.UNCONFIGURED,
                target_id=self.id,
                message=f"httpx is not installed ({exc.name})",
            )
        except Exception as exc:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message=f"embedding request failed: {exc!r}",
            )
        return HealthStatus(
            level=HealthLevel.HEALTHY,
            target_id=self.id,
            message=f"model {self.model_id!r} reachable ({self.dimensions} dims)",
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is not None:
                return self._client
            try:
                import httpx
            except ModuleNotFoundError:
                raise
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
            if self._organization:
                headers["OpenAI-Organization"] = self._organization
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=headers,
                timeout=self._timeout,
            )
            return self._client


__all__ = ["OpenAIEmbeddingProvider"]
