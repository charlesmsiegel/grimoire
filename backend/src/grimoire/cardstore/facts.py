"""What a card file says of itself, before anything is planned: read-only, and no library is opened.

``card_facts`` decodes a card file exactly as ``CardImporter.plan`` would (a PNG card's embedded document
included) and names what an import would start from: the container, the format, what it becomes, its name,
whether the file is also the card's picture, and the lorebook the card embeds. It is what a person is shown
between choosing a file and asking for the preview; the preview alone decides what is written.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from grimoire.cards import CHARACTER, LOREBOOK, SCENARIO, CharacterCard, LorebookCard, ScenarioCard, parse_card

from .library import _decode, _title, unwrap


@dataclass(frozen=True)
class LorebookFacts:
    """A lorebook a card carries: the name its record would have, and how many entries were read."""

    name: str
    entries: int


@dataclass(frozen=True)
class CardFacts:
    """``container`` is ``png`` or ``json``; ``keyword`` the PNG text chunk the card was under. ``sha256``
    and ``size`` are the card document's own (a PNG card's embedded JSON, not the picture). ``portrait``
    says the file itself is the card's picture. ``lorebook`` is the embedded lorebook of a character or
    scenario, or a lorebook card's own book. ``losses`` counts the fields that would not be carried over."""

    container: str
    keyword: str | None
    format: str
    kind: str
    name: str
    sha256: str
    size: int
    portrait: bool
    lorebook: LorebookFacts | None
    losses: int


def card_facts(data: bytes, *, as_scenario: bool = False) -> CardFacts:
    """Raises ``CardError`` for a file ``plan`` would refuse before reading the library."""
    found = unwrap(data)
    card = parse_card(_decode(found.card, "card"), as_scenario=as_scenario)
    title = _title(card)
    kind = LOREBOOK if isinstance(card, LorebookCard) else SCENARIO if isinstance(card, ScenarioCard) else CHARACTER
    assert isinstance(card, (CharacterCard, ScenarioCard, LorebookCard))
    book = None
    if card.lorebook is not None:
        named = title if isinstance(card, LorebookCard) else card.lorebook.name or f"{title} lore"
        book = LorebookFacts(named, len(card.lorebook.entries))
    return CardFacts(
        container="png" if found.png is not None else "json", keyword=found.keyword, format=card.format, kind=kind,
        name=title, sha256=hashlib.sha256(found.card).hexdigest(), size=len(found.card),
        portrait=found.png is not None and not isinstance(card, LorebookCard), lorebook=book, losses=len(card.losses))


__all__ = ["CardFacts", "LorebookFacts", "card_facts"]
