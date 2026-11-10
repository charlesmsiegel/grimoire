"""JSON views of what the media and sync adapter reads (114): plain dicts of strings, numbers, booleans and lists.

Every ref is written in its own string form, so a client hands it back unchanged. Nothing here reads or writes
a file; ``api.py`` gathers the values. A view never substitutes one picture for another: a slot is shown,
hidden or unavailable exactly as 038's resolver says (docs/IMAGES.md).
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from grimoire.core.identity import ImageRef, InstanceRef, SourceRef
from grimoire.images import Crop, Focus, Hidden, ImageObject, Placement, Resolution, Shown, Unavailable

#: The provenance keys 018 defines; any other key an importer kept is shown under ``other``.
KNOWN_PROVENANCE = ("origin", "note", "legacy_id")
#: The most characters of one kept provenance value shown: provenance is open, and a card may carry a lot.
MAX_PROVENANCE_TEXT = 2000


def _ref(ref: object) -> str | None:
    return None if ref is None else str(ref)


def box(crop: Crop | None) -> dict | None:
    return None if crop is None else {"x": crop.x, "y": crop.y, "w": crop.w, "h": crop.h}


def point(focus: Focus | None) -> dict | None:
    return None if focus is None else {"x": focus.x, "y": focus.y}


def provenance_view(provenance: Mapping[str, object] | None) -> dict:
    """018's open provenance: ``origin``, ``note`` and ``legacy_id`` as text, every other key as compact JSON
    (cut at ``MAX_PROVENANCE_TEXT`` characters), so an importer's own record is visible, never dropped."""
    if not provenance:
        return {"origin": None, "note": None, "legacy_id": None, "other": {}}
    other = {}
    for key in sorted(provenance):
        if key in KNOWN_PROVENANCE:
            continue
        text = json.dumps(_plain(provenance[key]), ensure_ascii=False, sort_keys=True)
        other[key] = text if len(text) <= MAX_PROVENANCE_TEXT else text[:MAX_PROVENANCE_TEXT] + "…"
    return {**{key: None if provenance.get(key) is None else str(provenance[key]) for key in KNOWN_PROVENANCE},
            "other": other}


def _plain(value: object) -> object:
    """A frozen JSON value (mapping proxies, tuples) as plain JSON."""
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def image_view(ref: ImageRef, image: ImageObject | None, etag: str | None, problem: str | None,
               used_by: list[dict]) -> dict:
    """One picture of a gallery: its blob and provenance, or why it cannot be read; and the placements of its
    own scope that name it (``used_by``: ``subject``, ``slot``, ``mode``)."""
    if image is None:
        return {"image": str(ref), "state": "unavailable", "reason": problem, "used_by": used_by}
    return {"image": str(ref), "state": "readable" if problem is None else "unavailable", "reason": problem,
            "etag": etag, "media_type": image.blob.media_type, "size": image.blob.size, "sha256": image.blob.sha256,
            "provenance": provenance_view(image.provenance), "used_by": used_by}


def placement_view(placement: Placement) -> dict:
    """A placement's own fields as written (not as resolved): what a subject itself put in one slot."""
    return {"mode": placement.mode, "role": placement.role, "image": _ref(placement.image),
            "crop": box(placement.crop), "focus": point(placement.focus),
            "expression": None if placement.expression is None else
            {"outfit": placement.expression.outfit, "name": placement.expression.name},
            "provenance": provenance_view(placement.provenance)}


def slot_view(slot: object, resolution: Resolution) -> dict:
    """One resolved slot, with who decided it (046's ``origin``: this subject's own placement or an ancestor's)."""
    if isinstance(slot, Shown):
        view = {"slot": slot.slot, "state": "shown", "role": slot.role, "image": str(slot.image),
                "media_type": slot.blob.media_type, "size": slot.blob.size, "layer": slot.layer,
                "crop": box(slot.crop), "focus": point(slot.focus), "provenance": provenance_view(slot.provenance)}
    elif isinstance(slot, Hidden):
        view = {"slot": slot.slot, "state": "hidden"}
    elif isinstance(slot, Unavailable):
        view = {"slot": slot.slot, "state": "unavailable", "reason": slot.reason, "note": slot.text}
    else:  # pragma: no cover -- 038 has exactly three kinds of slot
        raise TypeError(f"unknown slot {slot!r}")
    origin = resolution.origin(view["slot"])
    view["origin"] = None if origin is None else origin.origin
    view["placed_by"] = None if origin is None else str(origin.placed_by)
    return view


def subject_ref(text: str) -> SourceRef | InstanceRef:
    """A placement subject from its string form: a campaign instance or an unpinned definition."""
    if text.startswith("campaign:"):
        return InstanceRef.parse(text)
    ref = SourceRef.parse(text)
    if ref.pinned:
        raise ValueError("placements attach to the entity, not one revision")
    return ref


def report_problems(problems: tuple) -> list[dict]:  # type: ignore[type-arg]
    return [{"path": problem.path, "code": problem.code, "detail": problem.detail} for problem in problems]


__all__ = ["KNOWN_PROVENANCE", "MAX_PROVENANCE_TEXT", "box", "image_view", "placement_view", "point",
           "provenance_view", "report_problems", "slot_view", "subject_ref"]
