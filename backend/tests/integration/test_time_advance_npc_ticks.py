"""§3c — Time advancement with NPC ticks.

TimeEngine drives NPC ticks for offscreen significant characters. The
``TimeEngineService`` exists but ``TestApp`` doesn't construct one
today, and the NPC-tick contract still depends on the
character-significance index that has not landed for tests.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_time_advance_ticks_offscreen_npcs() -> None:
    # spec 2026-05-18 §3c — TimeEngineService.advance + NpcTick path
    pytest.skip(
        "upstream API not exposed yet: TestApp does not construct "
        "TimeEngineService and the significant-character index is not wired."
    )
