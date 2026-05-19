"""Extras module exception hierarchy."""

from __future__ import annotations


class ExtrasError(Exception):
    """Base for extras-module errors."""


class ExtrasNotFoundError(ExtrasError):
    """Requested extras key does not exist in the resolved view."""


class ExtrasHardCapError(ExtrasError):
    """Write would exceed a hard cap (per-entity, per-string)."""


class ExtrasPromotionError(ExtrasError):
    """promote-to-fact / promote-to-library cannot proceed."""


class ExtrasSoftCapWarning(UserWarning):
    """Non-fatal: write succeeded but breached a soft cap."""
