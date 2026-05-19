"""Transient-state core types."""

from __future__ import annotations

from datetime import UTC, datetime

from grimoire.types.transient import (
    EntityKind,
    ObserverKind,
    Provenance,
    TransientConflict,
    TransientValue,
)


def test_provenance_enum_values():
    assert Provenance.EXTRACTOR_AUTO.value == "extractor:auto"
    assert Provenance.EXTRACTOR_REVIEWED.value == "extractor:reviewed"
    assert Provenance.USER_HUD.value == "user:hud"
    assert Provenance.USER_EDIT.value == "user:edit"


def test_provenance_mechanics_with_module_id():
    p = Provenance.mechanics("wod")
    assert p.value == "mechanics:wod"
    assert p.module_id == "wod"
    assert p == "mechanics:wod"


def test_provenance_parse_round_trip():
    assert Provenance.parse("user:edit") is Provenance.USER_EDIT
    parsed = Provenance.parse("mechanics:dnd5e")
    assert parsed.module_id == "dnd5e"
    assert parsed.value == "mechanics:dnd5e"


def test_entity_kind_enum_complete():
    assert {e.value for e in EntityKind} == {"character", "location", "faction", "scene"}


def test_observer_kind_enum_complete():
    assert {o.value for o in ObserverKind} == {
        "author",
        "pc_owner",
        "other_pc",
        "audience",
    }


def test_transient_value_roundtrip():
    now = datetime.now(UTC)
    v = TransientValue(
        id=1,
        entity_id="char_florence",
        field="mood",
        value="guarded",
        provenance=Provenance.EXTRACTOR_AUTO,
        confidence=0.82,
        source_post_id="p_4710",
        created_at=now,
        expires_at=None,
        in_game_at=None,
        decayed=False,
    )
    assert v.entity_id == "char_florence"
    assert v.field == "mood"
    assert v.confidence == 0.82


def test_transient_conflict_carries_both_writes():
    now = datetime.now(UTC)
    user = TransientValue(
        id=1,
        entity_id="x",
        field="mood",
        value="happy",
        provenance=Provenance.USER_EDIT,
        confidence=1.0,
        source_post_id=None,
        created_at=now,
        expires_at=None,
        in_game_at=None,
        decayed=False,
    )
    extractor = TransientValue(
        id=2,
        entity_id="x",
        field="mood",
        value="sad",
        provenance=Provenance.EXTRACTOR_AUTO,
        confidence=0.8,
        source_post_id="p_1",
        created_at=now,
        expires_at=None,
        in_game_at=None,
        decayed=False,
    )
    conflict = TransientConflict(current=user, losing=extractor)
    assert conflict.current.value == "happy"
    assert conflict.losing.value == "sad"
