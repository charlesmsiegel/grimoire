"""028: a deletion on one device propagates to the other, holds back what would orphan a record, and
``resolve`` can end a conflict toward the deletion (docs/SYNC.md row 9 and "Ending a conflict").

The synthetic pair (``sync_helpers``): world harbor with wren@r1, campaign c1's wren-a pinned to
r1, campaign c2's wren-b following wren's head, and c1's chat instance harbor-talk.
"""

from __future__ import annotations

import functools
import json
import tempfile
from pathlib import Path

import pytest

from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.store import InvalidEnvelope, LibraryStore, ProcessLocker, RecordEnvelope, SourceManifest, etag
from grimoire.store.layout import conflict_parts
from grimoire.sync import ResolveRefused, SyncInterrupted, resolve, sync, sync_until_quiet
from grimoire.sync import deletion, keeps as kept_record, resolution, session as sync_session
from grimoire.sync.documents import keeps_name, parse_keeps, render_keeps
from grimoire.testing import CrashScenario, Invariant, explore_crashes, idempotent_recovery, no_residue, run_plans
from grimoire.testing.fileops import snapshot

from store_helpers import HARBOR, WREN_R2, drop_empty_dirs, harbor_manifest, instance, owner_handle
from sync_helpers import (
    DESKTOP, PATHS, PHONE, WREN_A, WREN_B, canonical, chat_record, converged, edit_instance, handle, handles, kept,
    make_pair,
    new_revision, put,
)

PAIR = (DESKTOP, PHONE)


@pytest.fixture
def pair(tmp_path):
    desktop, phone = make_pair(tmp_path)
    yield desktop, phone
    desktop.close()
    phone.close()


def _archive(store: LibraryStore, peer: str) -> dict:
    return json.loads(store.read_sync_state(f"peers/{peer}/archive.json"))["entries"]


def _held(store: LibraryStore, peer: str) -> dict[str, list[str]]:
    view = parse_keeps(store.read_sync_state(keeps_name(peer)), PAIR)
    return {path: list(entry.held) for path, entry in view.entries.items() if entry.held}


def _tree(root: Path) -> dict:
    return {key: value for key, value in snapshot(root).items() if not key.endswith("/.lock")}


# -- propagation -------------------------------------------------------------------------------

def test_a_deletion_on_one_device_is_made_on_the_other_and_its_version_is_kept(pair):
    desktop, phone = pair
    path = PATHS["wren_b"]
    original = (phone.root / path).read_bytes()
    (desktop.root / path).unlink()
    report = sync(desktop, phone, handles())
    assert report.conflicts == () and (path, PHONE) in report.deleted
    assert not (phone.root / path).exists() and not (desktop.root / path).exists()
    assert kept(phone) == [original]  # recoverable on the device that deleted it for the sync
    assert _held(phone, DESKTOP) == {path: [etag(original)]}  # its only copy: never dismissable
    for store, peer in ((desktop, PHONE), (phone, DESKTOP)):
        assert _archive(store, peer)[path] == {"deleted": etag(original)}
    again = sync(desktop, phone, handles())
    assert again.conflicts == () and again.copied == () and again.deleted == ()


def test_a_restore_after_the_tombstone_is_a_conflict_never_a_silent_copy(pair):
    desktop, phone = pair
    path = PATHS["wren_b"]
    original = (phone.root / path).read_bytes()
    (desktop.root / path).unlink()
    sync(desktop, phone, handles())
    (phone.root / path).write_bytes(original)  # restored by hand from the kept copy
    report = sync(desktop, phone, handles())
    assert report.conflict(path).code == "resurrected" and not (desktop.root / path).exists()


def test_an_edit_against_a_deletion_stays_a_conflict(pair):
    desktop, phone = pair
    path = PATHS["wren_a"]
    (desktop.root / path).unlink()
    edit_instance(phone, WREN_A, 7)
    edited = (phone.root / path).read_bytes()
    report = sync(desktop, phone, handles())
    assert report.conflict(path).code == "edit_delete" and report.deleted == ()
    assert (phone.root / path).read_bytes() == edited


