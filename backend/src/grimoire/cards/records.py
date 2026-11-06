"""Map parsed cards to source-record payloads, and classify every text by authored role (Sprint 037).

Pure. The importer (``grimoire.cardstore``) wraps these payloads in store envelopes.

- A character becomes a ``grimoire.entity/0`` **individual** with ``agent`` and spoken
  ``communicative`` traits. Its description is 009's field, verbatim. Its personality becomes the
  022 ``personality`` section when it is a valid section, otherwise it is kept verbatim under
  ``card.unsectioned`` (a ``not_section`` loss). The card's scenario prompt and example messages
  are not about the character, so they are never sections: they sit under ``card.texts``. Everything
  else a card says (greetings, instructions, authorship, tags, assets, extensions, unknown fields)
  sits under the 037-owned ``card`` key too, which 022 does not compose.
- A lorebook becomes a ``grimoire.lorebook/1`` record and a scenario a ``grimoire.scenario/1``
  record; neither is an entity, so 022 returns them raw.
- ``card.roles`` (and a scenario's ``roles``) names the role of each section and text.

No role is ``accepted_event``: nothing a card says ever becomes an event, evidence or belief.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

from grimoire.core.entity import EntityDefinition
from grimoire.core.facets import FacetSet
from grimoire.core.identity import SourceRef
from grimoire.core.portrayal import PortrayalError, authored_sections

from .model import (
    CharacterCard, Greeting, LoreEntry, Lorebook, LorebookCard, Loss, ParsedCard, ScenarioCard, Authorship, Asset,
    CastRef,
)

ENTITY_SCHEMA = "grimoire.entity/0"
LOREBOOK_SCHEMA = "grimoire.lorebook/1"
SCENARIO_SCHEMA = "grimoire.scenario/1"
IMPORT_SCHEMA = "grimoire.card-import/1"
IMPORT_KIND, IMPORT_ID = "card-import", "import"

#: every role an imported text can have; ``accepted_event`` is deliberately not one
ROLES = frozenset({
    "authored_description", "authored_style", "authored_voice", "scenario_instruction", "private_instruction",
    "opening_greeting", "lorebook_content", "scenario_premise", "scenario_opening", "creator_note",
})
#: the one card text 022 composes: how the character is (authored style)
_CHARACTER_SECTIONS = (("personality", "authored_style"),)
#: card texts that are not about the character and so are never 022 sections (a prompt builder
#: reading the effective view must not take a scenario instruction or example dialogue as the character)
_CHARACTER_TEXTS = (("scenario", "scenario_instruction"), ("example-messages", "authored_voice"))
_SCENARIO_TEXT_ROLES = {"setting": "authored_description", "personality": "authored_style",
                        "example-messages": "authored_voice", "post-history-instructions": "private_instruction"}
_PROBE = SourceRef("card", "character", "probe", "r1")
MAX_SLUG = 48


_DEVICE_NAMES = frozenset({"con", "prn", "aux", "nul"} | {f"com{n}" for n in range(10)}
                          | {f"lpt{n}" for n in range(10)})


def slugify(text: str, fallback: str = "card") -> str:
    """An identity segment from display text: ASCII letters and digits, ``-`` between words."""
    folded = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(char for char in folded if not unicodedata.combining(char)).encode("ascii", "ignore")
    slug, dash = [], False
    for char in ascii_text.decode("ascii").lower():
        if char.isalnum():
            slug.append(char)
            dash = False
        elif slug and not dash:
            slug.append("-")
            dash = True
    result = "".join(slug).strip("-")[:MAX_SLUG].strip("-") or fallback
    if result in _DEVICE_NAMES:  # a name Windows reserves cannot be a folder anywhere
        result = f"{result}-card"
    return result


def section_ok(name: str, text: str) -> bool:
    """Whether 022 would compose ``text`` as section ``name`` (one source of truth: 022's own check)."""
    probe = EntityDefinition(_PROBE, "probe", "individual", FacetSet(()), extras={"sections": {name: text}})
    try:
        authored_sections(probe)
    except PortrayalError:
        return False
    return True


def _authorship(value: Authorship) -> dict[str, object]:
    found: dict[str, object] = {}
    for key in ("creator", "character_version", "creator_notes", "creation_date", "modification_date"):
        if getattr(value, key) is not None:
            found[key] = getattr(value, key)
    if value.source:
        found["source"] = list(value.source)
    return found


def _greeting(greeting: Greeting) -> dict[str, object]:
    found: dict[str, object] = {"id": greeting.greeting_id, "text": greeting.text}
    if greeting.group_only:
        found["group_only"] = True
    return found


def _asset(asset: Asset) -> dict[str, object]:
    found: dict[str, object] = {"type": asset.type, "uri": asset.uri}
    if asset.name is not None:
        found["name"] = asset.name
    if asset.ext is not None:
        found["ext"] = asset.ext
    if asset.extras:
        found["extras"] = _plain(asset.extras)
    return found


def _cast(cast: CastRef) -> dict[str, object]:
    found: dict[str, object] = {}
    if cast.ref is not None:
        found["ref"] = cast.ref
    if cast.name is not None:
        found["name"] = cast.name
    if cast.extras:
        found["extras"] = _plain(cast.extras)
    return found


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(child) for child in value]
    return value


