"""C1: a held-back path whose two devices differ is a named ``held`` conflict, never silent.

021's accepted events and wrap-ups, 023's holdings, and the 017 relations of a campaign with an
accepted event are held back until 028's merge rules exist. Nothing is copied, archived or kept for
them, but a divergence is reported (docs/SYNC.md, conflict code ``held``) and ``resolve`` refuses it.
"""

from __future__ import annotations

import shutil

import pytest

from grimoire.core import Moment
from grimoire.core.identity import HoldingsRef, RelationRef
from grimoire.graphstore import RelationLibrary
from grimoire.store import LibraryStore, ScopeRecord, engine, etag
from grimoire.sync import ResolveRefused, identify, resolve, sync, sync_until_quiet
from grimoire.sync.documents import parse_archive

from chat_fixtures import Clock
from effects_fixtures import C, INES, TAM, Handles
from effects_fixtures import seed as seed_effects
from store_helpers import OWNER, owner_handle
from sync_helpers import DESKTOP, PHONE, handles, make_pair, put
from test_sync_families import _accept, _effects_pair

HOLDINGS = "campaigns/c1/holdings/character/wren-a.json"


def _relations(store, tag):
    class Ids:
        n = 0

        def __call__(self, prefix):
            Ids.n += 1
            return f"{prefix}-{tag}-{Ids.n}"
    return RelationLibrary(store, owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER)


def _edit_both_and_add(desktop, phone):
    """Each device ends ``r-seed-2`` differently; the phone also adds a relation (the held2 repro)."""
    ref = RelationRef(C, "r-seed-2")
    for store, seq in ((desktop, 8), (phone, 9)):
        library = _relations(store, store.root.name)
        _, found = library.read(ref)
        library.end(ref, until=Moment(seq), provenance="director", expected=found).commit()
    _relations(phone, "phone").add(C, "knows", TAM, INES, since=Moment(2), provenance="director").commit()
    return f"campaigns/{C}/relations/r-seed-2.json", f"campaigns/{C}/relations/r-phone-1.json"


def _bytes(store, path):
    target = store.root / path
    return target.read_bytes() if target.exists() else None


def test_held_relation_edits_are_held_conflicts_not_silence(tmp_path):
    """held2 inverted: with an accepted event, both relation edits are reported, on every sync."""
    desktop, phone, keys, _ = _effects_pair(tmp_path)
    try:
        _accept(desktop, keys)
        seed_2, added = _edit_both_and_add(desktop, phone)
        before = {path: (_bytes(desktop, path), _bytes(phone, path)) for path in (seed_2, added)}
        report = sync_until_quiet(desktop, phone, handles())  # returns normally: held never raises
        assert report.conflicts != ()
        for path in (seed_2, added):
            assert report.conflict(path).code == "held"
            assert path in report.skipped[DESKTOP] or path in report.skipped[PHONE]
            assert (_bytes(desktop, path), _bytes(phone, path)) == before[path]  # nothing copied
        assert not [path for path, _ in report.copied if "/relations/" in path or "/accepted/" in path]
        assert desktop.conflicts() == () and phone.conflicts() == ()  # nothing kept for a held path
        again = sync(desktop, phone, handles())  # still reported: not converged until identical
        assert {again.conflict(path).code for path in (seed_2, added)} == {"held"}
        assert not again.wrote
    finally:
        desktop.close()
        phone.close()


def test_without_an_accepted_event_the_same_edits_are_an_ordinary_conflict(tmp_path):
    desktop, phone, _, _ = _effects_pair(tmp_path)
    try:
        seed_2, added = _edit_both_and_add(desktop, phone)
        report = sync(desktop, phone, handles())
        assert report.conflict(seed_2).code == "concurrent"
        assert (added, DESKTOP) in report.copied
    finally:
        desktop.close()
        phone.close()


