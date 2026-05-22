"""Tests for the bundled `imagegen-replicate` plugin.

Replicate's protocol is two-step (POST a prediction → poll until
terminal → GET the output URL). The mock transport implements the
handshake so the generate path can be exercised end-to-end without a
real API token.
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


OUTPUT_URL = "https://replicate.delivery/pbxt/abc/out-0.png"


def _make_handler(
    *,
    prediction_id: str = "pred-1",
    initial_status: str = "succeeded",
    poll_statuses: tuple[str, ...] = (),
    output: object = None,
):
    """Build a mock transport handler.

    `initial_status` is what the POST returns; `poll_statuses` is the
    sequence the GET polls walk through before the final `succeeded`.
    """

    if output is None:
        output = [OUTPUT_URL]
    poll_iter = iter(poll_statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        # `httpx` keeps the base URL's prefix on `request.url.path`, so
        # strip `/v1` for matching.
        path = request.url.path
        if path.startswith("/v1"):
            path = path[len("/v1") :] or "/"
        full_url = str(request.url)

        is_create = path == "/predictions" or (
            path.startswith("/models/") and path.endswith("/predictions")
        )
        if request.method == "POST" and is_create:
            body = {
                "id": prediction_id,
                "status": initial_status,
                "urls": {"get": f"https://api.replicate.com/v1/predictions/{prediction_id}"},
            }
            if initial_status == "succeeded":
                body["output"] = output
            return httpx.Response(201, json=body)

        if request.method == "GET" and path == f"/predictions/{prediction_id}":
            try:
                next_status = next(poll_iter)
            except StopIteration:
                next_status = "succeeded"
            body = {
                "id": prediction_id,
                "status": next_status,
                "urls": {"get": f"https://api.replicate.com/v1/predictions/{prediction_id}"},
            }
            if next_status == "succeeded":
                body["output"] = output
            elif next_status == "failed":
                body["error"] = "model crashed"
            return httpx.Response(200, json=body)

        if request.method == "GET" and full_url == OUTPUT_URL:
            return httpx.Response(200, content=_png_bytes())

        if request.method == "GET" and path == "/account":
            return httpx.Response(200, json={"username": "tester", "type": "user"})

        return httpx.Response(404, json={"error": f"unmocked {request.method} {full_url}"})

    return handler


def _install_mock_transport(backend, handler) -> list[httpx.Request]:
    captured: list[httpx.Request] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    backend._client = httpx.AsyncClient(
        base_url=backend._base_url,
        headers={
            "Authorization": f"Bearer {backend._api_token}",
            "Content-Type": "application/json",
        },
        transport=httpx.MockTransport(_capture),
    )
    # Unauthenticated client used for downloading the rendered image from
    # the CDN — wired to the same handler so the test transport can serve
    # both api.replicate.com and replicate.delivery URLs.
    backend._download_client = httpx.AsyncClient(transport=httpx.MockTransport(_capture))
    return captured


def test_manifest_discovers_and_loads() -> None:
    result = load_bundled("imagegen-replicate", config={"api_token": "r8-test"})
    assert result.ok, result.errors
    assert result.manifest is not None
    assert PluginKind.IMAGEGEN_BACKEND in result.manifest.implements
    instance = result.instances[0].instance
    assert_protocol_attrs(
        instance,
        ["id", "name", "capabilities", "generate", "list_models", "list_samplers", "health_check"],
    )
    assert instance.id == "imagegen-replicate"


def test_defaults(replicate_module) -> None:
    backend = replicate_module.ReplicateImageGenBackend(config={"api_token": "r8-test"})
    assert backend.id == "imagegen-replicate"
    assert backend._default_model == "black-forest-labs/flux-schnell"
    assert backend.capabilities.text_to_image is True
    assert backend.capabilities.image_to_image is False
    assert backend.capabilities.supports_seed is True


def test_split_model(replicate_module) -> None:
    assert replicate_module._split_model("stability-ai/sdxl") == ("stability-ai", "sdxl", None)
    assert replicate_module._split_model("stability-ai/sdxl:abc123") == (
        "stability-ai",
        "sdxl",
        "abc123",
    )
    assert replicate_module._split_model("") == (None, None, None)
    assert replicate_module._split_model("just-a-name") == ("just-a-name", None, None)


def test_build_input_includes_common_fields(replicate_module) -> None:
    request = GenerationRequest(
        prompt="a forest",
        negative_prompt="blurry",
        width=768,
        height=512,
        steps=20,
        cfg_scale=7.0,
        sampler="K_EULER",
        seed=42,
        extra={"output_format": "png", "aspect_ratio": "3:2"},
    )
    inputs = replicate_module._build_input(request)
    assert inputs["prompt"] == "a forest"
    assert inputs["negative_prompt"] == "blurry"
    assert inputs["width"] == 768
    assert inputs["height"] == 512
    assert inputs["num_inference_steps"] == 20
    assert inputs["guidance_scale"] == 7.0
    assert inputs["seed"] == 42
    assert inputs["scheduler"] == "K_EULER"
    assert inputs["output_format"] == "png"
    assert inputs["aspect_ratio"] == "3:2"


def test_build_input_omits_unset_fields(replicate_module) -> None:
    request = GenerationRequest(prompt="x", width=64, height=64, steps=1, cfg_scale=1.0)
    inputs = replicate_module._build_input(request)
    assert "negative_prompt" not in inputs
    assert "seed" not in inputs
    assert "scheduler" not in inputs


def test_first_output_url_handles_shapes(replicate_module) -> None:
    fn = replicate_module._first_output_url
    assert fn("https://x/out.png") == "https://x/out.png"
    assert fn(["https://x/out.png"]) == "https://x/out.png"
    assert fn([{"url": "https://x/out.png"}]) == "https://x/out.png"
    assert fn({"image": "https://x/out.png"}) == "https://x/out.png"
    assert fn(None) is None
    assert fn([]) is None


@pytest.mark.asyncio
async def test_generate_owner_name_uses_model_endpoint(replicate_module) -> None:
    backend = replicate_module.ReplicateImageGenBackend(
        config={"api_token": "r8-test", "poll_interval_seconds": 0.001, "wait_seconds": 0}
    )
    captured = _install_mock_transport(backend, _make_handler())
    result = await backend.generate(
        GenerationRequest(
            prompt="a knight",
            negative_prompt="blurry",
            width=1024,
            height=1024,
            steps=4,
            cfg_scale=3.5,
            seed=7,
            model="black-forest-labs/flux-schnell",
        )
    )
    assert result.image_bytes == _png_bytes()
    assert result.backend == "imagegen-replicate"
    assert result.model == "black-forest-labs/flux-schnell"
    assert result.seed == 7
    assert result.actual_params["prediction_id"] == "pred-1"
    assert result.actual_params["output_url"] == OUTPUT_URL

    post = next(r for r in captured if r.method == "POST")
    assert post.url.path.endswith("/models/black-forest-labs/flux-schnell/predictions")
    payload = json.loads(post.content.decode("utf-8"))
    assert "version" not in payload
    assert payload["input"]["prompt"] == "a knight"
    assert payload["input"]["seed"] == 7
    assert payload["input"]["negative_prompt"] == "blurry"


@pytest.mark.asyncio
async def test_generate_does_not_leak_api_token_to_cdn(replicate_module) -> None:
    """The CDN download must not carry the Replicate API token.

    Regression test for the security review on PR #416: the original
    implementation reused the API client (which has
    `Authorization: Bearer <token>` as a default header) to fetch the
    image from `replicate.delivery`, leaking the token to the CDN host.
    """
    backend = replicate_module.ReplicateImageGenBackend(
        config={"api_token": "r8-secret", "poll_interval_seconds": 0.001, "wait_seconds": 0}
    )
    captured = _install_mock_transport(backend, _make_handler())
    await backend.generate(
        GenerationRequest(
            prompt="x",
            width=64,
            height=64,
            steps=1,
            cfg_scale=1.0,
            seed=1,
            model="black-forest-labs/flux-schnell",
        )
    )
    download = next(r for r in captured if str(r.url) == OUTPUT_URL)
    header_names = {k.lower() for k in download.headers}
    assert "authorization" not in header_names


@pytest.mark.asyncio
async def test_generate_pinned_version_uses_predictions_endpoint(replicate_module) -> None:
    backend = replicate_module.ReplicateImageGenBackend(
        config={"api_token": "r8-test", "poll_interval_seconds": 0.001, "wait_seconds": 0}
    )
    captured = _install_mock_transport(backend, _make_handler())
    await backend.generate(
        GenerationRequest(
            prompt="x",
            width=64,
            height=64,
            steps=1,
            cfg_scale=1.0,
            seed=1,
            model="stability-ai/sdxl:abc123",
        )
    )
    post = next(r for r in captured if r.method == "POST")
    assert post.url.path.endswith("/predictions")
    assert "/models/" not in post.url.path
    payload = json.loads(post.content.decode("utf-8"))
    assert payload["version"] == "abc123"


@pytest.mark.asyncio
async def test_generate_polls_until_terminal(replicate_module) -> None:
    backend = replicate_module.ReplicateImageGenBackend(
        config={"api_token": "r8-test", "poll_interval_seconds": 0.001, "wait_seconds": 0}
    )
    handler = _make_handler(
        initial_status="starting",
        poll_statuses=("processing", "processing", "succeeded"),
    )
    captured = _install_mock_transport(backend, handler)
    result = await backend.generate(
        GenerationRequest(prompt="x", width=64, height=64, steps=1, cfg_scale=1.0, seed=1)
    )
    assert result.image_bytes == _png_bytes()
    polls = [
        r for r in captured if r.method == "GET" and r.url.path.endswith("/predictions/pred-1")
    ]
    assert len(polls) >= 3


@pytest.mark.asyncio
async def test_generate_raises_on_failed_prediction(replicate_module) -> None:
    backend = replicate_module.ReplicateImageGenBackend(
        config={"api_token": "r8-test", "poll_interval_seconds": 0.001, "wait_seconds": 0}
    )
    handler = _make_handler(initial_status="starting", poll_statuses=("failed",))
    _install_mock_transport(backend, handler)
    with pytest.raises(RuntimeError, match="failed"):
        await backend.generate(
            GenerationRequest(prompt="x", width=64, height=64, steps=1, cfg_scale=1.0, seed=1)
        )


@pytest.mark.asyncio
async def test_generate_requires_api_token(replicate_module) -> None:
    backend = replicate_module.ReplicateImageGenBackend()
    with pytest.raises(RuntimeError, match="api_token"):
        await backend.generate(
            GenerationRequest(prompt="x", width=64, height=64, steps=1, cfg_scale=1.0, seed=1)
        )


@pytest.mark.asyncio
async def test_generate_rejects_bare_model_name(replicate_module) -> None:
    backend = replicate_module.ReplicateImageGenBackend(
        config={"api_token": "r8-test", "wait_seconds": 0}
    )
    _install_mock_transport(backend, _make_handler())
    with pytest.raises(RuntimeError, match="owner/name"):
        await backend.generate(
            GenerationRequest(
                prompt="x", width=64, height=64, steps=1, cfg_scale=1.0, seed=1, model="just-name"
            )
        )


@pytest.mark.asyncio
async def test_generate_raises_on_http_error(replicate_module) -> None:
    backend = replicate_module.ReplicateImageGenBackend(
        config={"api_token": "r8-test", "wait_seconds": 0}
    )
    _install_mock_transport(backend, lambda r: httpx.Response(429, text="rate limited"))
    with pytest.raises(RuntimeError, match="429"):
        await backend.generate(
            GenerationRequest(prompt="x", width=64, height=64, steps=1, cfg_scale=1.0, seed=1)
        )


@pytest.mark.asyncio
async def test_generate_wait_header_sent(replicate_module) -> None:
    backend = replicate_module.ReplicateImageGenBackend(
        config={"api_token": "r8-test", "wait_seconds": 5}
    )
    captured = _install_mock_transport(backend, _make_handler())
    await backend.generate(
        GenerationRequest(prompt="x", width=64, height=64, steps=1, cfg_scale=1.0, seed=1)
    )
    post = next(r for r in captured if r.method == "POST")
    assert post.headers.get("prefer") == "wait=5"


@pytest.mark.asyncio
async def test_list_models_static(replicate_module) -> None:
    backend = replicate_module.ReplicateImageGenBackend(config={"api_token": "r8-test"})
    models = await backend.list_models()
    ids = {m.id for m in models}
    assert "black-forest-labs/flux-schnell" in ids
    assert "stability-ai/sdxl" in ids


@pytest.mark.asyncio
async def test_list_samplers_empty(replicate_module) -> None:
    backend = replicate_module.ReplicateImageGenBackend(config={"api_token": "r8-test"})
    assert await backend.list_samplers() == []


@pytest.mark.asyncio
async def test_health_check_unconfigured_without_token(replicate_module) -> None:
    backend = replicate_module.ReplicateImageGenBackend()
    status = await backend.health_check()
    assert status.level == HealthLevel.UNCONFIGURED


@pytest.mark.asyncio
async def test_health_check_healthy(replicate_module) -> None:
    backend = replicate_module.ReplicateImageGenBackend(config={"api_token": "r8-test"})
    _install_mock_transport(backend, _make_handler())
    status = await backend.health_check()
    assert status.level == HealthLevel.HEALTHY


@pytest.mark.asyncio
async def test_health_check_rejected_token(replicate_module) -> None:
    backend = replicate_module.ReplicateImageGenBackend(config={"api_token": "r8-test"})
    _install_mock_transport(backend, lambda r: httpx.Response(401, text="bad token"))
    status = await backend.health_check()
    assert status.level == HealthLevel.UNCONFIGURED


@pytest.mark.asyncio
async def test_conformance(replicate_module) -> None:
    backend = replicate_module.ReplicateImageGenBackend(
        config={"api_token": "r8-test", "poll_interval_seconds": 0.001, "wait_seconds": 0}
    )
    _install_mock_transport(backend, _make_handler())
    report = await ImageGenBackendConformance().run(backend)
    assert report.ok, report.failed
    skipped = {name for name, _ in report.skipped}
    # Determinism is skipped because deterministic_seed=False.
    assert "test_generate_same_seed_same_image" in skipped
