"""Tests for the Context Builder's lore tier-routing.

Spec: docs/superpowers/specs/2026-05-19-card-imports-design.md §4.
Lore entries flow to spotlight/background/archive tiers based on the
``position`` field on the underlying ``LoreEntry``.
"""

from __future__ import annotations

from dataclasses import dataclass

from grimoire.context.builder import _route_lore_to_tier
from grimoire.types.state import ContextTier


@dataclass
class _LoreStub:
    id: str = "x"
    world_id: str = "w1"
    title: str = "X"
    body: str = "body of X"
    position: object | None = None
    at_depth: int | None = None


def test_before_cast_lands_in_spotlight() -> None:
    item = _route_lore_to_tier(_LoreStub(position="before_cast"))
    assert item is not None
    assert item.tier == ContextTier.SPOTLIGHT
    assert item.section == "lore-before"


def test_after_cast_lands_in_background() -> None:
    item = _route_lore_to_tier(_LoreStub(position="after_cast"))
    assert item is not None
    assert item.tier == ContextTier.BACKGROUND
    assert item.section == "lore-after"


def test_at_depth_lands_in_background_with_depth_section() -> None:
    item = _route_lore_to_tier(_LoreStub(position="at_depth", at_depth=2))
    assert item is not None
    assert item.tier == ContextTier.BACKGROUND
    assert item.section == "lore-depth-2"


def test_archive_position_lands_in_archive() -> None:
    item = _route_lore_to_tier(_LoreStub(position="archive"))
    assert item is not None
    assert item.tier == ContextTier.ARCHIVE
    assert item.section == "lore-archive"


def test_missing_position_falls_back_to_archive_for_legacy_stubs() -> None:
    item = _route_lore_to_tier(_LoreStub(position=None))
    assert item is not None
    assert item.tier == ContextTier.ARCHIVE


def test_enum_position_value_recognized() -> None:
    from grimoire.types.world import LorePosition

    item = _route_lore_to_tier(_LoreStub(position=LorePosition.BEFORE_CAST))
    assert item is not None
    assert item.tier == ContextTier.SPOTLIGHT


def test_text_includes_title_and_body() -> None:
    stub = _LoreStub(title="The Pact", body="Sealed in blood.", position="after_cast")
    item = _route_lore_to_tier(stub)
    assert item is not None
    assert "The Pact" in item.text
    assert "Sealed in blood." in item.text


def test_source_owner_uses_library_path() -> None:
    item = _route_lore_to_tier(_LoreStub(id="pact", world_id="wod", position="archive"))
    assert item is not None
    assert item.source.owner_id == "library:worlds/wod/lore/pact"
    assert item.source.kind == "lore"
