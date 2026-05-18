"""§3f — Crossover composition.

A campaign binds two settings: characters from setting A and locations
from setting B. ``LibraryService.list_for_composition`` walks the refs
in priority order and respects each ref's ``include`` filter.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.testing import TestApp
from grimoire.types.common import EntityKind

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_crossover_picks_characters_from_a_and_locations_from_b(
    tmp_path: Path,
) -> None:
    async with TestApp.with_fixtures("two_worlds", root=tmp_path) as app:
        assert app.state_store is not None
        assert app.library is not None

        await app.state_store.upsert_campaign(
            campaign_id="cmp-crossover",
            name="Crossover",
        )
        # Aurora supplies characters only.
        await app.state_store.upsert_world_ref(
            campaign_id="cmp-crossover",
            world_id="aurora",
            priority=1,
            include=["characters"],
            track_latest=True,
        )
        # Borealis supplies locations only.
        await app.state_store.upsert_world_ref(
            campaign_id="cmp-crossover",
            world_id="borealis",
            priority=2,
            include=["locations"],
            track_latest=True,
        )

        chars = await app.library.list_for_composition("cmp-crossover", EntityKind.CHARACTER)
        locs = await app.library.list_for_composition("cmp-crossover", EntityKind.LOCATION)

        char_ids = {c.asset_id for c in chars}
        loc_ids = {loc.asset_id for loc in locs}

        assert char_ids == {"nova"}
        assert loc_ids == {"orbital-7"}
