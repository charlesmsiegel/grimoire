"""A2, sync part: an instance id held by two kinds in one campaign is never synced, and never formed by sync.

The scan marks every member of such a pair ``blocked`` (``instance_id_conflict``), exactly the files
``load()`` excludes; a copy the receiver's store refuses because it would form a pair is
``unapplicable``; instance copies of one campaign are admitted cumulatively, so two that form a pair
never both pass. A placement whose subject is a pair member is held.
"""

from __future__ import annotations

import pytest

from grimoire.core.identity import InstanceRef, PlacementRef
from grimoire.images import Placement
from grimoire.imagestore import ImageLibrary
from grimoire.store import InstanceIdConflict, LibraryStore, etag, replica
from grimoire.store.envelope import record_document
from grimoire.store.jsonfmt import render
from grimoire.store.layout import address_parts
from grimoire.sync import ResolveRefused, identify, resolve, sync
from grimoire.sync.session import Copy, _admit_units

from image_fixtures import PNG, WREN_A, handle
from image_fixtures import seed as seed_art
from store_helpers import OWNER, WREN_R1, instance, owner_handle, seed
from sync_helpers import DESKTOP, PHONE, handles, make_pair, put

H = {DESKTOP: handle("sources", "campaigns", "assets", "kept"), PHONE: handle("sources", "campaigns", "assets", "kept")}
CHARACTER = InstanceRef("c1", "character", "wren-a")
CREATURE = InstanceRef("c1", "creature", "wren-a")


def _path(ref) -> str:  # type: ignore[no-untyped-def]
    return "/".join(address_parts(ref))


def _hand_made(store: LibraryStore, ref: InstanceRef) -> None:
    """A pair member written by hand (or arrived by a file copy): the store would refuse to create it."""
    target = store.root / _path(ref)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(render(record_document(instance(ref, WREN_R1), None)))


def test_the_scan_blocks_both_members_of_a_pair_exactly_as_load_excludes_them(tmp_path):
    desktop, phone = make_pair(tmp_path)
    try:
        _hand_made(desktop, CREATURE)
        scan = desktop.scan()
        members = {_path(CHARACTER), _path(CREATURE)}
        assert dict(scan.pairs) == {path: "instance_id_conflict" for path in members}
        assert members <= set(scan.files)  # still read: their pins still count
        assert {scan.blocked_reason(path) for path in members} == {"instance_id_conflict"}
        loaded = desktop.load()
        assert {problem.path for problem in loaded.problems if problem.code == "instance_id_conflict"} == members
        assert not {CHARACTER, CREATURE} & set(loaded.instances)
        report = sync(desktop, phone, handles())
        for path in members:
            conflict = report.conflict(path)
            assert conflict.code == "blocked" and "instance_id_conflict on desktop" in conflict.detail
        assert not (phone.root / _path(CREATURE)).exists()
        data = (desktop.root / _path(CREATURE)).read_bytes()  # resolve cannot end a pair
        with pytest.raises(ResolveRefused, match="instance_id_conflict"):
            resolve(desktop, phone, _path(CREATURE), etag(data), {DESKTOP: etag(data), PHONE: None}, handles())
        (desktop.root / _path(CREATURE)).unlink()  # the user ends it: deletes (or renames) one member
        assert sync(desktop, phone, handles()).conflict(_path(CHARACTER)) is None
    finally:
        desktop.close()
        phone.close()


def test_a_pair_formed_across_devices_is_unapplicable_on_each_receiver(tmp_path):
    """Each device creates ``x`` offline as another kind: each copy is refused by the receiver's store
    (``InstanceIdConflict``, a per-path error), so the sync completes instead of stalling."""
    desktop, phone = make_pair(tmp_path)
    try:
        put(desktop, instance(InstanceRef("c1", "character", "x"), WREN_R1), None)
        put(phone, instance(InstanceRef("c1", "creature", "x"), WREN_R1), None)
        for _ in range(2):  # never SyncInterrupted, on every sync
            report = sync(desktop, phone, handles())
            assert report.conflict("campaigns/c1/instances/character/x.json").code == "unapplicable"
            assert report.conflict("campaigns/c1/instances/creature/x.json").code == "unapplicable"
            assert "instance" in report.conflict("campaigns/c1/instances/creature/x.json").detail
        assert not (desktop.root / "campaigns/c1/instances/creature/x.json").exists()
        assert not (phone.root / "campaigns/c1/instances/character/x.json").exists()
    finally:
        desktop.close()
        phone.close()


