"""Parsed card values (Sprint 037): what a card says, kept apart by authored role.

Nothing here is a fact about a fiction. A card's description, greetings, lore and instructions are
authored material: the importer stores them as source records, and no value in this module is an
accepted event, a belief or evidence. Unknown fields are kept verbatim (``extras``), and every field
that could not be used is reported as a ``Loss`` rather than dropped silently.
"""

from __future__ import annotations

import re

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

#: Character Card V3 (the ``spec`` value of a V3 card)
CCV3 = "chara_card_v3"
#: Character Card V3's standalone lorebook export
LOREBOOK_V3 = "lorebook_v3"
#: Grimoire's own scenario-only document
SCENARIO_DOC = "grimoire.scenario-card/1"
FORMATS = frozenset({CCV3, LOREBOOK_V3, SCENARIO_DOC})

#: what a card import becomes
CHARACTER, SCENARIO, LOREBOOK = "character", "scenario", "lorebook"

MAX_NAME = 256
#: opaque JSON kept from a card (extensions, unknown fields) may nest at most this deep
MAX_KEPT_DEPTH = 48


class CardError(ValueError):
    """A card this slice cannot import; nothing was staged. ``reason`` is machine-readable.

    Reasons: ``unsupported_format`` (``detail`` names it: ``chara_card_v2``, ``png``, ``charx``,
    ``unknown``), ``invalid_card`` (``detail`` is the path of the offending field), ``too_large``,
    ``invalid_json``. Messages never echo card text.
    """

    def __init__(self, message: str, reason: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.detail = detail

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason, self.detail))


@dataclass(frozen=True)
class Loss:
    """A field that was not imported as typed data (it stays in the preserved original bytes).

    ``path`` locates it (``data.tags[2]``); it never echoes the field's text. Reasons:
    ``wrong_type``, ``too_deep``, ``rounded_number``, ``not_fetched`` (a remote asset),
    ``unavailable`` (an embedded or default asset this slice cannot read), ``not_section`` (kept, but
    not as a 022 section), ``not_applicable`` (a field the imported kind has no place for),
    ``too_long`` (a lorebook name over ``MAX_NAME``), ``blank`` (an empty greeting).
    ``rounded_number`` (a decimal a double cannot hold, imported as the nearest double) is reported
    by the importer, which reads the bytes.
    """

    path: str
    reason: str


def _empty() -> Mapping[str, object]:
    return MappingProxyType({})


@dataclass(frozen=True)
class Greeting:
    """One opening line. Never canon: a greeting is how a chat *may* start, not what happened."""

    greeting_id: str
    text: str
    group_only: bool = False


@dataclass(frozen=True)
class LoreEntry:
    """One lorebook entry: trigger keys and authored content. Trigger semantics are not interpreted.

    ``settings`` holds the entry's known V3 settings that were well typed (``constant``,
    ``selective``, ``insertion_order``, ``priority``, ``case_sensitive``, ``use_regex``,
    ``position``, ``id``, ``name``, ``comment``); ``extensions`` and unknown keys (``extras``) are
    verbatim.
    """

    keys: tuple[str, ...]
    content: str
    enabled: bool = True
    secondary_keys: tuple[str, ...] = ()
    settings: Mapping[str, object] = field(default_factory=_empty)
    extensions: Mapping[str, object] = field(default_factory=_empty)
    extras: Mapping[str, object] = field(default_factory=_empty)
    #: the entry's place in the book's ``entries`` array (from 0); preview paths use it
    index: int = 0
    #: a secondary key could not be read and was dropped (a loss was reported): ``secondary_keys``
    #: is then narrower than the author wrote, so 031 skips the entry when it is selective
    secondary_keys_lost: bool = False


@dataclass(frozen=True)
class Lorebook:
    name: str | None
    description: str | None
    entries: tuple[LoreEntry, ...]
    settings: Mapping[str, object] = field(default_factory=_empty)
    extensions: Mapping[str, object] = field(default_factory=_empty)
    extras: Mapping[str, object] = field(default_factory=_empty)


