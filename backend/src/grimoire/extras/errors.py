"""Extras module exception hierarchy."""

from __future__ import annotations


class ExtrasError(Exception):
    """Base for extras-module errors."""

    http_status = 500


class ExtrasNotFoundError(ExtrasError):
    """Requested extras key does not exist in the resolved view."""

    http_status = 404


class ExtrasHardCapError(ExtrasError):
    """Write would exceed a hard cap (per-entity, per-string)."""

    http_status = 409


class ExtrasPromotionError(ExtrasError):
    """promote-to-fact / promote-to-library cannot proceed."""

    http_status = 409


class ExtrasSoftCapWarning(UserWarning):
    """Non-fatal: write succeeded but breached a soft cap."""
