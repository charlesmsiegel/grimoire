"""Effective placements of one subject: a pure, deterministic function of data (Sprint 018).

The caller (``grimoire.imagestore``) gathers files; this module decides. There is no network,
no model call and no name matching. Every slot ends as exactly one of ``Shown``, ``Hidden`` or
``Unavailable``, and nothing ever falls back to a different picture: a campaign ``hide`` hides
even when the source has a picture there, a broken reference stays broken, verbatim, and an
``unresolved`` placement (038: a picture that belongs here but is missing) stays unavailable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
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

#: What decided a slot, from the resolved subject's point of view (046). ``local``: the subject's own
#: placement, with nothing inherited beneath it; ``replaced``: its own picture over an inherited one;
#: ``cleared``: its own ``hide`` over an inherited slot; ``adjusted``: its own re-crop of the inherited
#: picture; ``missing``: its own ``unresolved``; ``broken``: its own placement cannot be read;
#: ``inherited``: an ancestor's placement (``placed_by``), unchanged by the subject.
ORIGINS = ("local", "replaced", "cleared", "adjusted", "missing", "broken", "inherited")


@dataclass(frozen=True)
class ArtOrigin:
    slot: str
    origin: str
    #: the subject whose placement decided the slot: the resolved subject, or an ancestor in its lineage
    placed_by: SourceRef | InstanceRef


@dataclass(frozen=True)
class Resolution:
    subject: SourceRef | InstanceRef
    slots: tuple[Slot, ...]
    problems: tuple[Problem, ...] = ()
    #: 046: who decided each shown, hidden or unavailable slot (``ArtOrigin``), in slot order
    origins: tuple[ArtOrigin, ...] = ()
    #: 046: the subjects whose placements were layered, root first; the resolved subject is last
    lineage: tuple[SourceRef | InstanceRef, ...] = ()

    def slot(self, name: str) -> Slot | None:
        return next((entry for entry in self.slots if entry.slot == name), None)

    def origin(self, name: str) -> ArtOrigin | None:
        return next((entry for entry in self.origins if entry.slot == name), None)


@dataclass(frozen=True)
class Layer:
    """One subject's own placements in a lineage (046), layered root first.

    ``scope`` is the owner whose images this layer may ``set``: its source, or ``campaign:<id>`` for
    an instance (``None``: nothing is in scope). ``also`` is one more owner in scope: for an instance,
    its derivation entity's source, exactly as 018 allowed. ``derived`` marks a 022 derived portrayal,
    whose placements may also ``hide``, ``adjust`` or ``inherit``.
    """

    subject: SourceRef | InstanceRef
    entries: Mapping[str, PlacementEntry]
    scope: str | None
    derived: bool = False
    also: str | None = None


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


def _readable(slot: str, entry: PlacementEntry, derived: bool = False) -> Unavailable | None:
    if entry.placement is None:
        return Unavailable(slot, entry.reason or "invalid_placement", entry.text)
    if validate_placement(entry.ref.subject, slot, entry.placement, derived=derived) is not None:
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


def _allowed(layer: Layer) -> frozenset[str]:
    return frozenset(owner for owner in (layer.scope, layer.also) if owner is not None)


def _step(slot: str, layers: Sequence[Layer], index: int, below: Slot | None,
          facts: Facts) -> tuple[Slot | None, str | None]:
    """``(result, origin)`` of layer ``index`` over what the layers beneath show (``below``).
    ``origin`` is ``None`` when the layer leaves the slot as inherited (no entry, or ``inherit``)."""
    layer = layers[index]
    entry = layer.entries.get(slot)
    if entry is None:
        return below, None
    broken = _readable(slot, entry, layer.derived)
    if broken is not None:
        return broken, "broken"
    placement = entry.placement
    assert placement is not None
    instance = isinstance(layer.subject, InstanceRef)
    if placement.mode == "inherit":  # an explicit reset: exactly as if there were no placement
        return below, None
    if placement.mode == "hide":  # never falls back to the inherited picture, or any picture by name
        return Hidden(slot), "local" if below is None else "cleared"
    if placement.mode == "unresolved":  # a missing picture stays missing: never the inherited one instead
        return _unresolved(slot, placement), "missing"
    if placement.mode == "set":
        shown = _show(slot, entry, _allowed(layer), facts, "campaign" if instance else "source")
        return shown, "local" if below is None else "replaced"
    # adjust: re-crop exactly the inherited picture it names, or show nothing
    if below is None or isinstance(below, Hidden):
        return Unavailable(slot, "nothing_to_adjust", str(placement.image)), "adjusted"
    if isinstance(below, Unavailable):
        return below, "adjusted"
    if below.image != placement.image:
        return Unavailable(slot, "stale_adjust", str(placement.image)), "adjusted"
    return Shown(slot, below.role, below.image, below.blob, placement.crop or below.crop,
                 placement.focus or below.focus, "campaign_adjusted" if instance else "source_adjusted",
                 placement.provenance if placement.provenance is not None else below.provenance), "adjusted"


def resolve_lineage(subject: SourceRef | InstanceRef, layers: Sequence[Layer], facts: Facts, *,
                    problems: Iterable[Problem] = ()) -> Resolution:
    """The effective placements of ``subject`` over its lineage (046): ``layers`` root first, the
    subject's own layer last. Each layer decides a slot or leaves it as the layers beneath show it.

    A standalone definition is one layer. A 022 derived portrayal is its base chain's entities, root
    first, then its own (``derived``). An instance is its derivation entity's lineage, then its campaign
    layer. Nothing is matched by name, and a broken placement never falls back to a picture beneath it.
    """
    if not layers or layers[-1].subject != subject:
        raise ValueError(f"the last layer must be {subject}'s own")
    slots: list[Slot] = []
    origins: list[ArtOrigin] = []
    for slot in sorted({name for layer in layers for name in layer.entries}):
        result: Slot | None = None
        decided: tuple[str, SourceRef | InstanceRef] | None = None
        for index in range(len(layers)):
            result, origin = _step(slot, layers, index, result, facts)
            if origin is not None:
                decided = (origin, layers[index].subject)
            elif result is None:
                decided = None
        if result is None:
            continue
        slots.append(result)
        if decided is not None:
            origin, by = decided
            origins.append(ArtOrigin(slot, origin if by == subject else "inherited", by))
    ordered = tuple(sorted(set(problems), key=lambda p: (p.path, p.code, p.detail)))
    return Resolution(subject, tuple(slots), ordered, tuple(origins), tuple(layer.subject for layer in layers))


def resolve(subject: SourceRef | InstanceRef, source_layer: Mapping[str, PlacementEntry],
            campaign_layer: Mapping[str, PlacementEntry], facts: Facts, *, derivation_source: str | None,
            problems: Iterable[Problem] = ()) -> Resolution:
    """The effective placements of ``subject`` over one standalone source layer (018's resolver).

    For a definition, ``source_layer`` is its own placements and ``derivation_source`` its source.
    For an instance, ``source_layer`` is its derivation entity's placements (owned by
    ``derivation_source``, or empty and ``None`` when the derivation is missing or ambiguous),
    and ``campaign_layer`` its own: campaign images, and the derivation source's, are in scope.
    A lineage through 022 derived portrayals is ``resolve_lineage``.
    """
    if isinstance(subject, SourceRef):
        if campaign_layer:
            raise ValueError("a definition has no campaign layer")
        return resolve_lineage(subject, [Layer(subject, source_layer, derivation_source)], facts, problems=problems)
    layers = []
    if source_layer:
        entity = next(iter(source_layer.values())).ref.subject
        layers.append(Layer(entity, source_layer, derivation_source))
    layers.append(Layer(subject, campaign_layer, f"campaign:{subject.campaign_id}", also=derivation_source))
    return resolve_lineage(subject, layers, facts, problems=problems)
