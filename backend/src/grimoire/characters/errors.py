"""Characters module errors."""

from __future__ import annotations


class CharactersError(Exception):
    """Base error for the Characters module."""


class CharacterNotFoundError(CharactersError):
    """Raised when a character cannot be resolved through the cascade."""


class ImportError_(CharactersError):
    """Raised when an import payload cannot be parsed."""


class PromotionError(CharactersError):
    """Raised when promotion of an emergent character to the library fails."""
