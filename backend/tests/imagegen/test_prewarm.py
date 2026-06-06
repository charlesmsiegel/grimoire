"""§12 ImageGenService.prewarm(backend_id)."""

from __future__ import annotations

import pytest

from grimoire.imagegen import InMemoryDiffusersBackend


class _EnsurePipelineStub(InMemoryDiffusersBackend):
    """Backend whose lazy-load hook records that it ran (observable state)."""

    id = "warm-ensure"
    name = "Warm (ensure_pipeline)"

    def __init__(self) -> None:
        super().__init__()
        self.warmed = False

    async def _ensure_pipeline(self):  # type: ignore[override]
        self.warmed = True
        return None


class _PrewarmHookStub(InMemoryDiffusersBackend):
    """Backend that exposes the public ``prewarm`` hook instead."""

    id = "warm-public"
    name = "Warm (prewarm)"

    def __init__(self) -> None:
        super().__init__()
        self.warmed = False

    async def prewarm(self) -> None:
        self.warmed = True


async def test_prewarm_loads_backend_pipeline(service) -> None:
    # prewarm must drive the backend's lazy-load hook to warm state.
    svc, _ = service
    backend = _EnsurePipelineStub()
    svc.registry.register(backend)
    await svc.prewarm("warm-ensure")
    assert backend.warmed is True


async def test_prewarm_honours_public_prewarm_hook(service) -> None:
    # A backend exposing the public ``prewarm`` coroutine is warmed too.
    svc, _ = service
    backend = _PrewarmHookStub()
    svc.registry.register(backend)
    await svc.prewarm("warm-public")
    assert backend.warmed is True


async def test_prewarm_skips_when_backend_has_no_hook(service) -> None:
    svc, _ = service
    # In-memory backend has no warm hook by default; call should not raise.
    await svc.prewarm("diffusers-memory")


async def test_prewarm_unknown_backend_raises(service) -> None:
    svc, _ = service
    with pytest.raises(KeyError):
        await svc.prewarm("nope")
