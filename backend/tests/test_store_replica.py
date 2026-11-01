"""Store extensions for synchronization (019): exact bytes, kept versions, sync state, preflight, scan."""

from __future__ import annotations

import json
import os

import pytest

from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.security import Approval, Authorizer, Grant, Permission, ResourcePath, payload_digest
from grimoire.store import (
    ImmutableRevision, InvalidEnvelope, LinkRefused, InvalidSourceState, LibraryStore, NameCollision, ProcessLocker,
    RecordEnvelope, RevisionReferenced, StaleWrite, StoreError, WriteConflict, WriteDenied, etag,
)
from grimoire.store.journal import Entry, Journal, parse
from grimoire.store.layout import conflict_parts, is_kept_target, kept_address, sync_state_parts
from grimoire.store.errors import RecoveryFailed
from grimoire.testing import (
    CrashScenario, Interleave, RootedFileOps, Target, atomic_outcome, completes_after, explore_crashes,
    idempotent_recovery, no_residue, preserves_interleave, run_plans,
)
from grimoire.testing.fileops import snapshot

from store_helpers import (
    HARBOR, OWNER, WREN, WREN_A, WREN_R1, drop_empty_dirs, harbor_manifest, instance, make_dir_link,
    owner_handle, seed, wren,
)

ALL = ("sources", "campaigns", "kept")
WREN_A_PATH = "campaigns/c1/instances/character/wren-a.json"
R1_PATH = "sources/world/harbor/_source/records/character/wren/r1.json"
MANIFEST_PATH = "sources/world/harbor/_source/manifest.json"


@pytest.fixture
def pair(tmp_path):
    """Two libraries seeded identically by the real store."""
    stores = []
    for name in ("desktop", "phone"):
        store = LibraryStore.create(tmp_path / name)
        seed(store)
        stores.append(store)
    yield stores
    for store in stores:
        store.close()


def _edited(store, ref=WREN_A, hp=9):
    """Edit an instance through the ordinary ``put`` and return its new exact bytes."""
    current = store.read(ref)
    txn = store.transaction()
    txn.put(instance(ref, WREN_R1, hp=hp), current.etag)
    txn.commit(owner_handle())
    return (store.root / WREN_A_PATH).read_bytes()


# -- put_exact ---------------------------------------------------------------------

def test_exact_bytes_are_published_verbatim_so_two_libraries_converge(pair):
    desktop, phone = pair
    data = _edited(desktop)
    txn = phone.transaction()
    address = txn.put_exact(WREN_A_PATH, data, phone.read(WREN_A).etag)
    assert address == WREN_A_PATH and txn.preview() == {WREN_A_PATH: data}
    result = txn.commit(owner_handle())
    assert result.etags == {WREN_A_PATH: etag(data)}
    assert (phone.root / WREN_A_PATH).read_bytes() == data  # previous is the desktop's, not rewritten


def test_exact_bytes_must_be_a_valid_envelope_at_their_own_path(pair):
    desktop, phone = pair
    data = (desktop.root / WREN_A_PATH).read_bytes()
    document = json.loads(data)
    txn = phone.transaction()
    cases = {
        "malformed": b"{not json",
        "unsupported_format": json.dumps({**document, "format": "grimoire.record/9"}).encode(),
        "address_mismatch": json.dumps({**document, "address": str(InstanceRef("c1", "character", "other"))}).encode(),
    }
    for code, bad in cases.items():
        with pytest.raises(InvalidEnvelope) as refused:
            txn.put_exact(WREN_A_PATH, bad, None)
        assert refused.value.code == code
    for path in ("authority/security/0000000001.json", ".sync/device.json", ".txn/journal.json",
                 ".conflicts/0123456789abcdef/x.json~0123456789ab", "campaigns/c1/instances/character/Wren-a.json",
                 "campaigns/c1/instances/character/wren-a.txt", "grimoire-library.json"):
        with pytest.raises(InvalidEnvelope):
            txn.put_exact(path, data, None)
    manifest = (desktop.root / MANIFEST_PATH).read_bytes()
    with pytest.raises(InvalidEnvelope) as refused:
        txn.put_exact("sources/world/other/_source/manifest.json", manifest, None)
    assert refused.value.code == "address_mismatch"