def test_sync_in_either_order_leaves_byte_identical_libraries(tmp_path):
    trees = []
    for order in ("ab", "ba"):
        desktop, phone = make_pair(tmp_path / order)
        (desktop.root / PATHS["wren_b"]).unlink()
        edit_instance(phone, WREN_A, 4)
        sync(*((desktop, phone) if order == "ab" else (phone, desktop)), handles())
        desktop.close(), phone.close()
        trees.append(_tree(tmp_path / order))
    assert trees[0] == trees[1]


def test_a_source_deleted_on_one_device_is_held_back_while_anything_needs_it(pair):
    """The source's manifest and revision go only once nothing left names them: wren-a pins r1 and
    wren-b follows wren's head (an unpinned reference needs some revision of wren)."""
    desktop, phone = pair
    source = [PATHS["manifest"], PATHS["r1"]]
    for path in source:
        (desktop.root / path).unlink()
    report = sync(desktop, phone, handles())
    assert {conflict.path: conflict.code for conflict in report.conflicts} == dict.fromkeys(source, "delete_dependency")
    assert all((phone.root / path).exists() for path in source) and report.deleted == ()
    assert "wren-a" in report.conflict(PATHS["r1"]).detail  # names what still needs it
    for path in (PATHS["wren_a"], PATHS["wren_b"]):  # the people who used the source go too
        (desktop.root / path).unlink()
    report = sync_until_quiet(desktop, phone, handles())
    assert report.conflicts == ()
    assert not any((phone.root / path).exists() for path in [*source, PATHS["wren_a"], PATHS["wren_b"]])
    assert len(kept(phone)) == 4


def test_a_deletion_the_grant_refuses_is_denied_and_nothing_is_removed(pair):
    desktop, phone = pair
    path = PATHS["wren_b"]
    original = (phone.root / path).read_bytes()
    (desktop.root / path).unlink()
    report = sync(desktop, phone, {DESKTOP: handle(), PHONE: owner_handle("sources", "kept")})
    assert report.conflict(path).code == "denied" and report.deleted == ()
    assert (phone.root / path).read_bytes() == original
    report = sync(desktop, phone, handles())  # granted now: the deletion is retried
    assert report.conflicts == () and not (phone.root / path).exists()


def test_a_denied_deletion_drops_only_itself_never_the_rest_of_its_source(pair):
    """A denied revision deletion is not a denied write of its source: the other changes to that
    source (a new entity and its manifest) still land (028; denial is source-coarse only for writes)."""
    desktop, phone = pair
    new_revision(desktop, "r2", "a captain")
    for ref in (WREN_A, WREN_B):
        put(desktop, instance(ref, WREN_R2, base=WREN_R2))
    assert sync(desktop, phone, handles()).conflicts == ()  # r1 is no longer anyone's: deletable
    (desktop.root / PATHS["r1"]).unlink()
    _add_kestrel(desktop, {"character/wren": "r2", "character/kestrel": "k1"})
    grant = owner_handle("sources/world/harbor/_source/manifest.json",
                         "sources/world/harbor/_source/records/character/kestrel", "campaigns", "kept")
    report = sync(desktop, phone, {DESKTOP: handle(), PHONE: grant})
    assert {conflict.path: conflict.code for conflict in report.conflicts} == {PATHS["r1"]: "denied"}
    assert (phone.root / PATHS["r1"]).exists() and report.deleted == ()
    assert phone.read_source(HARBOR).etag == desktop.read_source(HARBOR).etag
    assert (phone.root / KESTREL_PATH).exists()


KESTREL = SourceRef(HARBOR, "character", "kestrel")
KESTREL_PATH = "sources/world/harbor/_source/records/character/kestrel/k1.json"


