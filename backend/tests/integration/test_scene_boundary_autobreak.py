"""§3b — Scene boundary detection with auto-break.

The orchestrator inspects each player input via
``SceneManager.is_scene_break``. The boundary heuristic returns
confidence ``0.85`` for "hours later"-style prose — above the default
``SceneBreakConfig.auto_threshold`` of ``0.8``. The orchestrator should
therefore close the active scene and start a new one before the model
streams its response.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.scenes.types import SceneInit
from grimoire.testing import TestApp

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_orchestrator_auto_breaks_scene_on_time_jump(tmp_path: Path) -> None:
    async with TestApp.with_fixtures("simple_world", root=tmp_path / "data") as app:
        campaign_id = "cmp-ironhold-1"
        pc_ref = "library:worlds/ironhold/characters/pc-aria"

        assert app.state_store is not None
        assert app.scene_manager is not None
        assert app.orchestrator is not None

        await app.state_store.add_pc(
            campaign_id=campaign_id,
            character_ref=pc_ref,
            display_name="Aria",
        )
        opening = await app.scene_manager.start_scene(
            SceneInit(
                campaign_id=campaign_id,
                title="At the forge",
                present_pc_refs=[pc_ref],
                present_character_refs=[pc_ref, "garrick"],
                location_ref="library:worlds/ironhold/locations/town-square",
            )
        )

        app.llm.queue_stream("main", ["The town wakes around her."])
        # Empty extractor payload — we don't care about deltas here.
        app.llm.queue_response("extractor", {})

        before = await app.scene_manager.list_scenes(campaign_id)
        assert len(before) == 1, "fixture should start with a single scene"

        result = await app.orchestrator.submit_post(
            campaign_id, pc_ref, "Hours later, she returns to the square."
        )
        assert result.accepted
        assert result.turn_id

        # The orchestrator should have closed the opening scene and
        # started a new one.
        after = await app.scene_manager.list_scenes(campaign_id)
        assert len(after) == 2, f"expected 2 scenes after auto-break; got {len(after)}"

        opening_after = await app.scene_manager.get_scene(opening.id)
        assert opening_after.closed_at_turn == result.turn_id

        new_scene_id = next(s.id for s in after if s.id != opening.id)
        new_posts = await app.scene_manager.get_posts(new_scene_id)
        assert [p.body for p in new_posts] == ["The town wakes around her."], (
            "model response should land in the freshly-opened scene"
        )

        # And the player input remains on the closed scene.
        opening_posts = await app.scene_manager.get_posts(opening.id)
        assert [p.body for p in opening_posts] == [
            "Hours later, she returns to the square."
        ]
