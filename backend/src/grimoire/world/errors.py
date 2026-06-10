"""World module errors."""

from __future__ import annotations


class WorldError(Exception):
    """Base error for the World module."""

    http_status = 500


class WorldNotFoundError(WorldError):
    """Raised when a world / entity is not found."""

    http_status = 404


class CompositionError(WorldError):
    """Raised when a campaign composition can't be resolved cleanly."""

    http_status = 409


class OverrideTargetError(WorldError):
    """Raised when a campaign override could never become visible — e.g. the
    target is shadowed by campaign-local emergent content, which the read
    cascade resolves first."""

    http_status = 409
