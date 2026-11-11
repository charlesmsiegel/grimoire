"""A batch of card files for the review UI (110): listed first, then proposed as one write. No store of its own.

``scan_cards`` reads every file of a batch and says, row by row, what an import would do with it: what the
card is, the lorebook it embeds, and its result (new, an update of a card already here, already here
unchanged, or the same name as another card), each decided by 037's planner by exact identity. A file that
cannot be imported is a row with its reason, never dropped. ``plan_cards`` then stages the chosen rows as one
037 batch (``grimoire.cardstore.batch``): one transaction, one preview, one digest.

A scan holds the files in memory so the review needs no second upload; nothing here writes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field

from grimoire.cards import CardError
from grimoire.cardstore import (
    MAX_BATCH_CARDS, BatchCard, BatchCardRefused, CardBatchImport, CardImporter, card_facts, outcome, plan_batch,
    plan_card,
)
from grimoire.imagestore.disk import InvalidImage
from grimoire.store import StaleWrite, StoreError

from .views import jsonable

#: the bytes of every file of one batch together (one request carries them, base64, under the app's cap)
MAX_BATCH_BYTES = 32 * 1024 * 1024
#: scanned batches held at once; the oldest is dropped (dropped files are chosen again) past this
MAX_HELD_BATCHES = 2


class BatchRefused(ValueError):
    """A batch op could not be done; nothing was staged. ``reason`` is machine-readable."""

    def __init__(self, reason: str, message: str, details: object = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.details = details


@dataclass(frozen=True)
class BatchFile:
    """One uploaded file: its bytes, or why it could not be taken (``refusal``: reason and message)."""

    origin: str | None
    data: bytes | None = None
    refusal: tuple[str, str] | None = None


@dataclass
class ScannedBatch:
    """The files of a scan and the row shown for each (``rows[i]`` is about ``files[i]``)."""

    files: list[BatchFile]
    rows: list[dict] = field(default_factory=list)

    def view(self) -> dict:
        importable = [row for row in self.rows if row["importable"]]
        return {"cards": self.rows,
                "counts": {"files": len(self.rows), "importable": len(importable),
                           "refused": len(self.rows) - len(importable),
                           "new": sum(row["result"]["status"] == "new" for row in importable),
                           "update": sum(row["result"]["status"] == "update" for row in importable),
                           "unchanged": sum(row["result"]["status"] == "unchanged" for row in importable)}}


def _refused(index: int, origin: str | None, reason: str, message: str) -> dict:
    return {"item": index, "origin": origin, "importable": False, "refused": {"reason": reason, "message": message}}


def _result(report) -> dict:  # type: ignore[no-untyped-def]
    found = outcome(report)
    return {"status": found.status, "source": found.source, "from": found.from_revision, "to": found.to_revision,
            "same_name": found.same_name}


def scan_cards(importer: CardImporter, files: Sequence[BatchFile]) -> ScannedBatch:
    """List a batch. Each importable card is planned on its own (nothing is written), after the ones before
    it: a later card never takes an earlier one's new destination, and the same card twice is refused the
    second time. ``ImportDenied`` (the owner may not write card sources at all) is raised, not a row."""
    scanned = ScannedBatch(list(files))
    seen: dict[str, int] = {}
    reserved: set[str] = set()
    for index, file in enumerate(files):
        if file.refusal is not None or file.data is None:
            reason, message = file.refusal or ("invalid_field", "no file was sent")
            scanned.rows.append(_refused(index, file.origin, reason, message))
            continue
        try:
            facts = card_facts(file.data)
            if facts.sha256 in seen:
                first = files[seen[facts.sha256]].origin or f"file {seen[facts.sha256] + 1}"
                raise CardError(f"this is the same card as {first}, which is already in this batch",
                                "duplicate_in_batch", "card")
            planned = plan_card(importer, BatchCard(file.data, file.origin), reserved)
        except CardError as error:
            scanned.rows.append(_refused(index, file.origin, error.reason, str(error)))
            continue
        except InvalidImage as error:
            scanned.rows.append(_refused(index, file.origin, "invalid_portrait",
                                         f"the picture is not one this app stores ({error})"))
            continue
        except StaleWrite:
            raise
        except StoreError as error:
            reason = getattr(error, "reason", None)
            scanned.rows.append(_refused(index, file.origin, reason if isinstance(reason, str) and reason else "refused",
                                         str(error)))
            continue
        seen[facts.sha256] = index
        report = planned.report
        reserved.add(report.source)
        embedded = facts.lorebook if facts.kind != "lorebook" else None
        scanned.rows.append({
            "item": index, "origin": file.origin, "importable": True, "name": facts.name, "kind": facts.kind,
            "format": facts.format, "container": facts.container, "portrait": facts.portrait,
            "sha256": hashlib.sha256(file.data).hexdigest(),
            "lorebooks": [] if embedded is None else [{"name": embedded.name, "entries": embedded.entries,
                                                       "origin": "embedded"}],
            "entries": facts.lorebook.entries if facts.kind == "lorebook" and facts.lorebook else None,
            "result": _result(report), "writes": len(report.paths), "losses": len(report.losses)})
    return scanned


def plan_cards(importer: CardImporter, scanned: ScannedBatch, include: Sequence[int]) -> tuple[CardBatchImport, dict]:
    """Stage the rows ``include`` names as one batch; the view lists each card with its own report and files,
    and every file of the scan that is left out with why. Raises ``BatchRefused``."""
    if not include:
        raise BatchRefused("invalid_selection", "choose at least one card to review")
    if len(set(include)) != len(include) or len(include) > MAX_BATCH_CARDS:
        raise BatchRefused("invalid_selection", "each card is included once")
    for index in include:
        if not 0 <= index < len(scanned.rows) or not scanned.rows[index]["importable"]:
            raise BatchRefused("invalid_selection", f"file {index + 1} of this batch is not a card that can be imported")
    chosen = sorted(include)
    cards = [BatchCard(scanned.files[index].data, scanned.files[index].origin) for index in chosen]  # type: ignore[arg-type]
    try:
        planned = plan_batch(importer, cards)
    except BatchCardRefused as error:
        index = chosen[error.index]
        raise BatchRefused(error.reason, f"{scanned.rows[index]['origin'] or 'a card'}: {error}",
                           {"item": index, "detail": error.detail}) from None
    view_cards = []
    for index, card in zip(chosen, planned.cards):
        row = scanned.rows[index]
        view_cards.append({"item": index, "origin": row["origin"], "name": row["name"], "kind": row["kind"],
                           "result": _result(card.report), "paths": list(card.report.paths),
                           "report": jsonable(card.report)})
    left_out = []
    for row in scanned.rows:
        if row["item"] in chosen:
            continue
        if row["importable"]:
            left_out.append({"item": row["item"], "origin": row["origin"], "name": row["name"], "why": "not_included"})
        else:
            left_out.append({"item": row["item"], "origin": row["origin"], "why": "refused", **row["refused"]})
    return planned, {"count": len(view_cards), "cards": view_cards, "left_out": left_out}


__all__ = ["BatchFile", "BatchRefused", "MAX_BATCH_BYTES", "MAX_HELD_BATCHES", "ScannedBatch", "plan_cards",
           "scan_cards"]