@dataclass(frozen=True)
class Asset:
    """A V3 ``assets`` entry, recorded as written. Nothing is fetched or decoded."""

    type: str
    uri: str
    name: str | None
    ext: str | None
    #: unknown keys of the entry, verbatim
    extras: Mapping[str, object] = field(default_factory=_empty)
    #: the entry's place in the card's ``assets`` array (from 0), so a loss names the right entry
    index: int = 0


@dataclass(frozen=True)
class Authorship:
    """Who made the card and its own version and dates, kept as written (license/privacy evidence)."""

    creator: str | None = None
    character_version: str | None = None
    creator_notes: str | None = None
    source: tuple[str, ...] = ()
    #: CCV3 types these as numbers (Unix seconds); kept as written
    creation_date: int | float | None = None
    modification_date: int | float | None = None


@dataclass(frozen=True)
class CharacterCard:
    """A Character Card V3 character. Each text keeps the role its field gives it."""

    format: str
    spec_version: str
    name: str
    description: str = ""
    personality: str = ""
    scenario: str = ""
    example_messages: str = ""
    greetings: tuple[Greeting, ...] = ()
    system_prompt: str = ""
    post_history_instructions: str = ""
    nickname: str | None = None
    tags: tuple[str, ...] = ()
    authorship: Authorship = Authorship()
    lorebook: Lorebook | None = None
    assets: tuple[Asset, ...] = ()
    extensions: Mapping[str, object] = field(default_factory=_empty)
    #: unknown keys of ``data`` and of the top level (``data.<key>`` / ``<key>``), verbatim
    extras: Mapping[str, object] = field(default_factory=_empty)
    losses: tuple[Loss, ...] = ()


@dataclass(frozen=True)
class CastRef:
    """One initial cast member a scenario names: a source reference text or a bare name, as written.

    A name is never resolved to an identity; it can only produce suggestions.
    """

    ref: str | None = None
    name: str | None = None
    #: the entry's other keys, verbatim
    extras: Mapping[str, object] = field(default_factory=_empty)


@dataclass(frozen=True)
class ScenarioCard:
    """A scenario-only import: a premise and openings, never a fake ``Agent`` character."""

    format: str
    title: str
    premise: str = ""
    openings: tuple[Greeting, ...] = ()
    instructions: str = ""
    #: other authored texts (``setting``: a V3 card's description imported as a scenario)
    texts: Mapping[str, str] = field(default_factory=_empty)  # type: ignore[assignment]
    cast: tuple[CastRef, ...] = ()
    tags: tuple[str, ...] = ()
    authorship: Authorship = Authorship()
    lorebook: Lorebook | None = None
    assets: tuple[Asset, ...] = ()
    extensions: Mapping[str, object] = field(default_factory=_empty)
    extras: Mapping[str, object] = field(default_factory=_empty)
    losses: tuple[Loss, ...] = ()
    #: where a piece came from in the document, when that differs from its name (a V3 card read
    #: as a scenario: ``premise`` -> ``data.scenario``); used for preview field paths
    paths: Mapping[str, str] = field(default_factory=_empty)  # type: ignore[assignment]


@dataclass(frozen=True)
class LorebookCard:
    """A standalone lorebook import (``lorebook_v3``)."""

    format: str
    lorebook: Lorebook
    extras: Mapping[str, object] = field(default_factory=_empty)
    losses: tuple[Loss, ...] = ()


ParsedCard = CharacterCard | ScenarioCard | LorebookCard


_SAFE_KEY = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,63}\Z")


def key_path(path: str, key: str, position: int) -> str:
    """The loss path of an unknown or kept key: the key itself when it is a short plain name, else
    ``{position}`` (its place among its mapping's keys, from 0), so a path never carries card text."""
    segment = key if _SAFE_KEY.match(key) else f"{{{position}}}"
    return f"{path}.{segment}" if path else segment
