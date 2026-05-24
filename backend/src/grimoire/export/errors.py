"""Errors raised by the Export module."""

from __future__ import annotations


class ExportError(Exception):
    """Base class for export failures."""

    http_status = 500


class UnknownAdapterError(ExportError, KeyError):
    """Raised when no adapter is registered under the requested id."""

    http_status = 404


class EmptyExportError(ExportError):
    """Raised when the selection resolves to no scenes."""

    http_status = 400


class ValidationFailed(ExportError):
    """Raised when EPUBCheck (or another validator) rejects the artifact."""

    http_status = 400

    def __init__(self, message: str, *, log: list[str] | None = None) -> None:
        super().__init__(message)
        self.log = list(log or [])


__all__ = [
    "EmptyExportError",
    "ExportError",
    "UnknownAdapterError",
    "ValidationFailed",
]
