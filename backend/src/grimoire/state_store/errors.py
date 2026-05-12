"""State Store error types."""

from __future__ import annotations


class StateStoreError(Exception):
    """Base for State Store errors."""


class NotFoundError(StateStoreError):
    """A requested entity, scene, delta, or file does not exist."""


class ConflictError(StateStoreError):
    """A write conflicts with the current state (e.g. last-write-wins warning)."""


class InvalidRefError(StateStoreError):
    """A composite ref could not be parsed."""
