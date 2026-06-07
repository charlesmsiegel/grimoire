"""ImageGen core.

Implementation of spec 12. Exposes:

- :class:`ImageGenService` — the top-level facade callable by Orchestrator
  and Frontend (implements :class:`grimoire.types.ImageGenProtocol`).
- :class:`IntegratedDiffusersBackend` — default backend that wraps
  HuggingFace ``diffusers`` (lazy import).
- :class:`InMemoryDiffusersBackend` — a tiny deterministic backend used in
  tests and as a fallback when the real ``diffusers`` weights are absent.
- Prompt composition + trigger evaluation helpers.

Bundled plugin backends (A1111, ComfyUI, DALL-E) live under
``backend/bundled_plugins/`` and are loaded through the Plugins module; the
same :class:`ImageGenBackend` protocol applies to both the integrated
backend and plugin backends.
"""

from __future__ import annotations

from grimoire.imagegen.backend import (
    InMemoryDiffusersBackend,
    IntegratedDiffusersBackend,
    cache_key_for_request,
)
from grimoire.imagegen.config import ImageGenConfig
from grimoire.imagegen.health_prober import ImageGenHealthProber
from grimoire.imagegen.integration import ImageGenIntegration
from grimoire.imagegen.prompt import (
    ComposedPrompt,
    PromptComposer,
    compose_negative_prompt,
    compose_prompt_parts,
)
from grimoire.imagegen.service import (
    BackendRegistry,
    ImageGenService,
    TriggerConfig,
    should_illustrate,
)
from grimoire.imagegen.visual_extractor import LLMVisualExtractor

__all__ = [
    "BackendRegistry",
    "ComposedPrompt",
    "ImageGenConfig",
    "ImageGenHealthProber",
    "ImageGenIntegration",
    "ImageGenService",
    "InMemoryDiffusersBackend",
    "IntegratedDiffusersBackend",
    "LLMVisualExtractor",
    "PromptComposer",
    "TriggerConfig",
    "cache_key_for_request",
    "compose_negative_prompt",
    "compose_prompt_parts",
    "should_illustrate",
]
