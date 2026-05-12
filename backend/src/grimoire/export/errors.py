"""Errors raised by the Export module."""

from __future__ import annotations


class ExportError(Exception):
    """Base class for export failures."""


class UnknownAdapterError(ExportError, KeyError):
    """Raised when no adapter is registered under the requested id."""


class EmptyExportError(ExportError):
    """Raised when the selection resolves to no scenes."""


class ValidationFailed(ExportError):
    """Raised when EPUBCheck (or another validator) rejects the artifact."""

    def __init__(self, message: str, *, log: list[str] | None = None) -> None:
        super().__init__(message)
        self.log = list(log or [])


__all__ = [
    "EmptyExportError",
    "ExportError",
    "UnknownAdapterError",
    "ValidationFailed",
]
