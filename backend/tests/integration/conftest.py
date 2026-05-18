"""Shared fixtures for the integration suite.

Every test in this directory inherits the ``integration`` marker so
the CI ``backend-integration`` job picks it up. Named campaign + library
fixtures are registered in the process-wide
:mod:`grimoire.testing.fixtures_registry` so individual tests can call
``TestApp.with_fixtures("simple_world", root=tmp_path)`` without
threading a registry argument through every call site.
"""

from __future__ import annotations

import pytest

from grimoire.testing import (
    CampaignFixture,
    LibraryCampaignFixture,
    TestApp,
    fixtures_registry,
)

pytestmark = pytest.mark.integration


def _register_simple_world() -> None:
    """A single-setting, single-campaign fixture used by most §3 tests."""

    async def bind_setting(app: TestApp, campaign: CampaignFixture) -> None:
        assert app.state_store is not None
        await app.state_store.upsert_world_ref(
            campaign_id=campaign.campaign_id,
            world_id="ironhold",
            priority=1,
            include=None,
            track_latest=True,
        )

    fixture = LibraryCampaignFixture(
        name="simple_world",
        library_assets=[
            {
                "library_id": "worlds/ironhold/world",
                "frontmatter": {
                    "id": "ironhold",
                    "name": "Ironhold",
                    "tags": ["fantasy"],
                    "genre": "low-magic fantasy",
                    "version": 1,
                    "atmosphere": {"default_register": "neutral"},
                    "defaults": {"starting_location": "town-square"},
                },
                "body": "",
            },
        ],
        library_entities=[
            {
                "library_id": "worlds/ironhold/characters/garrick",
                "frontmatter": {
                    "id": "garrick",
                    "name": "Garrick",
                    "tags": ["npc", "smith"],
                },
                "body": "# Garrick\n\nThe town smith.",
            },
            {
                "library_id": "worlds/ironhold/locations/town-square",
                "frontmatter": {
                    "id": "town-square",
                    "name": "Town Square",
                    "tags": ["public"],
                },
                "body": "# Town Square\n\nThe heart of Ironhold.",
            },
        ],
        campaigns=[
            CampaignFixture(
                campaign_id="cmp-ironhold-1",
                name="Ironhold Run #1",
                setup=bind_setting,
            ),
        ],
    )
    fixtures_registry.register("simple_world", fixture)


def _register_two_worlds() -> None:
    """A two-setting fixture for crossover / version-pinning tests."""

    fixture = LibraryCampaignFixture(
        name="two_worlds",
        library_assets=[
            {
                "library_id": "worlds/aurora/world",
                "frontmatter": {
                    "id": "aurora",
                    "name": "Aurora",
                    "version": 1,
                    "tags": ["sci-fi"],
                },
            },
            {
                "library_id": "worlds/borealis/world",
                "frontmatter": {
                    "id": "borealis",
                    "name": "Borealis",
                    "version": 1,
                    "tags": ["sci-fi"],
                },
            },
        ],
        library_entities=[
            {
                "library_id": "worlds/aurora/characters/nova",
                "frontmatter": {"id": "nova", "name": "Nova", "tags": ["pilot"]},
                "body": "Pilot.",
            },
            {
                "library_id": "worlds/borealis/locations/orbital-7",
                "frontmatter": {"id": "orbital-7", "name": "Orbital 7"},
                "body": "A station.",
            },
        ],
        campaigns=[],
    )
    fixtures_registry.register("two_worlds", fixture)


_register_simple_world()
_register_two_worlds()


@pytest.fixture(autouse=True)
def _ensure_registered_fixtures():
    """Re-register fixtures before each test.

    Other modules' tests may clear the process-wide registry (e.g.
    ``tests/testing/test_fixtures.py`` does this via its own autouse
    cleanup). Re-registering on every integration test keeps this suite
    independent of cross-module ordering.
    """
    _register_simple_world()
    _register_two_worlds()
    yield
