"""Image objects and placements: pure values and the rules that need no storage (Sprint 018).

Three things are never conflated: a *blob* (exact bytes, owned by the store), an *image object*
(this picture, owned by one source or campaign, pointing at one blob) and a *placement* (one
use of an image object by one subject in one slot). Field spellings are provisional
(``/0`` schema ids), not a frozen on-disk format.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from grimoire.core.identity import SLOT_MAX, ImageRef, InstanceRef, LegacyId, SourceRef, validate_segment
from grimoire.core.json_value import JsonValueError, freeze_object

IMAGE_SCHEMA = "grimoire.image/0"
PLACEMENT_SCHEMA = "grimoire.placement/0"
#: Blob extension -> media type; equal to ``grimoire.store.media.MEDIA`` (pinned by a test).
MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
#: ``unresolved`` (Sprint 038) is the explicit placement of art that belongs in a slot but whose picture
#: is not here (a remote or embedded asset never fetched, say): it shows nothing and never falls back.
#: ``inherit`` (Sprint 046) is the explicit reset of a slot to what the subject inherits: it reads
#: exactly as no placement at all. The store never deletes a placement (sync never propagates a
#: deletion either), so taking an override back is a compare-and-swap rewrite to ``inherit``.
MODES = ("set", "adjust", "hide", "unresolved", "inherit")
#: the modes a standalone source placement may use: it shows its own picture, or says one is missing
SOURCE_MODES = ("set", "unresolved")
#: the modes a derived portrayal's placement may use (046): it inherits its base's art slot by slot,
#: so it may also clear (``hide``), re-crop (``adjust``) or reset (``inherit``) an inherited slot
DERIVED_MODES = ("set", "adjust", "hide", "unresolved", "inherit")
ROLES = ("avatar", "portrait", "gallery", "expression")
#: Crop and focus are integer basis points of the whole image: 0..10000, never floats.
BP = 10000
TEXT_LIMIT = 2000
_PART = re.compile(r"[a-z0-9][a-z0-9_-]*")
_EXT_OF = {media: ext for ext, media in MEDIA_TYPES.items()}


class ImageModelError(ValueError):
    """A value this version refuses; ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str = "invalid_field") -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


def _empty() -> Mapping[str, object]:
    return freeze_object({})


def _bp(value: object, label: str) -> int:
    if type(value) is not int or not 0 <= value <= BP:
        raise ImageModelError(f"{label} must be an integer 0..{BP} (basis points), not {value!r}")
    return value


def _json_object(value: object, label: str) -> Mapping[str, object]:
    try:
        return freeze_object(value)
    except JsonValueError as error:
        raise ImageModelError(f"{label}: {error}") from None


