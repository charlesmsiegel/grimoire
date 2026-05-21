"""ImageGen LRU cache is bounded and cleared on aclose.

Regression for the BUGS.md HIGH item: ``_results`` and ``_cache`` grew
without bound (every seeded GenerationResult retained for the life of
the service, image bytes included) — long-running servers OOM.
"""

from __future__ import annotations

from grimoire.imagegen import BackendRegistry, ImageGenService
from grimoire.imagegen.config import ImageGenConfig
from grimoire.state_store import StateStore
from grimoire.types.imagegen import GenerationRequest, GenerationResult


def _result(seed: int) -> GenerationResult:
    return GenerationResult(
        image_bytes=b"x" * 8,
        thumbnail_bytes=b"t" * 4,
        backend="diffusers-memory",
        model="m",
        seed=seed,
    )


def _request(seed: int) -> GenerationRequest:
    return GenerationRequest(prompt=f"prompt-{seed}", seed=seed)


async def test_cache_evicts_oldest_when_at_capacity(
    store: StateStore, registry: BackendRegistry
) -> None:
    svc = ImageGenService(
        store=store,
        registry=registry,
        default_backend_id="diffusers-memory",
        config=ImageGenConfig(caching_max_entries=2),
    )
    try:
        backend = registry.get("diffusers-memory")
        for seed in (1, 2, 3):
            svc._store_in_cache(
                "camp-1",
                _request(seed),
                backend=backend,
                result=_result(seed),
                image_id=f"img-{seed}",
            )

        assert len(svc._cache) == 2
        assert len(svc._results) == 2
        # Seed 1 (the oldest) was evicted from both halves of the pair.
        assert svc._lookup_cache("camp-1", _request(1), backend=backend) is None
        assert svc._lookup_cache("camp-1", _request(2), backend=backend) is not None
        assert svc._lookup_cache("camp-1", _request(3), backend=backend) is not None
    finally:
        await svc.aclose()


async def test_cache_lookup_promotes_entry_to_most_recently_used(
    store: StateStore, registry: BackendRegistry
) -> None:
    svc = ImageGenService(
        store=store,
        registry=registry,
        default_backend_id="diffusers-memory",
        config=ImageGenConfig(caching_max_entries=2),
    )
    try:
        backend = registry.get("diffusers-memory")
        svc._store_in_cache(
            "camp-1", _request(1), backend=backend, result=_result(1), image_id="img-1"
        )
        svc._store_in_cache(
            "camp-1", _request(2), backend=backend, result=_result(2), image_id="img-2"
        )
        # Touch seed=1 so it becomes MRU; seed=2 is now the oldest.
        svc._lookup_cache("camp-1", _request(1), backend=backend)
        svc._store_in_cache(
            "camp-1", _request(3), backend=backend, result=_result(3), image_id="img-3"
        )

        assert svc._lookup_cache("camp-1", _request(1), backend=backend) is not None
        assert svc._lookup_cache("camp-1", _request(2), backend=backend) is None
        assert svc._lookup_cache("camp-1", _request(3), backend=backend) is not None
    finally:
        await svc.aclose()


async def test_aclose_clears_results_and_cache(
    store: StateStore, registry: BackendRegistry
) -> None:
    svc = ImageGenService(
        store=store,
        registry=registry,
        default_backend_id="diffusers-memory",
    )
    backend = registry.get("diffusers-memory")
    svc._store_in_cache("camp-1", _request(1), backend=backend, result=_result(1), image_id="img-1")
    assert svc._cache and svc._results

    await svc.aclose()

    assert not svc._cache
    assert not svc._results
