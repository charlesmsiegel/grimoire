"""§3a — Turn-loop end-to-end with mock LLM.

Driving the orchestrator should append a scene post, extract deltas, and
record continuity facts. The shipped ``OrchestratorService`` requires
explicit ``context_builder`` and ``extractor`` collaborators that the
unit-level ``TestApp`` does not wire today — so the end-to-end pipe
isn't reachable through ``TestApp`` alone yet.

Skeleton lives here so the test surfaces once the harness exposes those
collaborators.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_submit_turn_records_post_deltas_and_continuity() -> None:
    # spec 2026-05-18 §3a — needs orchestrator + extractor wired into TestApp
    pytest.skip(
        "upstream API not exposed yet: TestApp does not construct "
        "OrchestratorService(context_builder=..., extractor=...). "
        "Reach via orchestrator.submit_post(...) once wired."
    )
