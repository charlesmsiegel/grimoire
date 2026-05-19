"""Slug generation and scene filename helpers.

Scene files are named ``NNNN-slug.{md,yaml}`` where ``NNNN`` is a per-campaign
zero-padded ordinal and ``slug`` is derived from the scene title or first
location (see specs/10-scene-manager.md).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_DEFAULT_ORDINAL_WIDTH = 4
_DEFAULT_MAX_SLUG_LEN = 60
_FILENAME_RE = re.compile(r"\A(?P<ordinal>\d+)-(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)\Z")


def slugify(
    text: str,
    *,
    max_len: int = _DEFAULT_MAX_SLUG_LEN,
    fallback: str = "untitled",
) -> str:
    """Convert ``text`` into a URL-safe slug.

    - Unicode is NFKD-normalized; combining marks are dropped (``café`` →
      ``cafe``).
    - Non-alphanumeric runs collapse to a single ``-``.
    - Result is lowercased and trimmed to ``max_len`` without splitting a
      trailing hyphen.

    Returns ``fallback`` (default ``"untitled"``) if nothing usable remains.
    """
    if max_len <= 0:
        raise ValueError("max_len must be positive")

    normalized = unicodedata.normalize("NFKD", text)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    hyphenated = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")

    if not hyphenated:
        return fallback

    if len(hyphenated) > max_len:
        hyphenated = hyphenated[:max_len].rstrip("-")
        if not hyphenated:
            return fallback

    return hyphenated


def scene_filename(
    ordinal: int,
    title_or_slug: str,
    *,
    ext: str = "md",
    width: int = _DEFAULT_ORDINAL_WIDTH,
) -> str:
    """Build ``NNNN-slug.ext`` for a scene file.

    ``title_or_slug`` is passed through :func:`slugify` so callers can hand
    over a raw scene title.
    """
    if ordinal < 0:
        raise ValueError("ordinal must be non-negative")
    if width < 1:
        raise ValueError("width must be at least 1")
    slug = slugify(title_or_slug)
    return f"{ordinal:0{width}d}-{slug}.{ext.lstrip('.')}"


@dataclass(slots=True, frozen=True)
class SceneFilenameParts:
    ordinal: int
    slug: str
    ext: str


def parse_scene_filename(filename: str) -> SceneFilenameParts:
    """Parse ``NNNN-slug.ext`` into its parts.

    Raises ``ValueError`` if the filename doesn't match the expected pattern.
    """
    if "." not in filename:
        raise ValueError(f"scene filename missing extension: {filename!r}")
    stem, _, ext = filename.rpartition(".")
    match = _FILENAME_RE.match(stem)
    if not match:
        raise ValueError(f"scene filename does not match NNNN-slug pattern: {filename!r}")
    return SceneFilenameParts(
        ordinal=int(match.group("ordinal")),
        slug=match.group("slug"),
        ext=ext,
    )