def _add_kestrel(store: LibraryStore, heads: dict[str, str]) -> None:
    txn = store.transaction()
    txn.put(RecordEnvelope(KESTREL.at("k1"), {"bio": "a gull"}, name="Kestrel"), None)
    txn.put(harbor_manifest(heads), store.read_source(HARBOR).etag)
    txn.commit(handle())


@pytest.mark.parametrize("declared", [True, False], ids=["declared-head", "only-revision"])
def test_an_entity_dropped_from_a_source_goes_in_one_sync(pair, declared):
    """A source drops an entity by deleting its revisions (with the manifest edit that drops its
    head, if one was declared): the store judges the deletion over what the same commit leaves, so
    neither the old manifest's head nor an entity's last revision being its implicit head holds it."""
    desktop, phone = pair
    _add_kestrel(desktop, {"character/kestrel": "k1"} if declared else {})
    assert sync(desktop, phone, handles()).conflicts == () and (phone.root / KESTREL_PATH).exists()
    if declared:
        put(desktop, harbor_manifest(), desktop.read_source(HARBOR).etag)
    (desktop.root / KESTREL_PATH).unlink()
    report = sync(desktop, phone, handles())
    assert report.conflicts == () and (KESTREL_PATH, PHONE) in report.deleted
    assert not (phone.root / KESTREL_PATH).exists()
    assert phone.read_source(HARBOR).etag == desktop.read_source(HARBOR).etag


def test_one_deletion_the_store_refuses_leaves_a_source_deleted_as_a_whole(pair, monkeypatch):
    """Split on failure, deletions join the ones already admitted: a source going as a whole (its
    manifest, its revision, the records that used it) is never refused piece by piece because of
    an unrelated deletion the store refuses."""
    desktop, phone = pair
    put(desktop, chat_record(["a stranger"]))
    assert sync(desktop, phone, handles()).conflicts == ()
    gone = [PATHS["manifest"], PATHS["r1"], PATHS["wren_a"], PATHS["wren_b"]]
    for path in [*gone, PATHS["chat"]]:
        (desktop.root / path).unlink()
    preflight = sync_session._preflight

    def refusing(txn, what):  # the store refuses the chat's deletion (any reason it may have)
        if PATHS["chat"] in txn.staged():
            return InvalidEnvelope("refused for this test")
        return preflight(txn, what)
    monkeypatch.setattr(sync_session, "_preflight", refusing)
    report = sync(desktop, phone, handles())
    assert {conflict.path: conflict.code for conflict in report.conflicts} == {PATHS["chat"]: "unapplicable"}
    assert not any((phone.root / path).exists() for path in gone) and (phone.root / PATHS["chat"]).exists()


def test_a_record_written_after_the_plan_that_needs_a_deletion_stops_its_commit(pair, monkeypatch):
    """The plan read the receiver earlier: under the data commit's lock its deletions are judged again
    over the device as it is now, so a chat made meanwhile with the deleted character in it stops
    the commit (nothing removed), and the next sync holds the deletion back."""
    desktop, phone = pair
    path = PATHS["wren_b"]
    (desktop.root / path).unlink()
    holds = sync_session._commit_holds

    def then_a_new_chat(plan, device, applied):  # the person keeps playing on the phone meanwhile
        holds(plan, device, applied)
        put(phone, RecordEnvelope(InstanceRef("c2", "chat", "dockside"), {"participants": [str(WREN_B)], "history": []}))
    monkeypatch.setattr(sync_session, "_commit_holds", then_a_new_chat)
    with pytest.raises(SyncInterrupted):
        sync(desktop, phone, handles())
    assert (phone.root / path).exists()
    monkeypatch.undo()
    report = sync(desktop, phone, handles())
    assert report.conflict(path).code == "delete_dependency" and (phone.root / path).exists()


