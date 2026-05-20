"""Library module — worlds, entity cards, style guides, image presets.

Implements spec 18. The Library is the user-authored content layer: files on
disk under ``data/library/`` are the source of truth, the :class:`StateStore`
provides a SQLite index for fast queries, and a campaign-side cascade
(emergent → override → snapshot/live) resolves entities at read time.

This package wraps :class:`grimoire.state_store.StateStore` with a
domain-shaped API: callers receive typed :class:`LibraryEntity`,
:class:`WorldMeta`, :class:`Greeting`, and :class:`ResolvedEntity` values
instead of raw rows. Writes are mediated so files and the index stay in
sync; reads follow the cascade documented in the spec.
"""

from grimoire.library.classify import Suggestion, suggest_kind
from grimoire.library.config import (
    LibraryConfig,
    LibraryIndexingConfig,
    LibraryPromotionConfig,
    LibraryReclassificationConfig,
    LibraryVersionPinningConfig,
)
from grimoire.library.errors import (
    LibraryConflictError,
    LibraryError,
    LibraryNotFoundError,
    PromotionError,
    ReclassificationError,
)
from grimoire.library.reclassify import (
    ReclassificationResult,
    apply_mapping,
    required_overrides_for,
)
from grimoire.library.service import LibraryService

__all__ = [
    "LibraryConfig",
    "LibraryConflictError",
    "LibraryError",
    "LibraryIndexingConfig",
    "LibraryNotFoundError",
    "LibraryPromotionConfig",
    "LibraryReclassificationConfig",
    "LibraryService",
    "LibraryVersionPinningConfig",
    "PromotionError",
    "ReclassificationError",
    "ReclassificationResult",
    "Suggestion",
    "apply_mapping",
    "required_overrides_for",
    "suggest_kind",
]