def test_instance_copies_of_one_campaign_are_admitted_cumulatively(tmp_path):
    """Two copies that would form a pair each pass alone; admitted in path order, the second is refused."""
    store = LibraryStore.create(tmp_path / "dest")
    seed(store)
    try:
        copies = [Copy(_path(ref), "origin", "dest", render(record_document(instance(ref, WREN_R1), None)), None)
                  for ref in (InstanceRef("c1", "creature", "x"), InstanceRef("c1", "character", "x"))]
        for copy in copies:
            assert _admit_units(store, [copy]) == ([copy], [])  # each alone is fine
        admitted, rejected = _admit_units(store, copies)
        assert [copy.path for copy in admitted] == ["campaigns/c1/instances/character/x.json"]
        assert [(copy.path, type(error)) for copy, error in rejected] == [
            ("campaigns/c1/instances/creature/x.json", InstanceIdConflict)]
    finally:
        store.close()


def test_two_copies_that_would_form_a_pair_never_stall_the_plan(tmp_path, monkeypatch):
    """Defence in depth: even if a pair escaped the scan, cumulative admission keeps the plan whole."""
    desktop, phone = make_pair(tmp_path)
    try:
        _hand_made(desktop, InstanceRef("c1", "character", "x"))
        _hand_made(desktop, InstanceRef("c1", "creature", "x"))
        monkeypatch.setattr(replica, "_instance_pairs", lambda paths: {})
        report = sync(desktop, phone, handles())
        copied = [path for path, _ in report.copied if path.endswith("/x.json")]
        refused = [conflict.path for conflict in report.conflicts if conflict.code == "unapplicable"]
        assert copied == ["campaigns/c1/instances/character/x.json"]
        assert refused == ["campaigns/c1/instances/creature/x.json"]
    finally:
        desktop.close()
        phone.close()


def test_a_placement_whose_subject_is_pair_blocked_is_held(tmp_path):
    """Cross-workstream (A2 x C2): the placement's subject is unknown while it is a pair member."""
    desktop = LibraryStore.create(tmp_path / DESKTOP)
    seed_art(desktop)
    phone = LibraryStore.create(tmp_path / PHONE)
    try:
        identify(desktop, DESKTOP)
        identify(phone, PHONE)
        assert sync(desktop, phone, H).conflicts == ()
        images = ImageLibrary(desktop, H[DESKTOP], owner=OWNER)
        batch = images.change()
        ref = batch.add_image("campaign:c1", "wren", PNG)
        batch.place(WREN_A, "avatar", Placement("set", "avatar", ref), None)
        batch.commit()
        _hand_made(desktop, CREATURE)
        report = sync(desktop, phone, H)
        placement = _path(PlacementRef(WREN_A, "avatar"))
        assert report.conflict(_path(CREATURE)).code == "blocked"
        assert report.conflict(placement).code == "head_dependency"
        assert _path(WREN_A) in report.conflict(placement).detail
        assert not (phone.root / placement).exists()
        assert (_path(ref), PHONE) in report.copied  # the image itself needs only its campaign and blob
    finally:
        desktop.close()
        phone.close()


def test_the_store_refuses_a_cross_kind_pair_that_sync_could_otherwise_deliver(tmp_path):
    """The store rule sync relies on (W-A): creating the second kind of an id is refused per path."""
    store = LibraryStore.create(tmp_path / "lib")
    seed(store)
    try:
        txn = store.transaction()
        txn.put_exact(_path(CREATURE), render(record_document(instance(CREATURE, WREN_R1), None)), None,
                      regenerable=True)
        with pytest.raises(InstanceIdConflict):
            txn.commit(owner_handle())
    finally:
        store.close()
