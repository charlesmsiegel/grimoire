"""Tests for the bundled `imagegen-comfyui` plugin.

ComfyUI's protocol is multi-step (POST /prompt → poll /history/{id} →
GET /view). The mock transport implements that handshake so the
generate path can be exercised end-to-end without a server.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

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


def _make_handler(prompt_id: str = "abc123"):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/prompt":
            return httpx.Response(
                200,
                json={"prompt_id": prompt_id, "number": 1, "node_errors": {}},
            )
        if path == f"/history/{prompt_id}":
            return httpx.Response(
                200,
                json={
                    prompt_id: {
                        "outputs": {
                            "9": {
                                "images": [
                                    {
                                        "filename": "grimoire_00001_.png",
                                        "subfolder": "",
                                        "type": "output",
                                    }
                                ]
                            }
                        }
                    }
                },
            )
        if path == "/view":
            return httpx.Response(200, content=_png_bytes())
        if path == "/object_info/CheckpointLoaderSimple":
            return httpx.Response(
                200,
                json={
                    "CheckpointLoaderSimple": {
                        "input": {
                            "required": {
                                "ckpt_name": [
                                    ["sd_xl_base_1.0.safetensors", "anime.safetensors"],
                                    {},
                                ]
                            }
                        }
                    }
                },
            )
        if path == "/object_info/KSampler":
            return httpx.Response(
                200,
                json={
                    "KSampler": {
                        "input": {"required": {"sampler_name": [["euler", "dpmpp_2m"], {}]}}
                    }
                },
            )
        if path == "/system_stats":
            return httpx.Response(200, json={"system": {"os": "linux"}})
        return httpx.Response(404, json={"error": "not found"})

    return handler


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


def test_manifest_discovers_and_loads() -> None:
    result = load_bundled("imagegen-comfyui")
    assert result.ok, result.errors
    assert result.manifest is not None
    assert PluginKind.IMAGEGEN_BACKEND in result.manifest.implements
    instance = result.instances[0].instance
    assert_protocol_attrs(
        instance,
        ["id", "name", "capabilities", "generate", "list_models", "list_samplers", "health_check"],
    )
    assert instance.id == "imagegen-comfyui"


def test_defaults(comfyui_module) -> None:
    backend = comfyui_module.ComfyUIImageGenBackend()
    assert backend.id == "imagegen-comfyui"
    assert backend._base_url == "http://127.0.0.1:8188"
    assert backend.capabilities.text_to_image is True


def test_fill_workflow_sets_known_fields(comfyui_module) -> None:
    workflow = comfyui_module._minimal_sdxl_workflow()
    request = GenerationRequest(
        prompt="forest at dusk",
        negative_prompt="blurry, low quality",
        width=768,
        height=512,
        steps=10,
        cfg_scale=7.5,
        sampler="dpmpp_2m",
        seed=99,
    )
    filled = comfyui_module._fill_workflow(workflow, request, seed=99)
    ksampler = filled["3"]["inputs"]
    assert ksampler["seed"] == 99
    assert ksampler["steps"] == 10
    assert ksampler["cfg"] == 7.5
    assert ksampler["sampler_name"] == "dpmpp_2m"
    assert filled["5"]["inputs"]["width"] == 768
    assert filled["5"]["inputs"]["height"] == 512
    assert filled["6"]["inputs"]["text"] == "forest at dusk"
    assert filled["7"]["inputs"]["text"] == "blurry, low quality"


@pytest.mark.asyncio
async def test_generate_full_flow(comfyui_module) -> None:
    backend = comfyui_module.ComfyUIImageGenBackend(config={"poll_interval_seconds": 0.001})
    requests = _install_mock_transport(backend, _make_handler())
    result = await backend.generate(
        GenerationRequest(
            prompt="x", width=64, height=64, steps=1, cfg_scale=1.0, sampler="euler", seed=5
        )
    )
    assert result.image_bytes == _png_bytes()
    assert result.seed == 5
    paths = [r.url.path for r in requests]
    assert "/prompt" in paths
    assert "/history/abc123" in paths
    assert "/view" in paths

    prompt_call = next(r for r in requests if r.url.path == "/prompt")
    payload = json.loads(prompt_call.content.decode("utf-8"))
    assert "prompt" in payload
    assert payload["client_id"] == backend._client_id


@pytest.mark.asyncio
async def test_generate_raises_on_prompt_error(comfyui_module) -> None:
    backend = comfyui_module.ComfyUIImageGenBackend(config={"poll_interval_seconds": 0.001})
    _install_mock_transport(backend, lambda r: httpx.Response(400, json={"error": "bad workflow"}))
    with pytest.raises(RuntimeError, match="/prompt"):
        await backend.generate(
            GenerationRequest(prompt="x", width=64, height=64, steps=1, cfg_scale=1.0, seed=1)
        )


@pytest.mark.asyncio
async def test_workflow_loaded_from_directory(comfyui_module, tmp_path: Path) -> None:
    workflows = tmp_path / "wf"
    workflows.mkdir()
    custom = comfyui_module._minimal_sdxl_workflow()
    custom["3"]["inputs"]["steps"] = 5  # tweak so we can confirm load happened
    (workflows / "sdxl.json").write_text(json.dumps(custom), encoding="utf-8")
    (workflows / "default.json").write_text(json.dumps(custom), encoding="utf-8")

    backend = comfyui_module.ComfyUIImageGenBackend(
        config={"workflows_dir": str(workflows), "default_workflow": "default"}
    )
    assert backend.list_workflows() == ["default", "sdxl"]

    # Selecting by model
    request = GenerationRequest(
        prompt="x", width=64, height=64, steps=1, cfg_scale=1.0, seed=1, model="sdxl"
    )
    chosen = backend._resolve_workflow(request)
    assert chosen["3"]["inputs"]["steps"] == 5


@pytest.mark.asyncio
async def test_list_models_and_samplers(comfyui_module) -> None:
    backend = comfyui_module.ComfyUIImageGenBackend()
    _install_mock_transport(backend, _make_handler())
    models = await backend.list_models()
    assert [m.id for m in models] == ["sd_xl_base_1.0.safetensors", "anime.safetensors"]
    samplers = await backend.list_samplers()
    assert samplers == ["euler", "dpmpp_2m"]


@pytest.mark.asyncio
async def test_list_samplers_falls_back(comfyui_module) -> None:
    backend = comfyui_module.ComfyUIImageGenBackend()
    _install_mock_transport(backend, lambda r: httpx.Response(500))
    samplers = await backend.list_samplers()
    assert "euler" in samplers


@pytest.mark.asyncio
async def test_health_check_healthy(comfyui_module) -> None:
    backend = comfyui_module.ComfyUIImageGenBackend()
    _install_mock_transport(backend, _make_handler())
    status = await backend.health_check()
    assert status.level == HealthLevel.HEALTHY


@pytest.mark.asyncio
async def test_health_check_unhealthy_on_500(comfyui_module) -> None:
    backend = comfyui_module.ComfyUIImageGenBackend()
    _install_mock_transport(backend, lambda r: httpx.Response(500))
    status = await backend.health_check()
    assert status.level == HealthLevel.UNHEALTHY


@pytest.mark.asyncio
async def test_conformance(comfyui_module) -> None:
    backend = comfyui_module.ComfyUIImageGenBackend(config={"poll_interval_seconds": 0.001})
    _install_mock_transport(backend, _make_handler())
    report = await ImageGenBackendConformance().run(backend)
    assert report.ok, report.failed
