"""Sync acceptance and failure scenarios (019), on two real libraries in temporary folders."""

from __future__ import annotations

import ast
import json
import os
import shutil
from pathlib import Path

import pytest

from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.security import Authorizer, Grant, Permission, ResourcePath
from grimoire.store import LibraryStore, LinkSpec, RecordEnvelope, SourceManifest, etag
from grimoire.sync import (
    ArchiveTooLarge, IdentityExists, NoDeviceIdentity, ResolveRefused, SameDevice, SyncInterrupted, identify,
    plan_sync, resolve, sync, sync_until_quiet,
)
from grimoire.sync import session as sync_session

from store_helpers import OWNER, WREN_R1, instance, make_dir_link, owner_handle, seed
from sync_helpers import (
    DESKTOP, PATHS, PHONE, WREN_A, WREN_B, canonical, chat_record, edit_instance, handle, handles, kept,
    make_pair, new_revision, put,
)

SYNC_SRC = Path(__file__).resolve().parents[1] / "src" / "grimoire" / "sync"


def _tree(root: Path) -> dict[str, bytes]:
    """Every file below ``root`` except the writer lock, by relative path."""
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*"))
            if path.is_file() and path.name != "lock"}


def _reopen(root: Path, name: str) -> LibraryStore:
    return LibraryStore.open(root / name)


@pytest.fixture
def pair(tmp_path):
    desktop, phone = make_pair(tmp_path)
    yield desktop, phone
    desktop.close()
    phone.close()


def _diverge(desktop, phone):
    """S1 + S2: non-overlapping edits on each device, and one overlapping chat edit."""
    edit_instance(desktop, WREN_A, 9)
    put(desktop, instance(InstanceRef("c3", "character", "zed"), WREN_R1), None)
    edit_instance(phone, WREN_B, 5)
    new_revision(phone, "r2", "a captain")
    put(desktop, chat_record(["character:wren", "character:mara"]))
    put(phone, chat_record(["character:wren"], world="world:harbor"))


# -- S1 / S2 -------------------------------------------------------------------------------

def test_s1_non_overlapping_edits_merge_and_both_orders_give_identical_bytes(tmp_path):
    (tmp_path / "one").mkdir()
    desktop, phone = make_pair(tmp_path / "one")
    _diverge(desktop, phone)
    desktop.close(), phone.close()
    shutil.copytree(tmp_path / "one", tmp_path / "two")
    results = []
    for folder, swap in (("one", False), ("two", True)):
        desktop, phone = _reopen(tmp_path / folder, DESKTOP), _reopen(tmp_path / folder, PHONE)
        report = sync(phone, desktop, handles()) if swap else sync(desktop, phone, handles())
        assert {path for path, _ in report.copied} == {PATHS["wren_a"], PATHS["wren_b"], PATHS["manifest"],
                                                       PATHS["r2"], "campaigns/c3/instances/character/zed.json"}
        assert [(found.path, found.code) for found in report.conflicts] == [(PATHS["chat"], "concurrent")]
        mine, theirs = canonical(desktop), canonical(phone)
        assert {path: data for path, data in mine.items() if path != PATHS["chat"]} == {
            path: data for path, data in theirs.items() if path != PATHS["chat"]}
        for store in (desktop, phone):
            view = store.load()
            assert view.problems == () and view.broken == ()
        assert desktop.load().index.resolve(SourceRef("world:harbor", "character", "wren")).ref.revision == "r2"
        assert not sync(desktop, phone, handles()).wrote  # quiescent
        desktop.close(), phone.close()
        results.append({**{f"d/{k}": v for k, v in _tree(tmp_path / folder / DESKTOP).items()},
                        **{f"p/{k}": v for k, v in _tree(tmp_path / folder / PHONE).items()}})
    assert results[0] == results[1]  # archives, pending markers and kept versions included


@pytest.mark.parametrize("newer", [DESKTOP, PHONE])
def test_s2_the_proving_scenario_timestamps_never_decide(tmp_path, newer):
    desktop, phone = make_pair(tmp_path)
    edit_instance(desktop, WREN_A, 9)
    edit_instance(phone, WREN_A, 4)
    mine, theirs = (desktop.root / PATHS["wren_a"]).read_bytes(), (phone.root / PATHS["wren_a"]).read_bytes()
    for store in (desktop, phone):
        stamp = 4_000_000_000 if store.root.name == newer else 1_000_000
        os.utime(store.root / PATHS["wren_a"], (stamp, stamp))
    report = sync(desktop, phone, handles())
    found = report.conflict(PATHS["wren_a"])
    assert found.code == "concurrent" and dict(found.kept) == {DESKTOP: True, PHONE: True}
    assert (desktop.root / PATHS["wren_a"]).read_bytes() == mine
    assert (phone.root / PATHS["wren_a"]).read_bytes() == theirs
    assert kept(desktop) == [theirs] and kept(phone) == [mine]
    again = sync(phone, desktop, handles())
    assert not again.wrote and again.conflict(PATHS["wren_a"]).code == "concurrent"
    assert kept(desktop) == [theirs] and kept(phone) == [mine]  # never duplicated


