"""Several cards imported as one approved write (an extension of Sprint 037's importer). An adapter.

``plan_batch`` stages every card of a batch into **one** 018 image batch, so the whole batch is one store
transaction: ``CardBatchImport.commit`` writes every file of every card or, on any refusal (a library that
moved, a missing approval), none of any card. Each card is planned exactly as ``CardImporter.plan`` plans it
(the same destination rules, reports, hand-edit protection and propose checks), with two additions that only
a batch needs: a card never takes the new destination another card of the batch was given, the same card
document twice is refused (``duplicate_in_batch``), and no two cards of a batch may write the same source
(``duplicate_destination``).

``plan_card`` plans one card on its own with the same rules, for listing a batch before it is reviewed, and
``outcome`` says what a planned card would do in the words a list needs. See docs/CARDS.md.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from grimoire.cards import CardError
from grimoire.core.identity import SourceRef
from grimoire.imagestore import ImageCommit
from grimoire.store import StoreError

from .library import CardImport, CardImporter, ImportReport

#: the most cards one batch stages (one transaction, one review)
MAX_BATCH_CARDS = 50


@dataclass(frozen=True)
class BatchCard:
    """One card file of a batch: its bytes (a card document or a PNG card) and the label it came under.
    ``update_same_card`` is ``plan``'s: the one card source whose last import had exactly this card is
    updated instead of a second source being made."""

    data: bytes
    origin: str | None = None
    update_same_card: bool = True


class BatchCardRefused(CardError):
    """One card of a batch could not be staged, so the batch was not. ``index`` is its place in the batch;
    ``reason`` and ``detail`` are the card's own refusal."""

    def __init__(self, index: int, message: str, reason: str, detail: str | None = None) -> None:
        super().__init__(message, reason, detail)
        self.index = index

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.index, str(self), self.reason, self.detail))


@dataclass(frozen=True)
class CardOutcome:
    """What one planned card does. ``status`` is the report's (``new``, ``update``, ``unchanged``).
    ``from_revision`` and ``to_revision`` are the imported entity's head before and after, when this import
    moves it (an update that changes only pictures, lorebooks or the import record leaves both ``None``).
    ``same_name`` says a definition of the same kind elsewhere already has this name, or the default
    destination was taken: reported, and never a reason to treat the two as one card."""

    status: str
    source: str
    from_revision: str | None
    to_revision: str | None
    same_name: bool


def outcome(report: ImportReport) -> CardOutcome:
    def main(refs: Sequence[SourceRef]) -> str | None:
        return next((ref.revision for ref in refs if (ref.kind, ref.entity_id) == (report.main.kind, report.main.entity_id)),
                    None)

    moved_from = main(report.replaces)
    same_name = False
    for collision in report.collisions:
        if collision.kind == "destination_taken":
            same_name = True
        elif collision.kind == "same_name":
            for text in collision.refs:
                try:
                    same_name = same_name or SourceRef.parse(text).kind == report.kind
                except ValueError:
                    continue
    return CardOutcome(report.status, report.source, moved_from, main(report.writes) if moved_from is not None else None,
                       same_name)


def plan_card(importer: CardImporter, card: BatchCard, reserved: Collection[str] = ()) -> CardImport:
    """One card planned on its own, as it would be inside a batch whose other cards write ``reserved``."""
    plan = importer._read(card.data, origin=card.origin, update_same_card=card.update_same_card)
    with importer.store.read_pass():
        return importer._stage(plan, None, reserved=frozenset(reserved))


class CardBatchImport:
    """Several planned cards in one transaction: ``cards`` (each card's own report and files, in order),
    the exact bytes of them all (``preview``) and ``commit(approvals)``."""

    def __init__(self, cards: Sequence[CardImport], batch: object) -> None:
        self.cards = tuple(cards)
        self._batch = batch
        self._preview: dict[str, bytes] = {}
        for card in self.cards:
            self._preview.update(card.preview())
        self._committed = False

    def preview(self) -> dict[str, bytes]:
        """The bytes each path will hold, across every card: what a human approves."""
        return dict(self._preview)

    def commit(self, approvals: Mapping[str, str] | None = None) -> ImageCommit:
        """Write every card's previewed bytes in one store transaction, or nothing (``StaleWrite`` if the
        library moved under any card, ``WriteDenied`` without the grants and approvals an untrusted
        handle needs)."""
        if self._committed:
            raise StoreError("this batch was already committed")
        if not self._preview:  # every card unchanged: nothing to write
            self._committed = True
            return ImageCommit({}, (), ())
        result = self._batch.commit(approvals)  # type: ignore[attr-defined]
        self._committed = True
        return result


def plan_batch(importer: CardImporter, cards: Sequence[BatchCard]) -> CardBatchImport:
    """Stage ``cards`` into one batch; raises ``BatchCardRefused`` naming the first card that cannot be
    staged (nothing is staged then), or ``ImportDenied``. Writes nothing."""
    if not cards:
        raise CardError("a batch needs at least one card", "invalid_options", "cards")
    if len(cards) > MAX_BATCH_CARDS:
        raise CardError(f"a batch takes at most {MAX_BATCH_CARDS} cards", "too_many_cards", "cards")
    batch = importer.images.change()
    planned: list[CardImport] = []
    reserved: set[str] = set()
    seen: dict[str, int] = {}
    with importer.store.read_pass():
        for index, card in enumerate(cards):
            try:
                plan = importer._read(card.data, origin=card.origin, update_same_card=card.update_same_card)
                document = hashlib.sha256(plan.original).hexdigest()
                if document in seen:  # the same card document twice (as a PNG and as JSON, say) is one card
                    raise CardError(f"this is the same card as card {seen[document] + 1} of the batch",
                                    "duplicate_in_batch", "card")
                seen[document] = index
                staged = importer._stage(plan, None, shared=batch, reserved=frozenset(reserved))
            except BatchCardRefused:
                raise
            except CardError as error:
                raise BatchCardRefused(index, f"card {index + 1} of the batch: {error}", error.reason,
                                       error.detail) from None
            reserved.add(staged.report.source)
            planned.append(staged)
    return CardBatchImport(planned, batch)


__all__ = ["BatchCard", "BatchCardRefused", "CardBatchImport", "CardOutcome", "MAX_BATCH_CARDS", "outcome",
           "plan_batch", "plan_card"]
