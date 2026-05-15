"""World module errors."""

from __future__ import annotations


class WorldError(Exception):
    """Base error for the World module."""


class WorldNotFoundError(WorldError):
    """Raised when a world / entity is not found."""


class CompositionError(WorldError):
    """Raised when a campaign composition can't be resolved cleanly."""
