"""``HoldingsRef`` (Sprint 023): a strict, separate reference family for one holder's counted holdings."""

from __future__ import annotations

import pytest

from grimoire.core.identity import ChatRef, HoldingsRef, InstanceRef, ScopeRef, parse_ref

REF = HoldingsRef("c1", "character", "mara-1")


def test_holdings_ref_round_trips():
    assert str(REF) == "campaign:c1/character/mara-1#holdings"
    assert HoldingsRef.parse(str(REF)) == REF


@pytest.mark.parametrize("text", [
    "campaign:c1#holdings", "campaign:c1/character/mara-1#holdings/x", "campaign:c1/Character/m#holdings",
    "campaign:c1/character/mara-1", " campaign:c1/a/b#holdings", "campaign:c1/a/b#holdings ",
    "campaign:c1/a/b/c#holdings", "campaign:c1#chat/t", "campaign:c1", "world:x/a/b#holdings",
])
def test_holdings_ref_rejects_look_alikes(text):
    with pytest.raises(ValueError):
        HoldingsRef.parse(text)


@pytest.mark.parametrize("parts", [("C1", "character", "m"), ("c1", "Character", "m"), ("c1", "character", "M"),
                                   ("c1", "", "m"), ("c1", "character", "a/b")])
def test_holdings_ref_constructor_validates_every_segment(parts):
    with pytest.raises(ValueError):
        HoldingsRef(*parts)


def test_parse_ref_and_the_other_families_never_return_holdings():
    with pytest.raises(ValueError):
        parse_ref(str(REF))
    for family in (ChatRef, ScopeRef, InstanceRef):
        with pytest.raises(ValueError):
            family.parse(str(REF))


def test_of_and_holder_invert():
    holder = InstanceRef("c1", "character", "mara-1")
    assert HoldingsRef.of(holder) == REF and REF.holder == holder
    with pytest.raises(TypeError):
        HoldingsRef.of(ChatRef("c1", "t"))  # type: ignore[arg-type]


def test_holdings_ref_text_limit():
    # Three 128-character segments stay under the 512-character text limit; a longer segment is refused.
    assert len(str(HoldingsRef("c" * 128, "k" * 128, "i" * 128))) <= 512
    with pytest.raises(ValueError):
        HoldingsRef("c1", "character", "m" * 129)
    with pytest.raises(ValueError):
        HoldingsRef.parse("campaign:c1/character/" + "m" * 129 + "#holdings")