# -- S3 world-optional chat ------------------------------------------------------------------

def test_s3_a_chat_conflict_keeps_every_participant_and_one_chat_record(pair):
    desktop, phone = pair
    put(desktop, chat_record(["character:wren", "character:mara"]))
    put(phone, chat_record(["character:wren"], world="world:harbor"))
    report = sync(desktop, phone, handles())
    assert report.conflict(PATHS["chat"]).code == "concurrent"
    versions = [json.loads(data)["payload"] for data in kept(desktop) + kept(phone)]
    assert {"world": "world:harbor"}.items() <= versions[0].items() or {"world": "world:harbor"}.items() <= versions[1].items()
    assert any("character:mara" in version["participants"] for version in versions)
    for store in (desktop, phone):
        chats = [ref for ref in store.load().instances if ref.kind == "chat"]
        assert chats == [InstanceRef("c1", "chat", "harbor-talk")]  # kept versions are never indexed
        assert not (store.root / "worlds").exists()
    resolve(desktop, phone, PATHS["chat"], etag((desktop.root / PATHS["chat"]).read_bytes()),
            {DESKTOP: etag((desktop.root / PATHS["chat"]).read_bytes()),
             PHONE: etag((phone.root / PATHS["chat"]).read_bytes())}, handles())
    assert (desktop.root / PATHS["chat"]).read_bytes() == (phone.root / PATHS["chat"]).read_bytes()
    assert sync(desktop, phone, handles()).conflicts == ()
    assert [ref for ref in phone.load().instances if ref.kind == "chat"] == [InstanceRef("c1", "chat", "harbor-talk")]


# -- S4 tombstones ---------------------------------------------------------------------------

def test_s4_deletions_are_never_propagated_or_silently_resurrected(pair):
    desktop, phone = pair
    original = (phone.root / PATHS["wren_b"]).read_bytes()
    (desktop.root / PATHS["wren_b"]).unlink()
    report = sync(desktop, phone, handles())
    assert report.conflict(PATHS["wren_b"]).code == "deleted"
    assert (phone.root / PATHS["wren_b"]).read_bytes() == original and not (desktop.root / PATHS["wren_b"]).exists()
    assert kept(desktop) == [original]  # the deleting device still holds the other version
    (phone.root / PATHS["wren_b"]).unlink()
    assert sync(desktop, phone, handles()).conflicts == ()
    archive = json.loads(desktop.read_sync_state(f"peers/{PHONE}/archive.json"))
    assert archive["entries"][PATHS["wren_b"]] == {"deleted": etag(original)}  # the tombstone
    (phone.root / PATHS["wren_b"]).write_bytes(original)  # an old copy restored from a backup
    assert sync(desktop, phone, handles()).conflict(PATHS["wren_b"]).code == "resurrected"
    assert not (desktop.root / PATHS["wren_b"]).exists()
    edit_instance(phone, WREN_B, 1)  # genuinely new content at the deleted path
    report = sync(desktop, phone, handles())
    assert (PATHS["wren_b"], DESKTOP) in report.copied


# -- S5 missing ancestors and lineage ---------------------------------------------------------

def _independent_copies(tmp_path):
    desktop = LibraryStore.create(tmp_path / DESKTOP)
    seed(desktop, handle())
    desktop.close()
    shutil.copytree(tmp_path / DESKTOP, tmp_path / PHONE)
    desktop, phone = _reopen(tmp_path, DESKTOP), _reopen(tmp_path, PHONE)
    identify(desktop, DESKTOP), identify(phone, PHONE)
    return desktop, phone


def test_s5_a_first_sync_needs_a_proven_ancestor(tmp_path):
    desktop, phone = _independent_copies(tmp_path)
    edit_instance(desktop, WREN_A, 9)
    edit_instance(phone, WREN_A, 4)
    edit_instance(phone, WREN_B, 7)  # store lineage: previous = the desktop's current bytes
    report = sync(desktop, phone, handles())
    assert report.conflict(PATHS["wren_a"]).code == "missing_ancestor"
    assert (PATHS["wren_b"], DESKTOP) in report.copied
    desktop.close(), phone.close()


def test_s5b_lineage_is_ignored_once_the_pair_has_an_archive(pair):
    desktop, phone = pair
    zed = InstanceRef("c3", "character", "zed")
    path = "campaigns/c3/instances/character/zed.json"
    put(desktop, instance(zed, WREN_R1, hp=1), None)
    (phone.root / path).parent.mkdir(parents=True)
    shutil.copy(desktop.root / path, phone.root / path)  # copied by hand, never synced
    edit_instance(phone, zed, 2)  # its previous names the desktop's bytes
    edit_instance(desktop, zed, 3)
    assert sync(desktop, phone, handles()).conflict(path).code == "missing_ancestor"


