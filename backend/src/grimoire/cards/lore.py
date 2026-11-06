"""031 lore readers for imported card records (037): ``grimoire.lorebook/1`` and ``grimoire.scenario/1``.

A reader turns a stored record's payload into 031 ``LoreEntry`` values; ``grimoire.contextstore``
uses ``LORE_READERS`` by default, so a bound card lorebook's entries trigger like any lore. A
scenario record yields none: its embedded lorebook is its own record, which a chat's snapshot selects
with the scenario (``grimoire.effective``).

**Audience.** A card lorebook has no per-entry audience. As in SillyTavern, a triggered entry is
known to everyone in the scene, so every entry is ``common``: it reaches each NPC's pack and the
director's lore alike, and is never director-only or one NPC's secret.

**What is read**, as far as 031's trigger model goes:
- ``keys`` (primary keys) and ``constant``;
- ``secondary_keys`` when ``selective`` is true, with SillyTavern's ``extensions.selectiveLogic``
  (0 AND ANY, 1 NOT ALL, 2 NOT ANY, 3 AND ALL; anything else is AND ANY, the V3 meaning);
- ``insertion_order`` as 031's ``order`` (lowest first), rounded and clamped to ±``MAX_ORDER``.

**What is ignored** (031 has no place for it): ``priority`` (031's budget sheds by its own rank:
constant first, then key hits, then order), ``position`` (one lore lane), ``case_sensitive`` (031
always casefolds), ``name`` and ``comment`` (labels, not lore), ``id`` (cards may repeat it), other
``extensions`` (probability, groups, depth, sticky, cooldown, delay, match_whole_words: 031 always
matches whole words) and the book's ``scan_depth``, ``token_budget`` and ``recursive_scanning``
(031's budget governs, and lore never triggers lore).

**What is skipped**, one entry at a time, because one entry 031 refuses blanks its whole source.
Each skip is handed over as a ``common`` ``SkippedEntry`` (so the pack reports ``skipped_entry``,
naming the entry and the reason, never its text): a ``use_regex`` entry that is not constant
(``use_regex``: 031 matches words, not patterns); content that is blank, unsafe or over
``MAX_ENTRY_TEXT`` after ``\\r\\n`` and ``\\r`` become ``\\n`` (``content``); a non-constant entry
left with no usable key (``keys``); and a selective entry with any secondary key 031 cannot use, or
more than ``MAX_KEYS`` of them, or one the importer could not read and dropped (the entry's
``secondary_keys_lost``) (``secondary_keys``): secondary keys are all or nothing, since dropping
one would make an AND ALL or NOT ANY entry fire where its author's logic would not. A disabled entry
is left out silently. A bad primary key is dropped, which can only make its entry fire less, a
repeated one kept once, and only the first ``MAX_KEYS`` kept. Entries past ``MAX_ENTRIES``, skips
included, are left out. An entry's id is its place in the record (``entry-<n>``), never the card's
own ``id``.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping

from grimoire.context import (
    MAX_ENTRIES, MAX_ENTRY_TEXT, MAX_KEYS, MAX_ORDER, ContextError, EntryReader, LoreEntry, SkippedEntry, safe_text,
    valid_key,
)

from .records import LOREBOOK_SCHEMA, SCENARIO_SCHEMA

#: the audience of every card lore entry: anyone in the scene may know it (see the module docstring)
CARD_LORE_AUDIENCE = "common"
#: SillyTavern's ``selectiveLogic`` values, as 031's ``secondary_logic``
_SELECTIVE_LOGIC = {0: "and_any", 1: "not_all", 2: "not_any", 3: "and_all"}


def _keys(value: object) -> tuple[str, ...]:
    """The usable keys of ``value``, trimmed, in order, once each, at most ``MAX_KEYS``."""
    if not isinstance(value, (list, tuple)):
        return ()
    found = dict.fromkeys(key.strip() for key in value if type(key) is str and valid_key(key.strip()))
    return tuple(found)[:MAX_KEYS]


def _all_keys(value: object) -> tuple[str, ...] | None:
    """Every key of ``value``, trimmed and once each, or ``None`` when one cannot be used or there are too
    many. Secondary keys are all or nothing: dropping one would make an AND ALL or NOT ANY entry fire
    where its author's logic would not."""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or not all(type(key) is str and valid_key(key.strip()) for key in value):
        return None
    found = tuple(dict.fromkeys(key.strip() for key in value))
    return found if len(found) <= MAX_KEYS else None


