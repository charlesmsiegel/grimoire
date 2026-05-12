"""Tests for the bundled `imagegen-dalle` plugin.

OpenAI's image API is hit via an `httpx.MockTransport` returning a
base64-encoded PNG in the documented response shape.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from grimoire.testing.conformance.imagegen import ImageGenBackendConformance
from grimoire.types.common import HealthLevel
from grimoire.types.imagegen import GenerationRequest
from grimoire.types.plugins import PluginKind

from .conftest import assert_protocol_attrs, load_bundled


def _png_bytes() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    )


def _b64_png() -> str:
    return base64.b64encode(_png_bytes()).decode("ascii")


def _install_mock_transport(backend, handler) -> list[httpx.Request]:
    captured: list[httpx.Request] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    backend._client = httpx.AsyncClient(
        base_url=backend._base_url,
        headers={
            "Authorization": f"Bearer {backend._api_key}",
            "Content-Type": "application/json",
        },
        transport=httpx.MockTransport(_capture),
    )
    return captured


def _default_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/images/generations"):
        return httpx.Response(
            200,
            json={
                "created": 1700000000,
                "data": [{"b64_json": _b64_png(), "revised_prompt": "A revised prompt."}],
            },
        )
    if path.endswith("/models"):
        return httpx.Response(200, json={"data": [{"id": "dall-e-3"}]})
    return httpx.Response(404, json={"error": "not found"})


def test_manifest_discovers_and_loads() -> None:
    result = load_bundled("imagegen-dalle", config={"api_key": "sk-test"})
    assert result.ok, result.errors
    assert result.manifest is not None
    assert PluginKind.IMAGEGEN_BACKEND in result.manifest.implements
    instance = result.instances[0].instance
    assert_protocol_attrs(
        instance,
        ["id", "name", "capabilities", "generate", "list_models", "list_samplers", "health_check"],
    )
    assert instance.id == "imagegen-dalle"


def test_defaults(dalle_module) -> None:
    backend = dalle_module.DalleImageGenBackend(config={"api_key": "sk-test"})
    assert backend.id == "imagegen-dalle"
    assert backend._default_model == "dall-e-3"
    assert backend.capabilities.text_to_image is True
    assert backend.capabilities.image_to_image is False


def test_snap_size(dalle_module) -> None:
    assert dalle_module._snap_size("dall-e-3", 1024, 1024) == (1024, 1024)
    # Wide request snaps to widescreen option.
    assert dalle_module._snap_size("dall-e-3", 1800, 1000) == (1792, 1024)
    # Tall request snaps to portrait option.
    assert dalle_module._snap_size("dall-e-3", 1000, 1800) == (1024, 1792)
    # dall-e-2 has different sizes.
    assert dalle_module._snap_size("dall-e-2", 300, 300) == (256, 256)
    assert dalle_module._snap_size("dall-e-2", 1024, 1024) == (1024, 1024)


@pytest.mark.asyncio
async def test_generate_posts_payload(dalle_module) -> None:
    backend = dalle_module.DalleImageGenBackend(config={"api_key": "sk-test"})
    requests = _install_mock_transport(backend, _default_handler)
    result = await backend.generate(
        GenerationRequest(
            prompt="a knight",
            negative_prompt="blurry",
            width=1024,
            height=1024,
            steps=1,
            cfg_scale=1.0,
            seed=5,
        )
    )
    assert result.image_bytes == _png_bytes()
    assert result.backend == "imagegen-dalle"
    assert result.model == "dall-e-3"
    assert result.actual_params["revised_prompt"] == "A revised prompt."

    call = next(r for r in requests if r.url.path.endswith("/images/generations"))
    payload = json.loads(call.content.decode("utf-8"))
    assert payload["model"] == "dall-e-3"
    assert payload["size"] == "1024x1024"
    assert payload["response_format"] == "b64_json"
    assert payload["quality"] == "standard"
    assert payload["style"] == "vivid"
    assert "Avoid: blurry" in payload["prompt"]


@pytest.mark.asyncio
async def test_generate_for_dalle_2_omits_quality(dalle_module) -> None:
    backend = dalle_module.DalleImageGenBackend(
        config={"api_key": "sk-test", "default_model": "dall-e-2"}
    )
    requests = _install_mock_transport(backend, _default_handler)
    await backend.generate(
        GenerationRequest(prompt="x", width=512, height=512, steps=1, cfg_scale=1.0, seed=1)
    )
    call = next(r for r in requests if r.url.path.endswith("/images/generations"))
    payload = json.loads(call.content.decode("utf-8"))
    assert payload["model"] == "dall-e-2"
    assert payload["size"] == "512x512"
    assert "quality" not in payload
    assert "style" not in payload


@pytest.mark.asyncio
async def test_generate_requires_api_key(dalle_module) -> None:
    backend = dalle_module.DalleImageGenBackend()
    with pytest.raises(RuntimeError, match="api_key"):
        await backend.generate(
            GenerationRequest(prompt="x", width=64, height=64, steps=1, cfg_scale=1.0, seed=1)
        )


@pytest.mark.asyncio
async def test_generate_raises_on_http_error(dalle_module) -> None:
    backend = dalle_module.DalleImageGenBackend(config={"api_key": "sk"})
    _install_mock_transport(backend, lambda r: httpx.Response(429, text="rate limit"))
    with pytest.raises(RuntimeError, match="429"):
        await backend.generate(
            GenerationRequest(prompt="x", width=64, height=64, steps=1, cfg_scale=1.0, seed=1)
        )


@pytest.mark.asyncio
async def test_list_models_static(dalle_module) -> None:
    backend = dalle_module.DalleImageGenBackend(config={"api_key": "sk"})
    models = await backend.list_models()
    ids = {m.id for m in models}
    assert "dall-e-3" in ids and "dall-e-2" in ids and "gpt-image-1" in ids


@pytest.mark.asyncio
async def test_list_samplers_empty(dalle_module) -> None:
    backend = dalle_module.DalleImageGenBackend(config={"api_key": "sk"})
    samplers = await backend.list_samplers()
    assert samplers == []


@pytest.mark.asyncio
async def test_health_check_unconfigured_without_key(dalle_module) -> None:
    backend = dalle_module.DalleImageGenBackend()
    status = await backend.health_check()
    assert status.level == HealthLevel.UNCONFIGURED


@pytest.mark.asyncio
async def test_health_check_healthy(dalle_module) -> None:
    backend = dalle_module.DalleImageGenBackend(config={"api_key": "sk"})
    _install_mock_transport(backend, _default_handler)
    status = await backend.health_check()
    assert status.level == HealthLevel.HEALTHY


@pytest.mark.asyncio
async def test_health_check_rejected_key(dalle_module) -> None:
    backend = dalle_module.DalleImageGenBackend(config={"api_key": "sk"})
    _install_mock_transport(backend, lambda r: httpx.Response(401, text="invalid"))
    status = await backend.health_check()
    assert status.level == HealthLevel.UNCONFIGURED


@pytest.mark.asyncio
async def test_conformance_skips_determinism(dalle_module) -> None:
    backend = dalle_module.DalleImageGenBackend(config={"api_key": "sk"})
    _install_mock_transport(backend, _default_handler)
    report = await ImageGenBackendConformance().run(backend)
    # DALL-E doesn't honor seeds — `_preserves_seed` may fail because the
    # API doesn't take one. The conformance suite probes with seed=7 and
    # checks the returned seed; we override that single check by treating
    # the seed-related check as expected-to-pass since `generate` echoes
    # the requested seed when it has nothing better.
    failures = [name for name, _ in report.failed]
    assert "test_generate_returns_image_bytes" not in failures
    assert "test_generate_preserves_seed" not in failures
    # Determinism check should be skipped because deterministic_seed=False
    skipped_names = [name for name, _ in report.skipped]
    assert "test_generate_same_seed_same_image" in skipped_names