def _put(target: dict[str, object], key: str, value: object) -> None:
    """Set ``key`` unless ``value`` is empty, so a payload carries no noise."""
    if value is None or value == "" or value == () or value == [] or value == {}:
        return
    if isinstance(value, Mapping) and not value:
        return
    target[key] = _plain(value)


def character_payload(card: CharacterCard, *, lorebook: SourceRef | None = None
                      ) -> tuple[dict[str, object], tuple[Loss, ...]]:
    """The character's ``grimoire.entity/0`` payload and the ``not_section`` losses it records."""
    sections: dict[str, str] = {}
    unsectioned: dict[str, str] = {}
    roles: dict[str, str] = {}
    losses: list[Loss] = []
    texts: dict[str, str] = {}
    for (name, role), text in zip(_CHARACTER_SECTIONS, (card.personality,)):
        if not text.strip():
            continue
        roles[name] = role
        if section_ok(name, text):
            sections[name] = text
        else:
            unsectioned[name] = text
            losses.append(Loss(f"character.sections.{name}", "not_section"))
    for (name, role), text in zip(_CHARACTER_TEXTS, (card.scenario, card.example_messages)):
        if text.strip():
            texts[name] = text
            roles[f"texts.{name}"] = role
    payload: dict[str, object] = {
        "name": card.name, "nature": "individual",
        "traits": [{"facet": "agent"}, {"facet": "communicative", "speaks": ["speech"], "understands": ["speech"]}],
    }
    if card.description:
        payload["description"] = card.description
        roles["description"] = "authored_description"
    _put(payload, "sections", sections)
    meta: dict[str, object] = {"format": card.format, "spec_version": card.spec_version, "roles": roles}
    _put(meta, "unsectioned", unsectioned)
    _put(meta, "texts", texts)
    _put(meta, "greetings", [_greeting(greeting) for greeting in card.greetings])
    instructions: dict[str, str] = {}
    _put(instructions, "system_prompt", card.system_prompt)
    _put(instructions, "post_history_instructions", card.post_history_instructions)
    _put(meta, "instructions", instructions)
    _put(meta, "nickname", card.nickname)
    _put(meta, "tags", list(card.tags))
    _put(meta, "authorship", _authorship(card.authorship))
    if lorebook is not None:
        meta["lorebook"] = str(lorebook)
    _put(meta, "assets", [_asset(asset) for asset in card.assets])
    _put(meta, "extensions", card.extensions)
    _put(meta, "extras", card.extras)
    payload["card"] = meta
    return payload, tuple(losses)