def test_exact_definition_revisions_are_create_only_and_compare_and_swap_holds(pair):
    desktop, phone = pair
    revision = (desktop.root / R1_PATH).read_bytes()
    txn = phone.transaction()
    with pytest.raises(ImmutableRevision):
        txn.put_exact(R1_PATH, revision, etag(revision))
    txn.put_exact(WREN_A_PATH, _edited(desktop), "sha256:" + "0" * 64)
    with pytest.raises(StaleWrite):
        txn.commit(owner_handle())


def test_an_unreferenced_revision_can_be_replaced_and_a_referenced_one_cannot(pair):
    desktop, phone = pair
    for store in pair:  # r2 exists on both devices with different bytes; heads still name r1
        txn = store.transaction()
        txn.put(wren("r2", "captain" if store is desktop else "pirate"), None)
        txn.put(harbor_manifest({"character/wren": "r1"}), store.read_source(HARBOR).etag)
        txn.commit(owner_handle())
    r2 = R1_PATH.replace("r1.json", "r2.json")
    chosen, replaced = (desktop.root / r2).read_bytes(), (phone.root / r2).read_bytes()
    txn = phone.transaction()
    txn.put_exact(r2, chosen, etag(replaced), replace_unreferenced=True)
    txn.keep_version(r2, replaced)
    txn.commit(owner_handle(*ALL))
    assert (phone.root / r2).read_bytes() == chosen
    assert replaced in [path.read_bytes() for path in (phone.root / ".conflicts").rglob("*") if path.is_file()]
    # r1 is the head of world:harbor and the base of both instances
    r1 = (phone.root / R1_PATH).read_bytes()
    edited = r1.replace(b"a sailor", b"a ghost")
    txn = phone.transaction()
    txn.put_exact(R1_PATH, edited, etag(r1), replace_unreferenced=True)
    with pytest.raises(RevisionReferenced):
        txn.check()
    with pytest.raises(RevisionReferenced):
        txn.commit(owner_handle())
    with pytest.raises(InvalidEnvelope):
        phone.transaction().put_exact(WREN_A_PATH, (phone.root / WREN_A_PATH).read_bytes(), None,
                                      replace_unreferenced=True)


def test_an_unreadable_instance_means_a_revision_cannot_be_proven_unreferenced(pair):
    desktop, phone = pair
    txn = phone.transaction()
    txn.put(wren("r2", "pirate"), None)
    txn.put(harbor_manifest({"character/wren": "r1"}), phone.read_source(HARBOR).etag)
    txn.commit(owner_handle())
    (phone.root / "campaigns/c9/instances/character").mkdir(parents=True)
    (phone.root / "campaigns/c9/instances/character/broken.json").write_bytes(b"{")
    r2 = R1_PATH.replace("r1.json", "r2.json")
    current = (phone.root / r2).read_bytes()
    txn = phone.transaction()
    txn.put_exact(r2, current.replace(b"pirate", b"corsair"), etag(current), replace_unreferenced=True)
    with pytest.raises(RevisionReferenced, match="cannot prove"):
        txn.check()


# -- kept versions -------------------------------------------------------------------

def test_a_kept_version_lands_in_its_slot_under_its_own_address(pair):
    desktop, phone = pair
    theirs = _edited(desktop)
    txn = phone.transaction()
    kept = txn.keep_version(WREN_A_PATH, theirs)
    parts = tuple(WREN_A_PATH.split("/"))
    assert kept == "/".join(conflict_parts(parts, etag(theirs))) and is_kept_target(tuple(kept.split("/")))
    address = str(kept_address(parts, etag(theirs)))
    assert address.startswith("kept/") and len(address.split("/")) == 4
    assert txn.preview() == {address: theirs}
    with pytest.raises(WriteDenied):  # the record's own grant does not cover kept versions
        txn.commit(owner_handle("sources", "campaigns"))
    txn = phone.transaction()
    txn.keep_version(WREN_A_PATH, theirs)
    assert txn.commit(owner_handle(*ALL)).etags == {address: etag(theirs)}
    assert (phone.root / kept).read_bytes() == theirs
    assert (phone.root / WREN_A_PATH).read_bytes() != theirs  # the canonical file is untouched
    reuse = phone.transaction()
    assert reuse.keep_version(WREN_A_PATH, theirs) == kept and reuse.preview() == {}  # nothing staged


