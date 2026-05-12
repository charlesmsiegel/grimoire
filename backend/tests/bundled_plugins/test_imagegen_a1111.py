"""Tests for the bundled `imagegen-a1111` plugin.

The HTTP layer is mocked with `httpx.MockTransport` so tests don't need
a running a1111 server. The mock transport returns canned responses in
the shape a1111's `/sdapi/v1/*` routes use.
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
    # 1x1 transparent PNG.
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
        headers={"Content-Type": "application/json"},
        transport=httpx.MockTransport(_capture),
    )
    return captured


def _default_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/sdapi/v1/txt2img":
        body = json.loads(request.content.decode("utf-8"))
        info = json.dumps(
            {
                "seed": body.get("seed", 0),
                "sd_model_name": "sd_xl_base_1.0",
                "steps": body.get("steps"),
                "cfg_scale": body.get("cfg_scale"),
                "sampler_name": body.get("sampler_name"),
                "negative_prompt": body.get("negative_prompt", ""),
            }
        )
        return httpx.Response(200, json={"images": [_b64_png()], "info": info})
    if path == "/sdapi/v1/img2img":
        body = json.loads(request.content.decode("utf-8"))
        info = json.dumps({"seed": body.get("seed", 0), "sd_model_name": "model"})
        return httpx.Response(200, json={"images": [_b64_png()], "info": info})
    if path == "/sdapi/v1/sd-models":
        return httpx.Response(
            200,
            json=[
                {"title": "sd_xl_base_1.0 [abc]", "model_name": "sd_xl_base_1.0"},
                {"title": "anime_v2 [def]", "model_name": "anime_v2"},
            ],
        )
    if path == "/sdapi/v1/samplers":
        return httpx.Response(
            200, json=[{"name": "DPM++ 2M Karras"}, {"name": "Euler a"}, {"name": "UniPC"}]
        )
    return httpx.Response(404, json={"error": "not found"})


def test_manifest_discovers_and_loads() -> None:
    result = load_bundled("imagegen-a1111")
    assert result.ok, result.errors
    assert result.manifest is not None
    assert PluginKind.IMAGEGEN_BACKEND in result.manifest.implements
    instance = result.instances[0].instance
    assert_protocol_attrs(
        instance,
        ["id", "name", "capabilities", "generate", "list_models", "list_samplers", "health_check"],
    )
    assert instance.id == "imagegen-a1111"


def test_defaults(a1111_module) -> None:
    backend = a1111_module.A1111ImageGenBackend()
    assert backend.id == "imagegen-a1111"
    assert backend._base_url == "http://127.0.0.1:7860"
    assert backend.capabilities.text_to_image is True


@pytest.mark.asyncio
async def test_generate_txt2img(a1111_module) -> None:
    backend = a1111_module.A1111ImageGenBackend()
    requests = _install_mock_transport(backend, _default_handler)
    result = await backend.generate(
        GenerationRequest(
            prompt="a knight in a sunlit grove",
            negative_prompt="blurry",
            width=512,
            height=512,
            steps=4,
            cfg_scale=6.0,
            sampler="Euler a",
            seed=42,
        )
    )

    assert result.image_bytes
    assert result.seed == 42
    assert result.backend == "imagegen-a1111"
    assert result.model == "sd_xl_base_1.0"
    txt2img_calls = [r for r in requests if r.url.path == "/sdapi/v1/txt2img"]
    assert len(txt2img_calls) == 1
    sent = json.loads(txt2img_calls[0].content.decode("utf-8"))
    assert sent["prompt"] == "a knight in a sunlit grove"
    assert sent["negative_prompt"] == "blurry"
    assert sent["sampler_name"] == "Euler a"
    assert sent["seed"] == 42


@pytest.mark.asyncio
async def test_generate_random_seed(a1111_module) -> None:
    backend = a1111_module.A1111ImageGenBackend()
    _install_mock_transport(backend, _default_handler)
    result = await backend.generate(
        GenerationRequest(prompt="x", width=64, height=64, steps=1, cfg_scale=1.0, seed=None)
    )
    assert result.seed >= 0  # backend assigned its own


@pytest.mark.asyncio
async def test_generate_img2img(a1111_module) -> None:
    backend = a1111_module.A1111ImageGenBackend()
    requests = _install_mock_transport(backend, _default_handler)
    await backend.generate(
        GenerationRequest(
            prompt="repaint",
            width=64,
            height=64,
            steps=1,
            cfg_scale=1.0,
            seed=7,
            init_image=_png_bytes(),
            init_image_strength=0.55,
        )
    )
    img2img_calls = [r for r in requests if r.url.path == "/sdapi/v1/img2img"]
    assert len(img2img_calls) == 1
    sent = json.loads(img2img_calls[0].content.decode("utf-8"))
    assert sent["init_images"]
    assert sent["denoising_strength"] == 0.55


@pytest.mark.asyncio
async def test_generate_raises_on_http_error(a1111_module) -> None:
    backend = a1111_module.A1111ImageGenBackend()
    _install_mock_transport(backend, lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(RuntimeError, match="500"):
        await backend.generate(
            GenerationRequest(prompt="x", width=64, height=64, steps=1, cfg_scale=1.0, seed=1)
        )


@pytest.mark.asyncio
async def test_list_models_and_samplers(a1111_module) -> None:
    backend = a1111_module.A1111ImageGenBackend()
    _install_mock_transport(backend, _default_handler)
    models = await backend.list_models()
    assert [m.id for m in models] == ["sd_xl_base_1.0 [abc]", "anime_v2 [def]"]
    samplers = await backend.list_samplers()
    assert "Euler a" in samplers


@pytest.mark.asyncio
async def test_list_samplers_falls_back_on_error(a1111_module) -> None:
    backend = a1111_module.A1111ImageGenBackend()
    _install_mock_transport(backend, lambda r: httpx.Response(500))
    samplers = await backend.list_samplers()
    assert samplers  # non-empty fallback


@pytest.mark.asyncio
async def test_health_check_healthy(a1111_module) -> None:
    backend = a1111_module.A1111ImageGenBackend()
    _install_mock_transport(backend, _default_handler)
    status = await backend.health_check()
    assert status.level == HealthLevel.HEALTHY


@pytest.mark.asyncio
async def test_health_check_unhealthy_on_500(a1111_module) -> None:
    backend = a1111_module.A1111ImageGenBackend()
    _install_mock_transport(backend, lambda r: httpx.Response(500))
    status = await backend.health_check()
    assert status.level == HealthLevel.UNHEALTHY


@pytest.mark.asyncio
async def test_health_check_unconfigured_on_401(a1111_module) -> None:
    backend = a1111_module.A1111ImageGenBackend()
    _install_mock_transport(backend, lambda r: httpx.Response(401))
    status = await backend.health_check()
    assert status.level == HealthLevel.UNCONFIGURED


@pytest.mark.asyncio
async def test_conformance(a1111_module) -> None:
    backend = a1111_module.A1111ImageGenBackend()
    _install_mock_transport(backend, _default_handler)
    report = await ImageGenBackendConformance().run(backend)
    assert report.ok, report.failed
