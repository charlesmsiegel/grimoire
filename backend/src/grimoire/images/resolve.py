"""Effective placements of one subject: a pure, deterministic function of data (Sprint 018).

The caller (``grimoire.imagestore``) gathers files; this module decides. There is no network,
no model call and no name matching. Every slot ends as exactly one of ``Shown``, ``Hidden`` or
``Unavailable``, and nothing ever falls back to a different picture: a campaign ``hide`` hides
even when the source has a picture there, a broken reference stays broken, verbatim, and an
``unresolved`` placement (038: a picture that belongs here but is missing) stays unavailable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from grimoire.core.identity import ImageRef, InstanceRef, PlacementRef, SourceRef

from .model import BlobInfo, Crop, Focus, ImageObject, Placement, slot_role, validate_placement


@dataclass(frozen=True)
class PlacementEntry:
    """One placement file as read: a decoded placement, or why it could not be (``reason``)."""

    ref: PlacementRef
    placement: Placement | None
    reason: str | None = None  # invalid_placement | unsupported_format
    text: str = ""


@dataclass(frozen=True)
class ImageEntry:
    ref: ImageRef
    image: ImageObject | None
    reason: str | None = None  # missing_image | invalid_image | unsupported_format


@dataclass(frozen=True)
class BlobFact:
    presence: str  # absent | present | lookalike
    size: int | None = None


@dataclass(frozen=True)
class Shown:
    slot: str
    role: str
    image: ImageRef
    blob: BlobInfo
    crop: Crop | None
    focus: Focus | None
    layer: str  # source | campaign | campaign_adjusted
    provenance: Mapping[str, object] | None


@dataclass(frozen=True)
class Hidden:
    slot: str


@dataclass(frozen=True)
class Unavailable:
    slot: str
    reason: str
    text: str


@dataclass(frozen=True)
class Problem:
    path: str
    code: str
    detail: str


Slot = Shown | Hidden | Unavailable


@dataclass(frozen=True)
class Resolution:
    subject: SourceRef | InstanceRef
    slots: tuple[Slot, ...]
    problems: tuple[Problem, ...] = ()

    def slot(self, name: str) -> Slot | None:
        return next((entry for entry in self.slots if entry.slot == name), None)


@dataclass(frozen=True)
class Facts:
    images: Mapping[ImageRef, ImageEntry]
    blobs: Mapping[tuple[str, str], BlobFact]


def _show(slot: str, entry: PlacementEntry, allowed: frozenset[str], facts: Facts, layer: str) -> Shown | Unavailable:
    placement = entry.placement
    assert placement is not None and placement.image is not None
    image = placement.image
    if image.scope not in allowed:
        return Unavailable(slot, "wrong_scope", str(image))
    found = facts.images.get(image)
    if found is None or found.image is None:
        return Unavailable(slot, found.reason if found is not None and found.reason else "missing_image", str(image))
    blob = found.image.blob
    fact = facts.blobs.get((blob.sha256, blob.ext))
    if fact is None or fact.presence != "present":
        return Unavailable(slot, "missing_blob", str(image))
    if fact.size != blob.size:
        return Unavailable(slot, "blob_size_mismatch", str(image))
    return Shown(slot, placement.role or slot_role(slot) or "gallery", image, blob, placement.crop, placement.focus,
                 layer, placement.provenance)


def _unresolved(slot: str, placement: Placement) -> Unavailable:
    """A missing picture stays missing, verbatim: its note (never a picture found by name) says why."""
    note = placement.provenance.get("note") if placement.provenance is not None else None
    return Unavailable(slot, "unresolved", note if isinstance(note, str) else "")


def _readable(slot: str, entry: PlacementEntry) -> Unavailable | None:
    if entry.placement is None:
        return Unavailable(slot, entry.reason or "invalid_placement", entry.text)
    if validate_placement(entry.ref.subject, slot, entry.placement) is not None:
        return Unavailable(slot, "invalid_placement", entry.text)
    return None


def inherited(slot: str, source_layer: Mapping[str, PlacementEntry], source: str | None,
              facts: Facts) -> Shown | Unavailable | None:
    """What a source layer shows in ``slot`` (``None``: nothing there). ``source`` is the layer's owner."""
    entry = source_layer.get(slot)
    if entry is None:
        return None
    broken = _readable(slot, entry)
    if broken is not None:
        return broken
    assert entry.placement is not None
    if entry.placement.mode == "unresolved":
        return _unresolved(slot, entry.placement)
    allowed = frozenset({source}) if source is not None else frozenset()
    return _show(slot, entry, allowed, facts, "source")


def _campaign(slot: str, entry: PlacementEntry, base: Shown | Unavailable | None, allowed: frozenset[str],
              facts: Facts) -> Slot:
    broken = _readable(slot, entry)
    if broken is not None:
        return broken
    placement = entry.placement
    assert placement is not None
    if placement.mode == "hide":
        return Hidden(slot)
    if placement.mode == "unresolved":  # the campaign's own picture is missing: never the source's instead
        return _unresolved(slot, placement)
    if placement.mode == "set":
        return _show(slot, entry, allowed, facts, "campaign")
    # adjust: re-crop exactly the inherited picture it names, or show nothing
    if base is None:
        return Unavailable(slot, "nothing_to_adjust", str(placement.image))
    if isinstance(base, Unavailable):
        return base
    if base.image != placement.image:
        return Unavailable(slot, "stale_adjust", str(placement.image))
    return Shown(slot, base.role, base.image, base.blob, placement.crop or base.crop, placement.focus or base.focus,
                 "campaign_adjusted", placement.provenance if placement.provenance is not None else base.provenance)


def resolve(subject: SourceRef | InstanceRef, source_layer: Mapping[str, PlacementEntry],
            campaign_layer: Mapping[str, PlacementEntry], facts: Facts, *, derivation_source: str | None,
            problems: Iterable[Problem] = ()) -> Resolution:
    """The effective placements of ``subject``.

    For a definition, ``source_layer`` is its own placements and ``derivation_source`` its source.
    For an instance, ``source_layer`` is its derivation entity's placements (owned by
    ``derivation_source``, or empty and ``None`` when the derivation is missing or ambiguous),
    and ``campaign_layer`` its own: campaign images, and the derivation source's, are in scope.
    """
    if isinstance(subject, SourceRef) and campaign_layer:
        raise ValueError("a definition has no campaign layer")
    allowed = frozenset({f"campaign:{subject.campaign_id}"} if isinstance(subject, InstanceRef) else set())
    if derivation_source is not None and isinstance(subject, InstanceRef):
        allowed |= {derivation_source}
    slots: list[Slot] = []
    for slot in sorted(set(source_layer) | set(campaign_layer)):
        base = inherited(slot, source_layer, derivation_source, facts)
        entry = campaign_layer.get(slot)
        result = base if entry is None else _campaign(slot, entry, base, allowed, facts)
        if result is not None:
            slots.append(result)
    ordered = tuple(sorted(set(problems), key=lambda p: (p.path, p.code, p.detail)))
    return Resolution(subject, tuple(slots), ordered)
