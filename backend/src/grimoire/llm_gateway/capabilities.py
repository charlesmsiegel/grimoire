"""Static provider-capability table (spec extraction-modes-design).

`ProviderCapabilities` exposes the tool-use facts the Extractor needs in
order to pick a mode. The table is hard-coded because Grimoire pins
providers per route and the matrix is small; dynamic probing is left as
a follow-up. Unknown providers get a safe default that disables tool
use.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    supports_tool_use: bool = False
    streaming_tool_use: bool = False
    max_tool_count: int = 0


_TABLE: dict[str, ProviderCapabilities] = {
    "anthropic": ProviderCapabilities(
        supports_tool_use=True,
        streaming_tool_use=True,
        max_tool_count=128,
    ),
    "openai": ProviderCapabilities(
        supports_tool_use=True,
        streaming_tool_use=True,
        max_tool_count=128,
    ),
    "google": ProviderCapabilities(
        supports_tool_use=True,
        streaming_tool_use=False,
        max_tool_count=64,
    ),
    "openrouter": ProviderCapabilities(supports_tool_use=False),
    "local-llamacpp": ProviderCapabilities(supports_tool_use=False),
}


def capabilities_for(provider_id: str) -> ProviderCapabilities:
    """Look up the static capability entry for ``provider_id``.

    Unknown providers return `ProviderCapabilities()` (no tool use) so
    routing falls back to `TOGETHER`/`SEPARATE` instead of failing.
    """
    return _TABLE.get(provider_id, ProviderCapabilities())


__all__ = ["ProviderCapabilities", "capabilities_for"]
