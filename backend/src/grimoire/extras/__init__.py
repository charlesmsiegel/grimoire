"""Narrative Extras module.

``ExtrasService`` is the public surface; writes route through ``LibraryService``
for library scope and ``StateStore.write_override`` for campaign overrides. The
SQLite mirror (``entity_extras`` + ``entity_extras_fts``) is for query only --
reads cascade-resolve frontmatter.

See ``docs/superpowers/specs/2026-05-19-narrative-extras-design.md``.
"""

from .errors import (
    ExtrasError,
    ExtrasHardCapError,
    ExtrasNotFoundError,
    ExtrasPromotionError,
    ExtrasSoftCapWarning,
)
from .mirror import ExtrasMirror
from .service import ExtrasSearchHit, ExtrasService, ExtrasSetResult

__all__ = [
    "ExtrasError",
    "ExtrasHardCapError",
    "ExtrasMirror",
    "ExtrasNotFoundError",
    "ExtrasPromotionError",
    "ExtrasSearchHit",
    "ExtrasService",
    "ExtrasSetResult",
    "ExtrasSoftCapWarning",
]
