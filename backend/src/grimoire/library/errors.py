"""Library exception hierarchy."""

from __future__ import annotations


class LibraryError(Exception):
    """Base class for Library module errors."""

    http_status = 500


class LibraryNotFoundError(LibraryError):
    """Raised when a requested world / entity / style guide / preset is absent."""

    http_status = 404


class LibraryConflictError(LibraryError):
    """Raised when a write would collide with an existing library entity."""

    http_status = 409


class PromotionError(LibraryError):
    """Raised when promote_to_library cannot proceed (missing source, etc.)."""

    http_status = 400


class ReclassificationError(LibraryError):
    """Raised when reclassify_entity / undo_reclassification cannot proceed."""

    http_status = 400
