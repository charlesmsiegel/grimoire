"""The facet error type, in a leaf module so facet value modules can share it.

``grimoire.core.facets`` re-exports it; both names are the same class.
"""

from __future__ import annotations


class FacetError(ValueError):
    """An invalid facet or facet combination; ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))
