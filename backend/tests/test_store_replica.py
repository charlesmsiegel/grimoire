"""Store extensions for synchronization (019): exact bytes, kept versions, sync state, preflight, scan."""

from __future__ import annotations

import json
import os

import pytest

from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.security import Approval, Authorizer, Grant, Permission, ResourcePath, payload_digest
from grimoire.store import (
    ImmutableRevision, InvalidEnvelope, LinkRefused, LinkSpec, SourceManifest, InvalidSourceState, LibraryStore, NameCollision, ProcessLocker,
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
    txn.keep_version(R1_PATH, r1)
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
    txn.keep_version(r2, current)
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
    assert reuse.keep_version(WREN_A_PATH, theirs, regenerable=True) == kept and reuse.preview() == {}  # nothing staged


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
            txn.keep_version(WREN_A_PATH, theirs, regenerable=REGENERABLE_KEEP[0])
            txn.put_sync_state("peers/desktop/pending.json", b"{}\n", None)
            txn.commit(owner_handle(*ALL))

    def human(ops, root):
        ops.write_bytes(root / "lib" / WREN_A_PATH, b'{"a human": 1}\n')

    REGENERABLE_KEEP = [True]

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
    # a kept version that may be the only copy (resolve's replaced version: ``hold``) is preserved
    REGENERABLE_KEEP[0] = False
    run = run_plans(scenario, [[Interleave(guard, human)]], tmp_path / "hold").runs[1]
    held = [key for key, value in run.recovered.items() if key.startswith("lib/.conflicts/") and value == theirs]
    assert len(held) == 1 and is_kept_target(tuple(held[0].split("/")[1:]))  # in the record's own folder


def _replay(ops, root, template):
    for key, data in sorted(template.items()):
        if data is None:
            ops.make_dirs(root / "lib" / key)
        else:
            ops.write_bytes(root / "lib" / key, data)


@pytest.mark.parametrize("regenerable", [False, True], ids=["publish-and-hold", "replica-and-keep"])
def test_a_mixed_commit_survives_every_crash_and_error(tmp_path, regenerable):
    """Both journal flavours: plain exact puts and kept versions, and the ones sync uses."""
    locker = ProcessLocker()
    with LibraryStore.create(tmp_path / "lib", locker=locker) as store:
        seed(store)
        theirs = (store.root / WREN_A_PATH).read_bytes().replace(b'"hp": 3', b'"hp": 5')
    template = snapshot(tmp_path / "lib")

    def action(ops, root):
        with LibraryStore.open(root / "lib", ops=ops, locker=locker) as store:
            txn = store.transaction()
            txn.put_exact("campaigns/c4/instances/character/new.json",
                          theirs.replace(b"c1/character/wren-a", b"c4/character/new"), None,
                          regenerable=regenerable)
            txn.keep_version(WREN_A_PATH, theirs, regenerable=regenerable)
            txn.put_sync_state("peers/desktop/pending.json", b"{}\n", None)
            txn.commit(owner_handle(*ALL))

    scenario = CrashScenario(
        "exact + kept + state", lambda ops, root: _replay(ops, root, template), action,
        lambda ops, root: LibraryStore.open(root / "lib", ops=ops, locker=locker).close(),
        (atomic_outcome(), idempotent_recovery(), no_residue("lib/.txn/*.new", "lib/.txn/journal.json"),
         completes_after(Target("replace", target="lib/.txn/journal.json"))),
        normalize=drop_empty_dirs)
    explore_crashes(scenario, tmp_path / "errors", errors=True).assert_ok()
    if not regenerable:
        return  # publish/hold recovery is the store's ordinary table, crash-explored in test_store_recovery
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


def test_an_aborted_commit_drops_exact_copies_instead_of_keeping_orphans(tmp_path):
    """Another library's bytes (put_exact) are regenerable: an abort never parks them in .conflicts."""
    locker = ProcessLocker()
    with LibraryStore.create(tmp_path / "lib", locker=locker) as store:
        seed(store)
        theirs = (store.root / WREN_A_PATH).read_bytes().replace(b'"hp": 3', b'"hp": 6')
    template = snapshot(tmp_path / "lib")

    def action(ops, root):
        with LibraryStore.open(root / "lib", ops=ops, locker=locker) as store:
            txn = store.transaction()
            txn.put_exact(WREN_A_PATH, theirs, store.read(WREN_A).etag, regenerable=REGENERABLE[0])
            txn.commit(owner_handle(*ALL))

    def human(ops, root):
        ops.write_bytes(root / "lib" / WREN_A_PATH, b'{"a human": 1}\n')

    REGENERABLE = [True]
    scenario = CrashScenario(
        "abort a replica", lambda ops, root: _replay(ops, root, template), action,
        lambda ops, root: LibraryStore.open(root / "lib", ops=ops, locker=locker).close(),
        (preserves_interleave(), idempotent_recovery()), normalize=drop_empty_dirs)
    guard = Target("read_bytes", path=f"lib/{WREN_A_PATH}", nth=3)
    report = run_plans(scenario, [[Interleave(guard, human)]], tmp_path / "work")
    report.assert_ok()
    run = report.runs[1]
    assert "WriteConflict" in run.action_error
    assert not [key for key, value in run.recovered.items() if key.startswith("lib/.conflicts/") and value]
    REGENERABLE[0] = False  # without the flag, put_exact's bytes are preserved like any put
    run = run_plans(scenario, [[Interleave(guard, human)]], tmp_path / "plain").runs[1]
    assert theirs in [value for key, value in run.recovered.items() if key.startswith("lib/.conflicts/")]



# -- brutal-review round 1 regressions ---------------------------------------------------------

def test_recovery_never_shadows_a_parent_renamed_to_a_lookalike(tmp_path):
    """A crash after the commit point, then the new folder renamed by case while down: recovery
    preserves the version instead of creating a second, differently cased folder."""
    from grimoire.testing import Crash, SimulatedCrash
    LibraryStore.create(tmp_path / "lib").close()
    with LibraryStore.open(tmp_path / "lib") as store:
        seed(store)
    locker = ProcessLocker()
    ops = RootedFileOps(tmp_path, plan=[Crash(Target("replace", target="lib/.txn/journal.json"), "after")])
    store = LibraryStore.open(tmp_path / "lib", ops=ops, locker=locker)
    txn = store.transaction()
    txn.put(instance(InstanceRef("c3", "creature", "pup"), WREN_R1), None)
    with pytest.raises(SimulatedCrash):
        txn.commit(owner_handle())
    store.close()
    campaigns = tmp_path / "lib" / "campaigns"
    os.rename(campaigns / "c3", campaigns / "C3")
    if (campaigns / "c3").exists():
        pytest.skip("a case-insensitive filesystem: no look-alike folder can exist")
    with LibraryStore.open(tmp_path / "lib", locker=locker) as store:
        report = store.last_recovery
    assert sorted(os.listdir(campaigns)) == ["C3", "c1", "c2"]
    assert report.preserved and not report.published


def test_a_commit_inside_a_read_only_phase_is_refused(pair):
    desktop, phone = pair
    with phone.cached_reads():
        txn = phone.transaction()
        txn.put(instance(InstanceRef("c3", "character", "x"), WREN_R1), None)
        with pytest.raises(StoreError, match="read-only phase"):
            txn.commit(owner_handle())


def test_a_stray_sync_folder_never_blocks_an_ordinary_write(pair, tmp_path):
    desktop, phone = pair
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    make_dir_link(phone.root / ".sync", outside)
    txn = phone.transaction()
    txn.put(instance(InstanceRef("c3", "character", "x"), WREN_R1), None)
    txn.commit(owner_handle())  # no sync state in this commit: the linked .sync is not its business
    state = phone.transaction()
    state.put_sync_state("device.json", b"{}\n", None)
    with pytest.raises(LinkRefused):
        state.check()


def _cards(store):
    """catalog:cards with an entity whose r1 is no longer the head (r2 is)."""
    from store_helpers import OWNER as owner
    txn = store.transaction()
    txn.put(SourceManifest("catalog:cards", owner, "Cards", {"lore/notes": "r2"}), None)
    for revision in ("r1", "r2"):
        txn.put(RecordEnvelope(SourceRef("catalog:cards", "lore", "notes", revision), {"r": revision}), None)
    txn.commit(owner_handle())


R1 = SourceRef("catalog:cards", "lore", "notes", "r1")
R1_FILE = "sources/catalog/cards/_source/records/lore/notes/r1.json"


def _replace_r1(store):
    current = (store.root / R1_FILE).read_bytes()
    txn = store.transaction()
    txn.put_exact(R1_FILE, current.replace(b'"r1"', b'"other"'), etag(current), replace_unreferenced=True)
    txn.keep_version(R1_FILE, current)
    txn.check()


def test_an_unreferenced_old_revision_can_be_replaced(pair):
    _cards(pair[1])
    _replace_r1(pair[1])  # nothing pins r1 any more


@pytest.mark.parametrize("pin", ["head", "link", "link-subject", "instance", "derived-from", "instance-payload",
                                 "definition-payload", "chat-pinned", "chat-resolved", "knowledge"])
def test_each_reference_rule_alone_pins_a_revision(pair, pin):
    """One pin per case, so every rule in check_unreferenced is proven on its own."""
    from store_helpers import OWNER as owner
    from grimoire.store import ScopeRecord
    from grimoire.core.identity import ChatRef
    desktop, phone = pair
    _cards(phone)
    txn = phone.transaction()
    if pin == "head":
        txn.put(SourceManifest("catalog:cards", owner, "Cards", {"lore/notes": "r1"}),
                phone.read_source("catalog:cards").etag)
        message = "as its head"
    elif pin == "link":
        other = SourceRef("catalog:cards", "lore", "other", "r1")
        txn.put(RecordEnvelope(other, {}), None)
        txn.put(SourceManifest("catalog:cards", owner, "Cards", {"lore/notes": "r2"}, (
            LinkSpec(str(other), "derived_variant", str(R1)),)), phone.read_source("catalog:cards").etag)
        message = "links"
    elif pin == "link-subject":
        other = SourceRef("catalog:cards", "lore", "other", "r1")
        txn.put(RecordEnvelope(other, {}), None)
        txn.put(SourceManifest("catalog:cards", owner, "Cards", {"lore/notes": "r2"}, (
            LinkSpec(str(R1), "derived_variant", str(other)),)), phone.read_source("catalog:cards").etag)
        message = "links"
    elif pin == "instance":
        txn.put(RecordEnvelope(InstanceRef("c9", "lore", "copy"), {}, base=str(R1)), None)
        message = "is based on"
    elif pin == "derived-from":
        txn.put(RecordEnvelope(InstanceRef("c9", "lore", "copy"), {}, derived_from=str(R1)), None)
        message = "is based on"
    elif pin == "instance-payload":  # 009's codec stores a pinned source inside the payload
        txn.put(RecordEnvelope(InstanceRef("c9", "lore", "copy"), {"source": str(R1)}), None)
        message = "in its payload"
    elif pin == "definition-payload":
        txn.put(RecordEnvelope(SourceRef("catalog:cards", "lore", "sequel", "r1"), {"after": str(R1)}), None)
        message = "in its payload"
    elif pin == "knowledge":
        from grimoire.core.identity import KnowledgeRef
        from grimoire.store import KnowledgeRecord
        txn.put(KnowledgeRecord(KnowledgeRef("c9", "evidence", "e-1"), "grimoire.test/0", {"about": str(R1)}), None)
        message = "binds"
    else:
        binding = ({"ref": str(R1)} if pin == "chat-pinned"
                   else {"ref": str(R1.unpinned()), "resolved_revision": "r1"})
        txn.put(ScopeRecord(ChatRef("c9", "t-1"), "grimoire.chat/0", {"bindings": [binding]}), None)
        message = "binds"
    txn.commit(owner_handle())
    with pytest.raises(RevisionReferenced, match=message):
        _replace_r1(phone)



def test_only_a_blocked_path_that_could_hold_records_blocks_a_revision_replace(pair, tmp_path):
    desktop, phone = pair
    _cards(phone)
    outside = tmp_path / "outside"
    outside.mkdir()
    make_dir_link(phone.root / "backup", outside)  # a root-level link: holds no records
    (phone.root / "sources/catalog/cards/notes.md").write_text("mine")
    (phone.root / "sources/catalog/cards/Notes.md").write_text("look-alike")
    _replace_r1(phone)  # neither can pin anything
    make_dir_link(phone.root / "campaigns" / "c8", outside)  # could hold instances
    with pytest.raises(RevisionReferenced, match="cannot prove"):
        _replace_r1(phone)


def test_a_kept_slot_must_be_a_small_non_negative_int(pair):
    desktop, phone = pair
    data = (phone.root / WREN_A_PATH).read_bytes()
    for slot in (-1, 10 ** 10, True, "0"):
        with pytest.raises(InvalidEnvelope, match="slot"):
            phone.transaction().keep_version(WREN_A_PATH, data, slot=slot)


def test_exact_kinds_cover_every_synced_kind_and_nothing_of_018():
    from grimoire.store.replica import EXACT_KINDS
    from grimoire.sync.model import SYNCED_KINDS
    assert SYNCED_KINDS <= EXACT_KINDS
    assert not EXACT_KINDS & {"blob", "image", "placement", "ledger"}


@pytest.mark.parametrize("path", ["campaigns/c1/images/portrait.json",
                                  "sources/world/harbor/_source/placements/character/wren/portrait.json"])
def test_put_exact_refuses_018_paths(pair, path):
    with pytest.raises(InvalidEnvelope) as caught:
        pair[0].transaction().put_exact(path, b"{}", None)
    assert caught.value.code == "not_canonical"


# -- brutal-review round 3: a revision replace never loses the replaced bytes -------------------------

def _r1_now_history(store):
    """catalog:cards' r1 is unreferenced history; return (current bytes, a different version)."""
    _cards(store)
    current = (store.root / R1_FILE).read_bytes()
    return current, current.replace(b'"r1"', b'"other"')


def test_a_revision_replace_needs_the_replaced_bytes_kept_beside_it(pair):
    phone = pair[1]
    current, other = _r1_now_history(phone)
    txn = phone.transaction()
    txn.put_exact(R1_FILE, other, etag(current), replace_unreferenced=True)
    with pytest.raises(InvalidEnvelope, match="kept in the same transaction"):
        txn.check()
    with pytest.raises(InvalidEnvelope, match="kept in the same transaction"):
        txn.commit(owner_handle(*ALL))
    assert (phone.root / R1_FILE).read_bytes() == current
    wrong = phone.transaction()  # a kept version of other bytes does not count
    wrong.put_exact(R1_FILE, other, etag(current), replace_unreferenced=True)
    wrong.keep_version(R1_FILE, other)
    with pytest.raises(InvalidEnvelope, match="kept in the same transaction"):
        wrong.check()


def test_a_revision_replace_commits_alone_so_no_write_can_pin_it_unseen(pair):
    phone = pair[1]
    current, other = _r1_now_history(phone)
    txn = phone.transaction()
    txn.put_exact(R1_FILE, other, etag(current), replace_unreferenced=True)
    txn.keep_version(R1_FILE, current)
    txn.put(RecordEnvelope(InstanceRef("c9", "lore", "copy"), {}, base=str(R1)), None)  # a new pin, same commit
    with pytest.raises(InvalidEnvelope, match="committed alone"):
        txn.check()
    with pytest.raises(InvalidEnvelope, match="committed alone"):
        txn.commit(owner_handle(*ALL))
    assert (phone.root / R1_FILE).read_bytes() == current


def test_a_denied_kept_copy_drops_its_revision_replace_in_partial_mode(pair):
    phone = pair[1]
    current, other = _r1_now_history(phone)
    txn = phone.transaction()
    key = txn.put_exact(R1_FILE, other, etag(current), replace_unreferenced=True)
    txn.keep_version(R1_FILE, current)
    result = txn.commit(owner_handle("sources"), on_denied=lambda keys: {})  # kept/ is not granted
    assert key in result.dropped and key not in result.etags
    assert (phone.root / R1_FILE).read_bytes() == current


def test_a_pin_in_a_scope_records_extra_fields_counts(pair):
    from grimoire.core.identity import ChatRef
    from grimoire.store import ScopeRecord
    phone = pair[1]
    _cards(phone)
    txn = phone.transaction()
    txn.put(ScopeRecord(ChatRef("c9", "t-1"), "grimoire.chat/0", {"seq": 0}, extra={"x-pin": str(R1)}), None)
    txn.commit(owner_handle())
    with pytest.raises(RevisionReferenced, match="binds"):
        _replace_r1(phone)


@pytest.mark.parametrize("folder", ["sources/world/harbor/_source/images", "sources/world/harbor/_source/placements",
                                    "campaigns/c1/images", "campaigns/c1/placements", "campaigns/c1/scenes"])
def test_a_linked_018_folder_under_a_source_never_blocks_a_revision_replace(pair, tmp_path, folder):
    phone = pair[1]
    _cards(phone)
    outside = tmp_path / "art"
    outside.mkdir()
    (phone.root / folder).parent.mkdir(parents=True, exist_ok=True)
    make_dir_link(phone.root / folder, outside)
    _replace_r1(phone)  # images and placements attach to entities, never to revisions


def test_a_kept_version_is_only_for_records_recovery_accepts(pair):
    """Recovery accepts only ``.json`` kept paths, so keep_version refuses blobs (and other kinds) up front."""
    for path in ("assets/blobs/ab/" + "ab" * 32 + ".png", "campaigns/c1/images/portrait.json"):
        with pytest.raises(InvalidEnvelope, match="can have kept versions"):
            pair[0].transaction().keep_version(path, b"{}")



def test_recovery_recreates_the_folder_of_a_committed_create(tmp_path, monkeypatch):
    """R7 narrowing: recovery's publish makes a missing parent first (a tidied-away empty folder)."""
    import shutil
    from grimoire.store import engine
    root = tmp_path / "lib"
    store = LibraryStore.create(root)
    seed(store)
    txn = store.transaction()
    txn.put(instance(InstanceRef("c8", "character", "new"), WREN_R1), None)

    def die(*args, **kwargs):
        raise RuntimeError("the process died right after the commit point")

    monkeypatch.setattr(engine, "settle", die)
    with pytest.raises(RuntimeError):
        txn.commit(owner_handle())
    monkeypatch.undo()
    assert (root / ".txn" / "journal.json").exists()
    store.close()
    shutil.rmtree(root / "campaigns" / "c8")  # an empty folder tidied away while the app was down
    reopened = LibraryStore.open(root)  # 010 recovery finishes the committed create
    try:
        assert (root / "campaigns/c8/instances/character/new.json").exists()
        assert InstanceRef("c8", "character", "new") in reopened.load().instances
    finally:
        reopened.close()


def test_a_revision_replace_keeps_the_replaced_bytes_as_a_hold_never_a_regenerable_keep(pair):
    phone = pair[1]
    current, other = _r1_now_history(phone)
    txn = phone.transaction()
    txn.put_exact(R1_FILE, other, etag(current), replace_unreferenced=True)
    txn.keep_version(R1_FILE, current, regenerable=True)  # an abort would drop it: the only copy lost
    with pytest.raises(InvalidEnvelope, match="must be a hold"):
        txn.check()


def test_recovery_refuses_a_journal_that_would_overwrite_a_kept_version():
    from grimoire.store.jsonfmt import render
    target = ".conflicts/" + "0" * 16 + "/r1.json~" + "a" * 12
    for kind in ("keep", "hold"):
        document = {"format": "grimoire.journal/1", "state": "commit", "entries": [
            {"kind": kind, "target": target, "temp": "x~tmp1.new", "old": "sha256:" + "1" * 64,
             "new": "sha256:" + "2" * 64}]}
        with pytest.raises(RecoveryFailed, match="existing kept version"):
            parse(render(document))


@pytest.mark.parametrize("where", ["manifest-extra", "link-extra", "instance-extra", "definition-extra",
                                   "payload-key"])
def test_pins_in_unknown_fields_and_dict_keys_count(pair, where):
    """Any text naming the revision pins it, wherever an envelope keeps it (round 6: extra and keys)."""
    from store_helpers import OWNER as owner
    phone = pair[1]
    _cards(phone)
    txn = phone.transaction()
    if where == "manifest-extra":
        txn.put(SourceManifest("catalog:cards", owner, "Cards", {"lore/notes": "r2"}, extra={"x-pin": [str(R1)]}),
                phone.read_source("catalog:cards").etag)
    elif where == "link-extra":
        other, third = (SourceRef("catalog:cards", "lore", name, "r1") for name in ("other", "third"))
        txn.put(RecordEnvelope(other, {}), None)
        txn.put(RecordEnvelope(third, {}), None)
        txn.put(SourceManifest("catalog:cards", owner, "Cards", {"lore/notes": "r2"}, (
            LinkSpec(str(other), "derived_variant", str(third), extra={"via": str(R1)}),)),
            phone.read_source("catalog:cards").etag)
    elif where == "instance-extra":
        txn.put(RecordEnvelope(InstanceRef("c9", "lore", "copy"), {}, extra={"x-pin": str(R1)}), None)
    elif where == "definition-extra":
        txn.put(RecordEnvelope(SourceRef("catalog:cards", "lore", "sequel", "r1"), {}, extra={"x-after": str(R1)}),
                None)
    else:
        txn.put(RecordEnvelope(InstanceRef("c9", "lore", "copy"), {"by_source": {str(R1): 1}}), None)
    txn.commit(owner_handle())
    with pytest.raises(RevisionReferenced):
        _replace_r1(phone)


def test_a_linked_campaign_record_folder_blocks_a_revision_replace(pair, tmp_path):
    phone = pair[1]
    _cards(phone)
    outside = tmp_path / "chats-elsewhere"
    outside.mkdir()
    (phone.root / "campaigns" / "c1").mkdir(parents=True, exist_ok=True)
    make_dir_link(phone.root / "campaigns" / "c1" / "chats", outside)
    with pytest.raises(RevisionReferenced, match="cannot prove"):
        _replace_r1(phone)


def test_on_denied_may_only_return_valid_sync_state(pair):
    phone = pair[1]
    txn = phone.transaction()
    txn.put(instance(InstanceRef("c9", "character", "x"), WREN_R1), None)
    key = txn.put_sync_state("device.json", b'{"format": "grimoire.sync.device/1", "device": "phone"}\n', None)
    with pytest.raises(InvalidEnvelope, match="must be JSON"):
        txn.commit(owner_handle("sources"), on_denied=lambda keys: {key: b"\xff not json"})



def test_a_reused_hold_slot_is_re_verified_at_commit(pair):
    """A revision replace whose old bytes are already kept: if that kept file vanishes before the commit,
    the commit stops (compare-and-swap on the reused slot) instead of destroying the only copy."""
    phone = pair[1]
    current, other = _r1_now_history(phone)
    first = phone.transaction()
    slot = first.keep_version(R1_FILE, current)
    first.commit(owner_handle(*ALL))  # the old bytes are kept already
    txn = phone.transaction()
    txn.put_exact(R1_FILE, other, etag(current), replace_unreferenced=True)
    assert txn.keep_version(R1_FILE, current) == slot  # reused: staged as a no-op guard
    txn.check()
    (phone.root / slot).unlink()  # deleted by hand between planning and commit
    with pytest.raises(StaleWrite):
        txn.commit(owner_handle(*ALL))
    assert (phone.root / R1_FILE).read_bytes() == current



def test_put_exact_never_overwrites_a_knowledge_record(pair):
    from grimoire.core.identity import KnowledgeRef
    from grimoire.store import KnowledgeRecord
    phone = pair[1]
    txn = phone.transaction()
    txn.put(KnowledgeRecord(KnowledgeRef("c9", "evidence", "e-1"), "grimoire.test/0", {"n": 1}), None)
    txn.commit(owner_handle())
    path = "campaigns/c9/evidence/evidence/e-1.json"
    current = (phone.root / path).read_bytes()
    with pytest.raises(ImmutableRevision, match="create-only"):
        phone.transaction().put_exact(path, current.replace(b'"n": 1', b'"n": 2'), etag(current))
