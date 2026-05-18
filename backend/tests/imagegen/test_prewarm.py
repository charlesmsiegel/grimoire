"""§12 ImageGenService.prewarm(backend_id)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


async def test_prewarm_calls_backend_ensure_pipeline(service) -> None:
    svc, _ = service
    backend = svc.registry.get("diffusers-memory")
    backend._ensure_pipeline = AsyncMock(return_value=None)
    await svc.prewarm("diffusers-memory")
    backend._ensure_pipeline.assert_awaited_once()


async def test_prewarm_skips_when_backend_has_no_hook(service) -> None:
    svc, _ = service
    # In-memory backend has no _ensure_pipeline by default; call should not raise.
    await svc.prewarm("diffusers-memory")


async def test_prewarm_unknown_backend_raises(service) -> None:
    svc, _ = service
    with pytest.raises(KeyError):
        await svc.prewarm("nope")
