"""Tests for the bundled `imagegen-diffusers` plugin.

`diffusers`, `torch`, and `Pillow` are heavy plugin-side dependencies
that aren't in the core test venv. Tests that don't need them (manifest
shape, config parsing, curated catalog, sampler list, "lib missing"
health) run unconditionally; tests that exercise `generate()` import-
skip on PIL+torch and stub `_build_pipeline` with a fake pipeline so no
weights are downloaded.
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from grimoire.types.common import HealthLevel
from grimoire.types.imagegen import GenerationRequest
from grimoire.types.plugins import PluginKind

from .conftest import assert_protocol_attrs, load_bundled


# --------------------------------------------------------------------- #
# Fake pipeline scaffolding (only built inside tests that have PIL)
# --------------------------------------------------------------------- #


def _make_fake_pipeline_factory():
    """Construct the `_FakePipeline` class against the live PIL.

    Wrapped in a function so the test module can load on environments
    without Pillow — only generate()-touching tests call this.
    """
    from PIL import Image

    class _FakeSchedulerConfig(dict):
        pass

    class _FakeScheduler:
        def __init__(self, name: str = "PNDMScheduler") -> None:
            type(self).__name__ = name
            self.config = _FakeSchedulerConfig({"num_train_timesteps": 1000})

    class _FakeOutput:
        def __init__(self, images: list[Any]) -> None:
            self.images = images

    class _FakePipeline:
        def __init__(self) -> None:
            self.scheduler = _FakeScheduler()
            self.calls: list[dict[str, Any]] = []
            self.applied_options: list[str] = []

        def __call__(self, **kwargs: Any) -> _FakeOutput:
            self.calls.append(kwargs)
            width = int(kwargs.get("width", 64))
            height = int(kwargs.get("height", 64))
            if "image" in kwargs:
                width, height = kwargs["image"].size
            img = Image.new("RGB", (width, height), color=(120, 60, 200))
            return _FakeOutput([img])

        def enable_attention_slicing(self) -> None:
            self.applied_options.append("attention_slicing")

        def enable_vae_slicing(self) -> None:
            self.applied_options.append("vae_slicing")

        def enable_vae_tiling(self) -> None:
            self.applied_options.append("vae_tiling")

        def enable_sequential_cpu_offload(self) -> None:
            self.applied_options.append("cpu_offload")

        def to(self, device: str) -> "_FakePipeline":
            self.applied_options.append(f"to:{device}")
            return self

    return _FakePipeline


def _install_fake_pipeline(backend, pipe=None):
    """Bypass `_build_pipeline` so tests stay offline + fast."""
    factory = _make_fake_pipeline_factory()
    fake = pipe or factory()

    def _stub(
        *,
        model_id: str,
        device: str,
        dtype: Any,
        dtype_str: str,
        img2img: bool,
    ):
        backend._device = device
        return fake

    backend._build_pipeline = _stub  # type: ignore[method-assign]
    return fake


def _png_bytes(width: int = 8, height: int = 8) -> bytes:
    # `synthesize_png` lives in core and uses only the standard library —
    # gives us valid PNG bytes for img2img tests without needing PIL.
    from grimoire.imagegen.backend import synthesize_png

    return synthesize_png(width, height, seed=0, prompt="test")


# --------------------------------------------------------------------- #
# Manifest + protocol
# --------------------------------------------------------------------- #


def test_manifest_discovers_and_loads() -> None:
    result = load_bundled("imagegen-diffusers")
    assert result.ok, result.errors
    assert result.manifest is not None
    assert result.manifest.id == "imagegen-diffusers"
    assert PluginKind.IMAGEGEN_BACKEND in result.manifest.implements
    instance = result.instances[0].instance
    assert_protocol_attrs(
        instance,
        ["id", "name", "capabilities", "generate", "list_models", "list_samplers", "health_check"],
    )
    assert instance.id == "imagegen-diffusers"


def test_defaults(diffusers_module) -> None:
    backend = diffusers_module.DiffusersImageGenBackend()
    assert backend.id == "imagegen-diffusers"
    assert backend._active_model == diffusers_module.DEFAULT_MODEL
    assert backend._default_scheduler == diffusers_module.DEFAULT_SCHEDULER
    assert backend.capabilities.text_to_image is True
    assert backend.capabilities.image_to_image is True
    assert backend.deterministic_seed is True


def test_config_overrides(diffusers_module) -> None:
    backend = diffusers_module.DiffusersImageGenBackend(
        config={
            "active_model": "black-forest-labs/FLUX.1-schnell",
            "device": "cuda",
            "dtype": "bfloat16",
            "variant": "fp16",
            "default_scheduler": "Euler a",
            "attention_slicing": False,
            "vae_slicing": False,
            "cpu_offload": True,
            "safety_checker": True,
            "cache_folder": "/tmp/hf",
            "hf_token": "hf_xxx",
            "timeout_seconds": 30,
        }
    )
    assert backend._active_model == "black-forest-labs/FLUX.1-schnell"
    assert backend._device_pref == "cuda"
    assert backend._dtype_pref == "bfloat16"
    assert backend._variant == "fp16"
    assert backend._default_scheduler == "Euler a"
    assert backend._attention_slicing is False
    assert backend._cpu_offload is True
    assert backend._safety_checker is True
    assert backend._cache_folder == "/tmp/hf"
    assert backend._hf_token == "hf_xxx"
    assert backend._timeout == 30


def test_legacy_base_model_key_still_honored(diffusers_module) -> None:
    # The pre-plugin `IntegratedDiffusersBackend` used `base_model`;
    # existing configs shouldn't need manual migration.
    backend = diffusers_module.DiffusersImageGenBackend(
        config={"base_model": "runwayml/stable-diffusion-v1-5"}
    )
    assert backend._active_model == "runwayml/stable-diffusion-v1-5"


# --------------------------------------------------------------------- #
# list_models / list_samplers
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_models_returns_curated_catalog(diffusers_module) -> None:
    backend = diffusers_module.DiffusersImageGenBackend()
    models = await backend.list_models()
    ids = {m.id for m in models}
    assert "stabilityai/stable-diffusion-xl-base-1.0" in ids
    assert "black-forest-labs/FLUX.1-schnell" in ids
    assert "runwayml/stable-diffusion-v1-5" in ids
    by_id = {m.id: m for m in models}
    assert by_id["stabilityai/stable-diffusion-xl-base-1.0"].name.startswith("SDXL")
    # Gated entries are flagged so users know they need an HF token.
    assert "(gated)" in by_id["black-forest-labs/FLUX.1-dev"].name


@pytest.mark.asyncio
async def test_list_models_includes_unknown_active_model(diffusers_module) -> None:
    backend = diffusers_module.DiffusersImageGenBackend(
        config={"active_model": "some-org/custom-checkpoint"}
    )
    models = await backend.list_models()
    ids = {m.id for m in models}
    assert "some-org/custom-checkpoint" in ids


@pytest.mark.asyncio
async def test_list_samplers_returns_scheduler_names(diffusers_module) -> None:
    backend = diffusers_module.DiffusersImageGenBackend()
    samplers = await backend.list_samplers()
    assert "DPM++ 2M Karras" in samplers
    assert "Euler a" in samplers
    assert "UniPC" in samplers


# --------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_health_check_unconfigured_when_diffusers_missing(
    monkeypatch, diffusers_module
) -> None:
    backend = diffusers_module.DiffusersImageGenBackend()
    import builtins

    real_import = builtins.__import__

    def _block_diffusers(name, *args, **kwargs):
        if name == "diffusers" or name.startswith("diffusers."):
            raise ImportError("No module named 'diffusers'", name="diffusers")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block_diffusers)
    status = await backend.health_check()
    assert status.level == HealthLevel.UNCONFIGURED
    assert "diffusers" in status.message


@pytest.mark.asyncio
async def test_health_check_healthy_when_libraries_present(diffusers_module) -> None:
    pytest.importorskip("diffusers")
    pytest.importorskip("torch")
    backend = diffusers_module.DiffusersImageGenBackend()
    status = await backend.health_check()
    assert status.level == HealthLevel.HEALTHY
    assert backend._active_model in status.message


# --------------------------------------------------------------------- #
# Generation (needs PIL + torch installed)
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_generate_txt2img_passes_expected_kwargs(diffusers_module) -> None:
    pytest.importorskip("PIL")
    pytest.importorskip("torch")
    backend = diffusers_module.DiffusersImageGenBackend()
    fake = _install_fake_pipeline(backend)
    result = await backend.generate(
        GenerationRequest(
            prompt="a knight in a sunlit grove",
            negative_prompt="blurry",
            width=128,
            height=128,
            steps=4,
            cfg_scale=6.0,
            sampler="Euler a",
            seed=42,
        )
    )
    assert result.image_bytes.startswith(b"\x89PNG")
    assert result.seed == 42
    assert result.backend == "imagegen-diffusers"
    assert result.model == diffusers_module.DEFAULT_MODEL
    assert result.actual_params["sampler"] == "Euler a"
    assert result.actual_params["init_image"] is False

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["prompt"] == "a knight in a sunlit grove"
    assert call["negative_prompt"] == "blurry"
    assert call["width"] == 128
    assert call["height"] == 128
    assert call["num_inference_steps"] == 4
    assert call["guidance_scale"] == 6.0
    # The generator's seed must equal the request seed — confirms it
    # actually flows into the diffusion sampling and isn't just metadata.
    assert call["generator"].initial_seed() == 42
    # (Memory-saving switches live inside `_build_pipeline`, which the
    # stub bypasses; that path is verified by activating the plugin
    # against a real pipeline.)


@pytest.mark.asyncio
async def test_generate_random_seed_is_assigned_when_unset(diffusers_module) -> None:
    pytest.importorskip("PIL")
    pytest.importorskip("torch")
    backend = diffusers_module.DiffusersImageGenBackend()
    _install_fake_pipeline(backend)
    result = await backend.generate(
        GenerationRequest(prompt="x", width=64, height=64, steps=1, cfg_scale=1.0, seed=None)
    )
    assert result.seed >= 0


@pytest.mark.asyncio
async def test_generate_img2img_uses_init_image(diffusers_module) -> None:
    pytest.importorskip("PIL")
    pytest.importorskip("torch")
    backend = diffusers_module.DiffusersImageGenBackend()
    fake = _install_fake_pipeline(backend)
    await backend.generate(
        GenerationRequest(
            prompt="repaint",
            width=64,
            height=64,
            steps=1,
            cfg_scale=1.0,
            seed=7,
            init_image=_png_bytes(32, 32),
            init_image_strength=0.55,
        )
    )
    call = fake.calls[0]
    assert "image" in call
    assert call["strength"] == 0.55
    # img2img drops explicit width/height — diffusers infers from the
    # provided image instead.
    assert "width" not in call
    assert "height" not in call


@pytest.mark.asyncio
async def test_generate_request_model_overrides_active_model(diffusers_module) -> None:
    pytest.importorskip("PIL")
    pytest.importorskip("torch")
    backend = diffusers_module.DiffusersImageGenBackend()
    fake = _install_fake_pipeline(backend)
    result = await backend.generate(
        GenerationRequest(
            prompt="x",
            width=64,
            height=64,
            steps=1,
            cfg_scale=1.0,
            seed=1,
            model="Lykon/dreamshaper-xl-1-0",
        )
    )
    assert result.model == "Lykon/dreamshaper-xl-1-0"
    # `_active_model` config is untouched by per-request overrides.
    assert backend._active_model == diffusers_module.DEFAULT_MODEL
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_unknown_sampler_falls_back_to_pipeline_default(diffusers_module) -> None:
    pytest.importorskip("PIL")
    pytest.importorskip("torch")
    backend = diffusers_module.DiffusersImageGenBackend()
    fake = _install_fake_pipeline(backend)
    result = await backend.generate(
        GenerationRequest(
            prompt="x",
            width=64,
            height=64,
            steps=1,
            cfg_scale=1.0,
            seed=1,
            sampler="NotARealSampler",
        )
    )
    # Falls back to whatever the pipeline already had configured.
    assert result.actual_params["sampler"] == "PNDMScheduler"
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_pipeline_is_cached_per_model(diffusers_module) -> None:
    pytest.importorskip("PIL")
    pytest.importorskip("torch")
    backend = diffusers_module.DiffusersImageGenBackend()
    factory = _make_fake_pipeline_factory()
    pipe = factory()
    call_count = {"n": 0}

    def _counting_build(**kwargs):
        call_count["n"] += 1
        backend._device = kwargs["device"]
        return pipe

    backend._build_pipeline = _counting_build  # type: ignore[method-assign]

    req = GenerationRequest(prompt="x", width=64, height=64, steps=1, cfg_scale=1.0, seed=1)
    await backend.generate(req)
    await backend.generate(req)
    await backend.generate(req)
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_conformance(diffusers_module) -> None:
    pytest.importorskip("PIL")
    pytest.importorskip("torch")
    pytest.importorskip("diffusers")  # scheduler swap needs the real registry
    from grimoire.testing.conformance.imagegen import ImageGenBackendConformance

    backend = diffusers_module.DiffusersImageGenBackend()
    _install_fake_pipeline(backend)
    report = await ImageGenBackendConformance().run(backend)
    assert report.ok, report.failed
