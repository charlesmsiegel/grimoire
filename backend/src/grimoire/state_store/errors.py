"""State Store error types."""

from __future__ import annotations


class StateStoreError(Exception):
    """Base for State Store errors."""

    http_status = 500


class NotFoundError(StateStoreError):
    """A requested entity, scene, delta, or file does not exist."""

    http_status = 404


class ConflictError(StateStoreError):
    """A write conflicts with the current state (e.g. last-write-wins warning)."""

    http_status = 409


class InvalidRefError(StateStoreError):
    """A composite ref could not be parsed."""

    http_status = 400
