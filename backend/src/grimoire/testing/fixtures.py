"""Composite fixture types for ``TestApp.with_fixtures``.

This module defines the richer fixture shapes the integration suite
needs (§2 of the testing remaining-design spec):

* :class:`CampaignFixture` — seed data for a single campaign.
* :class:`LibraryCampaignFixture` — bundle of shared library assets,
  shared library entities, character families, and a list of
  :class:`CampaignFixture` records.

The seeding path is deliberately the simplest one that works against
the public ``StateStore`` + ``LibraryService`` surface today: we write
library files through ``StateStore.write_library_file`` (which keeps
the file index + delta log in sync) and create campaigns via
``StateStore.upsert_campaign``. Anything richer is expressed via the
per-campaign ``setup`` hook, which receives the live ``TestApp``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from grimoire.testing.app import TestApp


CampaignSetup = Callable[["TestApp", "CampaignFixture"], Awaitable[None]]
LibrarySeed = Callable[["TestApp"], Awaitable[None]]


@dataclass(slots=True)
class CampaignFixture:
    """Seed data for a single campaign within a ``LibraryCampaignFixture``.

    ``setup`` runs after the shared library has been seeded and after
    ``StateStore.upsert_campaign`` has created the campaign row, so the
    hook can call into any service to bind setting refs, write
    overrides, add PCs, etc.
    """

    __test__ = False  # pytest: not a test class

    campaign_id: str
    name: str
    description: str | None = None
    mechanics_module: str | None = None
    setup: CampaignSetup | None = None
    overrides: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LibraryCampaignFixture:
    """Library state + a list of campaigns built on top of it.

    Fields:

    * ``name`` — registry key.
    * ``library_assets`` — list of ``{library_id, frontmatter, body}``
      dicts that get written via ``StateStore.write_library_file``.
      ``library_id`` is the full composite id, e.g.
      ``settings/sakura-high`` or
      ``settings/sakura-high/characters/yuki``.
    * ``library_entities`` — alias for ``library_assets`` kept separate
      so callers can split "structural" assets (settings, presets) from
      "content" entities (characters, locations, lore) when seeding. The
      two lists are concatenated at seed time.
    * ``character_families`` — list of ``{family_id, members: [asset_id,
      ...], setting_id, frontmatter}`` records. Currently stored as
      library entities of kind ``family`` (frontmatter only) so they
      participate in normal library lookups; if a dedicated family
      service lands later, ``CharacterFamilies`` can absorb this without
      breaking the fixture shape.
    * ``campaigns`` — list of :class:`CampaignFixture` records.
    * ``library_setup`` — optional async hook that runs after
      ``library_assets`` + ``library_entities`` have been seeded but
      before any campaign is created. Useful for promoting / pinning /
      versioning operations that need the bulk-seeded state in place.
    """

    __test__ = False

    name: str
    library_assets: list[dict[str, Any]] = field(default_factory=list)
    library_entities: list[dict[str, Any]] = field(default_factory=list)
    character_families: list[dict[str, Any]] = field(default_factory=list)
    campaigns: list[CampaignFixture] = field(default_factory=list)
    library_setup: LibrarySeed | None = None


async def seed_library_campaign_fixture(
    app: TestApp,
    fixture: LibraryCampaignFixture,
) -> None:
    """Apply ``fixture`` against ``app`` after migrations have run.

    Used internally by :meth:`TestApp.with_fixtures`. Exposed for tests
    that want to compose multiple fixtures into a single ``TestApp``.
    """
    store = app.state_store
    if store is None:
        raise RuntimeError("seed_library_campaign_fixture requires an entered TestApp")

    # 1. Library assets + entities.
    for asset in [*fixture.library_assets, *fixture.library_entities]:
        await store.write_library_file(
            library_id=asset["library_id"],
            frontmatter=asset.get("frontmatter", {}),
            body=asset.get("body", ""),
            source=asset.get("source", "fixture"),
        )

    # 2. Character families. There is no dedicated `family` library
    #    kind yet, so we expose the raw records on ``TestApp`` for tests
    #    that want to assert on them. When the family service lands this
    #    branch should call into it instead of stashing the list.
    if fixture.character_families:
        app.character_families = list(fixture.character_families)

    # 3. Optional library-level setup hook (promotion / pinning / etc.).
    if fixture.library_setup is not None:
        await fixture.library_setup(app)

    # 4. Per-campaign rows + setup hooks.
    for campaign in fixture.campaigns:
        await store.upsert_campaign(
            campaign_id=campaign.campaign_id,
            name=campaign.name,
            description=campaign.description,
            mechanics_module=campaign.mechanics_module,
        )
        if campaign.setup is not None:
            await campaign.setup(app, campaign)


__all__ = [
    "CampaignFixture",
    "CampaignSetup",
    "LibraryCampaignFixture",
    "LibrarySeed",
    "seed_library_campaign_fixture",
]
