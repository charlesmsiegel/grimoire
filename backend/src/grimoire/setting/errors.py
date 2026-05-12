"""Setting module errors."""

from __future__ import annotations


class SettingError(Exception):
    """Base error for the Setting module."""


class SettingNotFoundError(SettingError):
    """Raised when a setting / entity is not found."""


class CompositionError(SettingError):
    """Raised when a campaign composition can't be resolved cleanly."""
