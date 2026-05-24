"""Characters module errors."""

from __future__ import annotations


class CharactersError(Exception):
    """Base error for the Characters module."""

    http_status = 500


class CharacterNotFoundError(CharactersError):
    """Raised when a character cannot be resolved through the cascade."""

    http_status = 404


class ImportError_(CharactersError):
    """Raised when an import payload cannot be parsed."""

    http_status = 400


class PromotionError(CharactersError):
    """Raised when promotion of an emergent character to the library fails."""

    http_status = 400