# -- S6 a library copied with its .sync ---------------------------------------------------------

def test_s6_a_clone_that_synced_with_the_phone_can_never_overwrite_the_original(tmp_path):
    desktop, phone = make_pair(tmp_path)
    desktop.close()
    shutil.copytree(tmp_path / DESKTOP, tmp_path / "clone")  # .sync came along: also "desktop"
    clone, desktop = _reopen(tmp_path, "clone"), _reopen(tmp_path, DESKTOP)
    edit_instance(clone, WREN_A, 7)
    assert (PATHS["wren_a"], PHONE) in sync(clone, phone, handles()).copied
    with pytest.raises(SameDevice):
        sync(clone, desktop, handles())
    original = (desktop.root / PATHS["wren_a"]).read_bytes()
    report = sync(desktop, phone, handles())
    assert report.conflict(PATHS["wren_a"]).code == "missing_ancestor"
    assert (desktop.root / PATHS["wren_a"]).read_bytes() == original  # nothing overwritten either way
    assert (phone.root / PATHS["wren_a"]).read_bytes() == (clone.root / PATHS["wren_a"]).read_bytes()
    assert sync(desktop, phone, handles()).conflict(PATHS["wren_a"]).code == "missing_ancestor"  # sticky
    for store in (clone, desktop, phone):
        store.close()


# -- S7 a source that cannot be applied --------------------------------------------------------

def _source(store, source, entity_kind="character", links=(), revision="r1"):
    txn = store.transaction()
    try:
        current = store.read_source(source).etag
    except Exception:  # noqa: BLE001 - a new source
        current = None
    txn.put(SourceManifest(source, OWNER, None, {f"{entity_kind}/x": revision}, tuple(links)), current)
    if current is None:
        txn.put(RecordEnvelope(SourceRef(source, entity_kind, "x", revision), {"n": 1}), None)
    txn.commit(handle())


def test_s7_one_poisoned_source_never_blocks_the_rest(pair):
    desktop, phone = pair
    _source(desktop, "catalog:shared", "character")  # the same entity id under two kinds
    _source(phone, "catalog:shared", "item")
    edit_instance(desktop, WREN_A, 8)
    report = sync(desktop, phone, handles())
    codes = {found.path: found.code for found in report.conflicts}
    assert codes["sources/catalog/shared/_source/manifest.json"] == "missing_ancestor"
    assert codes["sources/catalog/shared/_source/records/character/x/r1.json"] == "unapplicable"
    assert codes["sources/catalog/shared/_source/records/item/x/r1.json"] == "unapplicable"
    assert (PATHS["wren_a"], PHONE) in report.copied  # everything else converges
    assert phone.load().problems == () and desktop.load().problems == ()


def test_s7_cross_source_link_cycles_are_caught_cumulatively(pair):
    desktop, phone = pair
    for store in (desktop, phone):
        for source in ("set:a", "set:b", "set:c"):
            _source(store, source)
        store_refs = {source: str(SourceRef(source, "character", "x", "r1")) for source in ("set:a", "set:b", "set:c")}
    sync(desktop, phone, handles())
    refs = store_refs

    def link(source, target):
        return LinkSpec(refs[source], "derived_variant", refs[target])

    _source(desktop, "set:a", links=[link("set:a", "set:b")])
    _source(desktop, "set:c", links=[link("set:c", "set:a")])
    _source(phone, "set:b", links=[link("set:b", "set:c")])
    report = sync(desktop, phone, handles())
    codes = {found.path: found.code for found in report.conflicts}
    a, b, c = (f"sources/set/{name}/_source/manifest.json" for name in "abc")
    # on the phone a is admitted first; c, checked together with a, would close a -> b -> c -> a
    assert (a, PHONE) in report.copied and codes[c] == "unapplicable"
    assert not [problem for problem in phone.load().problems if problem.code == "bad_link"]
    # on the desktop b is admitted exactly as a local commit of it would be: under 010's accepted
    # link-order policy the cycle drops set:c's (the later declarer's) link, and load reports it
    assert (b, DESKTOP) in report.copied
    assert [problem.source for problem in desktop.load().problems if problem.code == "bad_link"] == ["set:c"]