@dataclass(frozen=True)
class BlobInfo:
    """What an image object says about its bytes; the store owns the bytes themselves."""

    sha256: str
    media_type: str
    size: int

    def __post_init__(self) -> None:
        if type(self.sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ImageModelError("blob.sha256 must be 64 lowercase hex digits")
        if self.media_type not in _EXT_OF:
            raise ImageModelError(f"blob.media_type must be one of {sorted(_EXT_OF)}, not {self.media_type!r}")
        if type(self.size) is not int or self.size < 1:
            raise ImageModelError("blob.size must be a positive integer")

    @property
    def ext(self) -> str:
        return _EXT_OF[self.media_type]


@dataclass(frozen=True)
class Crop:
    x: int
    y: int
    w: int
    h: int

    def __post_init__(self) -> None:
        for label in ("x", "y", "w", "h"):
            _bp(getattr(self, label), f"crop.{label}")
        if self.w < 1 or self.h < 1 or self.x + self.w > BP or self.y + self.h > BP:
            raise ImageModelError("crop must have w, h >= 1 and lie inside the image")


@dataclass(frozen=True)
class Focus:
    x: int
    y: int

    def __post_init__(self) -> None:
        _bp(self.x, "focus.x")
        _bp(self.y, "focus.y")


@dataclass(frozen=True)
class Expression:
    """An expression sprite's identity; its slot is ``expr.<outfit>.<name>``."""

    outfit: str
    name: str

    def __post_init__(self) -> None:
        for label in ("outfit", "name"):
            value = getattr(self, label)
            if type(value) is not str or not _PART.fullmatch(value):
                raise ImageModelError(f"expression.{label} must match {_PART.pattern}: {value!r}")


def validate_provenance(value: object) -> Mapping[str, object]:
    """Open provenance: known keys are checked, unknown keys are kept verbatim."""
    frozen = _json_object(value, "provenance")
    legacy = frozen.get("legacy_id")
    if legacy is not None:
        try:
            LegacyId.parse(legacy)
        except ValueError as error:
            raise ImageModelError(f"provenance.legacy_id: {error}") from None
    for label in ("origin", "note"):
        text = frozen.get(label)
        if text is not None and (type(text) is not str or len(text) > TEXT_LIMIT):
            raise ImageModelError(f"provenance.{label} must be text of at most {TEXT_LIMIT} characters")
    return frozen


@dataclass(frozen=True)
class ImageObject:
    blob: BlobInfo
    provenance: Mapping[str, object] | None = None
    extras: Mapping[str, object] = field(default_factory=_empty)

    def __post_init__(self) -> None:
        if not isinstance(self.blob, BlobInfo):
            raise ImageModelError("an image object needs a BlobInfo")
        if self.provenance is not None:
            object.__setattr__(self, "provenance", validate_provenance(self.provenance))
        extras = _json_object(self.extras, "extras")
        if set(extras) & {"blob", "provenance"}:
            raise ImageModelError("extras may not hold known fields")
        object.__setattr__(self, "extras", extras)


@dataclass(frozen=True)
class Placement:
    """One use of a picture in one slot. ``set`` shows ``image``; ``adjust`` re-crops the inherited
    picture (and names it); ``hide`` shows nothing and never falls back; ``unresolved`` (038) says a
    picture belongs here but is missing, shows nothing, and never falls back either; ``inherit`` (046)
    resets the slot to whatever is inherited, exactly as if there were no placement."""

    mode: str
    role: str | None = None
    image: ImageRef | None = None
    crop: Crop | None = None
    focus: Focus | None = None
    expression: Expression | None = None
    provenance: Mapping[str, object] | None = None
    extras: Mapping[str, object] = field(default_factory=_empty)

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ImageModelError(f"unknown placement mode {self.mode!r}", "invalid_placement")
        if self.role is not None and self.role not in ROLES:
            raise ImageModelError(f"unknown placement role {self.role!r}", "invalid_placement")
        if self.image is not None and not isinstance(self.image, ImageRef):
            raise ImageModelError("image must be an ImageRef")
        for label, kind in (("crop", Crop), ("focus", Focus), ("expression", Expression)):
            value = getattr(self, label)
            if value is not None and not isinstance(value, kind):
                raise ImageModelError(f"{label} must be a {kind.__name__}")
        if self.mode == "set" and (self.image is None or self.role is None):
            raise ImageModelError("a set placement needs a role and an image")
        if self.mode == "adjust" and self.image is None:
            raise ImageModelError("an adjust placement names the inherited image it adjusts")
        if self.mode in ("hide", "inherit") and any(getattr(self, label) is not None for label in
                                                    ("image", "crop", "focus", "expression")):
            raise ImageModelError(f"a {self.mode} placement carries no image, crop, focus or expression")
        if self.mode == "inherit" and self.role is not None:
            raise ImageModelError("an inherit placement takes the inherited role; it names none")
        if self.mode == "unresolved":
            # the missing picture's role (and, for a sprite, its outfit and name) is what is preserved;
            # there is no picture to name, crop or focus
            if self.role is None:
                raise ImageModelError("an unresolved placement needs the role of the missing picture")
            if any(getattr(self, label) is not None for label in ("image", "crop", "focus")):
                raise ImageModelError("an unresolved placement carries no image, crop or focus")
        if self.expression is not None and self.role not in (None, "expression"):
            raise ImageModelError("only an expression placement carries an expression")
        if self.mode in ("set", "unresolved") and self.role == "expression" and self.expression is None:
            raise ImageModelError("an expression placement needs its outfit and name")
        if self.provenance is not None:
            object.__setattr__(self, "provenance", validate_provenance(self.provenance))
        extras = _json_object(self.extras, "extras")
        if set(extras) & _PLACEMENT_KEYS:
            raise ImageModelError("extras may not hold known fields")
        object.__setattr__(self, "extras", extras)


_PLACEMENT_KEYS = frozenset({"mode", "role", "image", "crop", "focus", "expression", "provenance"})


def expression_slot(outfit: str, name: str) -> str:
    """The one slot an expression sprite may occupy."""
    Expression(outfit, name)
    slot = f"expr.{outfit}.{name}"
    if len(slot) > SLOT_MAX:
        raise ImageModelError(f"expression slot is longer than {SLOT_MAX} characters")
    return slot


def slot_role(slot: str) -> str | None:
    """The role a slot decides, or ``None`` for an invalid ``expr.`` slot."""
    if slot in ("avatar", "portrait"):
        return slot
    if slot.startswith("expr."):
        parts = slot.split(".")
        if len(parts) == 3 and all(_PART.fullmatch(part) for part in parts[1:]):
            return "expression"
        return None
    return "gallery"


def slot_expression(slot: str) -> Expression | None:
    return Expression(*slot.split(".")[1:]) if slot_role(slot) == "expression" else None


def validate_placement(subject: SourceRef | InstanceRef, slot: str, placement: Placement, *,
                       derived: bool = False) -> tuple[str, str] | None:
    """``(reason, detail)`` if ``placement`` may not sit in ``slot`` of ``subject``, else ``None``.

    ``derived`` (046) says a definition subject's head is a 022 derived portrayal, which inherits its
    base's art: it may then also ``hide``, ``adjust`` or ``inherit`` (``DERIVED_MODES``). A standalone
    definition has nothing to inherit, so it may only ``set`` or mark a picture ``unresolved``.
    """
    try:
        validate_segment(slot, "slot")
    except ValueError as error:
        return "invalid_field", str(error)
    if len(slot) > SLOT_MAX:
        return "invalid_field", f"slot is longer than {SLOT_MAX} characters"
    role = slot_role(slot)
    if role is None:
        return "role_slot_mismatch", f"{slot!r} is not expr.<outfit>.<name>"
    if isinstance(subject, SourceRef) and placement.mode not in (DERIVED_MODES if derived else SOURCE_MODES):
        return "mode_not_allowed", f"a standalone source placement may only set a picture or mark one " \
                                   f"unresolved, not {placement.mode}"
    if placement.role is not None and placement.role != role:
        return "role_slot_mismatch", f"slot {slot!r} holds a {role}, not a {placement.role}"
    if placement.expression is not None and placement.expression != slot_expression(slot):
        return "role_slot_mismatch", f"slot {slot!r} does not name {placement.expression}"
    return None