def test_a_deletion_whose_hold_the_record_cannot_take_is_held_back(pair, monkeypatch):
    """Each hold is recorded before its deletion: when the pair's kept-version record is full, the
    deletion is ``unapplicable`` at planning and the record stays, instead of the sync failing."""
    desktop, phone = pair
    path = PATHS["wren_b"]
    (desktop.root / path).unlink()
    record = parse_keeps(phone.read_sync_state(keeps_name(DESKTOP)), PAIR)
    current = render_keeps(PAIR, record.entries if record.state == "valid" else {})
    monkeypatch.setattr(kept_record, "RECORD_LIMIT", len(current) + 20)  # room for no held version
    report = sync(desktop, phone, handles())
    assert report.conflict(path).code == "unapplicable" and (phone.root / path).exists()


def test_a_hold_slot_taken_while_planning_interrupts_the_sync(pair, monkeypatch):
    """Another writer fills the slot planned for a hold before it is staged: the sync stops as
    interrupted (and replans next time), never with a raw store error, and nothing is removed."""
    desktop, phone = pair
    path = PATHS["wren_b"]
    (desktop.root / path).unlink()
    slot = deletion.hold_slot

    def then_taken(store, where, data):  # type: ignore[no-untyped-def]
        planned = slot(store, where, data)
        taken = phone.root / "/".join(conflict_parts(tuple(where.split("/")), etag(data), planned))
        taken.parent.mkdir(parents=True, exist_ok=True)
        taken.write_bytes(b'{"someone": "else"}')
        return planned
    monkeypatch.setattr(deletion, "hold_slot", then_taken)
    with pytest.raises(SyncInterrupted):
        sync(desktop, phone, handles())
    assert (phone.root / path).exists()


def test_a_record_that_cannot_be_read_on_the_receiver_holds_every_deletion_back(pair):
    desktop, phone = pair
    (desktop.root / PATHS["wren_b"]).unlink()
    (phone.root / "campaigns/c1/instances/character/odd.json").write_bytes(b"not json")
    report = sync(desktop, phone, handles())
    assert report.conflict(PATHS["wren_b"]).code == "delete_dependency"
    assert (phone.root / PATHS["wren_b"]).exists()


@pytest.mark.parametrize("step", ["_commit_holds", "_commit_data", "_verify", "_commit_keeps", "_commit_archive",
                                  "_commit_pending"])
def test_a_crash_before_any_step_reaches_the_uninterrupted_result(tmp_path, monkeypatch, step):
    """A restart and one more sync reach exactly what the uninterrupted sync and its quiet follow-up
    reach, byte for byte (holds, kept-version records, tombstones and markers included)."""
    def diverge(root):
        desktop, phone = make_pair(root)
        (desktop.root / PATHS["wren_b"]).unlink()
        edit_instance(desktop, WREN_A, 9)
        return desktop, phone
    desktop, phone = diverge(tmp_path / "completed")
    sync(desktop, phone, handles())
    sync(desktop, phone, handles())
    desktop.close(), phone.close()
    completed = _tree(tmp_path / "completed")

    desktop, phone = diverge(tmp_path / "crashed")

    def die(*args, **kwargs):
        raise RuntimeError(f"power loss before {step}")
    monkeypatch.setattr(sync_session, step, die)
    with pytest.raises(RuntimeError):
        sync(desktop, phone, handles())
    monkeypatch.undo()
    desktop.close(), phone.close()
    desktop, phone = LibraryStore.open(tmp_path / "crashed" / DESKTOP), LibraryStore.open(tmp_path / "crashed" / PHONE)
    sync(desktop, phone, handles())
    sync(desktop, phone, handles())
    desktop.close(), phone.close()
    assert converged(_tree(tmp_path / "crashed"), completed) is None