def test_s7_head_dependency_and_name_collision(pair):
    desktop, phone = pair
    for store in (desktop, phone):  # both name r1 as the head explicitly
        txn = store.transaction()
        txn.put(SourceManifest("world:harbor", OWNER, "Harbor", {"character/wren": "r1"}),
                store.read_source("world:harbor").etag)
        txn.commit(handle())
    sync(desktop, phone, handles())
    new_revision(desktop, "r2", "a captain")  # the desktop moves its head to its own r2
    txn = phone.transaction()  # the phone adds a different r2 and keeps its head on r1
    from store_helpers import wren
    txn.put(wren("r2", "a pirate"), None)
    txn.commit(handle())
    report = sync(desktop, phone, handles())
    assert report.conflict(PATHS["r2"]).code == "missing_ancestor"
    assert report.conflict(PATHS["manifest"]).code == "head_dependency"  # never point at the phone's r2
    assert json.loads((phone.root / PATHS["manifest"]).read_bytes())["heads"] == {"character/wren": "r1"}
    folder = phone.root / "campaigns" / "c4" / "instances" / "character"
    folder.mkdir(parents=True)
    (folder / "Wren-x.json").write_bytes(b"{}")  # a hand-made look-alike, on every filesystem
    put(desktop, instance(InstanceRef("c4", "character", "wren-x"), WREN_R1), None)
    assert sync(desktop, phone, handles()).conflict(
        "campaigns/c4/instances/character/wren-x.json").code == "name_collision"
    assert (folder / "Wren-x.json").read_bytes() == b"{}"


# -- S8 resolve ---------------------------------------------------------------------------------

def test_s8_resolve_ends_a_conflict_and_refuses_what_it_cannot_do_safely(pair):
    desktop, phone = pair
    edit_instance(desktop, WREN_A, 9)
    edit_instance(phone, WREN_A, 4)
    sync(desktop, phone, handles())
    mine, theirs = (desktop.root / PATHS["wren_a"]).read_bytes(), (phone.root / PATHS["wren_a"]).read_bytes()
    expected = {DESKTOP: etag(mine), PHONE: etag(theirs)}
    with pytest.raises(ResolveRefused):
        resolve(desktop, phone, PATHS["wren_a"], etag(b"neither"), expected, handles())
    with pytest.raises(ResolveRefused):
        resolve(desktop, phone, PATHS["wren_a"], etag(theirs), {**expected, DESKTOP: etag(b"stale")}, handles())
    assert resolve(desktop, phone, PATHS["wren_a"], etag(theirs), expected, handles()) == DESKTOP
    assert (desktop.root / PATHS["wren_a"]).read_bytes() == theirs
    assert mine in kept(desktop)  # the replaced version is kept
    report = sync(desktop, phone, handles())
    assert report.conflicts == () and not report.wrote
    edit_instance(desktop, WREN_A, 1)  # and the pair fast-forwards again afterwards
    assert (PATHS["wren_a"], PHONE) in sync(desktop, phone, handles()).copied


def test_s8_revision_resolve_replaces_only_an_unreferenced_revision(pair):
    desktop, phone = pair
    for store, bio in ((desktop, "captain"), (phone, "pirate")):
        txn = store.transaction()
        txn.put(RecordEnvelope(SourceRef("world:harbor", "character", "wren", "r9"), {"bio": bio}), None)
        manifest = store.read_source("world:harbor").manifest
        txn.put(SourceManifest(manifest.source, manifest.owner, manifest.title, {"character/wren": "r1"},
                               manifest.links, extra=manifest.extra), store.read_source("world:harbor").etag)
        txn.commit(handle())
    r9 = PATHS["r1"].replace("r1.json", "r9.json")
    assert sync(desktop, phone, handles()).conflict(r9).code == "missing_ancestor"
    mine, theirs = (desktop.root / r9).read_bytes(), (phone.root / r9).read_bytes()
    resolve(desktop, phone, r9, etag(mine), {DESKTOP: etag(mine), PHONE: etag(theirs)}, handles())
    assert (phone.root / r9).read_bytes() == mine and theirs in kept(phone)
    # a revision something pins (r1 is the head and the instances' base) cannot be replaced
    (desktop.root / PATHS["r1"]).write_bytes((desktop.root / PATHS["r1"]).read_bytes().replace(b"sailor", b"ghost"))
    assert sync(desktop, phone, handles()).conflict(PATHS["r1"]).code == "revision_modified"
    ours, other = (desktop.root / PATHS["r1"]).read_bytes(), (phone.root / PATHS["r1"]).read_bytes()
    with pytest.raises(ResolveRefused, match="RevisionReferenced|names|based|pin"):
        resolve(desktop, phone, PATHS["r1"], etag(ours), {DESKTOP: etag(ours), PHONE: etag(other)}, handles())
    assert (phone.root / PATHS["r1"]).read_bytes() == other