def test_a_kept_address_is_a_valid_resource_and_distinct_per_slot():
    parts = tuple(R1_PATH.split("/"))
    deep = ("sources", *[f"s{n}" for n in range(26)], "_source", "records", "character", "wren", "r1.json")
    seen = set()
    for target in (parts, deep, tuple(MANIFEST_PATH.split("/"))):
        for attempt in (0, 1, 3):
            address = kept_address(target, "sha256:" + "ab" * 32, attempt)
            assert isinstance(address, ResourcePath) and len(address.segments) == 4
            assert not address.reserved
            seen.add(address)
    assert len(seen) == 9
    authority = Authorizer([OWNER])
    authority.add_grant(Grant("g", OWNER, frozenset({Permission.APPLY}), ResourcePath.parse("kept"), OWNER))
    assert authority.handle_for(OWNER, trusted=True).authorize(
        Permission.APPLY, str(kept_address(parts, "sha256:" + "ab" * 32)), payload=b"{}").allowed


def test_an_edited_slot_moves_the_kept_version_on_and_a_planned_slot_must_not_move(pair):
    desktop, phone = pair
    theirs = _edited(desktop)
    parts = tuple(WREN_A_PATH.split("/"))
    first = phone.root.joinpath(*conflict_parts(parts, etag(theirs)))
    first.parent.mkdir(parents=True)
    first.write_bytes(b'{"a human": "edited this"}')
    txn = phone.transaction()
    assert txn.keep_version(WREN_A_PATH, theirs).endswith(".1")
    with pytest.raises(InvalidEnvelope):
        txn.keep_version(WREN_A_PATH, theirs)  # one kept version per record per transaction
    with pytest.raises(StaleWrite):
        phone.transaction().keep_version(WREN_A_PATH, theirs, slot=0)
    for bad in (b"not json", b"x" * (8 * 1024 * 1024 + 1)):
        with pytest.raises(InvalidEnvelope):
            phone.transaction().keep_version(WREN_A_PATH, bad)
    with pytest.raises(InvalidEnvelope):
        phone.transaction().keep_version(".sync/device.json", theirs)


def test_approvals_for_a_kept_version_and_the_record_are_different(pair):
    desktop, phone = pair
    theirs = _edited(desktop)
    authority = _sync_app("campaigns", "kept")
    txn = phone.transaction()
    txn.put_exact(WREN_A_PATH, theirs, phone.read(WREN_A).etag)
    mine = (phone.root / WREN_A_PATH).read_bytes()
    txn.keep_version(WREN_A_PATH, mine)
    preview = txn.preview()
    assert len(preview) == 2 and WREN_A_PATH in preview
    kept = next(key for key in preview if key.startswith("kept/"))
    for number, key in enumerate((WREN_A_PATH, kept)):
        authority.register_approval(Approval(f"a{number}", "app:sync", Permission.APPLY, ResourcePath.parse(key),
                                             payload_digest(preview[key]), OWNER))
    txn.commit(authority.handle_for("app:sync"), {WREN_A_PATH: "a0", kept: "a1"})
    assert authority.pending_approvals() == ()


# -- sync state ------------------------------------------------------------------------