def test_a_crash_after_the_deletion_then_a_restore_is_never_undone_silently(tmp_path, monkeypatch):
    """The marker alone records the deletion until the archives do: a restore of the deleted bytes
    on the receiver before the next sync is a conflict, never a copy back to the deleting device."""
    desktop, phone = make_pair(tmp_path)
    path = PATHS["wren_b"]
    original = (phone.root / path).read_bytes()
    (desktop.root / path).unlink()

    def die(*args, **kwargs):
        raise RuntimeError("power loss before the archives")
    monkeypatch.setattr(sync_session, "_commit_archive", die)
    with pytest.raises(RuntimeError):
        sync(desktop, phone, handles())
    monkeypatch.undo()
    assert not (phone.root / path).exists()
    (phone.root / path).write_bytes(original)
    report = sync(desktop, phone, handles())
    assert report.conflict(path) is not None and report.copied == ()
    assert not (desktop.root / path).exists()
    desktop.close(), phone.close()


# -- resolve toward the deletion --------------------------------------------------------------

def _expected(desktop, phone, path):
    return {device: etag((store.root / path).read_bytes()) if (store.root / path).exists() else None
            for device, store in ((DESKTOP, desktop), (PHONE, phone))}


def test_resolve_can_end_an_edit_delete_conflict_toward_the_deletion(pair):
    desktop, phone = pair
    path = PATHS["wren_a"]
    (desktop.root / path).unlink()
    edit_instance(phone, WREN_A, 7)
    edited = (phone.root / path).read_bytes()
    assert sync(desktop, phone, handles()).conflict(path).code == "edit_delete"
    assert resolve(desktop, phone, path, None, _expected(desktop, phone, path), handles()) == PHONE
    assert not (phone.root / path).exists() and edited in kept(phone)
    assert _held(phone, DESKTOP)[path] == [etag(edited)]
    for store, peer in ((desktop, PHONE), (phone, DESKTOP)):
        assert _archive(store, peer)[path] == {"deleted": etag(edited)}
    report = sync(desktop, phone, handles())
    assert report.conflicts == () and report.copied == () and report.deleted == ()


def test_resolve_toward_the_deletion_judges_again_under_its_lock(pair, monkeypatch):
    """A record written on the deleting device after resolve read it, which needs the deletion,
    refuses the resolve before anything is written: the record and the conflict stay."""
    desktop, phone = pair
    path = PATHS["wren_b"]
    (desktop.root / path).unlink()
    edit_instance(phone, WREN_B, 7)
    edited = (phone.root / path).read_bytes()
    assert sync(desktop, phone, handles()).conflict(path).code == "edit_delete"
    stage = deletion.stage

    def then_a_new_chat(txn, deletes):  # the person keeps playing on the phone meanwhile
        stage(txn, deletes)
        put(phone, RecordEnvelope(InstanceRef("c2", "chat", "dockside"), {"participants": [str(WREN_B)], "history": []}))
    monkeypatch.setattr(deletion, "stage", then_a_new_chat)
    with pytest.raises(ResolveRefused, match="delete_dependency"):
        resolve(desktop, phone, path, None, _expected(desktop, phone, path), handles())
    assert (phone.root / path).read_bytes() == edited and edited not in kept(phone)
    monkeypatch.undo()
    assert sync(desktop, phone, handles()).conflict(path).code == "edit_delete"


def test_resolve_toward_the_deletion_of_a_manifest_takes_its_agreed_revisions_along(pair):
    desktop, phone = pair
    for path in (PATHS["manifest"], PATHS["r1"], PATHS["wren_a"], PATHS["wren_b"]):
        (desktop.root / path).unlink()
    for path in (PATHS["wren_a"], PATHS["wren_b"]):  # the phone deletes the users of the source too
        (phone.root / path).unlink()
    txn = phone.transaction()  # and renames the source with wren@r1 as its head: an edit_delete that pins r1
    current = phone.read_source("world:harbor")
    txn.put(SourceManifest(current.manifest.source, current.manifest.owner, "Harbor (old)", {"character/wren": "r1"},
                           current.manifest.links, extra=current.manifest.extra), current.etag)
    txn.commit(handle())
    report = sync(desktop, phone, handles())
    assert report.conflict(PATHS["manifest"]).code == "edit_delete"
    assert report.conflict(PATHS["r1"]).code == "delete_dependency"
    manifest = PATHS["manifest"]
    assert resolve(desktop, phone, manifest, None, _expected(desktop, phone, manifest), handles()) == PHONE
    assert not (phone.root / manifest).exists() and not (phone.root / PATHS["r1"]).exists()
    assert sync(desktop, phone, handles()).conflicts == ()
    assert canonical(desktop) == canonical(phone)


