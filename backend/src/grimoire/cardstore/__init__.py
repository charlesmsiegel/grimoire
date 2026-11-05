"""Store-backed card import (Sprint 037): decode, stage, preview, approve, commit. An adapter.

The pure mapping lives in ``grimoire.cards``; this package reads bytes, chooses the destination
source, stages the records, picture and placement in one guarded batch and checks the handle may
propose every staged path. See docs/CARDS.md.
"""

from grimoire.cards import CardError

from .library import (
    CARD_NAMESPACE, MAX_CARD_BYTES, PORTRAIT_SLOT, CardImport, CardImporter, CastStatus, Collision, ImportDenied,
    ImportReport,
)

__all__ = [
    "CARD_NAMESPACE", "CardError", "CardImport", "CardImporter", "CastStatus", "Collision", "ImportDenied",
    "ImportReport", "MAX_CARD_BYTES", "PORTRAIT_SLOT",
]
