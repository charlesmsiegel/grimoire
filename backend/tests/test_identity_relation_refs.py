"""RelationRef: the address of one campaign relation record (Sprint 017)."""

from __future__ import annotations

import pytest

from grimoire.core.identity import ChatRef, RelationRef, ScopeRef, parse_ref


def test_relation_ref_round_trips():
    ref = RelationRef("c", "r-1")
    assert str(ref) == "campaign:c#relation/r-1"
    assert RelationRef.parse("campaign:c#relation/r-1") == ref
    assert ref.scope == ScopeRef("c")


@pytest.mark.parametrize("text", [
    "campaign:c#relation/R1", "campaign:c#chat/r1", "campaign:c/relation/r1", " campaign:c#relation/r1",
    "campaign:c#relation/", "campaign:#relation/r1", "campaign:c#relation/r1/x", 5, None,
])
def test_relation_ref_parse_is_strict(text):
    with pytest.raises(ValueError):
        RelationRef.parse(text)


def test_relation_ref_validates_segments_and_length():
    with pytest.raises(ValueError):
        RelationRef("c", "Bad")
    with pytest.raises(ValueError):
        RelationRef("c", "r" * 129)  # one segment is at most 128 characters
    assert len(str(RelationRef("c" * 128, "r" * 128))) <= 512


def test_parse_ref_never_returns_relation_ref():
    with pytest.raises(ValueError):
        parse_ref("campaign:c#relation/r1")


def test_relation_ref_is_not_a_chat_ref():
    with pytest.raises(ValueError):
        ChatRef.parse("campaign:c#relation/r1")