def test_s8_resolving_a_deletion_or_blocked_side_is_refused(pair):
    desktop, phone = pair
    original = (phone.root / PATHS["wren_b"]).read_bytes()
    (desktop.root / PATHS["wren_b"]).unlink()
    sync(desktop, phone, handles())
    with pytest.raises(ResolveRefused):  # a deletion cannot be chosen in this slice
        resolve(desktop, phone, PATHS["wren_b"], etag(b""), {DESKTOP: None, PHONE: etag(original)}, handles())
    # restoring the present version is allowed
    resolve(desktop, phone, PATHS["wren_b"], etag(original), {DESKTOP: None, PHONE: etag(original)}, handles())
    assert (desktop.root / PATHS["wren_b"]).read_bytes() == original
    (phone.root / PATHS["wren_a"]).write_bytes(b"{")
    with pytest.raises(ResolveRefused):
        resolve(desktop, phone, PATHS["wren_a"], etag((desktop.root / PATHS["wren_a"]).read_bytes()),
                {DESKTOP: etag((desktop.root / PATHS["wren_a"]).read_bytes()), PHONE: None}, handles())


# -- failures -------------------------------------------------------------------------------------

def test_f1_identity_is_explicit_and_unique(tmp_path):
    one, two = LibraryStore.create(tmp_path / "one"), LibraryStore.create(tmp_path / "two")
    with pytest.raises(NoDeviceIdentity):
        sync(one, two, {})
    identify(one, "desktop"), identify(two, "desktop")
    with pytest.raises(SameDevice):
        sync(one, two, {})
    with pytest.raises(IdentityExists):
        identify(two, "phone")
    assert identify(two, "phone", replace=True) == "phone"
    (one.root / ".sync" / "device.json").write_bytes(b"{broken")
    with pytest.raises(NoDeviceIdentity):
        sync(one, two, {})
    assert identify(one, "desktop") == "desktop"  # a damaged identity is replaced
    for bad in ("Desktop", "con", "a b"):
        with pytest.raises(ValueError):
            identify(one, bad)
    one.close(), two.close()


def test_f2_a_change_after_planning_interrupts_and_a_retry_converges(pair):
    desktop, phone = pair
    edit_instance(desktop, WREN_A, 9)
    plan = plan_sync(desktop, phone)
    edit_instance(phone, WREN_A, 2)  # the phone moved on after the plan was made
    archive = phone.read_sync_state(f"peers/{DESKTOP}/archive.json")
    with pytest.raises(SyncInterrupted):
        sync_session.apply_sync(plan, handles())
    assert phone.read_sync_state(f"peers/{DESKTOP}/archive.json") == archive  # no archive after a stop
    assert sync(desktop, phone, handles()).conflict(PATHS["wren_a"]).code == "concurrent"


@pytest.mark.parametrize("damage", ["garbage", "forged", "missing-one", "other-pair"])
def test_f3_an_ignored_archive_is_conservative(pair, damage):
    desktop, phone = pair
    archive = phone.root / ".sync" / "peers" / DESKTOP / "archive.json"
    if damage == "garbage":
        archive.write_bytes(b"{")
    elif damage == "forged":  # still parses, but its check digest no longer matches
        current = etag((phone.root / PATHS["wren_a"]).read_bytes()).encode()
        archive.write_bytes(archive.read_bytes().replace(current, b"sha256:" + b"0" * 64))
    elif damage == "missing-one":
        archive.unlink()
    else:
        document = json.loads(archive.read_bytes())
        document["devices"] = ["desktop", "tablet"]
        archive.write_text(json.dumps(document))
    edit_instance(desktop, WREN_A, 9)  # an ordinary fast-forward with a trusted archive
    (desktop.root / PATHS["wren_b"]).unlink()
    report = sync(desktop, phone, handles())
    assert report.copied == ()
    assert report.conflict(PATHS["wren_a"]).code == "missing_ancestor"
    assert report.conflict(PATHS["wren_b"]).code == "missing_ancestor"  # not re-created on the desktop
    assert not (desktop.root / PATHS["wren_b"]).exists()
    if damage != "missing-one":
        assert any("archive ignored" in notice for notice in report.notices)


def test_f4_blocked_paths_are_never_copied_or_overwritten(pair, tmp_path):
    desktop, phone = pair
    (phone.root / PATHS["wren_a"]).write_bytes(b"{not json")
    (phone.root / PATHS["wren_b"]).write_bytes(json.dumps({"format": "grimoire.record/9"}).encode())
    edit_instance(desktop, WREN_A, 9), edit_instance(desktop, WREN_B, 9)
    outside = tmp_path / "outside"
    (outside / "instances" / "character").mkdir(parents=True)
    make_dir_link(phone.root / "campaigns" / "c9", outside)
    put(desktop, instance(InstanceRef("c9", "character", "x"), WREN_R1), None)
    report = sync(desktop, phone, handles())
    assert report.copied == ()
    assert {found.code for found in report.conflicts} == {"blocked"}
    assert (phone.root / PATHS["wren_a"]).read_bytes() == b"{not json"
    assert list((outside / "instances" / "character").iterdir()) == []


