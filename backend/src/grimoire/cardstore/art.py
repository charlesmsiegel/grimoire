"""A card import's art: pictures, placements and hand edits (Sprints 037 and 038). An adapter.

``grimoire.cards.card_art`` decides which slot each picture fills; this module decides, against the
library, what an import writes there. One rule covers every slot, the portrait's included:

- a slot that already holds what this import would write is left as it is;
- an empty slot, or one that still holds what the last import of this card placed there, gets
  the card's picture (``set``) or, when the picture is missing, an explicit ``unresolved``
  placement that says why;
- anything else was chosen by a person (another picture, a crop, a ``hide``): it stands, and the
  import reports a ``hand_edited`` collision naming it, unless the plan replaces edits.

What the last import placed is read from its import record: ``avatars`` (the ``avatar`` slot,
Sprint 037's key, still written so an older reader keeps its meaning) and ``art`` (every other
slot, Sprint 038). A slot an import no longer fills keeps its placement and its record entry, so
the slot is still the card's own if the asset comes back.

Identical bytes are one image object per source (and one blob per library), whichever assets
carry them: the portrait's id is ``portrait-<sha256[:16]>`` (as Sprint 037 named it), any other
picture's ``art-<sha256[:16]>``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field

from grimoire.cards import CardError, Loss
from grimoire.cards.art import ArtPiece
from grimoire.core.identity import ImageRef, PlacementRef, SourceRef
from grimoire.images.model import Placement, slot_expression, slot_role
from grimoire.imagestore import ImageBatch, ImageLibrary, InvalidImage, InvalidPlacement
from grimoire.store import BlobCorrupt, ImageBlob, InvalidBlob, LibraryStore, NameCollision, RecordNotFound, StoreError

#: the slot the caller's portrait fills on the imported entity (018's slot decides the role)
PORTRAIT_SLOT = "avatar"
#: what an import record says an import left in a slot whose picture is missing
UNRESOLVED = "unresolved"
#: why a picture is missing, as an ``unresolved`` placement's note says it (never the card's text)
NOTES = {
    "not_fetched": "a remote picture; nothing is fetched",
    "not_embedded": "a picture packaged with the card file, which this import does not read",
    "no_default": "the card's own picture, which was not given with the card",
    "invalid_data": "a data address whose content cannot be decoded",
    "unsupported_uri": "an address this import does not read",
    "unsupported_media": "not a PNG, JPEG, GIF or WebP picture",
}


@dataclass(frozen=True)
class ArtSlot:
    """What one import does with one slot of the imported entity, for the report.

    ``status``: ``placed`` (written by this commit), ``unchanged`` (already there) or
    ``hand_edited`` (a person's choice stands). ``image`` is the card's picture for the slot and
    ``unresolved`` why it is missing (one of ``NOTES``); ``asset`` is the card asset's place in
    ``assets`` (``None`` for the caller's portrait)."""

    slot: str
    status: str
    image: ImageRef | None
    unresolved: str | None
    asset: int | None


@dataclass
class ArtStage:
    """Everything an import's art adds to its batch, and what its record and report say."""

    places: list[tuple[str, Placement, str | None]] = field(default_factory=list)
    #: new image objects: ref -> (bytes, provenance, the asset that brought them or None)
    adds: dict[ImageRef, tuple[bytes, dict[str, object], int | None]] = field(default_factory=dict)
    repairs: dict[str, bytes] = field(default_factory=dict)
    edited: list[str] = field(default_factory=list)
    #: slot -> the record's text for it after this import (an image ref, or ``unresolved``)
    recorded: dict[str, str] = field(default_factory=dict)
    slots: list[ArtSlot] = field(default_factory=list)
    losses: list[Loss] = field(default_factory=list)
    portrait: ImageRef | None = None

    @property
    def writes(self) -> bool:
        return bool(self.places or self.repairs)

    def stage(self, batch: ImageBatch, source_id: str, subject: SourceRef) -> None:
        """Add the repairs, pictures and placements to ``batch``, refusing as a ``CardError``."""
        for data in self.repairs.values():
            batch.repair_blob(data)
        for ref, (data, provenance, asset) in self.adds.items():
            try:
                batch.add_image(source_id, ref.image_id, data, provenance)
            except NameCollision:  # the shared blob store, not the destination: no target helps
                raise CardError("the library's picture store holds a differently spelled copy of "
                                "this picture's file", "portrait_unstorable", _detail(asset)) from None
        for slot, placement, expected in self.places:
            try:
                batch.place(subject, slot, placement, expected)
            except InvalidPlacement as error:
                if error.reason != "invalid_image":
                    raise
                raise CardError("the destination holds this picture's image record, but it cannot be "
                                "read", "portrait_unstorable", _detail(self._asset_of(slot))) from None

    def _asset_of(self, slot: str) -> int | None:
        return next((entry.asset for entry in self.slots if entry.slot == slot), None)


def _detail(asset: int | None) -> str:
    return "portrait" if asset is None else f"assets[{asset}]"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def recorded_art(avatars: Mapping[str, object], art: Mapping[str, object], subject: SourceRef) -> dict[str, str]:
    """slot -> what the last import placed there, from an import record's ``avatars`` and ``art``."""
    found: dict[str, str] = {}
    mine = art.get(str(subject))
    if isinstance(mine, Mapping):
        found.update({slot: text for slot, text in mine.items() if isinstance(slot, str) and isinstance(text, str)
                      and slot != PORTRAIT_SLOT})
    avatar = avatars.get(str(subject))
    if isinstance(avatar, str):
        found[PORTRAIT_SLOT] = avatar
    return found


def _matches(placement: Placement, slot: str, text: str | None) -> bool:
    """Whether ``placement`` is what an import writes for ``text`` (an image ref or ``unresolved``)
    in ``slot``: provenance aside, nothing a person could have changed (a crop, a focus) differs."""
    if text is None:
        return False
    role, expression = slot_role(slot), slot_expression(slot)
    if placement.role != role or placement.expression != expression or placement.crop is not None \
            or placement.focus is not None:
        return False
    if text == UNRESOLVED:
        return placement.mode == UNRESOLVED
    return placement.mode == "set" and placement.image is not None and str(placement.image) == text


def _image_present(store: LibraryStore, ref: ImageRef) -> bool:
    try:
        store.read(ref)
    except RecordNotFound:
        return False
    except (StoreError, ValueError):
        return True  # taken, if unreadable: placing over it is refused at plan (``portrait_unstorable``)
    return True


def _damaged(store: LibraryStore, blob: ImageBlob) -> bool:
    """Whether the library's copy of these exact bytes is there but no longer matches its name."""
    try:
        store.read_blob(blob.sha256, blob.ext)
    except BlobCorrupt:
        return True
    except (StoreError, ValueError):
        return False  # absent, or a look-alike name the batch reports
    return False


def _provenance(piece: ArtPiece) -> dict[str, object] | None:
    if piece.from_portrait:
        return None  # Sprint 037's portrait placement, exactly as it was written
    asset: dict[str, object] = {"index": piece.index, "type": piece.asset_type}
    if piece.name is not None:
        asset["name"] = piece.name
    found: dict[str, object] = {"origin": "character card asset", "x-card-asset": asset}
    if piece.reason is not None:
        found["note"] = NOTES[piece.reason]
    return found


def stage_art(images: ImageLibrary, source_id: str, subject: SourceRef, pieces: tuple[ArtPiece, ...],
              last: Mapping[str, str], replace_edits: bool, image_provenance: Mapping[str, object],
              asset_path) -> ArtStage:  # type: ignore[no-untyped-def]
    """What an import of ``pieces`` writes on ``subject`` in ``source_id``. Reads only."""
    stage = ArtStage(recorded=dict(last))
    portrait = next((piece for piece in pieces if piece.from_portrait), None)
    portrait_sha = _sha(portrait.data) if portrait is not None and portrait.data is not None else None
    if portrait_sha is not None:
        stage.portrait = ImageRef(source_id, f"portrait-{portrait_sha[:16]}")
    for piece in pieces:
        reason, ref, blob = piece.reason, None, None
        if piece.data is not None:
            try:
                blob = ImageBlob(piece.data)
            except InvalidBlob as error:
                if piece.from_portrait:  # Sprint 037: a portrait that is no picture refuses the import
                    raise InvalidImage(str(error), error.reason) from None
                reason = "unsupported_media"
                stage.losses.append(Loss(asset_path(piece.index), "unavailable"))
        if blob is not None:
            sha = blob.sha256
            ref = stage.portrait if sha == portrait_sha else ImageRef(source_id, f"art-{sha[:16]}")
        role, expression = slot_role(piece.slot), slot_expression(piece.slot)
        provenance = _provenance(piece if reason == piece.reason else _with_reason(piece, reason))
        if ref is not None:
            desired = Placement("set", role, ref, expression=expression, provenance=provenance)
            text = str(ref)
        else:
            desired = Placement(UNRESOLVED, role, expression=expression, provenance=provenance)
            text = UNRESOLVED
        placement_ref = PlacementRef(subject, piece.slot)
        try:
            existing, expected = images.read_placement(placement_ref)
        except RecordNotFound:
            existing, expected = None, None
        except InvalidPlacement as error:
            if error.reason == "unsupported_format":
                raise CardError("a slot of the imported entity holds a placement this version cannot replace",
                                "invalid_target", _detail(piece.index)) from None
            existing, expected = None, error.etag  # malformed, but replaceable
        if blob is not None and _damaged(images.store, blob):
            # the stored copy of these exact bytes no longer matches its name: repaired by this
            # import, whatever else it does (a hand edit of the slot included)
            stage.repairs.setdefault(blob.sha256, blob.data)
        if existing is not None and _matches(existing, piece.slot, text):
            status = "unchanged"
        elif (existing is None and expected is None) or plan_replaces(existing, piece.slot, last, replace_edits):
            status = "placed"
            stage.places.append((piece.slot, desired, expected))
        else:
            stage.edited.append(str(placement_ref))  # a person's choice (or a hide) stands
            stage.slots.append(ArtSlot(piece.slot, "hand_edited", ref, reason, piece.index))
            continue
        stage.recorded[piece.slot] = text
        stage.slots.append(ArtSlot(piece.slot, status, ref, reason, piece.index))
        if status == "placed" and ref is not None and blob is not None and ref not in stage.adds \
                and not _image_present(images.store, ref):
            stage.adds[ref] = (blob.data, dict(image_provenance), piece.index)
    return stage


def plan_replaces(existing: Placement | None, slot: str, last: Mapping[str, str], replace_edits: bool) -> bool:
    """Whether a slot that does not hold this import's picture is still the card's to write: it
    holds exactly what the last import placed, or the plan replaces edits (an unreadable
    placement is replaced only then, as Sprint 037 did for the portrait)."""
    return replace_edits or (existing is not None and _matches(existing, slot, last.get(slot)))


def _with_reason(piece: ArtPiece, reason: str | None) -> ArtPiece:
    return ArtPiece(piece.slot, None, reason, piece.index, piece.asset_type, piece.name)


__all__ = ["NOTES", "PORTRAIT_SLOT", "UNRESOLVED", "ArtSlot", "ArtStage", "recorded_art", "stage_art"]
