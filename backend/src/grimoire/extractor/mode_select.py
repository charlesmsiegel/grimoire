"""`select_mode`: decide which extraction mode to use for one turn.

The Orchestrator calls this once at turn start. The result is then
threaded into both the Context Builder (so it appends tracker
instructions or tool declarations) and the Extractor (so it routes its
internal pipeline). Truth table:

* aux task present  → `NONE` (hard short-circuit for auxiliary-tasks).
* preferred=AUTO     → pick the best supported, non-auto-disabled mode.
* preferred=TOOL_USE → degrade to `SEPARATE` when the provider lacks
                       tool use, or when tool-use is auto-disabled.
* preferred=TOGETHER → degrade to `SEPARATE` when together is
                       auto-disabled.
* preferred=NONE/SEPARATE → pass through unchanged.
"""

from __future__ import annotations

from typing import Protocol

from grimoire.llm_gateway.capabilities import ProviderCapabilities
from grimoire.types.extraction_modes import ExtractionMode


class _AutoDisableLike(Protocol):
    async def together_disabled(self, provider_id: str, model: str) -> bool: ...
    async def tool_use_disabled(self, provider_id: str, model: str) -> bool: ...


class _CampaignConfigLike(Protocol):
    mode: ExtractionMode


async def select_mode(
    *,
    campaign_config: _CampaignConfigLike,
    provider_caps: ProviderCapabilities,
    auto_disable: _AutoDisableLike,
    aux_task: object | None,
    provider_id: str,
    model: str,
) -> ExtractionMode:
    """Return the mode the Extractor + Context Builder should use this turn.

    `auto_disable` is read async because the production implementation
    queries SQLite; in-memory stubs are also welcome to be async.
    """
    if aux_task is not None:
        return ExtractionMode.NONE

    preferred = campaign_config.mode

    if preferred == ExtractionMode.AUTO:
        if provider_caps.supports_tool_use and not await auto_disable.tool_use_disabled(
            provider_id, model
        ):
            return ExtractionMode.TOOL_USE
        if not await auto_disable.together_disabled(provider_id, model):
            return ExtractionMode.TOGETHER
        return ExtractionMode.SEPARATE

    if preferred == ExtractionMode.TOOL_USE:
        if not provider_caps.supports_tool_use:
            return ExtractionMode.SEPARATE
        if await auto_disable.tool_use_disabled(provider_id, model):
            return ExtractionMode.SEPARATE
        return ExtractionMode.TOOL_USE

    if preferred == ExtractionMode.TOGETHER:
        if await auto_disable.together_disabled(provider_id, model):
            return ExtractionMode.SEPARATE
        return ExtractionMode.TOGETHER

    return preferred


__all__ = ["select_mode"]
