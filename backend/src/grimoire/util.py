"""Cross-module utilities.

Small helpers shared across services that don't fit any narrower module.
Keep this file narrow — anything domain-specific belongs in its own package.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

_DEFAULT_ID_HEX_WIDTH = 12
_SLUGIFY_ID_RE = re.compile(r"[^a-z0-9]+")


def new_id(prefix: str, *, length: int = _DEFAULT_ID_HEX_WIDTH) -> str:
    """Return ``"{prefix}_{N hex chars}"`` from a fresh UUID4.

    ``length`` is the count of hex characters after the prefix (default 12).
    The state store uses 16 for delta/embedding rows; service-layer IDs use 12.
    """
    return f"{prefix}_{uuid.uuid4().hex[:length]}"


def now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(UTC).isoformat()


def slugify_id(raw: str, *, fallback: str = "") -> str:
    """Lower-case ``raw`` and collapse non-alphanumeric runs into ``-``.

    Used for case-insensitive identifier matching (e.g. so callers can pass
    ``Alistair-Hyde-Smythe`` and find ``alistair-hyde-smythe``). Returns
    ``fallback`` if nothing usable remains.

    Distinct from :func:`grimoire.files.slug.slugify`, which also strips
    non-ASCII via NFKD normalization and enforces a max length — this helper
    is for ID lookup, not filename generation.
    """
    slug = _SLUGIFY_ID_RE.sub("-", raw.lower()).strip("-")
    return slug or fallback


__all__ = ["new_id", "now_iso", "slugify_id"]