def _grants(*scopes):
    authority = Authorizer([OWNER])
    for number, scope in enumerate(scopes):
        authority.add_grant(Grant(f"g{number}", OWNER, frozenset({Permission.APPLY}), ResourcePath.parse(scope), OWNER))
    return authority.handle_for(OWNER, trusted=True)


def test_f6_a_denied_record_becomes_a_conflict_and_everything_else_converges(pair):
    desktop, phone = pair
    edit_instance(desktop, WREN_A, 9)
    edit_instance(desktop, WREN_B, 9)
    new_revision(desktop, "r2", "a captain")
    limited = _grants("campaigns/c1", "kept")  # the phone may not take c2 or the world's change
    report = sync(desktop, phone, {DESKTOP: handle(), PHONE: limited})
    codes = {found.path: found.code for found in report.conflicts}
    assert codes == {PATHS["wren_b"]: "denied", PATHS["manifest"]: "denied", PATHS["r2"]: "denied"}
    assert (PATHS["wren_a"], PHONE) in report.copied
    assert phone.load().problems == ()
    report = sync(desktop, phone, handles())  # once the grant is fixed, the rest syncs
    assert report.conflicts == ()
    assert canonical(desktop) == canonical(phone)


def test_f6_kept_versions_need_their_own_grant(pair):
    desktop, phone = pair
    edit_instance(desktop, WREN_A, 9)
    edit_instance(phone, WREN_A, 4)
    report = sync(desktop, phone, {DESKTOP: handle(), PHONE: _grants("campaigns")})
    assert dict(report.conflict(PATHS["wren_a"]).kept) == {DESKTOP: True, PHONE: False}
    assert kept(phone) == []


def test_f7_sync_never_consults_clocks_or_randomness():
    banned_modules = {"time", "datetime", "random", "uuid", "secrets"}
    banned_names = {"st_mtime", "st_mtime_ns", "st_ctime", "st_atime", "getmtime", "getctime", "utime"}
    for path in SYNC_SRC.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not {alias.name.split(".")[0] for alias in node.names} & banned_modules, path
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in banned_modules, path
            elif isinstance(node, ast.Attribute):
                assert node.attr not in banned_names, (path, node.attr)
            elif isinstance(node, ast.Name):
                assert node.id not in banned_names, (path, node.id)


def test_f9_an_archive_too_large_is_refused_before_any_write(pair, monkeypatch):
    desktop, phone = pair
    edit_instance(desktop, WREN_A, 9)
    before = (_tree(desktop.root), _tree(phone.root))
    monkeypatch.setattr(sync_session, "ARCHIVE_LIMIT", 100)
    with pytest.raises(ArchiveTooLarge):
        sync(desktop, phone, handles())
    assert (_tree(desktop.root), _tree(phone.root)) == before


def test_f10_aba_after_an_interrupted_sync_stays_a_conflict_through_many_syncs(pair, monkeypatch):
    desktop, phone = pair
    old = (desktop.root / PATHS["wren_a"]).read_bytes()
    edit_instance(phone, WREN_A, 4)

    def crash(*args, **kwargs):
        raise RuntimeError("the process died before the archive")

    monkeypatch.setattr(sync_session, "_verify", crash)
    with pytest.raises(RuntimeError):
        sync(desktop, phone, handles())  # data(a) copied the phone's version onto the desktop
    monkeypatch.undo()
    assert (desktop.root / PATHS["wren_a"]).read_bytes() == (phone.root / PATHS["wren_a"]).read_bytes()
    (desktop.root / PATHS["wren_a"]).write_bytes(old)  # the human restores the exact old bytes
    for _ in range(3):
        report = sync(desktop, phone, handles())
        assert report.conflict(PATHS["wren_a"]).code == "missing_ancestor"
        assert (desktop.root / PATHS["wren_a"]).read_bytes() == old


def test_p15_a_first_sync_with_nothing_agreed_still_writes_an_archive(tmp_path):
    desktop, phone = LibraryStore.create(tmp_path / DESKTOP), LibraryStore.create(tmp_path / PHONE)
    identify(desktop, DESKTOP), identify(phone, PHONE)
    report = sync(desktop, phone, handles())
    assert report.wrote and desktop.read_sync_state(f"peers/{PHONE}/archive.json") is not None
    put(desktop, instance(WREN_A, WREN_R1), None)
    (phone.root / "campaigns/c1/instances/character").mkdir(parents=True)
    shutil.copy(desktop.root / PATHS["wren_a"], phone.root / PATHS["wren_a"])
    edit_instance(phone, WREN_A, 5)  # lineage would fast-forward a first sync; this is not one
    assert sync(desktop, phone, handles()).conflict(PATHS["wren_a"]).code == "missing_ancestor"
    desktop.close(), phone.close()


