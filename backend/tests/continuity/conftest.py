"""Shared fixtures for Continuity tests."""

from __future__ import annotations

import pytest

from grimoire.continuity import (
    ContinuityService,
    Fact,
    FactSource,
    FactSubject,
    InGameTime,
)


def make_fact(
    *,
    fact_id: str = "",
    text: str = "winifred promised the orchard.",
    post: str = "post-1",
    day: int = 1,
    confidence: float = 0.9,
    source: FactSource = FactSource.NARRATOR,
    speaker_id: str | None = None,
    characters: list[str] | None = None,
    locations: list[str] | None = None,
    factions: list[str] | None = None,
    items: list[str] | None = None,
    scope: str = "public",
    keywords: list[str] | None = None,
    tags: list[str] | None = None,
) -> Fact:
    return Fact(
        id=fact_id,
        text=text,
        established_in_post=post,
        established_at_in_game=InGameTime(day_count=day),
        confidence=confidence,
        source=source,
        speaker_id=speaker_id,
        about=FactSubject(
            character_ids=list(characters or []),
            location_ids=list(locations or []),
            faction_ids=list(factions or []),
            item_ids=list(items or []),
            scope=scope,
        ),
        keywords=list(keywords or []),
        tags=list(tags or []),
    )


@pytest.fixture
def service() -> ContinuityService:
    return ContinuityService()


@pytest.fixture
def fact_factory():
    return make_fact
