"""Pinned art (Sprint 046, the owner's "pin pictures too"): what a pinned chat's actor inherits.

A placement attaches to an entity and is rewritten in place (018), so the store keeps no older art.
A pinned actor therefore keeps a **snapshot**: each placement of its pinned base revision's lineage,
as it read when the actor was made or last rebased, in the instance file's ``art_pin`` field. Image
objects and blobs are create-only, so the pictures a snapshot names stay the same pictures.

The snapshot is data, never trusted: the resolver re-checks every frozen placement exactly as it
checks a live one (its mode, and that a ``set`` names its own entity's source), and the frozen
entities must be the pinned revision's own derivation chain. A snapshot that cannot be read, or that
names other entities, inherits **nothing**. A snapshot taken for another base revision (an earlier
build rebased the actor and kept the field) is ignored, and the actor's art is live again.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from grimoire.core.identity import PlacementRef, SourceRef
from grimoire.images import (
    Hidden, ImageModelError, Layer, Placement, PlacementEntry, Problem, Resolution, Shown, Slot, placement_from_payload,
    placement_to_payload,
)

from .lineage import Lineage

#: the instance envelope's own field (an unknown field to an older build, which keeps it and shows live art)
ART_PIN = "art_pin"
ART_PIN_FORMAT = "grimoire.art-pin/0"
#: frozen placements across a whole lineage; a lineage with more is not pinned, and its art stays live
MAX_PINNED = 1024
_TEXT = "art pin"


def freeze(base: SourceRef, layers: Sequence[Layer], problems: Sequence[Problem]) -> dict | None:
    """The snapshot of ``layers`` (a pinned ``base``'s lineage as resolved now, damage already guarded),
    or ``None`` when it holds more than ``MAX_PINNED`` placements."""
    if sum(len(layer.entries) for layer in layers) > MAX_PINNED:
        return None
    frozen = []
    for layer in layers:
        placements: dict[str, object] = {}
        for slot, entry in sorted(layer.entries.items()):
            if entry.placement is not None:
                placements[slot] = placement_to_payload(_bare(entry.placement))
            else:
                placements[slot] = {"invalid": entry.reason or "invalid_placement"}
        frozen.append({"entity": str(layer.subject), "derived": layer.derived, "placements": placements})
    return {"format": ART_PIN_FORMAT, "base": str(base), "layers": frozen,
            "problems": [{"path": problem.path, "code": problem.code, "detail": problem.detail}
                         for problem in sorted(set(problems), key=lambda p: (p.path, p.code, p.detail))]}


def _bare(placement: Placement) -> Placement:
    """What a pinned slot shows, and nothing more: a placement's open provenance and extras could be
    deep or large enough to refuse the instance file, and only an ``unresolved`` slot's note is shown."""
    note = placement.provenance.get("note") if placement.provenance is not None else None
    kept = {"note": note} if placement.mode == "unresolved" and type(note) is str else None
    return replace(placement, provenance=kept, extras={})


def pinned_base(value: object) -> str | None:
    """The base revision a stored snapshot was taken for, or ``None`` when there is none to read."""
    return value.get("base") if isinstance(value, Mapping) and type(value.get("base")) is str else None


def thaw(value: object, chain: Lineage) -> tuple[list[Layer], list[Problem]]:
    """The frozen layers of a snapshot over the pinned revision's own ``chain``, root first, or no
    layers and the reason: a snapshot that cannot be read, or whose entities are not that chain."""
    entity = chain.entities[-1]
    try:
        layers, problems = _layers(value)
    except (ImageModelError, ValueError, TypeError, KeyError) as error:
        return [], [Problem(str(entity), "art_pin_unreadable", f"{error}: nothing is inherited")]
    if tuple(layer.subject for layer in layers) != chain.entities:
        return [], [Problem(str(entity), "art_pin_mismatch",
                            "the pinned art names other entities than its revision's lineage: nothing is inherited")]
    # a frozen layer may hide, adjust or reset only where its revision's chain lets it: every member but
    # the root is a derived revision, and the root only while its own head is derived. A forged flag is
    # clamped, never trusted, so it cannot grant a standalone ancestor's slots a clear.
    allowed = [index > 0 or chain.derived[index] for index in range(len(layers))]
    return [replace(layer, derived=layer.derived and ok) for layer, ok in zip(layers, allowed)], problems


def _layers(value: object) -> tuple[list[Layer], list[Problem]]:
    if not isinstance(value, Mapping) or value.get("format") != ART_PIN_FORMAT:
        raise ValueError(f"not a {ART_PIN_FORMAT} snapshot")
    stored, listed = value.get("layers"), value.get("problems")
    if not isinstance(stored, Sequence) or isinstance(stored, str) or not stored:
        raise ValueError("a snapshot needs its layers")
    if not isinstance(listed, Sequence) or isinstance(listed, str):
        raise ValueError("a snapshot needs its problems")
    layers: list[Layer] = []
    for item in stored:
        entity = SourceRef.parse(item["entity"])
        if entity.pinned or type(item["derived"]) is not bool or not isinstance(item["placements"], Mapping):
            raise ValueError("a frozen layer is one unpinned entity, a derived flag and its placements")
        entries: dict[str, PlacementEntry] = {}
        for slot, payload in item["placements"].items():
            if type(slot) is not str:
                raise ValueError("a slot is text")
            ref = PlacementRef(entity, slot)
            if isinstance(payload, Mapping) and set(payload) == {"invalid"}:
                reason = payload["invalid"]
                entries[slot] = PlacementEntry(ref, None, reason if type(reason) is str else "invalid_placement",
                                               _TEXT)
                continue
            try:
                entries[slot] = PlacementEntry(ref, placement_from_payload(payload), None, _TEXT)
            except (ImageModelError, ValueError):
                entries[slot] = PlacementEntry(ref, None, "invalid_placement", _TEXT)
        layers.append(Layer(entity, entries, entity.source, item["derived"]))
    problems = [Problem(str(problem["path"]), str(problem["code"]), f"pinned: {problem['detail']}")
                for problem in listed]
    return layers, problems


# -- what a rebase would change ----------------------------------------------------------------

@dataclass(frozen=True)
class ArtChange:
    """One slot whose picture a reviewed rebase changes: what it shows now, and after (``None``: nothing)."""

    slot: str
    before: Slot | None
    after: Slot | None


def art_changes(before: Resolution, after: Resolution) -> tuple[ArtChange, ...]:
    """Every slot that shows a different picture, crop, focus, clear or reason in ``after``."""
    slots = sorted({entry.slot for entry in (*before.slots, *after.slots)})
    return tuple(ArtChange(slot, before.slot(slot), after.slot(slot)) for slot in slots
                 if _shown(before.slot(slot)) != _shown(after.slot(slot)))


def _shown(slot: Slot | None) -> tuple[object, ...]:
    if slot is None:
        return ("nothing",)
    if isinstance(slot, Shown):
        return ("shown", slot.role, slot.image, slot.crop, slot.focus)
    if isinstance(slot, Hidden):
        return ("hidden",)
    return ("unavailable", slot.reason, slot.text)


__all__ = ["ART_PIN", "ART_PIN_FORMAT", "MAX_PINNED", "ArtChange", "art_changes", "freeze", "pinned_base", "thaw"]