def test_sync_until_quiet_retries_an_interruption(pair, monkeypatch):
    desktop, phone = pair
    edit_instance(desktop, WREN_A, 9)
    calls = []
    real = sync_session.sync

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise SyncInterrupted("raced")
        return real(*args, **kwargs)

    monkeypatch.setattr(sync_session, "sync", flaky)
    report = sync_until_quiet(desktop, phone, handles())
    assert not report.wrote and len(calls) == 3


def test_f6_a_denied_source_another_source_needed_never_blocks_the_rest(pair):
    """Dropping a denied manifest can invalidate another admitted one: it is re-admitted, not fatal."""
    desktop, phone = pair
    ref = {source: str(SourceRef(source, "character", "x", "r1")) for source in ("set:a", "set:b")}
    for store in (desktop, phone):
        _source(store, "set:b")
        _source(store, "set:a", links=[LinkSpec(ref["set:a"], "derived_variant", ref["set:b"])])
    sync(desktop, phone, handles())
    _source(phone, "set:a", links=[])  # the phone moves the link from a to b, reversed
    _source(phone, "set:b", links=[LinkSpec(ref["set:b"], "derived_variant", ref["set:a"])])
    edit_instance(phone, WREN_A, 3)
    limited = _grants("campaigns", "kept", "sources/set/b", "sources/world")  # a's change is denied
    report = sync_until_quiet(desktop, phone, {PHONE: handle(), DESKTOP: limited})
    codes = {found.path: found.code for found in report.conflicts}
    assert codes["sources/set/a/_source/manifest.json"] == "denied"
    assert codes["sources/set/b/_source/manifest.json"] == "denied"  # it needed a's change: a cycle alone
    assert canonical(desktop)[PATHS["wren_a"]] == canonical(phone)[PATHS["wren_a"]]
    assert not [problem for problem in desktop.load().problems if problem.code == "bad_link"]


def test_f12_an_unreadable_sync_file_stops_the_sync_instead_of_looking_missing(pair, monkeypatch):
    desktop, phone = pair
    edit_instance(phone, WREN_A, 4)
    before = (_tree(desktop.root), _tree(phone.root))
    access, real = desktop._access, desktop._access.read

    def flaky(parts):
        if parts[-1] == "pending.json":
            raise PermissionError("held by another program")
        return real(parts)

    monkeypatch.setattr(access, "read", flaky)
    with pytest.raises(SyncInterrupted, match="cannot read"):
        sync(desktop, phone, handles())
    assert (_tree(desktop.root), _tree(phone.root)) == before
    monkeypatch.undo()
    assert (PATHS["wren_a"], DESKTOP) in sync(desktop, phone, handles()).copied


def test_f13_an_unreadable_record_is_blocked_and_never_kept_onto(pair, monkeypatch):
    desktop, phone = pair
    edit_instance(desktop, WREN_A, 9)
    access, real = phone._access, phone._access.read

    def flaky(parts):
        if "/".join(parts) == PATHS["wren_a"]:
            raise OSError("transient read error")
        return real(parts)

    monkeypatch.setattr(access, "read", flaky)
    report = sync(desktop, phone, handles())
    monkeypatch.undo()
    assert report.conflict(PATHS["wren_a"]).code == "blocked"
    assert kept(phone) == [] and kept(desktop) == []  # nothing to dismiss later
    assert (PATHS["wren_a"], PHONE) in sync(desktop, phone, handles()).copied  # the next sync heals it


def test_f13_a_directory_where_a_record_belongs_is_blocked(pair):
    desktop, phone = pair
    (phone.root / PATHS["wren_a"]).unlink()
    (phone.root / PATHS["wren_a"]).mkdir()
    edit_instance(desktop, WREN_A, 9)
    report = sync(desktop, phone, handles())
    assert report.conflict(PATHS["wren_a"]).code == "blocked"
    assert (phone.root / PATHS["wren_a"]).is_dir()


def test_f14_a_failed_verification_read_writes_no_archive(pair, monkeypatch):
    desktop, phone = pair
    edit_instance(desktop, WREN_A, 9)
    archives = [store.read_sync_state(f"peers/{other}/archive.json")
                for store, other in ((desktop, PHONE), (phone, DESKTOP))]

    def broken(path):
        raise OSError("the disk went away")

    monkeypatch.setattr(phone, "read_exact", broken)
    with pytest.raises(OSError):
        sync(desktop, phone, handles())
    monkeypatch.undo()
    assert [store.read_sync_state(f"peers/{other}/archive.json")
            for store, other in ((desktop, PHONE), (phone, DESKTOP))] == archives
    report = sync(desktop, phone, handles())  # the landed copy is verifiably agreed via its marker
    assert report.conflicts == () and canonical(desktop) == canonical(phone)


