"""Cloud embedding provider that calls the OpenAI embeddings API.

Uses an `httpx.AsyncClient` so requests share a single connection pool.
The client is created lazily on first call so importing the plugin
doesn't open network resources until the gateway actually routes through
it.

Dimensions come from the configured `dimensions` value (if any) or from
a small table of known model dimensions. If the configured model isn't
listed, `dimensions` stays at 0 until the first successful `embed` call
fills it in from the response payload.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from grimoire.types.common import HealthLevel, HealthStatus

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "text-embedding-3-small"
DEFAULT_BASE_URL = "https://api.openai.com/v1"

# Known native dimension counts for the OpenAI embedding models. Used so
# `dimensions` is populated before the first call; not authoritative —
# the response is trusted over this table.
KNOWN_DIMENSIONS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class OpenAIEmbeddingProvider:
    id = "embed-openai"
    name = "OpenAI Embeddings"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        self._api_key: str | None = cfg.get("api_key") or None
        self._base_url: str = str(cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        self.model_id: str = str(cfg.get("model") or DEFAULT_MODEL)
        self._configured_dimensions: int | None = (
            int(cfg["dimensions"]) if cfg.get("dimensions") is not None else None
        )
        self._organization: str | None = cfg.get("organization") or None
        self._timeout: float = float(cfg.get("timeout_seconds", 30))
        self.dimensions: int = self._configured_dimensions or KNOWN_DIMENSIONS.get(self.model_id, 0)
        self._client: Any | None = None
        self._client_lock = asyncio.Lock()

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
