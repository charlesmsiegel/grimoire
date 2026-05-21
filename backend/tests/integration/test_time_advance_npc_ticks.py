"""§3c — Time advancement with NPC ticks.

Drives :meth:`TimeEngineService.advance` against a campaign that has a
single library-composed ``major_npc`` (Garrick) and asserts that the
NPC-tick callable is invoked for him with the expected duration.

The tick callable is swapped via ``TestApp.install_npc_tick_fn`` so the
test can record invocations without depending on a real LLM-backed
generator.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grimoire.testing import TestApp
from grimoire.types.common import Duration, InGameTime
from grimoire.types.time import TimeAdvanceReason

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_time_advance_ticks_offscreen_npcs(tmp_path: Path) -> None:
    async with TestApp.with_fixtures("simple_world", root=tmp_path / "data") as app:
        campaign_id = "cmp-ironhold-1"

        assert app.time_engine is not None
        assert app.characters is not None

        # Sanity: the composed character set has Garrick available for the
        # tick pass.
        resolved = await app.characters.list_for_campaign(campaign_id)
        asset_ids = {r.character.id for r in resolved}
        assert "garrick" in asset_ids, (
            f"fixture should expose garrick via library composition; got {asset_ids}"
        )

        # Record every NPC-tick invocation.
        seen_payloads: list[dict[str, Any]] = []

        async def _recording_tick(payload: dict[str, Any]) -> dict[str, Any]:
            seen_payloads.append(dict(payload))
            return {
                "activities": ["worked the forge"],
                "location_at_end": "town-square",
                "mood_at_end": "satisfied",
                "new_facts": [],
                "relationship_changes": [],
                "secrets_kept": [],
                "next_intent": "rest",
                "should_seek_pc": False,
                "events_pc_would_witness": [],
            }

        app.install_npc_tick_fn(_recording_tick)

        # Advance one hour starting from a fixed wall-clock.
        from_time = InGameTime(moment=datetime(2026, 1, 1, 8, 0, tzinfo=UTC))
        duration = Duration(iso8601="PT1H", delta=timedelta(hours=1))

        result = await app.time_engine.advance(
            campaign_id,
            duration,
            TimeAdvanceReason.ACTIVITY_DURATION,
            from_time=from_time,
        )

        # Garrick (major_npc) was ticked once with the engine's payload.
        assert len(seen_payloads) == 1, (
            f"expected one NPC tick; got {[p['character_id'] for p in seen_payloads]}"
        )
        tick = seen_payloads[0]
        assert tick["character_id"] == "garrick"
        assert tick["duration_iso"] == "PT1H"

        # The structured summary lands on the result keyed by asset id.
        summary = result.npc_summaries.get("garrick")
        assert summary is not None, (
            f"expected garrick in npc_summaries; got {list(result.npc_summaries)}"
        )
        assert "worked the forge" in summary.activities
