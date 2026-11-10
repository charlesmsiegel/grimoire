"""The voice texts of an imported V0.2.1 portrayal, read from the verbatim card it already keeps (026).

026 stores each portrayal's V1 file verbatim in ``extras.v1.text``. 027's voice producer is pure and
may not parse JSON, so this adapter reads the card's ``data.mes_example``, ``data.personality`` and
``data.extensions`` out of that text and hands them over as plain strings. Nothing is written: the
stored revision's bytes stay exactly what the importer wrote, so a re-run sees no change and a
library imported before voices existed speaks without being imported again.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from grimoire.core.entity import EntityDefinition
from grimoire.core.identity import LegacyId
from grimoire.core.snapshot import EffectiveSnapshot
from grimoire.generation import SnapshotVoices, VoiceNote, VoiceTexts, imported_source, text_of
from grimoire.voice import VoiceCatalog

PERSONA_FORMAT = "persona_md"


def v1_voice_texts(definition: EntityDefinition) -> VoiceTexts | None:
    """The voice texts of a 026 portrayal (``extras.v1`` with its verbatim text); ``None`` otherwise.

    A persona (``persona_md``) has only its description, which 026 already took from its body. A card
    that does not parse as a JSON object speaks from its description alone, like a card with no
    example messages; one nested too deep to parse does too, noted ``card_unreadable``. A ``personality``
    section authored on the revision (109's editor) is its personality instead of the card's.
    """
    v1 = definition.extras.get("v1")
    if not (isinstance(v1, Mapping) and isinstance(v1.get("legacy_id"), str) and isinstance(v1.get("text"), str)):
        return None
    fmt = v1.get("format")
    if fmt == PERSONA_FORMAT:
        return VoiceTexts(description=definition.description)
    try:
        data = json.loads(v1["text"]).get("data")
    except (ValueError, AttributeError):
        data = None
    except RecursionError:  # nested past what json can parse: one bad card must not stop the catalog
        return VoiceTexts(description=definition.description, notes=(VoiceNote("card", 0, "card_unreadable"),))
    if not isinstance(data, Mapping):
        return VoiceTexts(description=definition.description)
    try:
        card_version = LegacyId.parse(v1["legacy_id"])
    except ValueError:
        card_version = None
    examples = text_of(data.get("mes_example"))
    try:
        imported, notes = imported_source(fmt, card_version, examples, data.get("extensions"))
    except RecursionError:  # extensions that parsed but nest too deep to copy: the verbatim text keeps them
        imported, notes = imported_source(fmt, card_version, examples, {})
        notes = (*notes, VoiceNote("extensions", 0, "extensions_not_carried"))
    name = data.get("name")
    speaker = name if isinstance(name, str) and name.strip() else None  # the card's own, whatever it is renamed
    return VoiceTexts(examples, _authored_personality(definition) or text_of(data.get("personality")),
                      definition.description, imported, notes, speaker)


def _authored_personality(definition: EntityDefinition) -> str:
    """A ``personality`` section authored on the revision (109's editor), which wins over the verbatim card's,
    as 037's card reader does; the verbatim text stays the import's provenance."""
    sections = definition.extras.get("sections")
    text = text_of(sections.get("personality")) if isinstance(sections, Mapping) else ""
    return text if text.strip() else ""


def imported_voices(snapshot: EffectiveSnapshot, authored: VoiceCatalog | None = None) -> SnapshotVoices:
    """The play catalog for ``snapshot`` with V1 portrayals read too: what a turn's caller builds."""
    return SnapshotVoices(snapshot, authored, readers=(v1_voice_texts,))


__all__ = ["imported_voices", "v1_voice_texts"]