@pytest.mark.parametrize("case", ["both hold it", "both lack it", "something still needs it"])
def test_resolve_toward_the_deletion_is_refused_where_a_sync_would_not_delete(pair, case):
    desktop, phone = pair
    path = PATHS["r1"] if case == "something still needs it" else PATHS["wren_a"]
    if case == "both hold it":
        edit_instance(desktop, WREN_A, 9)
        edit_instance(phone, WREN_A, 4)
    elif case == "both lack it":
        for store in pair:
            (store.root / path).unlink()
    else:
        (desktop.root / path).unlink()
    sync(desktop, phone, handles())
    before = _tree(desktop.root.parent)
    with pytest.raises(ResolveRefused, match="delete_dependency|both devices"):
        resolve(desktop, phone, path, None, _expected(desktop, phone, path), handles())
    after = _tree(desktop.root.parent)
    assert {key for key in after if after.get(key) != before.get(key) and not key.endswith("keeps.json")} == set()


def test_resolve_never_chooses_the_deletion_of_a_create_only_record(pair):
    desktop, phone = pair
    path = "campaigns/c1/images/wren.json"  # 018 image object: create-only
    (phone.root / path).parent.mkdir(parents=True, exist_ok=True)
    (phone.root / path).write_bytes(b"{}")
    with pytest.raises(ResolveRefused):
        resolve(desktop, phone, path, None, {DESKTOP: None, PHONE: etag(b"{}")}, handles())
    assert (phone.root / path).read_bytes() == b"{}"


def test_a_crash_after_resolve_deleted_reaches_the_tombstone(tmp_path, monkeypatch):
    desktop, phone = make_pair(tmp_path)
    path = PATHS["wren_a"]
    (desktop.root / path).unlink()
    edit_instance(phone, WREN_A, 7)
    sync(desktop, phone, handles())

    def die(*args, **kwargs):
        raise RuntimeError("power loss before the archives")
    monkeypatch.setattr(resolution, "_settle", die)
    with pytest.raises(RuntimeError):
        resolve(desktop, phone, path, None, _expected(desktop, phone, path), handles())
    monkeypatch.undo()
    assert not (phone.root / path).exists()
    report = sync(desktop, phone, handles())
    assert report.conflicts == () and report.copied == ()
    for store, peer in ((desktop, PHONE), (phone, DESKTOP)):
        assert "deleted" in _archive(store, peer)[path]
    desktop.close(), phone.close()


# -- under the 006 fault harness: a crash or an I/O error at every commit boundary ---------------------

