"""Store-backed pictures (Sprint 018): images, placements and blob repair in guarded batches.

An adapter over ``grimoire.store`` and the pure ``grimoire.images``. See docs/IMAGES.md.
"""

from .library import ImageBatch, ImageCommit, ImageLibrary, InvalidBatch, InvalidImage, InvalidPlacement, derivation

__all__ = ["ImageBatch", "ImageCommit", "ImageLibrary", "InvalidBatch", "InvalidImage", "InvalidPlacement", "derivation"]
