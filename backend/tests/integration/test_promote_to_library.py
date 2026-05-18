"""§3g — Promote a campaign-local emergent entity to the library.

A campaign creates an emergent location; the library promotion path
copies it into the setting's library directory. A *second* campaign
that binds the same setting then sees the promoted asset via
``list_for_composition``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.testing import TestApp
from grimoire.types.common import EntityKind

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_promoted_emergent_visible_to_new_campaign(tmp_path: Path) -> None:
    async with TestApp.with_fixtures("simple_world", root=tmp_path) as app:
        assert app.state_store is not None
        assert app.library is not None

        # Write an emergent location in the seeded campaign.
        await app.state_store.write_emergent(
            campaign_id="cmp-ironhold-1",
            kind="location",
            entity_id="hidden-shrine",
            frontmatter={"id": "hidden-shrine", "name": "Hidden Shrine"},
            body="A small clearing.",
            source="test",
        )

        # Promote it into the ``ironhold`` setting.
        library_path = await app.library.promote_to_library(
            campaign_id="cmp-ironhold-1",
            entity_kind=EntityKind.LOCATION,
            campaign_entity_id="hidden-shrine",
            target_world_id="ironhold",
        )
        assert library_path  # promotion returned a path

        # New campaign on the same setting picks up the promoted asset.
        await app.state_store.upsert_campaign(
            campaign_id="cmp-ironhold-fresh",
            name="Fresh Ironhold",
        )
        await app.state_store.upsert_world_ref(
            campaign_id="cmp-ironhold-fresh",
            world_id="ironhold",
            priority=1,
            include=None,
            track_latest=True,
        )

        locs = await app.library.list_for_composition("cmp-ironhold-fresh", EntityKind.LOCATION)
        assert "hidden-shrine" in {loc.asset_id for loc in locs}
