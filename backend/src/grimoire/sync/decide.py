"""The per-path decision table and base derivation (docs/SYNC.md). Pure: no files, clocks or randomness."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from .model import FIRST, IMMUTABLE_KINDS, NONE, PROPAGATED_DELETIONS, UNCERTAIN, Base, Decision, Side

AGREED = Decision("agreed")


def _conflict(code: str) -> Decision:
    return Decision("conflict", code)


def _copy(origin: str) -> Decision:
    return Decision("copy", origin=origin)  # type: ignore[arg-type]


def decide(kind: str, base: Base, left: Side, right: Side) -> Decision:
    """Rows 1-14 of the decision table, plus the immutable-revision override and (028) row 9's
    propagated deletion.

    ``kind`` is the canonical record kind (``syncable``); ``definition`` and ``knowledge`` records
    are immutable (create-only).
    Swapping ``left`` and ``right`` mirrors the decision.
    """
    if "blocked" in (left.state, right.state):                                     # 1
        return _conflict("blocked")
    if left.state == right.state == "absent" or (
            left.state == right.state == "present" and left.etag == right.etag):     # 2
        return AGREED
    one_absent = "absent" in (left.state, right.state)
    present = "left" if right.state == "absent" else "right"
    if base.kind == "uncertain":                                                   # 3
        return _conflict("missing_ancestor")
    if base.kind in ("first", "none"):
        if one_absent:                                                             # 4, 6
            return _copy(present)
        if base.kind == "first" and kind not in IMMUTABLE_KINDS:                          # 5
            left_descends = left.previous == right.etag
            right_descends = right.previous == left.etag
            if left_descends != right_descends:
                return _copy("left" if left_descends else "right")
        return _conflict("missing_ancestor")                                       # 5, 7
    if base.kind == "agreed":
        unchanged = {side for side, value in (("left", left), ("right", right))
                     if value.state == "present" and value.etag == base.etag}
        if unchanged:
            other = "right" if "left" in unchanged else "left"
            if (right if other == "right" else left).state == "absent":            # 9
                # 028: the other device deleted exactly the agreed version: delete it here too
                # (kept as a hold), except for the families that never delete
                return Decision("delete", origin=other) if kind in PROPAGATED_DELETIONS else _conflict("deleted")  # type: ignore[arg-type]
            if kind in IMMUTABLE_KINDS:                                               # 8 (immutable)
                return _conflict("revision_modified")
            return _copy(other)                                                    # 8
        if one_absent:                                                             # 10
            return _conflict("edit_delete")
        return _conflict("concurrent")                                             # 11
    # base.kind == "deleted"
    if one_absent:
        survivor = left if present == "left" else right
        # a blob's name is its content hash: the same blob again is never an outdated version, so
        # it is a create-only copy like any new file (row 13), and its tombstone never blocks it
        if survivor.etag == base.etag and kind != "blob":                          # 12
            return _conflict("resurrected")
        return _copy(present)                                                      # 13
    return _conflict("concurrent")                                                 # 14


def archive_candidate(path: str, left: ArchiveLike, right: ArchiveLike) -> Base:
    """Step 1 of base derivation: trusted only where both devices' own archives agree."""
    if left.state == right.state == "missing":
        return FIRST
    if left.state != "valid" or right.state != "valid":
        return UNCERTAIN
    mine, theirs = left.entries.get(path), right.entries.get(path)
    if mine is None and theirs is None:
        return NONE
    if mine != theirs or mine is None:
        return UNCERTAIN
    return Base(mine[0], mine[1])  # type: ignore[arg-type]


def derive_base(path: str, archives: tuple[ArchiveLike, ArchiveLike], pendings: tuple[PendingLike, PendingLike],
                current: tuple[str | None, str | None]) -> Base:
    """The base for ``path``: the archive candidate, overridden by unfinished-sync markers.

    A pending entry says "a sync published ``to`` here; the peer held ``to``". If every pending
    entry for the path names the same ``to`` and each device that has one still holds it, the
    copy verifiably landed: ``agreed to``. If both archives already agree on that ``to`` (archives
    are written only after verification), the marker is stale and the archive base stands.
    Anything else (a revert, an edit racing the copy, disagreeing entries, an unreadable marker)
    is ``uncertain``. A deletion's entry (028, ``"to": null``) works the same way: when every entry
    for the path deletes the same ``from`` and each device holding one is still absent, the base
    is ``deleted from``, the tombstone an uninterrupted sync records.
    """
    candidate = archive_candidate(path, *archives)
    entries = []
    for pending, etag in zip(pendings, current):
        if pending.state == "ignored":
            return UNCERTAIN
        entry = pending.entries.get(path)
        if entry is not None:
            entries.append((entry, etag))
    if entries:
        removals = {entry.get("from") for entry, _ in entries if _is_removal(entry)}
        if removals:  # 028: a sync or resolve deleted ``from`` here, and the peer was absent
            removed = next(iter(removals))
            if candidate == Base("deleted", removed):
                return candidate  # both archives already record the tombstone: the marker is stale
            if len(removals) == 1 and all(_is_removal(entry) and etag is None for entry, etag in entries):
                return Base("deleted", removed)  # the deletion verifiably landed: absent wherever marked
            return UNCERTAIN  # a restore, a re-creation or disagreeing entries
        targets = {entry.get("to") for entry, _ in entries}
        to = next(iter(targets)) if len(targets) == 1 else None
        if to is not None and candidate == Base("agreed", to) and all(entry.get("base") != to for entry, _ in entries):
            # a marker left by a crash after both archives agreed on its copy: stale. Not when the
            # archives already said ``to`` when it was written (a copy back to an older agreed version):
            # then they have not moved, and the marker still guards a restore.
            return candidate
        if len(targets) == 1 and None not in targets and all(entry.get("to") == etag for entry, etag in entries):
            return Base("agreed", targets.pop())
        return UNCERTAIN
    return candidate


def _is_removal(entry: Mapping[str, object]) -> bool:
    """A pending entry that records a deletion (028): ``{"from": E, "to": null}``, never ``{"uncertain"}``."""
    return "to" in entry and entry["to"] is None and entry.get("from") is not None


class ArchiveLike(Protocol):  # documents.ArchiveView
    state: str
    entries: Mapping[str, tuple[str, str]]


class PendingLike(Protocol):  # documents.PendingView
    state: str
    entries: Mapping[str, Mapping[str, object]]
