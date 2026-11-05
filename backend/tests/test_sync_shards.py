"""028: a pair's archive and pending marker past one file. A library whose bookkeeping no longer fits
010's file cap is split into shards instead of being refused; an older single-file archive is read
as it is and rewritten without losing agreement; a damaged shard makes the archive ignored (every
path it names uncertain), never silently trusted; a crash at any commit converges.

The cap is lowered with ``monkeypatch`` (``session.ARCHIVE_LIMIT``) so a few hundred synthetic
records stand in for the 23,000 that 010's 8 MiB cap holds."""

from __future__ import annotations

import functools
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from grimoire.core.identity import InstanceRef
from grimoire.images import Placement
from grimoire.imagestore import ImageLibrary
from grimoire.store import LibraryStore, ProcessLocker, RecordEnvelope, etag
from grimoire.sync import ArchiveTooLarge, identify, sync, sync_until_quiet
from grimoire.sync import bookkeeping
from grimoire.sync import session as sync_session
from grimoire.sync.documents import render_archive
from grimoire.testing import CrashScenario, Invariant, explore_crashes, idempotent_recovery, no_residue, run_plans
from grimoire.testing.fileops import snapshot

from image_fixtures import PNG, WREN_A as ART_SUBJECT, seed as seed_art
from store_helpers import OWNER, WREN_R1, drop_empty_dirs, instance, owner_handle
from sync_helpers import DESKTOP, PATHS, PHONE, WREN_A, canonical, edit_instance, handle, handles, make_pair, put

LIMIT = 12_000  # one file holds about 30 paths at this cap; the libraries below sync hundreds
PAIR = (DESKTOP, PHONE)
COUNT = 150


def _many(store: LibraryStore, count: int = COUNT, campaign: str = "c9") -> list[str]:
    txn = store.transaction()
    for number in range(count):
        txn.put(RecordEnvelope(InstanceRef(campaign, "character", f"extra-{number:03d}"), {"n": number}), None)
    txn.commit(handle())
    return [f"campaigns/{campaign}/instances/character/extra-{number:03d}.json" for number in range(count)]


def _archive(store: LibraryStore, peer: str):  # type: ignore[no-untyped-def]
    return bookkeeping.parse("archive", bookkeeping.pair_files(store, "archive", peer), peer, PAIR)


def _sync_files(store: LibraryStore, peer: str) -> dict[str, bytes]:
    folder = store.root / ".sync" / "peers" / peer
    return {path.name: path.read_bytes() for path in sorted(folder.iterdir())}


def _tree(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*"))
            if path.is_file() and path.name != "lock"}


@pytest.fixture
def small_cap(monkeypatch):
    monkeypatch.setattr(sync_session, "ARCHIVE_LIMIT", LIMIT)


def test_a_library_past_one_files_capacity_syncs_and_both_orders_give_identical_bytes(tmp_path, small_cap):
    (tmp_path / "one").mkdir()
    desktop, phone = make_pair(tmp_path / "one")
    paths = _many(desktop)
    desktop.close(), phone.close()
    shutil.copytree(tmp_path / "one", tmp_path / "two")
    trees = []
    for folder, swap in (("one", False), ("two", True)):
        desktop, phone = (LibraryStore.open(tmp_path / folder / name) for name in PAIR)
        report = sync(phone, desktop, handles()) if swap else sync(desktop, phone, handles())
        assert {path for path, _ in report.copied} == set(paths) and report.conflicts == ()
        assert canonical(desktop) == canonical(phone)
        assert not sync(desktop, phone, handles()).wrote  # quiet
        for store, peer in ((desktop, PHONE), (phone, DESKTOP)):
            view = _archive(store, peer)
            assert view.state == "valid" and {path for path, (kind, _) in view.entries.items() if kind == "agreed"} >= set(paths)
            assert len(render_archive(PAIR, view.entries)) > LIMIT  # 019's one file could not have held it
            files = _sync_files(store, peer)
            root = json.loads(files["archive.json"])
            assert root["format"] == "grimoire.sync.archive/2" and len(root["shards"]) == 16
            assert all(len(data) <= LIMIT for data in files.values())
        desktop.close(), phone.close()
        trees.append({**{f"d/{k}": v for k, v in _tree(tmp_path / folder / DESKTOP).items()},
                      **{f"p/{k}": v for k, v in _tree(tmp_path / folder / PHONE).items()}})
    assert trees[0] == trees[1]


def test_a_library_that_fits_one_file_keeps_019s_single_file(tmp_path, small_cap):
    desktop, phone = make_pair(tmp_path)
    edit_instance(desktop, WREN_A, 9)
    sync(desktop, phone, handles())
    assert set(_sync_files(desktop, PHONE)) == {"archive.json"}
    assert json.loads((desktop.root / ".sync/peers/phone/archive.json").read_bytes())["format"] == \
        "grimoire.sync.archive/1"


