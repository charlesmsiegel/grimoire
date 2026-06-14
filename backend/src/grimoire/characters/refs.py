"""Shared character-ref parsing.

A single home for turning the several equivalent character-ref spellings into a
structured view, used by both the character service and the sheet manager.
Keeping it here (a leaf with no service/sheet_manager dependency) lets both
import it without a circular import — ``service`` already imports
``sheet_manager``.
"""

from __future__ import annotations

from dataclasses import dataclass

from grimoire.util import canonicalize_character_ref

from .errors import CharactersError


@dataclass(frozen=True)
class CharacterRefView:
    """Structured view of a parsed character ref."""

    is_emergent: bool
    world_id: str | None
    asset_id: str


def parse_character_ref(ref: str) -> CharacterRefView:
    """Parse any recognized character-ref spelling into a :class:`CharacterRefView`.

    Refs are canonicalized first so over-qualified / shorthand spellings (e.g. a
    world-PC ref double-prefixed as ``<world>/worlds/<world>/characters/<id>``)
    resolve instead of raising — which previously silently dropped the PC's card
    from context (#464).
    """
    if not ref:
        raise CharactersError("empty character_ref")
    ref = canonicalize_character_ref(ref)
    if ref.startswith("campaign:emergent/"):
        _, _, rest = ref.partition("campaign:emergent/")
        parts = rest.strip("/").split("/")
        if parts[0] == "character" and len(parts) == 2:
            return CharacterRefView(True, None, parts[1])
        if len(parts) == 1:
            return CharacterRefView(True, None, parts[0])
    if ref.startswith("emergent/"):
        parts = ref.split("/")
        return CharacterRefView(True, None, parts[-1])
    if ref.startswith("library:"):
        _, _, path = ref.partition("library:")
        parts = path.strip("/").split("/")
        if len(parts) >= 4 and parts[0] == "worlds" and parts[2] in {"characters", "character"}:
            return CharacterRefView(False, parts[1], parts[3])
    parts = ref.strip("/").split("/")
    if len(parts) >= 4 and parts[0] == "worlds" and parts[2] in {"characters", "character"}:
        return CharacterRefView(False, parts[1], parts[3])
    raise CharactersError(f"unrecognized character_ref {ref!r}")
