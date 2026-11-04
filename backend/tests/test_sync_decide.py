"""The sync decision table, enumerated exhaustively; base derivation; archive and pending documents."""

from __future__ import annotations

import itertools

import pytest

from grimoire.store import etag
from grimoire.sync import Base, Decision, Side, decide, derive_base
from grimoire.sync.documents import (
    ArchiveView, PendingView, parse_archive, parse_device, parse_pending, render_archive, render_device,
    render_pending,
)
from grimoire.sync.model import FIRST, NONE, UNCERTAIN

E, X, Y = (etag(text) for text in (b"base", b"x", b"y"))
BASES = [FIRST, NONE, UNCERTAIN, Base("agreed", E), Base("deleted", E)]
SIDES = [Side.absent(), Side.present(E), Side.present(X), Side.present(Y), Side.present(X, previous=E),
         Side.present(E, previous=X), Side.present(Y, previous=X), Side.blocked("link")]
KINDS = ["definition", "manifest", "instance", "scope", "chat"]
COMBOS = list(itertools.product(KINDS, BASES, SIDES, SIDES))


def _oracle(kind, base, left, right):
    """The spec's table, written row by row (docs/SYNC.md)."""
    if "blocked" in (left.state, right.state):
        return Decision("conflict", "blocked")
    if left.state == right.state == "absent" or (left.state == right.state == "present" and left.etag == right.etag):
        return Decision("agreed")
    absent = [side for side, value in (("left", left), ("right", right)) if value.state == "absent"]
    present = "right" if absent == ["left"] else "left"
    if base.kind == "uncertain":
        return Decision("conflict", "missing_ancestor")
    if base.kind in ("first", "none") and absent:
        return Decision("copy", origin=present)
    if base.kind == "first":
        if kind != "definition":
            if left.previous == right.etag and right.previous != left.etag:
                return Decision("copy", origin="left")
            if right.previous == left.etag and left.previous != right.etag:
                return Decision("copy", origin="right")
        return Decision("conflict", "missing_ancestor")
    if base.kind == "none":
        return Decision("conflict", "missing_ancestor")
    if base.kind == "agreed":
        if left.etag == base.etag or right.etag == base.etag:
            changed = right if left.etag == base.etag else left
            origin = "right" if left.etag == base.etag else "left"
            if changed.state == "absent":
                return Decision("conflict", "deleted")
            return Decision("conflict", "revision_modified") if kind == "definition" else Decision("copy", origin=origin)
        return Decision("conflict", "edit_delete" if absent else "concurrent")
    if absent:
        survivor = right if absent == ["left"] else left
        return Decision("conflict", "resurrected") if survivor.etag == base.etag else Decision("copy", origin=present)
    return Decision("conflict", "concurrent")


@pytest.mark.parametrize("kind, base, left, right", COMBOS)
def test_every_combination_follows_the_table_and_is_mirror_symmetric(kind, base, left, right):
    decision = decide(kind, base, left, right)
    assert decision == _oracle(kind, base, left, right)
    assert decide(kind, base, right, left) == decision.mirrored()


def test_a_copy_only_ever_lands_on_an_absent_file_or_the_base_or_a_proven_ancestor():
    """The safety property behind the table, checked over every combination."""
    copies = 0
    for kind, base, left, right in COMBOS:
        decision = decide(kind, base, left, right)
        if decision.action != "copy":
            continue
        copies += 1
        origin, dest = (left, right) if decision.origin == "left" else (right, left)
        assert origin.state == "present" and base.kind != "uncertain"
        if dest.state == "absent":
            assert not (base.kind == "deleted" and origin.etag == base.etag)  # never resurrect
            continue
        assert kind != "definition"  # revisions are only ever created
        assert (base.kind == "agreed" and dest.etag == base.etag) or (
            base.kind == "first" and origin.previous == dest.etag)
    assert copies > 50


# -- base derivation --------------------------------------------------------------------

P = "campaigns/c1/instances/character/wren-a.json"
MISSING, IGNORED = ArchiveView("missing"), ArchiveView("ignored", reason="bad")
NO_PENDING = PendingView("missing")