@functools.cache
def _deleted_fixture() -> tuple[tuple[str, bytes | None], ...]:
    """A synced pair; the desktop deleted wren-b (propagated) and edited wren-a (copied)."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        desktop, phone = make_pair(root)
        (desktop.root / PATHS["wren_b"]).unlink()
        edit_instance(desktop, WREN_A, 9)
        desktop.close(), phone.close()
        return tuple(snapshot(root).items())


def _deletion_scenario() -> CrashScenario:
    locker = ProcessLocker()
    original = dict(_deleted_fixture())[f"{PHONE}/{PATHS['wren_b']}"]

    def setup(ops, root):
        for key, data in _deleted_fixture():
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

    def survives(outcome):
        if not any(value == original for value in outcome.recovered.values() if value is not None):
            return "the deleted version is kept nowhere"
        return None

    def converges(outcome):
        return converged(outcome.recovered, outcome.completed)
    return CrashScenario(
        "deletion", setup, once, once,
        (Invariant("converges", converges), Invariant("deleted_version_survives", survives), idempotent_recovery(),
         no_residue(*(f"{device}/.txn/{name}" for device in PAIR for name in ("*.new", "journal.json")))),
        normalize=drop_empty_dirs)


def _boundaries(trace) -> dict[int, frozenset[str]]:
    """Per commit: crashes before and after its commit point (the journal replace), an error there."""
    return {op.index: frozenset({"before", "after", "fail"}) for op in trace
            if op.kind == "replace" and (op.target or "").endswith("/.txn/journal.json")}


def test_a_deletion_survives_a_crash_or_error_at_every_commit_boundary(tmp_path):
    with tempfile.TemporaryDirectory() as folder:
        baseline = run_plans(_deletion_scenario(), [], Path(folder))
        baseline.assert_ok()
        trace = baseline.runs[0].action_trace
    points = _boundaries(trace)
    commits = [trace[index].target.split("/")[0] for index in sorted(points)]
    # holds(phone), data(phone), archive(phone), archive(desktop), pending(phone): the desktop receives nothing
    assert commits == [PHONE, PHONE, PHONE, DESKTOP, PHONE], commits
    report = explore_crashes(_deletion_scenario(), tmp_path, errors=True, torn=False,
                             select=lambda op: points.get(op.index, frozenset()))
    report.assert_ok()
    assert report.points_explored >= 3 * len(points)


@functools.cache
def _edit_delete_fixture() -> tuple[tuple[str, bytes | None], ...]:
    """The desktop deleted wren-a, the phone edited it: an ``edit_delete`` conflict, reported."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        desktop, phone = make_pair(root)
        (desktop.root / PATHS["wren_a"]).unlink()
        edit_instance(phone, WREN_A, 7)
        assert sync(desktop, phone, handles()).conflict(PATHS["wren_a"]).code == "edit_delete"
        desktop.close(), phone.close()
        return tuple(snapshot(root).items())


def test_resolving_toward_the_deletion_survives_a_crash_or_error_at_every_commit_boundary(tmp_path):
    """Whatever point it stops at, the phone's edited version stays somewhere and a restart's sync
    settles (a crash before the data leaves the conflict open; after it, the tombstone)."""
    locker = ProcessLocker()
    tree = _edit_delete_fixture()
    edited = dict(tree)[f"{PHONE}/{PATHS['wren_a']}"]

    def setup(ops, root):
        for key, data in tree:
            ops.make_dirs(root / key) if data is None else ops.write_bytes(root / key, data)

    def both(function):
        def phase(ops, root):
            desktop = LibraryStore.open(root / DESKTOP, ops=ops, locker=locker)
            try:
                phone = LibraryStore.open(root / PHONE, ops=ops, locker=locker)
                try:
                    function(desktop, phone)
                finally:
                    phone.close()
            finally:
                desktop.close()
        return phase

    def survives(outcome):
        if not any(value == edited for value in outcome.recovered.values() if value is not None):
            return "the phone's edited version is kept nowhere"
        if outcome.recovered.get(f"{DESKTOP}/{PATHS['wren_a']}") is not None:
            return "the deletion was undone"
        return None
    scenario = CrashScenario(
        "resolve-deletion", setup,
        both(lambda desktop, phone: resolve(desktop, phone, PATHS["wren_a"], None,
                                            {DESKTOP: None, PHONE: etag(edited)}, handles())),
        both(lambda desktop, phone: sync(desktop, phone, handles())),
        (Invariant("edited_version_survives", survives), idempotent_recovery()), normalize=drop_empty_dirs)
    with tempfile.TemporaryDirectory() as folder:
        baseline = run_plans(scenario, [], Path(folder))
        baseline.assert_ok()
        trace = baseline.runs[0].action_trace
    points = _boundaries(trace)
    report = explore_crashes(scenario, tmp_path, errors=True, torn=False,
                             select=lambda op: points.get(op.index, frozenset()))
    report.assert_ok()
    assert len(points) >= 5  # holds, data, archives, pending