def lorebook_payload(book: Lorebook, *, extras: Mapping[str, object] | None = None) -> dict[str, object]:
    """A ``grimoire.lorebook/1`` payload: authored lore, never facts; triggers kept, not interpreted."""
    payload: dict[str, object] = {}
    _put(payload, "name", book.name)
    _put(payload, "description", book.description)
    entries = []
    for entry in book.entries:
        found: dict[str, object] = {"keys": list(entry.keys), "content": entry.content}
        if not entry.enabled:
            found["enabled"] = False
        _put(found, "secondary_keys", list(entry.secondary_keys))
        _put(found, "settings", entry.settings)
        _put(found, "extensions", entry.extensions)
        _put(found, "extras", entry.extras)
        entries.append(found)
    payload["entries"] = entries
    _put(payload, "settings", book.settings)
    _put(payload, "extensions", book.extensions)
    # a lorebook document's own top-level unknowns (``extras["card"]``) and its book's unknowns are
    # kept apart, as for a character card: neither can overwrite the other
    merged = dict(_plain(book.extras))  # type: ignore[arg-type]
    if extras:
        merged = {**({"data": merged} if merged else {}), **_plain(extras)}  # type: ignore[dict-item]
    _put(payload, "extras", merged)
    return payload


def scenario_payload(card: ScenarioCard, *, lorebook: SourceRef | None = None) -> dict[str, object]:
    """A ``grimoire.scenario/1`` payload. Openings are possible starts, never canon; the cast is kept
    exactly as written (a name is never resolved to anyone)."""
    payload: dict[str, object] = {"format": card.format, "title": card.title}
    roles: dict[str, str] = {}
    if card.premise:
        payload["premise"] = card.premise
        roles["premise"] = "scenario_premise"
    if card.openings:
        payload["openings"] = [_greeting(opening) for opening in card.openings]
        roles["openings"] = "scenario_opening"
    if card.instructions:
        payload["instructions"] = card.instructions
        roles["instructions"] = "private_instruction"
    if card.texts:
        payload["texts"] = dict(card.texts)
        for name in card.texts:
            roles[f"texts.{name}"] = _SCENARIO_TEXT_ROLES.get(name, "authored_description")
    payload["roles"] = roles
    if lorebook is not None:
        payload["lorebook"] = str(lorebook)
    _put(payload, "cast", [_cast(cast) for cast in card.cast])
    _put(payload, "tags", list(card.tags))
    _put(payload, "authorship", _authorship(card.authorship))
    _put(payload, "assets", [_asset(asset) for asset in card.assets])
    _put(payload, "extensions", card.extensions)
    _put(payload, "extras", card.extras)
    return payload


def scenario_lorebook(schema: object, payload: Mapping[str, object]) -> SourceRef | None:
    """The pinned lorebook a stored scenario record names (``scenario_payload``'s ``lorebook``), if any.

    ``None`` for a record of another schema or a scenario without a lorebook. A ``grimoire.scenario/1``
    record whose ``lorebook`` is not a pinned lorebook ref (a hand edit) raises ``ValueError``: selecting
    some other revision, or none, would silently change what the scenario's scenes know.
    """
    if schema != SCENARIO_SCHEMA or "lorebook" not in payload:
        return None
    ref = SourceRef.parse(payload["lorebook"])
    if ref.kind != "lorebook" or not ref.pinned:
        raise ValueError(f"a scenario's lorebook must be a pinned lorebook ref, not {ref}")
    return ref


@dataclass(frozen=True)
class Piece:
    """One imported text, its authored role and where it came from. ``chars`` is its length, never its text."""

    role: str
    field: str
    chars: int


def _pieces_of_book(book: Lorebook | None, prefix: str) -> list[Piece]:
    """A lorebook's description and each entry (its content, with its name and comment) are lore."""
    if book is None:
        return []
    found = []
    if book.description and book.description.strip():
        found.append(Piece("lorebook_content", f"{prefix}.description", len(book.description)))
    found += [Piece("lorebook_content", f"{prefix}.entries[{entry.index}]", len(_entry_text(entry)))
              for entry in book.entries]
    return found


