"""Tests for the lore-entry heuristic classifier (spec §3)."""

from __future__ import annotations

import dataclasses

import pytest

from grimoire.library.classify import Suggestion, suggest_kind
from grimoire.types.common import EntityKind
from grimoire.types.world import LoreEntry


def _lore(title: str, body: str = "", **kw) -> LoreEntry:
    return LoreEntry(world_id="w", id="x", title=title, body=body, **kw)


def test_character_signal_proper_noun_plus_pronouns() -> None:
    entry = _lore(
        "Beatrice",
        body="She was born in 1789. Her family disowned her. She studied alchemy.",
    )
    s = suggest_kind(entry)
    assert s.kind == EntityKind.CHARACTER
    assert s.confidence >= 0.6
    assert "pronoun" in s.reason.lower() or "proper noun" in s.reason.lower()


def test_location_signal_the_plus_place_noun() -> None:
    entry = _lore(
        "The Tremere Chantry",
        body="Located within the inner District, the chantry rises three floors.",
    )
    s = suggest_kind(entry)
    assert s.kind == EntityKind.LOCATION


def test_faction_signal_clan_noun() -> None:
    entry = _lore(
        "Clan Tremere",
        body="Members of the Clan are bound by blood oath. Founded in the 12th century.",
    )
    s = suggest_kind(entry)
    assert s.kind == EntityKind.FACTION


def test_item_signal_artifact_noun() -> None:
    entry = _lore(
        "Sword of Caine",
        body="The blade grants its wielder unnatural strength. Forged in the first city.",
    )
    s = suggest_kind(entry)
    assert s.kind == EntityKind.ITEM


def test_no_strong_signal_returns_lore() -> None:
    entry = _lore("Background note", body="It rained that night and the streets were wet.")
    s = suggest_kind(entry)
    assert s.kind == EntityKind.LORE
    assert s.confidence == 0.0


def test_threshold_overrides_default() -> None:
    entry = _lore("Beatrice", body="She walked.")
    relaxed = suggest_kind(entry, threshold=0.1)
    strict = suggest_kind(entry, threshold=0.95)
    assert relaxed.kind == EntityKind.CHARACTER
    assert strict.kind == EntityKind.LORE


def test_suggestion_is_frozen_dataclass() -> None:
    s = Suggestion(kind=EntityKind.CHARACTER, confidence=0.7, reason="x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.confidence = 0.0  # type: ignore[misc]