def test_sync_state_is_trusted_bookkeeping_with_strict_names(pair):
    desktop, phone = pair
    nobody = Authorizer([OWNER]).handle_for(OWNER, trusted=True)  # no grants at all
    txn = phone.transaction()
    key = txn.put_sync_state("peers/desktop/archive.json", b'{"format": "x"}\n', None)
    assert key == ".sync/peers/desktop/archive.json" and txn.preview() == {key: b'{"format": "x"}\n'}
    txn.commit(nobody)
    assert phone.read_sync_state("peers/desktop/archive.json") == b'{"format": "x"}\n'
    txn = phone.transaction()  # rewriting an existing state file is not an "unsupported format" (P7)
    txn.put_sync_state("peers/desktop/archive.json", b'{"format": "y"}\n', etag(b'{"format": "x"}\n'))
    txn.commit(nobody)
    assert phone.read_sync_state("peers/desktop/archive.json") == b'{"format": "y"}\n'
    assert phone.read_sync_state("device.json") is None
    for name in ("peers/con/archive.json", "peers/../archive.json", "peers/Desk/archive.json",
                 "peers/desktop/other.json", "device", "peers/desktop", "../x.json"):
        with pytest.raises(ValueError):
            sync_state_parts(name)
    with pytest.raises(InvalidEnvelope):
        phone.transaction().put_sync_state("device.json", b"not json", None)
    assert ".sync" not in {problem.path.split("/")[0] for problem in phone.load().problems}


# -- preflight -------------------------------------------------------------------------

def test_check_raises_what_commit_would_and_writes_or_consumes_nothing(tmp_path):
    locker = ProcessLocker()
    LibraryStore.create(tmp_path / "lib", locker=locker).close()
    with LibraryStore.open(tmp_path / "lib", locker=locker) as store:
        seed(store)
    ops = RootedFileOps(tmp_path)
    with LibraryStore.open(tmp_path / "lib", ops=ops, locker=locker) as store:
        ops.trace.clear()
        txn = store.transaction()
        txn.check()  # library-level checks only
        txn.put(RecordEnvelope(SourceRef(HARBOR, "item", "wren", "r1"), {"x": 1}), None)  # kind reuse
        with pytest.raises(InvalidSourceState):
            txn.check()
        stale = store.transaction()
        stale.put_exact(WREN_A_PATH, (store.root / WREN_A_PATH).read_bytes(), None)
        with pytest.raises(StaleWrite):
            stale.check()
        assert not any(op.mutating for op in ops.trace)


def test_check_refuses_a_linked_sync_folder(tmp_path):
    store = LibraryStore.create(tmp_path / "lib")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    make_dir_link(store.root / ".sync", outside)
    with pytest.raises(LinkRefused):
        store.transaction().check()
    store.close()


# -- partial commits -------------------------------------------------------------------

def _sync_app(*scopes):
    """An untrusted app with grants that still need a human's approval per write."""
    authority = Authorizer([OWNER])
    for number, scope in enumerate(scopes):
        authority.add_grant(Grant(f"s{number}", "app:sync", frozenset({Permission.APPLY}),
                                  ResourcePath.parse(scope), OWNER))
    return authority


def _scoped(*scopes):
    authority = Authorizer([OWNER])
    for number, scope in enumerate(scopes):
        authority.add_grant(Grant(f"g{number}", OWNER, frozenset({Permission.APPLY}),
                                  ResourcePath.parse(scope), OWNER))
    return authority


def test_a_partial_commit_drops_path_denials_and_what_the_caller_says_depends_on_them(pair):
    desktop, phone = pair
    authority = _scoped("campaigns/c1", "kept")
    txn = phone.transaction()
    txn.put(instance(InstanceRef("c1", "character", "new"), WREN_R1), None)
    txn.put(instance(InstanceRef("c2", "character", "denied"), WREN_R1), None)
    txn.put(instance(InstanceRef("c1", "character", "dependent"), WREN_R1), None)
    txn.put_sync_state("peers/desktop/pending.json", b'{"v": 1}\n', None)
    seen = []

    def on_denied(keys):
        seen.append(keys)
        return {"campaigns/c1/instances/character/dependent.json": None,
                ".sync/peers/desktop/pending.json": b'{"v": 2}\n'}

    result = txn.commit(authority.handle_for(OWNER, trusted=True), on_denied=on_denied)
    assert seen == [frozenset({"campaigns/c2/instances/character/denied.json"})]
    assert result.denied == seen[0]
    assert result.dropped == {"campaigns/c1/instances/character/dependent.json"}
    assert set(result.etags) == {"campaigns/c1/instances/character/new.json", ".sync/peers/desktop/pending.json"}
    assert phone.read_sync_state("peers/desktop/pending.json") == b'{"v": 2}\n'
    assert not (phone.root / "campaigns/c2/instances/character/denied.json").exists()


