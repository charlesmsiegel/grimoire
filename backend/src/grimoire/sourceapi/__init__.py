"""Library-source authoring and its JSON ops (Sprint 109). See docs/SOURCE_AUTHORING.md."""

from .api import ATTACH_ROLE, UNAVAILABLE, SourceApi
from .authoring import (
    ENTITY,
    LOREBOOK,
    NATURES,
    OTHER,
    PORTRAYAL_EDITS,
    SCENARIO,
    AuthoringRefused,
    SourceAuthoring,
    Written,
    next_revision,
)

__all__ = [
    "ATTACH_ROLE", "ENTITY", "LOREBOOK", "NATURES", "OTHER", "PORTRAYAL_EDITS", "SCENARIO", "UNAVAILABLE",
    "AuthoringRefused", "SourceApi", "SourceAuthoring", "Written", "next_revision",
]