def test_a_holdings_divergence_is_held_and_resolve_refuses_it(tmp_path):
    desktop, phone = make_pair(tmp_path)
    try:
        put(desktop, ScopeRecord(HoldingsRef("c1", "character", "wren-a"), "grimoire.holdings/1", {"coins": 3}), None)
        report = sync(desktop, phone, handles())
        assert report.conflict(HOLDINGS).code == "held" and HOLDINGS in report.skipped[DESKTOP]
        assert not (phone.root / HOLDINGS).exists() and phone.conflicts() == ()
        data = (desktop.root / HOLDINGS).read_bytes()
        with pytest.raises(ResolveRefused, match="held back until 028"):
            resolve(desktop, phone, HOLDINGS, etag(data), {DESKTOP: etag(data), PHONE: None}, handles())
        assert not (phone.root / HOLDINGS).exists()
        # its only exit: both devices hold identical bytes (here, by hand)
        (phone.root / HOLDINGS).parent.mkdir(parents=True)
        (phone.root / HOLDINGS).write_bytes(data)
        assert sync(desktop, phone, handles()).conflict(HOLDINGS) is None
    finally:
        desktop.close()
        phone.close()


def test_a_blocked_side_of_a_held_path_counts_as_differing(tmp_path):
    desktop, phone = make_pair(tmp_path)
    try:
        for store in (desktop, phone):
            put(store, ScopeRecord(HoldingsRef("c1", "character", "wren-a"), "grimoire.holdings/1", {"coins": 3}), None)
        assert sync(desktop, phone, handles()).conflict(HOLDINGS) is None  # identical: agreed, no conflict
        (phone.root / HOLDINGS).write_bytes(b"{not json")
        report = sync(desktop, phone, handles())
        assert report.conflict(HOLDINGS).code == "held"
        assert (phone.root / HOLDINGS).read_bytes() == b"{not json" and phone.conflicts() == ()
    finally:
        desktop.close()
        phone.close()


def test_identical_held_bytes_are_no_conflict_and_stay_recorded_as_agreed(tmp_path):
    keys = Handles()
    desktop = LibraryStore.create(tmp_path / DESKTOP)
    seed_effects(desktop, keys)
    _accept(desktop, keys)
    desktop.close()
    shutil.copytree(tmp_path / DESKTOP, tmp_path / PHONE)
    desktop, phone = LibraryStore.open(tmp_path / DESKTOP), LibraryStore.open(tmp_path / PHONE)
    try:
        identify(desktop, DESKTOP)
        identify(phone, PHONE)
        report = sync(desktop, phone, handles())
        assert report.conflicts == ()
        relations = [path for path in desktop.scan().files if "/relations/" in path]
        assert relations and set(relations) <= set(report.skipped[DESKTOP])
        archive = parse_archive(desktop.read_sync_state(f"peers/{PHONE}/archive.json"), (DESKTOP, PHONE))
        assert all(archive.entries[path][0] == "agreed" for path in relations)
    finally:
        desktop.close()
        phone.close()


def test_resolve_refuses_a_held_relation_and_an_accepted_event(tmp_path):
    desktop, phone, keys, _ = _effects_pair(tmp_path)
    try:
        _accept(desktop, keys)
        report = sync(desktop, phone, handles())
        held = [conflict.path for conflict in report.conflicts if conflict.code == "held"]
        assert any("/accepted/" in path for path in held) and any("/relations/" in path for path in held)
        for path in held:
            mine, theirs = _bytes(desktop, path), _bytes(phone, path)
            with pytest.raises(ResolveRefused, match="held back until 028"):
                resolve(desktop, phone, path, etag(mine), {DESKTOP: etag(mine),
                                                           PHONE: None if theirs is None else etag(theirs)}, handles())
            assert _bytes(phone, path) == theirs
    finally:
        desktop.close()
        phone.close()


def test_an_unlistable_accepted_folder_is_a_held_conflict(tmp_path, monkeypatch):
    desktop, phone, keys, _ = _effects_pair(tmp_path)
    try:
        _accept(desktop, keys)
        sync(desktop, phone, handles())
        real = engine.Access.list_dir
        folder = ("campaigns", C, "accepted")

        def busy(self, parts):
            if tuple(parts) == folder and self.root == desktop.root:
                raise PermissionError(13, "held by another program")
            return real(self, parts)
        monkeypatch.setattr(engine.Access, "list_dir", busy)
        report = sync(desktop, phone, handles())
        monkeypatch.undo()
        assert report.conflict("/".join(folder)).code == "held"
        assert "cannot be listed" in report.conflict("/".join(folder)).detail
    finally:
        desktop.close()
        phone.close()
