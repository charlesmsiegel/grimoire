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

from grimoire.library.errors import (
    LibraryConflictError,
    LibraryError,
    LibraryNotFoundError,
    PromotionError,
)
from grimoire.library.service import LibraryService

__all__ = [
    "LibraryConflictError",
    "LibraryError",
    "LibraryNotFoundError",
    "LibraryService",
    "PromotionError",
]
