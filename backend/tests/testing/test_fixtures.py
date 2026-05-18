"""Tests for ``LibraryCampaignFixture`` and the fixtures registry (§2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.testing import (
    CampaignFixture,
    LibraryCampaignFixture,
    TestApp,
    TestAppFixture,
    fixtures_registry,
)


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Each test gets a clean registry so cross-test registration can't leak."""
    fixtures_registry.clear()
    yield
    fixtures_registry.clear()


@pytest.mark.asyncio
async def test_library_campaign_fixture_seeds_library_and_campaign(
    tmp_path: Path,
) -> None:
    """The fixture path writes library files + creates campaigns + runs hooks."""
    seen: dict[str, bool] = {"library_setup": False, "campaign_setup": False}

    async def library_setup(app: TestApp) -> None:
        seen["library_setup"] = True

    async def campaign_setup(app: TestApp, campaign: CampaignFixture) -> None:
        seen["campaign_setup"] = True
        # The library row already exists when the campaign hook runs.
        assert app.library is not None
        setting = await app.library.get_world("wod-london")
        assert setting.name == "London by Night"

    fixture = LibraryCampaignFixture(
        name="probe",
        library_assets=[
            {
                "library_id": "worlds/wod-london/world",
                "frontmatter": {
                    "id": "wod-london",
                    "name": "London by Night",
                    "version": 1,
                },
            },
        ],
        library_entities=[
            {
                "library_id": "worlds/wod-london/characters/alistair",
                "frontmatter": {
                    "id": "alistair",
                    "name": "Alistair",
                    "tags": ["vampire"],
                },
                "body": "An elder.",
            },
        ],
        character_families=[
            {
                "family_id": "house-thorne",
                "setting_id": "wod-london",
                "members": ["alistair"],
            },
        ],
        library_setup=library_setup,
        campaigns=[
            CampaignFixture(
                campaign_id="cmp-1",
                name="Test Campaign",
                setup=campaign_setup,
            ),
        ],
    )

    async with TestApp.with_fixtures(fixture, root=tmp_path) as app:
        assert app.library is not None
        # Library entity is queryable.
        alistair = await app.library.get_entity("wod-london", "character", "alistair")
        assert alistair.name == "Alistair"
        # Family records are surfaced on the app (no library kind yet).
        assert any(f["family_id"] == "house-thorne" for f in app.character_families)
        # Campaign row exists.
        row = await app.db.fetchone("SELECT id, name FROM campaigns WHERE id = ?", ("cmp-1",))
        assert row is not None
        assert row["name"] == "Test Campaign"

    assert seen["library_setup"] is True
    assert seen["campaign_setup"] is True


@pytest.mark.asyncio
async def test_fixtures_registry_lookup_via_string(tmp_path: Path) -> None:
    """String lookup consults the process-wide registry when no override is passed."""
    fixture = LibraryCampaignFixture(
        name="from-registry",
        library_assets=[
            {
                "library_id": "worlds/minimal/world",
                "frontmatter": {"id": "minimal", "name": "Minimal", "version": 1},
            }
        ],
        campaigns=[],
    )
    fixtures_registry.register("from-registry", fixture)

    async with TestApp.with_fixtures("from-registry", root=tmp_path) as app:
        assert app.library is not None
        setting = await app.library.get_world("minimal")
        assert setting.name == "Minimal"


@pytest.mark.asyncio
async def test_fixtures_registry_string_lookup_unknown_raises(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        async with TestApp.with_fixtures("no-such-fixture", root=tmp_path):
            pass


@pytest.mark.asyncio
async def test_explicit_registry_argument_still_works(tmp_path: Path) -> None:
    fixture = TestAppFixture(name="probe")
    async with TestApp.with_fixtures("probe", root=tmp_path, registry={"probe": fixture}) as app:
        assert app.state_store is not None