def _order(value: object) -> int:
    # an int is clamped exactly: one of hundreds of digits, which JSON allows, would overflow the
    # float that ``math.isfinite`` converts it to
    if type(value) is int:
        return max(-MAX_ORDER, min(MAX_ORDER, value))
    if type(value) is not float or not math.isfinite(value):
        return 0
    return max(-MAX_ORDER, min(MAX_ORDER, round(value)))


def _entry(position: int, raw: object) -> LoreEntry | SkippedEntry | None:
    """One stored entry as a ``common`` lore entry, ``None`` for a disabled one, or why it is skipped."""
    entry_id = f"entry-{position}"

    def skip(reason: str) -> SkippedEntry:
        return SkippedEntry(entry_id, reason, CARD_LORE_AUDIENCE)

    if not isinstance(raw, Mapping):
        return skip("malformed")
    if raw.get("enabled", True) is False:
        return None  # switched off by its author: nothing to report
    settings = raw.get("settings")
    settings = settings if isinstance(settings, Mapping) else {}
    constant = settings.get("constant") is True
    if settings.get("use_regex") is True and not constant:
        return skip("use_regex")
    content = raw.get("content")
    if type(content) is not str:
        return skip("content")
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    if not safe_text(content, MAX_ENTRY_TEXT):
        return skip("content")
    keys = _keys(raw.get("keys"))
    if not keys and not constant:
        return skip("keys")
    secondary: tuple[str, ...] | None = ()
    logic = "and_any"
    if settings.get("selective") is True:
        # the importer drops a secondary key it cannot read and marks the entry, so a dropped key
        # is skipped here rather than letting the entry fire on fewer conditions than written
        secondary = None if raw.get("secondary_keys_lost") is True else _all_keys(raw.get("secondary_keys"))
        if secondary is None:
            return skip("secondary_keys")
        extensions = raw.get("extensions")
        chosen = extensions.get("selectiveLogic") if isinstance(extensions, Mapping) else None
        logic = _SELECTIVE_LOGIC.get(chosen, "and_any") if type(chosen) is int else "and_any"
    try:
        return LoreEntry(entry_id, content, keys, constant, CARD_LORE_AUDIENCE,
                         _order(settings.get("insertion_order")), secondary, logic)
    except ContextError:  # a check this reader did not anticipate: skip the entry, never blank the source
        return skip("invalid")


def _book(book: Mapping[str, object]) -> Iterator[LoreEntry | SkippedEntry]:
    entries = book.get("entries")
    if not isinstance(entries, (list, tuple)):
        return
    kept = 0
    for position, raw in enumerate(entries):
        entry = _entry(position, raw)
        if entry is not None:
            yield entry
            kept += 1
            if kept == MAX_ENTRIES:  # 031 counts a skipped entry toward its limit too
                return


def read_lorebook(payload: Mapping[str, object]) -> list[LoreEntry | SkippedEntry]:
    """``grimoire.lorebook/1``: a character card's embedded lorebook, a separate lorebook file, or a
    standalone V3 lorebook, as the importer stored it."""
    return list(_book(payload))


def read_scenario(payload: Mapping[str, object]) -> list[LoreEntry]:
    """``grimoire.scenario/1``: no entries, and no ``no_entry_reader`` problem for a bound scenario.

    The importer stores a scenario's embedded lorebook as its own ``grimoire.lorebook/1`` record and
    keeps only its pinned ref (``scenario_lorebook``); a chat's snapshot selects that record with the
    scenario (``grimoire.effective``), so its entries are read once, by ``read_lorebook``. The scenario's premise,
    openings and instructions are prompts for play (035), never lore.
    """
    return []


#: The readers ``grimoire.contextstore`` uses when a caller passes none.
LORE_READERS: Mapping[str, EntryReader] = {LOREBOOK_SCHEMA: read_lorebook, SCENARIO_SCHEMA: read_scenario}

__all__ = ["CARD_LORE_AUDIENCE", "LORE_READERS", "read_lorebook", "read_scenario"]
