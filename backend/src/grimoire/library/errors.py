"""Library exception hierarchy."""

from __future__ import annotations


class LibraryError(Exception):
    """Base class for Library module errors."""


class LibraryNotFoundError(LibraryError):
    """Raised when a requested world / entity / style guide / preset is absent."""


class LibraryConflictError(LibraryError):
    """Raised when a write would collide with an existing library entity."""


class PromotionError(LibraryError):
    """Raised when promote_to_library cannot proceed (missing source, etc.)."""


class ReclassificationError(LibraryError):
    """Raised when reclassify_entity / undo_reclassification cannot proceed."""
