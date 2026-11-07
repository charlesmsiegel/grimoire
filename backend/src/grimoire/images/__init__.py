"""Image identity and placements: pure values, payload codec and resolver (Sprint 018).

Pure: no files, clocks, randomness or providers. The store-backed service is
``grimoire.imagestore``; blobs and records are stored by ``grimoire.store``. See docs/IMAGES.md.
"""

from .codec import ImageCodecError, image_from_payload, image_to_payload, placement_from_payload
from .codec import placement_to_payload
from .model import (
    BP, DERIVED_MODES, IMAGE_SCHEMA, MEDIA_TYPES, MODES, PLACEMENT_SCHEMA, ROLES, SOURCE_MODES, BlobInfo, Crop, Expression, Focus,
    ImageModelError, ImageObject, Placement, expression_slot, slot_expression, slot_role, validate_placement,
    validate_provenance,
)
from .resolve import (
    ORIGINS, ArtOrigin, BlobFact, Facts, Hidden, ImageEntry, Layer, PlacementEntry, Problem, Resolution, Shown,
    Slot, Unavailable, inherited, resolve, resolve_lineage,
)

__all__ = [
    "ArtOrigin", "BP", "DERIVED_MODES", "IMAGE_SCHEMA", "Layer", "ORIGINS", "Slot", "resolve_lineage", "MEDIA_TYPES", "MODES", "PLACEMENT_SCHEMA", "ROLES", "SOURCE_MODES", "BlobFact", "BlobInfo", "Crop",
    "Expression", "Facts", "Focus", "Hidden", "ImageCodecError", "ImageEntry", "ImageModelError", "ImageObject",
    "Placement", "PlacementEntry", "Problem", "Resolution", "Shown", "Unavailable", "expression_slot",
    "image_from_payload", "image_to_payload", "inherited", "placement_from_payload", "placement_to_payload",
    "resolve", "slot_expression", "slot_role", "validate_placement", "validate_provenance",
]