def test_more_than_even_the_widest_shards_hold_is_still_refused_before_any_write(tmp_path, monkeypatch):
    desktop, phone = make_pair(tmp_path)
    _many(desktop, 40)
    before = (_tree(desktop.root), _tree(phone.root))
    monkeypatch.setattr(sync_session, "ARCHIVE_LIMIT", 2_000)  # no shard of any width holds one path's room
    with pytest.raises(ArchiveTooLarge):
        sync(desktop, phone, handles())
    assert (_tree(desktop.root), _tree(phone.root)) == before


def test_an_older_single_file_archive_is_read_as_is_and_its_agreement_and_tombstones_survive(tmp_path, monkeypatch):
    desktop, phone = make_pair(tmp_path)
    gone = PATHS["wren_b"]
    deleted = (desktop.root / gone).read_bytes()
    for store in (desktop, phone):  # agreed and then deleted on both: a tombstone in the /1 file
        (store.root / gone).unlink()
    sync(desktop, phone, handles())
    assert json.loads((desktop.root / ".sync/peers/phone/archive.json").read_bytes())["entries"][gone] == {
        "deleted": etag(deleted)}
    monkeypatch.setattr(sync_session, "ARCHIVE_LIMIT", LIMIT)
    _many(phone)
    edit_instance(desktop, WREN_A, 9)  # agreed in the old file: an edit is a copy, not a missing ancestor
    report = sync(desktop, phone, handles())
    assert (PATHS["wren_a"], PHONE) in report.copied and report.conflicts == ()
    assert json.loads((desktop.root / ".sync/peers/phone/archive.json").read_bytes())["format"].endswith("/2")
    # the tombstone moved into a shard: restoring the deleted bytes is still a resurrection, not a copy
    (phone.root / gone).write_bytes(deleted)
    report = sync(desktop, phone, handles())
    assert report.conflict(gone).code == "resurrected" and not (desktop.root / gone).exists()


def _sharded_pair(root: Path) -> tuple[LibraryStore, LibraryStore, list[str]]:
    desktop, phone = make_pair(root)
    paths = _many(desktop)
    sync(desktop, phone, handles())
    return desktop, phone, paths


def _damage_shard(store: LibraryStore, peer: str, how: str) -> None:
    folder = store.root / ".sync" / "peers" / peer
    shard = sorted(folder.glob("archive.*.json"))[3]
    if how == "edited":  # still strict JSON with a valid look; its etag no longer matches the root
        document = json.loads(shard.read_bytes())
        document["entries"] = {}
        shard.write_text(json.dumps(document, indent=2) + "\n")
    elif how == "deleted":
        shard.unlink()
    elif how == "torn":
        shard.write_bytes(shard.read_bytes()[:40])
    elif how == "root deleted":  # shards without their root: never read as a first sync
        (folder / "archive.json").unlink()


@pytest.mark.parametrize("how", ["edited", "deleted", "torn", "root deleted"])
def test_a_damaged_shard_makes_the_archive_ignored_and_every_path_uncertain(tmp_path, small_cap, how):
    desktop, phone, paths = _sharded_pair(tmp_path)
    edited, gone = paths[5], paths[6]
    (phone.root / gone).unlink()  # a one-sided deletion the damaged archive would otherwise undo
    _damage_shard(phone, DESKTOP, how)
    put(desktop, RecordEnvelope(InstanceRef("c9", "character", "extra-005"), {"n": "changed"}))
    report = sync(desktop, phone, handles())
    assert any(notice.startswith(f"{PHONE}: archive ignored") for notice in report.notices)
    # no base is trusted: the edit is not copied and the deletion not undone, both are kept
    assert report.conflict(edited).code == "missing_ancestor" and report.conflict(edited).kept[PHONE]
    assert report.conflict(gone).code == "missing_ancestor" and not (phone.root / gone).exists()
    assert (edited, PHONE) not in report.copied
    # the uncertainty is recorded before the archive is rewritten: it survives the rewrite
    again = sync(desktop, phone, handles())
    assert again.conflict(edited).code == "missing_ancestor" and again.conflict(gone).code == "missing_ancestor"
    assert not (phone.root / gone).exists()


