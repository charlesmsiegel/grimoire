"""Library exception hierarchy."""

from __future__ import annotations


class LibraryError(Exception):
    """Base class for Library module errors."""


class LibraryNotFoundError(LibraryError):
    """Raised when a requested world / entity / style guide / preset is absent."""


class PromotionError(LibraryError):
    """Raised when promote_to_library cannot proceed (missing source, etc.)."""
