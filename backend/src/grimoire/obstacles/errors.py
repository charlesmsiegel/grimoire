"""The obstacle package's one error type (Sprint 040)."""

from __future__ import annotations


class ObstacleError(ValueError):
    """An invalid obstacle or attempt, or a refused step; ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


__all__ = ["ObstacleError"]