def _entry_text(entry: LoreEntry) -> str:
    """An entry's content with its ``name`` and ``comment`` settings, as one lore text."""
    extra = [entry.settings[key] for key in ("name", "comment") if isinstance(entry.settings.get(key), str)]
    return "\n".join([*extra, entry.content])


def pieces(card: ParsedCard, separate: Lorebook | None = None) -> tuple[Piece, ...]:
    """Every imported text with its role, so a preview shows greeting, premise, lore, instruction, voice
    and description apart. Nothing is ever an accepted event."""
    found: list[Piece] = []
    if isinstance(card, CharacterCard):
        for role, field, text in (
                ("authored_description", "data.description", card.description),
                ("authored_style", "data.personality", card.personality),
                ("scenario_instruction", "data.scenario", card.scenario),
                ("authored_voice", "data.mes_example", card.example_messages),
                ("private_instruction", "data.system_prompt", card.system_prompt),
                ("private_instruction", "data.post_history_instructions", card.post_history_instructions),
                ("creator_note", "data.creator_notes", card.authorship.creator_notes or "")):
            if text.strip():
                found.append(Piece(role, field, len(text)))
        for greeting in card.greetings:
            found.append(Piece("opening_greeting", f"greeting:{greeting.greeting_id}", len(greeting.text)))
        found += _pieces_of_book(card.lorebook, "data.character_book")
    elif isinstance(card, ScenarioCard):
        path = card.paths.get
        if card.premise.strip():
            found.append(Piece("scenario_premise", path("premise", "premise"), len(card.premise)))
        for opening in card.openings:
            found.append(Piece("scenario_opening", f"opening:{opening.greeting_id}", len(opening.text)))
        if card.instructions.strip():
            found.append(Piece("private_instruction", path("instructions", "instructions"), len(card.instructions)))
        for name, text in card.texts.items():
            found.append(Piece(_SCENARIO_TEXT_ROLES.get(name, "authored_description"),
                               path(f"texts.{name}", f"texts.{name}"), len(text)))
        if card.authorship.creator_notes and card.authorship.creator_notes.strip():
            found.append(Piece("creator_note", path("creator_notes", "creator_notes"),
                               len(card.authorship.creator_notes)))
        found += _pieces_of_book(card.lorebook, path("lorebook", "lorebook"))
    elif isinstance(card, LorebookCard):
        found += _pieces_of_book(card.lorebook, "data")
    found += _pieces_of_book(separate, "lorebook_file.data")
    assert all(piece.role in ROLES for piece in found)
    return tuple(found)


def mention_texts(card: ParsedCard, separate: Lorebook | None = None) -> dict[str, str]:
    """The texts a link suggestion may scan, keyed by field (the same keys ``pieces`` uses)."""
    texts: dict[str, str] = {}
    books: list[tuple[str, Lorebook | None]] = [("lorebook_file.data", separate)]
    if isinstance(card, CharacterCard):
        texts.update({"data.description": card.description, "data.personality": card.personality,
                      "data.scenario": card.scenario})
        texts.update({f"greeting:{greeting.greeting_id}": greeting.text for greeting in card.greetings})
        books.append(("data.character_book", card.lorebook))
    elif isinstance(card, ScenarioCard):
        path = card.paths.get
        texts[path("premise", "premise")] = card.premise
        texts.update({f"opening:{opening.greeting_id}": opening.text for opening in card.openings})
        texts.update({path(f"texts.{name}", f"texts.{name}"): text for name, text in card.texts.items()})
        books.append((path("lorebook", "lorebook"), card.lorebook))
    else:
        books.append(("data", card.lorebook))
    for prefix, book in books:
        if book is not None:
            if book.description:
                texts[f"{prefix}.description"] = book.description
            texts.update({f"{prefix}.entries[{entry.index}]": _entry_text(entry) for entry in book.entries})
    return {field: text for field, text in texts.items() if text.strip()}