def _archive(entry=None):
    return ArchiveView("valid", {P: entry} if entry else {})


def _pending(entry=None):
    return PendingView("valid", {P: entry} if entry else {})


@pytest.mark.parametrize("left, right, expected", [
    (MISSING, MISSING, FIRST),
    (_archive(), _archive(), NONE),
    (_archive(("agreed", E)), _archive(("agreed", E)), Base("agreed", E)),
    (_archive(("deleted", E)), _archive(("deleted", E)), Base("deleted", E)),
    (_archive(("agreed", E)), _archive(("agreed", X)), UNCERTAIN),  # a clone's archive: never trusted
    (_archive(("agreed", E)), _archive(), UNCERTAIN),
    (_archive(("agreed", E)), MISSING, UNCERTAIN),  # one device lost its archive
    (IGNORED, _archive(("agreed", E)), UNCERTAIN),
    (IGNORED, MISSING, UNCERTAIN),  # a damaged file is not "never synced"
])
def test_a_base_is_trusted_only_where_both_devices_records_agree(left, right, expected):
    assert derive_base(P, (left, right), (NO_PENDING, NO_PENDING), (E, E)) == expected


def test_pending_markers_make_an_unfinished_copy_agreed_or_uncertain():
    archives = (_archive(("agreed", E)), _archive(("agreed", E)))
    landed = _pending({"from": E, "to": X})
    assert derive_base(P, archives, (landed, NO_PENDING), (X, X)) == Base("agreed", X)
    assert derive_base(P, archives, (landed, NO_PENDING), (E, X)) == UNCERTAIN  # ABA: reverted to old bytes
    assert derive_base(P, archives, (landed, NO_PENDING), (Y, X)) == UNCERTAIN  # an edit racing the copy
    both = (landed, _pending({"from": E, "to": Y}))
    assert derive_base(P, archives, both, (X, Y)) == UNCERTAIN  # disagreeing markers
    assert derive_base(P, archives, (_pending({"uncertain": True}), NO_PENDING), (X, X)) == UNCERTAIN
    assert derive_base(P, archives, (PendingView("ignored"), NO_PENDING), (E, E)) == UNCERTAIN
    assert derive_base(P, archives, (_pending(), NO_PENDING), (E, X)) == Base("agreed", E)


# -- documents ---------------------------------------------------------------------------

PAIR = ("desktop", "phone")


def test_archive_and_pending_round_trip_and_damage_is_ignored():
    data = render_archive(PAIR, {P: ("agreed", E)})
    assert parse_archive(data, PAIR).entries == {P: ("agreed", E)}
    assert parse_archive(None, PAIR).state == "missing"
    damaged = [
        data.replace(E.encode(), X.encode()),  # check digest no longer matches
        data[:-5], b"[]", data.replace(b"grimoire.sync.archive/1", b"grimoire.sync.archive/2"),
        render_archive(("desktop", "tablet"), {P: ("agreed", E)}),
        render_archive(PAIR, {"notes.txt": ("agreed", E)}),
        render_archive(PAIR, {P: ("moved", E)}),
    ]
    for bad in damaged:
        view = parse_archive(bad, PAIR)
        assert view.state == "ignored" and view.reason and view.entries == {}
    pending = render_pending(PAIR, {P: {"from": None, "to": E}})
    assert parse_pending(pending, PAIR).entries[P] == {"from": None, "to": E}
    for bad in (render_pending(PAIR, {P: {"to": E}}), render_pending(PAIR, {P: {"uncertain": False}}),
                pending.replace(b"null", b'"x"')):
        assert parse_pending(bad, PAIR).state == "ignored"


def test_device_identity_documents_are_strict():
    assert parse_device(render_device("desktop")) == "desktop"
    for bad in (None, b"{", b'{"format": "grimoire.sync.device/1", "device": "Desk"}',
                b'{"format": "grimoire.sync.device/1", "device": "con"}',
                b'{"format": "grimoire.sync.device/2", "device": "desktop"}'):
        assert parse_device(bad) is None
    for bad in ("Desk", "con", "..", "a/b", ""):
        with pytest.raises(ValueError):
            render_device(bad)