def test_a_partial_commit_reruns_source_checks_and_refuses_bad_callbacks(pair):
    desktop, phone = pair
    authority = _scoped("sources/world/harbor/_source/records")  # revisions yes, manifest no
    txn = phone.transaction()
    txn.put(wren("r2", "captain"), None)
    txn.put(wren("r3", "admiral"), None)
    txn.put(harbor_manifest({"character/wren": "r3"}), phone.read_source(HARBOR).etag)
    with pytest.raises(InvalidSourceState):  # the callback missed that r2+r3 now lack a head
        txn.commit(authority.handle_for(OWNER, trusted=True), on_denied=lambda keys: {})
    for bad in ({"campaigns/nope.json": None}, {R1_PATH.replace("r1", "r2"): b"{}"}):
        txn = phone.transaction()
        txn.put(wren("r2", "captain"), None)
        txn.put(harbor_manifest({"character/wren": "r2"}), phone.read_source(HARBOR).etag)
        with pytest.raises(InvalidEnvelope):
            txn.commit(authority.handle_for(OWNER, trusted=True), on_denied=lambda keys, bad=bad: bad)


def test_a_partial_commit_with_everything_denied_stages_nothing(pair):
    desktop, phone = pair
    before = snapshot(phone.root)
    txn = phone.transaction()
    txn.put(instance(InstanceRef("c3", "character", "x"), WREN_R1), None)
    result = txn.commit(_scoped("kept").handle_for(OWNER, trusted=True), on_denied=lambda keys: {})
    assert result.etags == {} and result.denied == {"campaigns/c3/instances/character/x.json"}
    assert snapshot(phone.root) == before


def test_denials_that_are_not_about_a_path_still_refuse_the_whole_commit(pair):
    desktop, phone = pair
    txn = phone.transaction()
    txn.put(instance(InstanceRef("c3", "character", "x"), WREN_R1), None)

    class Broken:
        def authorize(self, *args, **kwargs):
            from grimoire.security.authorizer import Decision
            from grimoire.security.policy import Reason
            return Decision(False, Reason.INTERNAL_ERROR)

    with pytest.raises(WriteDenied):
        txn.commit(Broken(), on_denied=lambda keys: pytest.fail("never called"))


def test_a_failing_callback_names_the_approvals_it_consumed(pair):
    desktop, phone = pair
    authority = _sync_app("campaigns/c1")
    txn = phone.transaction()
    address = txn.put(instance(InstanceRef("c1", "character", "x"), WREN_R1), None)
    txn.put(instance(InstanceRef("c2", "character", "y"), WREN_R1), None)
    authority.register_approval(Approval("a1", "app:sync", Permission.APPLY, ResourcePath.parse(address),
                                         payload_digest(txn.preview()[address]), OWNER))

    def explode(keys):
        raise RuntimeError("bug")

    with pytest.raises(RuntimeError) as failed:
        txn.commit(authority.handle_for("app:sync"), {address: "a1"}, on_denied=explode)
    assert any("a1" in note for note in failed.value.__notes__)


# -- scan ------------------------------------------------------------------------------