def test_a_tombstone_in_a_damaged_shard_is_not_forgotten(tmp_path, small_cap):
    desktop, phone, paths = _sharded_pair(tmp_path)
    gone = paths[7]
    deleted = (desktop.root / gone).read_bytes()
    for store in (desktop, phone):
        (store.root / gone).unlink()
    sync(desktop, phone, handles())
    _damage_shard(desktop, PHONE, "edited")
    (phone.root / gone).write_bytes(deleted)  # restored on one device only
    report = sync(desktop, phone, handles())
    assert report.conflict(gone).code == "missing_ancestor" and not (desktop.root / gone).exists()


def test_shards_left_from_another_width_are_never_trusted_and_are_overwritten_safely(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_session, "ARCHIVE_LIMIT", LIMIT)
    desktop, phone, _ = _sharded_pair(tmp_path)
    monkeypatch.setattr(sync_session, "ARCHIVE_LIMIT", sync_session.MAX_FILE_BYTES)  # fits one file again
    edit_instance(desktop, WREN_A, 9)
    sync(desktop, phone, handles())
    files = _sync_files(desktop, PHONE)
    assert json.loads(files["archive.json"])["format"].endswith("/1") and "archive.0.json" in files
    # a stale shard is not listed by a /1 root: changing it changes nothing and is not reported
    stale = desktop.root / ".sync/peers/phone/archive.0.json"
    stale.write_bytes(b"{")
    edit_instance(desktop, WREN_A, 3)
    report = sync(desktop, phone, handles())
    assert report.notices == () and (PATHS["wren_a"], PHONE) in report.copied
    # sharding again overwrites the stale files (each by its own compare-and-swap) and stays quiet
    monkeypatch.setattr(sync_session, "ARCHIVE_LIMIT", LIMIT)
    _many(phone, 20, campaign="c8")
    assert sync_until_quiet(desktop, phone, handles()).conflicts == ()
    assert _archive(desktop, PHONE).state == "valid" and json.loads(stale.read_bytes())["shard"] == "0"


def test_a_pending_marker_past_one_file_is_sharded_and_carries_an_interrupted_sync(tmp_path, small_cap, monkeypatch):
    """A crash after both data commits: only the (sharded) markers record what landed."""
    desktop, phone = make_pair(tmp_path)
    paths = _many(desktop)

    def crash(*args, **kwargs):
        raise RuntimeError("power loss before the archives")
    monkeypatch.setattr(sync_session, "_commit_archive", crash)
    with pytest.raises(RuntimeError):
        sync(desktop, phone, handles())
    monkeypatch.undo()
    monkeypatch.setattr(sync_session, "ARCHIVE_LIMIT", LIMIT)
    marker = json.loads((phone.root / ".sync/peers/desktop/pending.json").read_bytes())
    assert marker["format"] == "grimoire.sync.pending/2"
    # the copy landed and the marker says so: a revert on the phone is a conflict, never undone silently
    (phone.root / paths[0]).unlink()
    report = sync_until_quiet(desktop, phone, handles())
    assert report.conflict(paths[0]).code in ("missing_ancestor", "deleted") and (desktop.root / paths[0]).exists()
    assert {conflict.path for conflict in report.conflicts} == {paths[0]}
    # once settled, each marker holds only the open conflict's path, and so fits one file again
    for store, peer in ((desktop, PHONE), (phone, DESKTOP)):
        pending = bookkeeping.parse("pending", bookkeeping.pair_files(store, "pending", peer), peer, PAIR)
        assert pending.state == "valid" and set(pending.entries) == {paths[0]}
        assert json.loads((store.root / f".sync/peers/{peer}/pending.json").read_bytes())["format"].endswith("/1")


def test_a_new_identity_retires_every_shard_and_forgets_no_deletion(tmp_path, small_cap):
    desktop, phone, paths = _sharded_pair(tmp_path)
    (desktop.root / paths[9]).unlink()
    identify(desktop, "tablet", replace=True)
    for path in (desktop.root / ".sync/peers/phone").iterdir():
        assert json.loads(path.read_bytes())["format"] == "grimoire.sync.retired/1"
    report = sync(desktop, phone, {"tablet": handle(), PHONE: handle()})
    assert report.conflict(paths[9]).code == "missing_ancestor" and not (desktop.root / paths[9]).exists()


