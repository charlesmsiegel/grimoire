"""Lore from a frozen view's bound ``lorebook`` and ``scenario`` sources (031), for any audience.

Shared by the actor packer, which keeps only ``common`` entries, and the director's lore pack,
which may keep ``director`` entries too. Who may read which audience is the caller's decision;
this module only reads the selected revisions, triggers entries on the texts it is given and
reports a source it could not read.
"""

from __future__ import annotations

import copy
import itertools
from collections.abc import Callable, Mapping

from grimoire.core.identity import SourceRef
from grimoire.core.index import Definition
from grimoire.core.snapshot import EffectiveSnapshot

from .model import MAX_ENTRIES, ContextError, EntryReader, LoreEntry, Origin, SourceProblem
from .pack import Candidate
from .trigger import SceneWords

#: A raw selected definition -> its stored record schema and payload, or ``None`` when it has none.
Unpack = Callable[[Definition], "tuple[str | None, Mapping[str, object]] | None"]
#: The only roles read for lore. They bind no actor, so their content can only be lore, via a reader.
LORE_ROLES = frozenset({"lorebook", "scenario"})
#: A reader may not claim an entity schema: entities are composed (022), never read raw for lore.
ENTITY_SCHEMA_FAMILY = "grimoire.entity/"


def check_readers(readers: object) -> None:
    if not isinstance(readers, Mapping) or not all(type(k) is str and callable(v) for k, v in readers.items()):
        raise ContextError("readers must map a record schema to a callable", "invalid_readers")
    if any(schema.startswith(ENTITY_SCHEMA_FAMILY) for schema in readers):
        raise ContextError("an entity schema is composed, never read for lore", "invalid_readers")


def _plain(value: object) -> object:
    """A fresh, mutable copy: mappings become dicts and arrays lists (the store's are read-only)."""
    if isinstance(value, Mapping):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(child) for child in value]
    return copy.deepcopy(value)


def _entries(reader: EntryReader, payload: Mapping[str, object]
             ) -> tuple[tuple[LoreEntry, ...], tuple[str, str] | None]:
    """The reader's entries, or none and ``(code, detail)`` when the source must contribute nothing.

    The reader gets a fresh plain copy of the payload (dicts and lists, never the store's read-only
    proxies), so it cannot change the loaded view it came from. A payload the reader cannot interpret
    is that source's problem, never the turn's: ``ValueError`` (``ContextError`` from an invalid
    ``LoreEntry`` is one), ``LookupError``, and the ``TypeError`` and ``AttributeError`` that malformed
    JSON shapes raise are caught, so a reader's own typo is reported the same way, every turn. Any
    other exception propagates. One bad entry blanks the whole source: a reader should skip, not
    raise on, an entry it cannot build.
    """
    try:
        copied = _plain(payload)
    except RecursionError:
        return (), ("unreadable_entries", "too_deep")
    except (TypeError, copy.Error):  # a leaf that cannot be copied is not record data
        return (), ("unreadable_entries", "uncopyable")
    try:
        entries = tuple(itertools.islice(reader(copied), MAX_ENTRIES + 1))
    except (ValueError, TypeError, LookupError, AttributeError) as error:
        detail = getattr(error, "reason", None)
        return (), ("unreadable_entries", detail if type(detail) is str else type(error).__name__)
    if not all(isinstance(entry, LoreEntry) for entry in entries):
        return (), ("unreadable_entries", "not_lore_entry")
    if len(entries) > MAX_ENTRIES:
        return (), ("too_many_entries", f">{MAX_ENTRIES}")
    if len({entry.entry_id for entry in entries}) != len(entries):
        return (), ("unreadable_entries", "duplicate_entry")
    return entries, None


def lore_problems(snapshot: EffectiveSnapshot) -> list[SourceProblem]:
    """Broken lore bindings and their drift notices: 022's reason or notice, per source."""
    found: list[SourceProblem] = []
    for resolution in snapshot.sources():
        if resolution.source.role not in LORE_ROLES:
            continue
        if resolution.resolved is None:
            reason = resolution.broken.reason if resolution.broken is not None else None
            found.append(SourceProblem("missing_source", resolution.source.ref, reason))
        found += [SourceProblem(notice.code, resolution.source.ref, notice.detail or None)
                  for notice in resolution.notices]
    return found


def lore(snapshot: EffectiveSnapshot, scene: SceneWords, readers: Mapping[str, EntryReader],
         unpack: Unpack | None, audiences: frozenset[str]
         ) -> tuple[list[Candidate], list[tuple[SourceRef, int]], list[SourceProblem]]:
    """Lore candidates of ``audiences`` from every bound lore source, the quiet ones counted per source.

    An entry of another audience is skipped without a count or a trace: what a reader may not see
    is not reported to it. Each candidate carries its entry's audience, so a renderer can tell the
    director's secrets from what anyone in the setting may know.
    """
    found: list[Candidate] = []
    untriggered: list[tuple[SourceRef, int]] = []
    problems: list[SourceProblem] = []
    read: set[SourceRef] = set()
    for resolution in snapshot.sources():
        role, pinned = resolution.source.role, resolution.resolved
        if role not in LORE_ROLES or pinned is None or pinned in read:
            continue  # a revision selected under both lore roles is read once
        read.add(pinned)
        raw = snapshot.resolve(pinned, role)  # the selected leaf revision itself, never an ancestor
        unpacked = unpack(raw) if unpack is not None else None
        if unpacked is not None and not (isinstance(unpacked, tuple) and len(unpacked) == 2
                                         and isinstance(unpacked[1], Mapping)):
            problems.append(SourceProblem("unreadable_entries", pinned, "invalid_unpack"))
            continue
        schema, payload = unpacked if unpacked is not None else (None, {})
        reader = readers.get(schema) if isinstance(schema, str) else None
        if reader is None:
            problems.append(SourceProblem("no_entry_reader", pinned, schema if isinstance(schema, str) else None))
            continue
        entries, refused = _entries(reader, payload)
        if refused is not None:
            problems.append(SourceProblem(refused[0], pinned, refused[1]))
            continue
        scene.prime(sequence for entry in entries if entry.audience in audiences and not entry.constant
                    for sequence in entry.key_sequences)  # one batch per source: a pass per key length
        quiet = 0
        for entry in entries:
            if entry.audience not in audiences:
                continue  # withheld, and not reported: what this reader may not see is the director's
            if entry.constant:
                rank: tuple[object, ...] = (0, 0, entry.order, str(pinned), entry.entry_id)
            else:
                hits = scene.count(entry.key_sequences)
                if not hits:
                    quiet += 1
                    continue
                rank = (1, -hits, entry.order, str(pinned), entry.entry_id)
            found.append(Candidate("lore", rank, entry.text, Origin("lore", pinned, name=entry.entry_id),
                                   audience=entry.audience))
        if quiet:
            untriggered.append((pinned, quiet))
    return found, untriggered, problems


__all__ = ["ENTITY_SCHEMA_FAMILY", "LORE_ROLES", "Unpack", "check_readers", "lore", "lore_problems"]
