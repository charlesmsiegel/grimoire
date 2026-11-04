"""Accepted-event and wrap-up references (Sprint 021): strict text forms, never returned by ``parse_ref``."""

from __future__ import annotations

import pytest

from grimoire.core.identity import AcceptedRef, ChatRef, RelationRef, ScopeRef, WrapUpRef, parse_ref


@pytest.mark.parametrize("ref, text", [
    (AcceptedRef("c1", "e-1"), "campaign:c1#accepted/e-1"),
    (WrapUpRef("c1", "w-0a1b"), "campaign:c1#wrapup/w-0a1b"),
])
def test_text_round_trips(ref, text):
    assert str(ref) == text
    assert type(ref).parse(text) == ref
    assert ref.scope == ScopeRef("c1")


@pytest.mark.parametrize("cls, text", [
    (AcceptedRef, "campaign:c1#relation/e-1"), (AcceptedRef, "campaign:c1#accepted/E-1"),
    (AcceptedRef, "campaign:c1#accepted/"), (AcceptedRef, "campaign:c1#accepted/e/1"),
    (WrapUpRef, "campaign:c1#wrapups/w"), (WrapUpRef, "campaign:C1#wrapup/w"), (WrapUpRef, " campaign:c1#wrapup/w"),
    (AcceptedRef, 7), (WrapUpRef, None),
])
def test_parse_rejects_rather_than_normalizes(cls, text):
    with pytest.raises((ValueError, TypeError)):
        cls.parse(text)


def test_segments_are_validated():
    with pytest.raises(ValueError):
        AcceptedRef("c1", "Bad")
    with pytest.raises(ValueError):
        WrapUpRef("c 1", "w")
    with pytest.raises(ValueError):
        AcceptedRef("c1", "e" * 129)


@pytest.mark.parametrize("text", ["campaign:c1#accepted/e-1", "campaign:c1#wrapup/w-1"])
def test_parse_ref_never_returns_them(text):
    with pytest.raises(ValueError):
        parse_ref(text)


def test_families_are_distinct():
    assert AcceptedRef("c1", "x") != RelationRef("c1", "x")
    assert str(WrapUpRef("c1", "x")) != str(ChatRef("c1", "x"))
