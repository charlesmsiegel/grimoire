"""Import helpers for SillyTavern v2/v3, charx, and plaintext characters.

Each helper returns a ``CharacterData`` (the lightweight create payload) and a
list of warnings. Callers (typically :class:`CharactersService`) decide
whether to write the resulting character to disk.

The SillyTavern v2/v3 (Character Card V2) and charx parsers are thin
wrappers around :mod:`grimoire.characters.ingest`; this module remains for
backwards compatibility with the existing public surface.
"""

from __future__ import annotations

import re

from grimoire.files import slugify
from grimoire.types.characters import CharacterData, CharacterRole, IngestOptions, VoiceAnchor

from .errors import ImportError_
from .ingest import ingest_character_card_v2


def _slugify(value: str) -> str:
    return slugify(value, fallback="character")


def parse_sillytavern(
    card: bytes, *, options: IngestOptions | None = None
) -> tuple[CharacterData, list[str]]:
    """Parse a SillyTavern v2/v3 character card.

    Accepts the canonical JSON envelope as well as a PNG with an embedded
    ``chara``/``ccv3`` tEXt chunk. Returns the projected ``CharacterData``
    plus any warnings. Delegates to
    :func:`grimoire.characters.ingest.ingest_character_card_v2`.
    """
    ingested = ingest_character_card_v2(card, options=options)
    return ingested.data, list(ingested.warnings)


def parse_charx(
    charx_bytes: bytes, *, options: IngestOptions | None = None
) -> tuple[CharacterData, list[str]]:
    """Parse a CharacterX (zip) bundle. See :func:`parse_sillytavern`."""
    ingested = ingest_character_card_v2(charx_bytes, options=options)
    return ingested.data, list(ingested.warnings)


def parse_plaintext(text: str) -> tuple[CharacterData, list[str]]:
    """Parse a plaintext character description.

    Heuristics: the first non-empty line is the name; quoted lines become
    voice samples; the remaining prose becomes the description / body.
    """
    lines = [ln.rstrip() for ln in (text or "").splitlines()]
    nonempty = [ln for ln in lines if ln.strip()]
    if not nonempty:
        raise ImportError_("plaintext import is empty")
    warnings: list[str] = []

    name_line = nonempty[0].strip()
    # Strip "Name:" prefix or markdown heading if present.
    name = re.sub(r"^#+\s*", "", name_line)
    name = re.sub(r"^name\s*[:\-]\s*", "", name, flags=re.IGNORECASE).strip()
    name = name or "Unnamed"
    asset_id = _slugify(name)

    samples = re.findall(r'"([^"\n]{3,})"', text)
    body_lines = [ln for ln in lines[1:] if not _looks_like_sample(ln)]
    body = "\n".join(body_lines).strip()
    description = body.split("\n", 1)[0] if body else ""

    voice = VoiceAnchor(
        summary=description[:200].strip(),
        samples=samples[:5],
    )
    if not samples:
        warnings.append("no quoted dialogue samples found")
    return (
        CharacterData(
            id=asset_id,
            name=name,
            role=CharacterRole.MINOR_NPC,
            voice=voice,
            description=description,
            body=body,
        ),
        warnings,
    )


def _looks_like_sample(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return stripped.startswith('"') and stripped.endswith('"') and len(stripped) >= 4
