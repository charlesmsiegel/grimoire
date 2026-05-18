"""§3e — Library composition cascade.

Library default vs campaign-local override: the library exposes
``Garrick`` with one description; a campaign writes an override; the
campaign's resolved entity reflects the override, while a *different*
campaign keeps the library default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.testing import TestApp

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_campaign_override_supersedes_library_for_resolving_campaign(
    tmp_path: Path,
) -> None:
    async with TestApp.with_fixtures("simple_world", root=tmp_path) as app:
        assert app.state_store is not None
        assert app.library is not None

        # Baseline: the seeded campaign sees the library default.
        resolved = await app.library.resolve("worlds/ironhold/characters/garrick", "cmp-ironhold-1")
        assert resolved.name == "Garrick"
        baseline_tags = list(resolved.frontmatter.get("tags") or [])
        assert "smith" in baseline_tags

        # A second campaign that binds the same setting also sees the default.
        await app.state_store.upsert_campaign(
            campaign_id="cmp-ironhold-2",
            name="Ironhold Run #2",
        )
        await app.state_store.upsert_world_ref(
            campaign_id="cmp-ironhold-2",
            world_id="ironhold",
            priority=1,
            include=None,
            track_latest=True,
        )

        # Write an override for the first campaign only.
        await app.state_store.write_override(
            campaign_id="cmp-ironhold-1",
            library_id="worlds/ironhold/characters/garrick",
            patch={"tags": ["npc", "smith", "wounded"], "alias": "Old Garrick"},
            source="test",
        )

        overridden = await app.library.resolve(
            "worlds/ironhold/characters/garrick", "cmp-ironhold-1"
        )
        unaffected = await app.library.resolve(
            "worlds/ironhold/characters/garrick", "cmp-ironhold-2"
        )

        # Campaign with the override has the patched tags.
        assert "wounded" in (overridden.frontmatter.get("tags") or [])
        # The other campaign still sees the library default.
        assert "wounded" not in (unaffected.frontmatter.get("tags") or [])
