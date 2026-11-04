"""Strict conversion between image/placement values and the store's opaque payloads (Sprint 018).

Unknown top-level fields are kept in ``extras`` and provenance is open; inside a known structure
(``blob``, ``crop``, ``focus``, ``expression``) an unknown or malformed key is refused with a
reason. A caller rewriting a placement should modify the ``Placement`` it read, so its extras
survive.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.core.identity import ImageRef
from grimoire.core.json_value import thaw

from .model import (
    BlobInfo, Crop, Expression, Focus, ImageModelError, ImageObject, Placement, _PLACEMENT_KEYS,
)


class ImageCodecError(ImageModelError):
    """A payload this version refuses to interpret."""


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(type(key) is str for key in value):
        raise ImageCodecError(f"{label} must be an object")
    return value


def _exact(value: object, label: str, keys: tuple[str, ...], optional: tuple[str, ...] = ()) -> Mapping[str, object]:
    mapping = _mapping(value, label)
    unknown = sorted(set(mapping) - set(keys) - set(optional))
    missing = sorted(set(keys) - set(mapping))
    if unknown or missing:
        raise ImageCodecError(f"{label}: unknown {unknown} / missing {missing}")
    return mapping


def _wrap(build):  # type: ignore[no-untyped-def]
    try:
        return build()
    except ImageCodecError:
        raise
    except ImageModelError as error:
        raise ImageCodecError(str(error), error.reason) from None
    except (TypeError, ValueError) as error:
        raise ImageCodecError(str(error)) from None


# -- image objects ---------------------------------------------------------------------------

def image_to_payload(image: ImageObject) -> dict:
    payload = dict(thaw(image.extras))  # type: ignore[call-overload]
    payload["blob"] = {"sha256": image.blob.sha256, "media_type": image.blob.media_type, "size": image.blob.size}
    if image.provenance is not None:
        payload["provenance"] = thaw(image.provenance)  # type: ignore[arg-type]
    return payload


def image_from_payload(payload: object) -> ImageObject:
    def build() -> ImageObject:
        mapping = _mapping(payload, "image payload")
        blob = _exact(mapping.get("blob"), "blob", ("sha256", "media_type", "size"))
        extras = {key: value for key, value in mapping.items() if key not in ("blob", "provenance")}
        return ImageObject(BlobInfo(blob["sha256"], blob["media_type"], blob["size"]),  # type: ignore[arg-type]
                           mapping.get("provenance"), extras)  # type: ignore[arg-type]
    return _wrap(build)


# -- placements ------------------------------------------------------------------------------

def placement_to_payload(placement: Placement) -> dict:
    payload = dict(thaw(placement.extras))  # type: ignore[call-overload]
    payload["mode"] = placement.mode
    if placement.role is not None:
        payload["role"] = placement.role
    if placement.image is not None:
        payload["image"] = str(placement.image)
    if placement.crop is not None:
        crop = placement.crop
        payload["crop"] = {"x": crop.x, "y": crop.y, "w": crop.w, "h": crop.h}
    if placement.focus is not None:
        payload["focus"] = {"x": placement.focus.x, "y": placement.focus.y}
    if placement.expression is not None:
        payload["expression"] = {"outfit": placement.expression.outfit, "name": placement.expression.name}
    if placement.provenance is not None:
        payload["provenance"] = thaw(placement.provenance)  # type: ignore[arg-type]
    return payload


def placement_from_payload(payload: object) -> Placement:
    def build() -> Placement:
        mapping = _mapping(payload, "placement payload")
        mode = mapping.get("mode")
        if type(mode) is not str:
            raise ImageCodecError("placement mode must be text", "invalid_placement")
        role = mapping.get("role")
        if role is not None and type(role) is not str:
            raise ImageCodecError("placement role must be text", "invalid_placement")
        image = mapping.get("image")
        if image is not None:
            if type(image) is not str:
                raise ImageCodecError("image must be reference text")
            image = ImageRef.parse(image)
        crop = mapping.get("crop")
        if crop is not None:
            crop = _exact(crop, "crop", ("x", "y", "w", "h"))
            crop = Crop(crop["x"], crop["y"], crop["w"], crop["h"])  # type: ignore[arg-type]
        focus = mapping.get("focus")
        if focus is not None:
            focus = _exact(focus, "focus", ("x", "y"))
            focus = Focus(focus["x"], focus["y"])  # type: ignore[arg-type]
        expression = mapping.get("expression")
        if expression is not None:
            expression = _exact(expression, "expression", ("outfit", "name"))
            expression = Expression(expression["outfit"], expression["name"])  # type: ignore[arg-type]
        extras = {key: value for key, value in mapping.items() if key not in _PLACEMENT_KEYS}
        return Placement(mode, role, image, crop, focus, expression,  # type: ignore[arg-type]
                         mapping.get("provenance"), extras)  # type: ignore[arg-type]
    return _wrap(build)
