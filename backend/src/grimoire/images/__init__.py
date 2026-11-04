"""Image identity and placements: pure values, payload codec and resolver (Sprint 018).

Pure: no files, clocks, randomness or providers. The store-backed service is
``grimoire.imagestore``; blobs and records are stored by ``grimoire.store``. See docs/IMAGES.md.
"""

from .codec import ImageCodecError, image_from_payload, image_to_payload, placement_from_payload
from .codec import placement_to_payload
from .model import (
    BP, IMAGE_SCHEMA, MEDIA_TYPES, MODES, PLACEMENT_SCHEMA, ROLES, BlobInfo, Crop, Expression, Focus,
    ImageModelError, ImageObject, Placement, expression_slot, slot_expression, slot_role, validate_placement,
    validate_provenance,
)
from .resolve import (
    BlobFact, Facts, Hidden, ImageEntry, PlacementEntry, Problem, Resolution, Shown, Unavailable, inherited,
    resolve,
)

__all__ = [
    "BP", "IMAGE_SCHEMA", "MEDIA_TYPES", "MODES", "PLACEMENT_SCHEMA", "ROLES", "BlobFact", "BlobInfo", "Crop",
    "Expression", "Facts", "Focus", "Hidden", "ImageCodecError", "ImageEntry", "ImageModelError", "ImageObject",
    "Placement", "PlacementEntry", "Problem", "Resolution", "Shown", "Unavailable", "expression_slot",
    "image_from_payload", "image_to_payload", "inherited", "placement_from_payload", "placement_to_payload",
    "resolve", "slot_expression", "slot_role", "validate_placement", "validate_provenance",
]
