"""Tests for `select_mode` truth table."""

from __future__ import annotations

from dataclasses import dataclass

from grimoire.extractor.mode_select import select_mode
from grimoire.llm_gateway.capabilities import ProviderCapabilities
from grimoire.types.extraction_modes import ExtractionMode


@dataclass
class _Config:
    mode: ExtractionMode


class _Stub:
    def __init__(self, together: bool = False, tool_use: bool = False) -> None:
        self._together = together
        self._tool_use = tool_use

    async def together_disabled(self, _provider: str, _model: str) -> bool:
        return self._together

    async def tool_use_disabled(self, _provider: str, _model: str) -> bool:
        return self._tool_use


CAPS_TOOL_USE = ProviderCapabilities(
    supports_tool_use=True, streaming_tool_use=True, max_tool_count=64
)
CAPS_NO_TOOL_USE = ProviderCapabilities(supports_tool_use=False)


async def _select(config_mode: ExtractionMode, *, caps, auto_disable, aux_task=None):
    return await select_mode(
        campaign_config=_Config(mode=config_mode),
        provider_caps=caps,
        auto_disable=auto_disable,
        aux_task=aux_task,
        provider_id="anthropic",
        model="opus",
    )


async def test_auxiliary_task_short_circuits_to_none():
    # Aux task wins regardless of capabilities or preferred mode.
    assert (
        await _select(
            ExtractionMode.TOGETHER,
            caps=CAPS_TOOL_USE,
            auto_disable=_Stub(),
            aux_task=object(),
        )
        == ExtractionMode.NONE
    )


async def test_auto_picks_tool_use_when_supported():
    assert (
        await _select(ExtractionMode.AUTO, caps=CAPS_TOOL_USE, auto_disable=_Stub())
        == ExtractionMode.TOOL_USE
    )


async def test_auto_falls_to_together_when_no_tool_use():
    assert (
        await _select(ExtractionMode.AUTO, caps=CAPS_NO_TOOL_USE, auto_disable=_Stub())
        == ExtractionMode.TOGETHER
    )


async def test_auto_falls_to_separate_when_both_disabled():
    assert (
        await _select(
            ExtractionMode.AUTO,
            caps=CAPS_TOOL_USE,
            auto_disable=_Stub(together=True, tool_use=True),
        )
        == ExtractionMode.SEPARATE
    )


async def test_auto_skips_tool_use_when_auto_disabled():
    assert (
        await _select(
            ExtractionMode.AUTO,
            caps=CAPS_TOOL_USE,
            auto_disable=_Stub(tool_use=True),
        )
        == ExtractionMode.TOGETHER
    )


async def test_preferred_tool_use_falls_to_separate_without_capability():
    assert (
        await _select(ExtractionMode.TOOL_USE, caps=CAPS_NO_TOOL_USE, auto_disable=_Stub())
        == ExtractionMode.SEPARATE
    )


async def test_preferred_tool_use_falls_to_separate_when_auto_disabled():
    assert (
        await _select(
            ExtractionMode.TOOL_USE,
            caps=CAPS_TOOL_USE,
            auto_disable=_Stub(tool_use=True),
        )
        == ExtractionMode.SEPARATE
    )


async def test_preferred_tool_use_kept_when_healthy_and_supported():
    assert (
        await _select(ExtractionMode.TOOL_USE, caps=CAPS_TOOL_USE, auto_disable=_Stub())
        == ExtractionMode.TOOL_USE
    )


async def test_preferred_together_falls_to_separate_when_auto_disabled():
    assert (
        await _select(
            ExtractionMode.TOGETHER,
            caps=CAPS_TOOL_USE,
            auto_disable=_Stub(together=True),
        )
        == ExtractionMode.SEPARATE
    )


async def test_preferred_together_kept_when_healthy():
    assert (
        await _select(ExtractionMode.TOGETHER, caps=CAPS_NO_TOOL_USE, auto_disable=_Stub())
        == ExtractionMode.TOGETHER
    )


async def test_preferred_separate_passes_through():
    assert (
        await _select(ExtractionMode.SEPARATE, caps=CAPS_TOOL_USE, auto_disable=_Stub())
        == ExtractionMode.SEPARATE
    )


async def test_preferred_none_passes_through():
    # NONE is normally set by the aux-task branch, but a campaign config
    # can also explicitly disable extraction.
    assert (
        await _select(ExtractionMode.NONE, caps=CAPS_TOOL_USE, auto_disable=_Stub())
        == ExtractionMode.NONE
    )
