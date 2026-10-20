"""Lazy immutable snapshots for one campaign read sweep, never held across mutations."""

from __future__ import annotations

from pathlib import Path

from . import _ledgers, _record_paths


class View:
    """One campaign's overlay context, for a read sweep that asks it per row.

    Every per-record resolver below turns on the same four facts: the
    campaign's own root, its world's root (a parse of campaign.md), its
    tombstones (deleted.json) and its detachments (detached.json). Asked row by
    row, a listing re-read those three files several times per row -- and
    re-resolved the store root for every path it built -- to learn something
    that cannot change between two rows of one listing. So a sweep builds one
    of these with `view()` and hands it to every row, and the listing costs a
    fixed handful of those reads however large the cast; a helper called alone
    builds its own and costs what it always did.

    Lazy, field by field, and that is what makes the second half true: passing
    a view never costs a read the helper would not have made, so `tagline` for
    a campaign that holds its own line still parses no campaign.md. The sets
    are frozen because one view is shared across rows, and a row that edited
    `gone` would change every later row's answer.

    A READ-sweep helper, and only that. It is a snapshot, so it must not live
    across a mutation -- a tombstone written mid-sweep is not in `gone` -- and
    it is never kept beyond the call that built it: no module state, nothing
    shared between requests, so there is no invalidation story to get wrong.
    That is why the writers here (`add_deleted`, `promote_image`, the creates
    and deletes) take none and keep reading the ledgers fresh.

    Plain properties over `None`, not `functools.cached_property`: on 3.11,
    this package's floor, that descriptor takes ONE lock per property for the
    whole class and holds it across the computation, so every request thread
    resolving any campaign's world root would queue behind whichever one was
    parsing a campaign.md. A view never crosses threads, so it needs no lock.
    """

    def __init__(self, cid: str) -> None:
        self.cid = cid
        self._croot: Path | None = None
        self._wroot: Path | None = None
        self._gone: frozenset[str] | None = None
        self._off: frozenset[str] | None = None

    @property
    def croot(self) -> Path:
        if self._croot is None:
            self._croot = _record_paths.croot_of(self.cid)
        return self._croot

    @property
    def wroot(self) -> Path:
        if self._wroot is None:
            self._wroot = _record_paths.wroot_of(self.cid)
        return self._wroot

    @property
    def gone(self) -> frozenset[str]:
        if self._gone is None:
            self._gone = frozenset(_ledgers.deleted(self.cid))
        return self._gone

    @property
    def off(self) -> frozenset[str]:
        if self._off is None:
            self._off = frozenset(_ledgers.detached(self.cid))
        return self._off


def view(cid: str) -> View:
    """A fresh `View` of `cid` for one read sweep. See `View` for its limits."""
    return View(cid)


def _view(cid: str, v: View | None) -> View:
    """`v` when it describes `cid`, else a fresh view.

    A view of a DIFFERENT campaign is refused rather than replaced: using it
    would resolve this campaign's records against another world and another
    campaign's tombstones, and replacing it quietly would hide the caller's
    bug behind a correct-looking answer."""
    if v is None:
        return View(cid)
    if v.cid != cid:
        raise ValueError(f"an overlay view of campaign {v.cid!r} cannot answer for {cid!r}")
    return v