def test_a_blobs_tombstone_is_not_kept_and_every_other_tombstone_is(tmp_path):
    """A blob's tombstone decides nothing a missing entry does not (its name is its content), so it is
    dropped; the image object's and placement's stay, so their resurrection is still a conflict."""
    art ={device: owner_handle("sources", "campaigns", "assets", "kept") for device in PAIR}
    desktop, phone = LibraryStore.create(tmp_path / DESKTOP), LibraryStore.create(tmp_path / PHONE)
    seed_art(desktop)
    identify(desktop, DESKTOP), identify(phone, PHONE)
    batch = ImageLibrary(desktop, art[DESKTOP], owner=OWNER).change()
    batch.place(ART_SUBJECT, "avatar", Placement("set", "avatar", batch.add_image("campaign:c1", "wren", PNG)), None)
    batch.commit()
    assert sync_until_quiet(desktop, phone, art).conflicts == ()
    files = {path: (desktop.root / path).read_bytes() for path in canonical(desktop)
             if path.startswith(("assets/", "campaigns/c1/images/", "campaigns/c1/placements/"))}
    files.update({path: (desktop.root / path).read_bytes() for path in desktop.scan().blobs})
    blob = next(path for path in files if path.startswith("assets/"))
    image = next(path for path in files if "/images/" in path)
    for store in (desktop, phone):
        for path in files:
            (store.root / path).unlink()
    sync_until_quiet(desktop, phone, art)
    entries = _archive(desktop, PHONE).entries
    assert blob not in entries and entries[image] == ("deleted", etag(files[image]))
    (desktop.root / blob).parent.mkdir(parents=True, exist_ok=True)
    (desktop.root / blob).write_bytes(files[blob])  # the same picture imported again
    report = sync(desktop, phone, art)
    assert (blob, PHONE) in report.copied
    (desktop.root / image).parent.mkdir(parents=True, exist_ok=True)
    (desktop.root / image).write_bytes(files[image])  # the deleted image object restored by hand
    assert sync(desktop, phone, art).conflict(image).code == "resurrected"


# -- under the fault harness: every commit boundary of a sharded sync ---------------------------------

DEVICES = PAIR
RESIDUE = no_residue(*(f"{device}/.txn/{name}" for device in DEVICES for name in ("*.new", "journal.json")))


@functools.cache
def _fault_fixture():
    """Both devices sharded and agreed; then the desktop adds and edits, the phone edits, and both
    change one record (a conflict): the next sync copies both ways, keeps both, rewrites shards."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        original = sync_session.ARCHIVE_LIMIT
        sync_session.ARCHIVE_LIMIT = LIMIT
        try:
            desktop, phone, _ = _sharded_pair(root)
        finally:
            sync_session.ARCHIVE_LIMIT = original
        _many(desktop, 10, campaign="c7")
        edit_instance(desktop, WREN_A, 9)
        put(phone, instance(InstanceRef("c9", "character", "extra-001"), WREN_R1, hp=2))
        put(phone, RecordEnvelope(InstanceRef("c9", "character", "extra-002"), {"n": "phone"}))
        put(desktop, RecordEnvelope(InstanceRef("c9", "character", "extra-002"), {"n": "desktop"}))
        desktop.close(), phone.close()
        return tuple(snapshot(root).items())


def _fault_scenario():
    locker = ProcessLocker()

    def setup(ops, root):
        for key, data in _fault_fixture():
            ops.make_dirs(root / key) if data is None else ops.write_bytes(root / key, data)

    def once(ops, root):
        desktop = LibraryStore.open(root / DESKTOP, ops=ops, locker=locker)
        try:
            phone = LibraryStore.open(root / PHONE, ops=ops, locker=locker)
            try:
                sync(desktop, phone, handles())
            finally:
                phone.close()
        finally:
            desktop.close()

    def converges(outcome):
        if outcome.recovered == outcome.completed:
            return None
        keys = sorted(key for key in set(outcome.recovered) | set(outcome.completed)
                      if outcome.recovered.get(key, "absent") != outcome.completed.get(key, "absent"))
        return f"differs from the uninterrupted sync at {keys[:4]}"
    return CrashScenario("sync-sharded", setup, once, once,
                         (Invariant("converges", converges), idempotent_recovery(), RESIDUE),
                         normalize=drop_empty_dirs)


def test_a_crash_or_error_at_any_commit_boundary_of_a_sharded_sync_converges(tmp_path, small_cap):
    with tempfile.TemporaryDirectory() as folder:
        baseline = run_plans(_fault_scenario(), [], Path(folder))
        baseline.assert_ok()
        trace = baseline.runs[0].action_trace
    commits = [op.index for op in trace if op.kind == "replace" and (op.target or "").endswith("/.txn/journal.json")]
    assert len(commits) == 6  # data(a), data(b), archive(b), archive(a), pending(b), pending(a)
    points = {}
    for index in commits:
        points[index] = frozenset({"before", "after", "fail"})
        publish = next((op.index for op in trace[index + 1:] if op.kind == "replace" and "/.txn/" in op.path), None)
        if publish is not None:
            points[publish] = frozenset({"fail"})
    report = explore_crashes(_fault_scenario(), tmp_path, errors=True, torn=False,
                             select=lambda op: points.get(op.index, frozenset()))
    report.assert_ok()
    assert report.points_explored >= 18