def test_f6_approvals_spent_by_an_interrupted_commit_are_reported(pair, monkeypatch):
    desktop, phone = pair
    edit_instance(desktop, WREN_A, 9)
    edit_instance(desktop, WREN_B, 9)
    authority = Authorizer([OWNER])
    authority.add_grant(Grant("g", "app:sync", frozenset({Permission.APPLY}), ResourcePath.parse("campaigns/c1"),
                              OWNER))
    plan = plan_sync(desktop, phone)
    preview = plan.preview()[PHONE]
    from grimoire.security import Approval, payload_digest
    authority.register_approval(Approval("a1", "app:sync", Permission.APPLY, ResourcePath.parse(PATHS["wren_a"]),
                                         payload_digest(preview[PATHS["wren_a"]]), OWNER))
    # wren_b (campaign c2) is denied; while it is pruned, the library is found changed
    monkeypatch.setattr(sync_session, "_preflight", lambda txn, what: (_ for _ in ()).throw(
        SyncInterrupted("a library changed")))
    with pytest.raises(SyncInterrupted) as stopped:
        sync_session.apply_sync(plan, {DESKTOP: handle(), PHONE: authority.handle_for("app:sync")},
                                {PHONE: {PATHS["wren_a"]: "a1"}})
    assert stopped.value.report.consumed_approvals == ("a1",)


def test_sync_until_quiet_gives_up_with_a_named_error(pair, monkeypatch):
    desktop, phone = pair
    report = sync_session.SyncReport((DESKTOP, PHONE), wrote=True)
    monkeypatch.setattr(sync_session, "sync", lambda *args, **kwargs: report)
    with pytest.raises(sync_session.SyncNotQuiet):
        sync_until_quiet(desktop, phone, handles(), attempts=2)


def _retitle(store, title, heads=None):
    current = store.read_source("world:harbor")
    txn = store.transaction()
    txn.put(SourceManifest("world:harbor", OWNER, title, heads if heads is not None else dict(current.manifest.heads)),
            current.etag)
    txn.commit(handle())


def test_s8_resolving_a_manifest_brings_its_absent_heads_and_refuses_differing_ones(pair):
    desktop, phone = pair
    new_revision(desktop, "r2", "a captain")  # the desktop moves its head to a new r2
    _retitle(phone, "Harbour")                # the phone retitles the manifest
    report = sync(desktop, phone, handles())
    assert report.conflict(PATHS["manifest"]).code == "concurrent"
    assert report.conflict(PATHS["r2"]).code == "unapplicable"  # headless on the phone without the manifest
    mine, theirs = (desktop.root / PATHS["manifest"]).read_bytes(), (phone.root / PATHS["manifest"]).read_bytes()
    resolve(desktop, phone, PATHS["manifest"], etag(mine), {DESKTOP: etag(mine), PHONE: etag(theirs)}, handles())
    assert (phone.root / PATHS["r2"]).read_bytes() == (desktop.root / PATHS["r2"]).read_bytes()
    assert (phone.root / PATHS["manifest"]).read_bytes() == mine and phone.load().problems == ()
    assert sync(desktop, phone, handles()).conflicts == ()
    # a head revision that differs between the devices must be resolved first
    new_revision(desktop, "r3", "an admiral")
    new_revision(phone, "r3", "a mutineer")
    _retitle(phone, "Harbour!")
    sync(desktop, phone, handles())
    mine, theirs = (desktop.root / PATHS["manifest"]).read_bytes(), (phone.root / PATHS["manifest"]).read_bytes()
    with pytest.raises(ResolveRefused, match="resolve it first"):
        resolve(desktop, phone, PATHS["manifest"], etag(mine), {DESKTOP: etag(mine), PHONE: etag(theirs)}, handles())


def test_f1_a_replaced_identity_makes_the_next_sync_conservative(pair):
    desktop, phone = pair
    identify(phone, "tablet", replace=True)
    edit_instance(desktop, WREN_A, 9)
    (desktop.root / PATHS["wren_b"]).unlink()
    report = sync(desktop, phone, {DESKTOP: handle(), "tablet": handle()})
    assert report.copied == ()  # not a first sync: nothing is copied, nothing re-created
    assert report.conflict(PATHS["wren_a"]).code == "missing_ancestor"
    assert report.conflict(PATHS["wren_b"]).code == "missing_ancestor"


def test_f4_a_record_at_the_wrong_address_is_blocked(pair):
    desktop, phone = pair
    data = json.loads((phone.root / PATHS["wren_a"]).read_bytes())
    data["address"] = str(InstanceRef("c1", "character", "imposter"))
    (phone.root / PATHS["wren_a"]).write_text(json.dumps(data))
    edit_instance(desktop, WREN_A, 9)
    assert sync(desktop, phone, handles()).conflict(PATHS["wren_a"]).code == "blocked"
