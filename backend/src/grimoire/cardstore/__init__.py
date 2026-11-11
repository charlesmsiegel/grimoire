"""Store-backed card import (Sprint 037): decode, stage, preview, approve, commit. An adapter.

The pure mapping lives in ``grimoire.cards``; this package reads bytes, chooses the destination
source, stages the records, picture and placement in one guarded batch and checks the handle may
propose every staged path. See docs/CARDS.md.
"""

from grimoire.cards import CardError

from .facts import CardFacts, LorebookFacts, card_facts
from .library import (
    CARD_NAMESPACE, MAX_CARD_BYTES, MAX_CARD_PNG_BYTES, PORTRAIT_SLOT, CardImport, CardImporter, CastStatus, Collision,
    ImportDenied, ImportReport, Unwrapped, unwrap,
)
from .png import CARD_KEYWORDS, PNG_SIGNATURE, EmbeddedCard, read_png_card

__all__ = [
    "CARD_KEYWORDS", "CARD_NAMESPACE", "CardError", "CardFacts", "CardImport", "CardImporter", "CastStatus", "Collision",
    "EmbeddedCard", "ImportDenied", "ImportReport", "LorebookFacts", "MAX_CARD_BYTES", "MAX_CARD_PNG_BYTES", "PNG_SIGNATURE",
    "PORTRAIT_SLOT", "Unwrapped", "card_facts", "read_png_card", "unwrap",
]
