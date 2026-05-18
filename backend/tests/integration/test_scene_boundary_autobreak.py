"""§3b — Scene boundary detection with auto-break.

``OrchestratorService._maybe_break_scene`` consults the LLM to decide
whether to close the current scene. Skeleton only — the orchestrator is
not constructed by ``TestApp`` yet.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_orchestrator_breaks_scene_when_llm_says_yes() -> None:
    # spec 2026-05-18 §3b — orchestrator._maybe_break_scene
    pytest.skip(
        "upstream API not exposed yet: grimoire.orchestrator.OrchestratorService "
        "is not constructed by TestApp; needs context_builder + extractor."
    )