def test_scan_lists_canonical_files_and_names_what_it_cannot_use(pair):
    desktop, phone = pair
    root = phone.root
    (root / "worlds").mkdir()
    (root / "notes.txt").write_text("mine")
    (root / "campaigns/c1/instances/character/.DS_Store").write_bytes(b"")
    (root / "campaigns/c1/instances/character/broken.json").write_bytes(b"{")
    (root / "campaigns/c1/instances/character/readme.md").write_text("x")
    (root / "campaigns/c1/scenes").mkdir()
    folder = root / "campaigns/c1/instances/item"
    folder.mkdir()
    (folder / "lamp.json").write_bytes(b"{}")
    try:
        (folder / "Lamp.json").write_bytes(b"{}")
        lookalikes = len(os.listdir(folder)) == 2
    except OSError:
        lookalikes = False
    found = phone.scan()
    assert set(found.files) == {MANIFEST_PATH, R1_PATH, WREN_A_PATH, "campaigns/c2/instances/character/wren-b.json"}
    assert found.files[WREN_A_PATH] == (root / WREN_A_PATH).read_bytes()
    assert found.blocked["campaigns/c1/instances/character/broken.json"] == "malformed"
    if lookalikes:
        assert found.blocked["campaigns/c1/instances/item/lamp.json"] == "lookalike"
    else:
        assert found.blocked["campaigns/c1/instances/item/lamp.json"] == "malformed"
    assert {"worlds", "notes.txt", "campaigns/c1/instances/character/readme.md"} <= set(found.skipped)
    assert not any(path.startswith((".txn", ".conflicts", ".sync", "grimoire-library")) for path in
                   [*found.files, *found.blocked, *found.skipped])


def test_scan_blocks_links_without_following_them(pair, tmp_path):
    desktop, phone = pair
    outside = tmp_path / "outside"
    (outside / "instances/character").mkdir(parents=True)
    (outside / "instances/character/ghost.json").write_bytes((phone.root / WREN_A_PATH).read_bytes())
    make_dir_link(phone.root / "campaigns" / "c7", outside)
    found = phone.scan()
    assert found.blocked["campaigns/c7"] == "link"
    assert found.blocked_reason("campaigns/c7/instances/character/ghost.json") == "link"
    assert not any(path.startswith("campaigns/c7/") for path in found.files)


# -- journal kinds and abort ------------------------------------------------------------

def test_the_journal_validates_each_entry_kind_against_its_target():
    kept = ".conflicts/0123456789abcdef/wren-a.json~0123456789ab"
    good = {"publish": WREN_A_PATH, "keep": kept, "state": ".sync/peers/desktop/pending.json"}
    tag = "sha256:" + "0" * 64
    for kind, target in good.items():
        data = Journal("commit", (Entry(tuple(target.split("/")), "rec~tmp1.new", None, tag, kind),)).render()
        assert parse(data).entries[0].kind == kind
    for kind, target in [("publish", kept), ("keep", WREN_A_PATH), ("state", kept), ("keep", kept + ".0"),
                         ("keep", ".conflicts/0123456789ABCDEF/wren-a.json~0123456789ab"),
                         ("state", ".sync/notes.json"), ("delete", WREN_A_PATH)]:
        data = Journal("commit", (Entry(tuple(target.split("/")), "rec~tmp1.new", None, tag, kind),)).render()
        with pytest.raises(RecoveryFailed):
            parse(data)


def test_an_aborted_commit_drops_kept_versions_and_state_instead_of_nesting_them(tmp_path):
    locker = ProcessLocker()
    with LibraryStore.create(tmp_path / "lib", locker=locker) as store:
        seed(store)
    theirs = b'{"theirs": true}\n'

    def action(ops, root):
        with LibraryStore.open(root / "lib", ops=ops, locker=locker) as store:
            txn = store.transaction()
            current = store.read(WREN_A)
            txn.put(instance(WREN_A, WREN_R1, hp=7), current.etag)
            txn.keep_version(WREN_A_PATH, theirs)
            txn.put_sync_state("peers/desktop/pending.json", b"{}\n", None)
            txn.commit(owner_handle(*ALL))

    def human(ops, root):
        ops.write_bytes(root / "lib" / WREN_A_PATH, b'{"a human": 1}\n')

    template = snapshot(tmp_path / "lib")
    scenario = CrashScenario(
        "abort with kept and state", lambda ops, root: _replay(ops, root, template), action,
        lambda ops, root: LibraryStore.open(root / "lib", ops=ops, locker=locker).close(),
        (preserves_interleave(), idempotent_recovery(), no_residue("lib/.txn/*.new", "lib/.txn/journal.json")),
        normalize=drop_empty_dirs)
    guard = Target("read_bytes", path=f"lib/{WREN_A_PATH}", nth=3)  # the pre-publish guard read
    report = run_plans(scenario, [[Interleave(guard, human)]], tmp_path / "work")
    report.assert_ok()
    run = report.runs[1]
    assert "WriteConflict" in run.action_error
    conflicts = [key for key, value in run.recovered.items() if key.startswith("lib/.conflicts/") and value]
    assert len(conflicts) == 1 and run.recovered[conflicts[0]] != theirs  # only the record's own bytes
    assert "lib/.sync/peers/desktop/pending.json" not in run.recovered


