"""§3h — Version pinning keeps an old asset version after library edit.

When a campaign binds a setting with ``track_latest=False`` the library
state is snapshotted at the bound version. Subsequent edits to the
library don't affect the pinned campaign, but a fresh ``track_latest``
campaign sees the new version immediately.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.testing import TestApp

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_pinned_campaign_keeps_old_version_after_library_edit(
    tmp_path: Path,
) -> None:
    async with TestApp.with_fixtures("simple_world", root=tmp_path) as app:
        assert app.state_store is not None
        assert app.library is not None

        # Create a pinned campaign on the seeded setting.
        await app.state_store.upsert_campaign(
            campaign_id="cmp-pinned",
            name="Pinned",
        )
        await app.state_store.upsert_world_ref(
            campaign_id="cmp-pinned",
            world_id="ironhold",
            priority=1,
            include=None,
            track_latest=False,
        )

        # Baseline: pinned campaign sees the v1 description.
        before = await app.library.resolve("worlds/ironhold/characters/garrick", "cmp-pinned")
        before_body = before.body

        # Mutate the library — Garrick's body changes.
        await app.state_store.write_library_file(
            library_id="worlds/ironhold/characters/garrick",
            frontmatter={
                "id": "garrick",
                "name": "Garrick",
                "tags": ["npc", "smith"],
                "version": 2,
            },
            body="# Garrick\n\nThe town smith — *rewritten*.",
            source="test",
        )

        # Pinned campaign should still see the old body.
        after_pinned = await app.library.resolve("worlds/ironhold/characters/garrick", "cmp-pinned")
        assert after_pinned.body == before_body, (
            "pinned campaign should be insulated from a library edit, "
            f"got new body: {after_pinned.body!r}"
        )

        # A new ``track_latest`` campaign sees the new body.
        await app.state_store.upsert_campaign(
            campaign_id="cmp-live",
            name="Live",
        )
        await app.state_store.upsert_world_ref(
            campaign_id="cmp-live",
            world_id="ironhold",
            priority=1,
            include=None,
            track_latest=True,
        )
        after_live = await app.library.resolve("worlds/ironhold/characters/garrick", "cmp-live")
        assert "rewritten" in after_live.body
