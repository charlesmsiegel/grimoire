"""Backend-level tests for ImageGen."""

from __future__ import annotations

from grimoire.imagegen import (
    InMemoryDiffusersBackend,
    IntegratedDiffusersBackend,
    cache_key_for_request,
)
from grimoire.imagegen.backend import synthesize_png
from grimoire.types.common import HealthLevel
from grimoire.types.imagegen import GenerationRequest


def _request(prompt: str = "hello", seed: int | None = 1) -> GenerationRequest:
    return GenerationRequest(prompt=prompt, width=32, height=32, steps=1, cfg_scale=1.0, seed=seed)


async def test_in_memory_backend_generates_image_bytes() -> None:
    backend = InMemoryDiffusersBackend()
    result = await backend.generate(_request())
    assert result.image_bytes.startswith(b"\x89PNG")
    assert result.thumbnail_bytes.startswith(b"\x89PNG")
    assert result.seed == 1
    assert result.backend == "diffusers-memory"


async def test_in_memory_backend_deterministic_seed() -> None:
    backend = InMemoryDiffusersBackend()
    a = await backend.generate(_request(seed=42))
    b = await backend.generate(_request(seed=42))
    assert a.image_bytes == b.image_bytes
    c = await backend.generate(_request(seed=99))
    assert a.image_bytes != c.image_bytes


async def test_in_memory_backend_random_seed_when_none() -> None:
    backend = InMemoryDiffusersBackend()
    result = await backend.generate(_request(seed=None))
    assert result.seed is not None
    assert result.seed >= 0


async def test_in_memory_backend_health_is_healthy() -> None:
    backend = InMemoryDiffusersBackend()
    status = await backend.health_check()
    assert status.level == HealthLevel.HEALTHY


async def test_in_memory_backend_lists_samplers_and_models() -> None:
    backend = InMemoryDiffusersBackend()
    samplers = await backend.list_samplers()
    models = await backend.list_models()
    assert isinstance(samplers, list) and samplers
    assert len(models) == 1
    assert models[0].id


async def test_integrated_diffusers_backend_health_when_torch_missing() -> None:
    # The real ``diffusers``/``torch`` deps aren't pinned in this project,
    # so :meth:`health_check` should report UNCONFIGURED instead of
    # raising — that's how the service knows to fall back.
    backend = IntegratedDiffusersBackend()
    try:
        import diffusers  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        status = await backend.health_check()
        assert status.level == HealthLevel.UNCONFIGURED
        assert "diffusers" in status.message or "torch" in status.message
    else:  # pragma: no cover - exercised only with the optional deps
        status = await backend.health_check()
        assert status.level == HealthLevel.HEALTHY


def test_cache_key_is_stable_for_same_request() -> None:
    a = cache_key_for_request(_request(prompt="x", seed=3))
    b = cache_key_for_request(_request(prompt="x", seed=3))
    assert a == b


def test_cache_key_differs_on_prompt_or_seed() -> None:
    base = cache_key_for_request(_request(prompt="x", seed=3))
    other_prompt = cache_key_for_request(_request(prompt="y", seed=3))
    other_seed = cache_key_for_request(_request(prompt="x", seed=4))
    assert base != other_prompt
    assert base != other_seed


def test_synthesize_png_emits_valid_png_header() -> None:
    png = synthesize_png(8, 8, seed=0)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert png[-8:-4] == b"IEND"