def _replay(ops, root, template):
    for key, data in sorted(template.items()):
        if data is None:
            ops.make_dirs(root / "lib" / key)
        else:
            ops.write_bytes(root / "lib" / key, data)


def test_a_mixed_commit_survives_every_crash_and_error(tmp_path):
    locker = ProcessLocker()
    with LibraryStore.create(tmp_path / "lib", locker=locker) as store:
        seed(store)
        theirs = (store.root / WREN_A_PATH).read_bytes().replace(b'"hp": 3', b'"hp": 5')
    template = snapshot(tmp_path / "lib")

    def action(ops, root):
        with LibraryStore.open(root / "lib", ops=ops, locker=locker) as store:
            txn = store.transaction()
            txn.put_exact("campaigns/c4/instances/character/new.json",
                          theirs.replace(b"c1/character/wren-a", b"c4/character/new"), None)
            txn.keep_version(WREN_A_PATH, theirs)
            txn.put_sync_state("peers/desktop/pending.json", b"{}\n", None)
            txn.commit(owner_handle(*ALL))

    scenario = CrashScenario(
        "exact + kept + state", lambda ops, root: _replay(ops, root, template), action,
        lambda ops, root: LibraryStore.open(root / "lib", ops=ops, locker=locker).close(),
        (atomic_outcome(), idempotent_recovery(), no_residue("lib/.txn/*.new", "lib/.txn/journal.json"),
         completes_after(Target("replace", target="lib/.txn/journal.json"))),
        normalize=drop_empty_dirs)
    explore_crashes(scenario, tmp_path / "errors", errors=True).assert_ok()
    report = explore_crashes(scenario, tmp_path / "recovery", recovery_crashes=True,
                             select=lambda op: op.kind == "replace")
    report.assert_ok()
    assert report.points_explored > 10


def test_r7_window_is_only_the_replace_when_a_file_is_created(tmp_path):
    """P3: a new file's directories are created before its guard read, not after."""
    locker = ProcessLocker()
    with LibraryStore.create(tmp_path / "lib", locker=locker) as store:
        seed(store)
    template = snapshot(tmp_path / "lib")
    target = "lib/campaigns/c8/instances/character/new.json"

    def action(ops, root):
        with LibraryStore.open(root / "lib", ops=ops, locker=locker) as store:
            txn = store.transaction()
            txn.put(instance(InstanceRef("c8", "character", "new"), WREN_R1), None)
            txn.commit(owner_handle())

    def human(ops, root):
        ops.make_dirs(root / "lib/campaigns/c8/instances/character")
        ops.write_bytes(root / target, b'{"human": 1}\n')

    scenario = CrashScenario("create", lambda ops, root: _replay(ops, root, template), action,
                             lambda ops, root: LibraryStore.open(root / "lib", ops=ops, locker=locker).close(),
                             (preserves_interleave(),), normalize=drop_empty_dirs)
    baseline = run_plans(scenario, [], tmp_path / "w").runs[0].action_trace
    commit = next(op.index for op in baseline if op.kind == "replace" and op.target == "lib/.txn/journal.json")
    publish = next(op.index for op in baseline if op.kind == "replace" and op.target == target)
    report = run_plans(scenario, [[Interleave(index, human)] for index in range(commit + 1, publish)],
                       tmp_path / "w")
    report.assert_ok()  # every position up to the replace keeps the human's file
    lost = run_plans(scenario, [[Interleave(publish, human)]], tmp_path / "w")
    assert "preserves_interleave" in {violation.invariant for violation in lost.violations}  # R7, pinned
