"""The kept versions a pair's syncs delivered to this device, and which of them it dismissed (028).

``.sync/peers/<peer>/keeps.json`` (``grimoire.sync.keeps/1``) records, per path in conflict, the
etags of the **peer's** versions a completed sync kept here, and the archive base the conflict
stands on. It is what makes ``dismiss`` safe: a file under ``.conflicts/`` may be a kept copy of
the peer's version or this device's own version that ``resolve`` held (perhaps its only copy
anywhere), and the slot names cannot tell them apart. Only an etag this record lists may be
dismissed, so a held version never is.

The record is rewritten only in a completed sync's last step (pending), after both archives, with
every conflict path whose keep this sync verified (written, or already standing in a slot) and
nothing else. A crash before that step leaves a delivered version unrecorded, which only refuses a
dismissal until the next sync records it: the safe direction. A path's entry is dropped once the
path is no longer in conflict or its archive base moved (``resolve`` drops it in its own commit),
so a dismissal never outlives its conflict: the same bytes in a later conflict are kept again.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from grimoire.store import LibraryStore, NameCollision, StoreError, etag
from grimoire.store.engine import MAX_CONFLICT_ATTEMPTS
from grimoire.store.layout import conflict_parts

from .bookkeeping import read_state
from .documents import KeepsView, KeptEntry, keeps_name, parse_keeps, render_keeps
from .model import Base

State = tuple[str, bytes, "str | None"]


def read_keeps(store: LibraryStore, peer: str, devices: tuple[str, str]) -> KeepsView:
    return parse_keeps(read_state(store, keeps_name(peer)), devices)


def base_token(candidate: Base) -> str | None:
    """The archive base a conflict stands on, as the record spells it; ``None`` when no archive
    agrees on one (a first sync, an ignored archive): nothing is recorded or honoured then."""
    if candidate.kind == "none":
        return "none"
    if candidate.kind in ("agreed", "deleted") and candidate.etag is not None:
        return f"{candidate.kind}:{candidate.etag}"
    return None


def archived_token(mine: tuple[str, str] | None, theirs: tuple[str, str] | None) -> str | None:
    """``base_token`` of the base the next sync will read from two archives' entries for one path."""
    if mine != theirs:
        return None
    return "none" if mine is None else f"{mine[0]}:{mine[1]}"


def next_keeps(view: KeepsView, peer: str, devices: tuple[str, str],
               opened: Mapping[str, tuple[str | None, str | None]]) -> State | None:
    """The record after a completed sync, or ``None`` if it does not change.

    ``opened`` maps every path still in conflict to its archive base token and, if this sync kept
    the peer's version here, that version's etag. An entry carries over only for a path still in
    conflict on the same base; an ignored record is replaced, since nothing in it was honoured."""
    old = view.entries if view.state == "valid" else {}
    entries: dict[str, KeptEntry] = {}
    for path, (base, delivered) in opened.items():
        if base is None:
            continue
        before = old.get(path)
        entry = before if before is not None and before.base == base else KeptEntry(base, frozenset())
        if delivered is not None:
            entry = KeptEntry(base, entry.delivered | {delivered}, entry.dismissed)
        if entry.delivered:
            entries[path] = entry
    if (view.state == "valid" and entries == dict(old)) or (view.state == "missing" and not entries):
        return None
    return keeps_name(peer), render_keeps(devices, entries), None if view.data is None else etag(view.data)


def without(view: KeepsView, peer: str, devices: tuple[str, str], paths: Iterable[str]) -> State | None:
    """The record without ``paths`` (conflicts ``resolve`` just ended), or ``None`` if it does not change."""
    paths = set(paths)
    if view.state != "valid" or not paths & set(view.entries):
        return None
    entries = {path: entry for path, entry in view.entries.items() if path not in paths}
    return keeps_name(peer), render_keeps(devices, entries), None if view.data is None else etag(view.data)


def kept_slots(store: LibraryStore, path: str, tag: str) -> tuple[str, ...]:
    """The kept-version files of ``path`` on this device that hold exactly the bytes ``tag`` names."""
    parts, found = tuple(path.split("/")), []
    for attempt in range(MAX_CONFLICT_ATTEMPTS):
        slot = "/".join(conflict_parts(parts, tag, attempt))
        try:
            data = store.read_exact(slot)
        except (NameCollision, StoreError, OSError):  # unreadable or a look-alike: never listed
            continue
        if data is not None and etag(data) == tag:
            found.append(slot)
    return tuple(found)


def peer_may_write(old: bytes | None, new: bytes, devices: tuple[str, str],
                   holds: Callable[[str, str], bool]) -> bool:
    """Whether a peer replaying a sync's last step may write ``new`` over ``old`` (028's transport).

    Exactly what ``next_keeps`` can produce: a valid record that dismisses nothing ``old`` did not on
    the same base (dismissing is this device's own decision, authorized by ``dismiss``), and whose
    newly delivered versions this device does hold under ``.conflicts`` for that path (``holds``)."""
    after, before = parse_keeps(new, devices), parse_keeps(old, devices)
    if after.state != "valid":
        return False
    previous = before.entries if before.state == "valid" else {}
    for path, entry in after.entries.items():
        prior = previous.get(path)
        same = prior is not None and prior.base == entry.base
        if not entry.dismissed <= (prior.dismissed if same else frozenset()):  # type: ignore[union-attr]
            return False
        if not all(holds(path, tag) for tag in entry.delivered - (prior.delivered if same else frozenset())):  # type: ignore[union-attr]
            return False
    return True
