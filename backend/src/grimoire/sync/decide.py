"""The per-path decision table and base derivation (docs/SYNC.md). Pure: no files, clocks or randomness."""

from __future__ import annotations

from collections.abc import Mapping

from .model import FIRST, NONE, UNCERTAIN, Base, Decision, Side

AGREED = Decision("agreed")


def _conflict(code: str) -> Decision:
    return Decision("conflict", code)


def _copy(origin: str) -> Decision:
    return Decision("copy", origin=origin)  # type: ignore[arg-type]


def decide(kind: str, base: Base, left: Side, right: Side) -> Decision:
    """Rows 1-14 of the decision table, plus the immutable-revision override.

    ``kind`` is the canonical record kind (``definition``, ``manifest`` or ``instance``).
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
        if base.kind == "first" and kind != "definition":                          # 5
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
                return _conflict("deleted")
            if kind == "definition":                                               # 8 (immutable)
                return _conflict("revision_modified")
            return _copy(other)                                                    # 8
        if one_absent:                                                             # 10
            return _conflict("edit_delete")
        return _conflict("concurrent")                                             # 11
    # base.kind == "deleted"
    if one_absent:
        survivor = left if present == "left" else right
        if survivor.etag == base.etag:                                             # 12
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
    copy verifiably landed: ``agreed to``. Anything else (a revert, an edit racing the copy,
    disagreeing entries, an unreadable marker) is ``uncertain``.
    """
    entries = []
    for pending, etag in zip(pendings, current):
        if pending.state == "ignored":
            return UNCERTAIN
        entry = pending.entries.get(path)
        if entry is not None:
            entries.append((entry, etag))
    if entries:
        targets = {entry.get("to") for entry, _ in entries}
        if len(targets) == 1 and None not in targets and all(entry.get("to") == etag for entry, etag in entries):
            return Base("agreed", targets.pop())
        return UNCERTAIN
    return archive_candidate(path, *archives)


class ArchiveLike:  # structural: documents.ArchiveView
    state: str
    entries: Mapping[str, tuple[str, str]]


class PendingLike:  # structural: documents.PendingView
    state: str
    entries: Mapping[str, Mapping[str, object]]
