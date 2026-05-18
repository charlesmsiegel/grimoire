"""§3i — Mechanics swap, same world.

Two campaigns share a setting but use different mechanics modules. The
``Composition`` reflects that: each campaign's resolved composition
reports its own ``mechanics`` id while pointing at the same setting.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.testing import TestApp

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_two_campaigns_share_setting_with_different_mechanics(
    tmp_path: Path,
) -> None:
    async with TestApp.with_fixtures("simple_world", root=tmp_path) as app:
        assert app.state_store is not None
        assert app.library is not None

        # Add a second campaign with a different mechanics module.
        await app.state_store.upsert_campaign(
            campaign_id="cmp-ironhold-pf2e",
            name="Ironhold (PF2e)",
            mechanics_module="pf2e",
        )
        await app.state_store.upsert_world_ref(
            campaign_id="cmp-ironhold-pf2e",
            world_id="ironhold",
            priority=1,
            include=None,
            track_latest=True,
        )
        # Update the seeded campaign to use a different mechanics module.
        await app.state_store.upsert_campaign(
            campaign_id="cmp-ironhold-1",
            name="Ironhold Run #1",
            mechanics_module="freeform",
        )

        comp_pf2e = await app.library.get_composition("cmp-ironhold-pf2e")
        comp_freeform = await app.library.get_composition("cmp-ironhold-1")

        # Same world, different mechanics.
        pf2e_worlds = {ref.world_id for ref in comp_pf2e.worlds}
        free_worlds = {ref.world_id for ref in comp_freeform.worlds}
        assert pf2e_worlds == free_worlds == {"ironhold"}
        assert comp_pf2e.mechanics == "pf2e"
        assert comp_freeform.mechanics == "freeform"
