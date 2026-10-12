"""Fresh membership capture keeps parsed rows coupled to their actual inputs."""

import io
import json
import os
import threading
import time
from pathlib import Path

import pytest

from grimoire.store import (
    campaigns,
    entities,
    group_refs,
    locks,
    membership,
    overlay,
    statcache,
    tokens,
    worlds,
)
from tests import test_group_membership_store as native_tests

GROUP = "groups:salt-circle"


def _write_group(root, *, members="characters:mara, pcs:winifred", leader="characters:mara", secrecy="public", body=""):
    path = root / "groups" / "salt-circle.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: Salt Circle\nmembers: {members}\nleader: {leader}\nsecrecy: {secrecy}\n---\n{body}", encoding="utf-8")
    return path


def _age_tree(root):
    old = time.time_ns() - 3 * statcache.RACY_WINDOW_NS
    for path in [*root.rglob("*"), root]:
        os.utime(path, ns=(old, old))


def _age_clock(monkeypatch):
    """utime cannot backdate a file's change time, which the memo now holds
    to the racy window too (spec 03): age the memo's clock instead, as the
    store's other memo tests do."""
    real_now = time.time_ns
    monkeypatch.setattr(statcache.time, "time_ns", lambda: real_now() + 3 * statcache.RACY_WINDOW_NS)


@pytest.fixture(autouse=True)
def _past_the_racy_window(monkeypatch):
    _age_clock(monkeypatch)


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    return cid, overlay.croot_of(cid), worlds.world_root(wid)


def test_campaign_inverse_follows_inherit_copy_tombstone(case):
    cid, croot, wroot = case
    _write_group(wroot)
    assert membership.campaign_index(cid).groups[GROUP].members == ("characters:mara", "pcs:winifred")
    _write_group(croot, members="pcs:winifred", leader="")
    assert membership.campaign_index(cid).groups[GROUP].members == ("pcs:winifred",)
    (croot / "groups" / "salt-circle.md").unlink()
    (croot / "deleted.json").write_text(json.dumps(["groups/salt-circle"]), encoding="utf-8")
    assert membership.campaign_index(cid).groups == {}


def test_owned_group_refs_remain_authored(case):
    cid, croot, _wroot = case
    _write_group(croot)
    (croot / "detached.json").write_text(json.dumps(["characters/mara"]), encoding="utf-8")
    got = membership.campaign_index(cid).groups[GROUP]
    assert got.members == ("characters:mara", "pcs:winifred")
    assert got.leader == "characters:mara"


def test_inherited_leader_and_members_share_s1_native_detachment_rule(case):
    cid, croot, wroot = case
    _write_group(wroot, members="characters:MARA, pcs:winifred", leader="characters:MARA")
    actor = wroot / "characters" / "mara"
    actor.mkdir(parents=True)
    (croot / "detached.json").write_text(json.dumps(["characters/mara"]), encoding="utf-8")
    _, context = overlay.read_group_membership(cid, "salt-circle")
    got = membership.campaign_index(cid).groups[GROUP]
    assert (got.members, got.leader) == (context.members, context.leader)
    # The exact marker is authoritative even if neither actor directory exists.
    _write_group(wroot)
    got = membership.campaign_index(cid).groups[GROUP]
    assert (got.members, got.leader) == (("pcs:winifred",), None)


def test_world_inverse_never_reads_actor_records_or_encodes_tokens(tmp_path, monkeypatch):
    _write_group(tmp_path)
    real = io.open
    opened = []

    def record_open(file, *args, **kwargs):
        opened.append(os.fspath(file))
        return real(file, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("membership derivation performed a token encode or campaign write/lock")

    monkeypatch.setattr(io, "open", record_open)
    monkeypatch.setattr(tokens, "record_tokens", forbidden)
    monkeypatch.setattr(locks, "campaign_lock", forbidden)
    got = membership.world_index(tmp_path)
    assert got.groups[GROUP].leader == "characters:mara"
    assert opened and all(Path(path).parent == tmp_path / "groups" for path in opened)


def test_empty_collection_uses_root_stamp(tmp_path):
    rows, stamps, digest = entities.group_listing(tmp_path)
    assert rows == [] and stamps is not None and digest
    assert str(tmp_path.resolve()) in {entry[0] for entry in stamps}
    _age_tree(tmp_path)
    assert membership.world_index(tmp_path).groups == {}
    _write_group(tmp_path)
    assert GROUP in membership.world_index(tmp_path).groups


def test_first_copy_creates_groups_directory_and_invalidates(case):
    cid, croot, wroot = case
    _write_group(wroot)
    _age_tree(croot)
    _age_tree(wroot)
    assert membership.campaign_index(cid).groups[GROUP].members[0] == "characters:mara"
    assert not (croot / "groups").exists()
    _write_group(croot, members="pcs:winifred")
    assert membership.campaign_index(cid).groups[GROUP].members == ("pcs:winifred",)


def test_private_memo_is_bounded_and_returns_deep_copies(tmp_path, monkeypatch):
    pool = membership._memo_pool
    pool.clear()
    assert pool is not statcache._cache
    _write_group(tmp_path)
    _age_tree(tmp_path)
    calls = []
    real = membership.index_from_rows

    def counting(rows):
        calls.append(1)
        return real(rows)

    monkeypatch.setattr(membership, "index_from_rows", counting)
    first = membership.world_index(tmp_path)
    first.groups[GROUP].members = ()
    first.by_actor["characters:mara"][0].roles = ("changed",)
    first.groups.clear()
    again = membership.world_index(tmp_path)
    assert again.groups[GROUP].members == ("characters:mara", "pcs:winifred")
    assert again.by_actor["characters:mara"][0].roles == ("leader", "member")
    assert len(calls) == 1
    for i in range(70):
        root = tmp_path / str(i)
        root.mkdir()
        _age_tree(root)
        assert membership.world_index(root).groups == {}
    assert len(pool) == 64



@pytest.fixture(autouse=True)
def _clear_membership_memo():
    membership._memo_pool.clear()


def _freeze_stats(monkeypatch, *roots):
    """An editor restore can preserve EVERY stat field; bytes remain evidence."""
    real = statcache.stamp
    saved = {os.fspath(path): real(path) for root in roots for path in [root, *root.rglob("*")]}

    def frozen(path):
        key = os.fspath(path)
        return saved[key] if key in saved else real(path)

    monkeypatch.setattr(statcache, "stamp", frozen)
    monkeypatch.setattr(statcache, "_unchanged", lambda stamps: all(frozen(stamp[0]) == stamp for stamp in stamps))
    return saved


@pytest.mark.parametrize("scope", ["world", "campaign"])
@pytest.mark.parametrize("field", ["members", "secrecy"])
def test_preserved_stat_members_and_secrecy_edits_are_fresh(case, monkeypatch, scope, field):
    cid, croot, wroot = case
    root = wroot if scope == "world" else croot
    path = _write_group(root, members="characters:mara", secrecy="secret")
    _age_tree(croot)
    _age_tree(wroot)
    _freeze_stats(monkeypatch, croot, wroot)
    get_index = (lambda: membership.world_index(wroot)) if scope == "world" else (lambda: membership.campaign_index(cid))
    first = get_index()
    assert membership._memo_pool
    before = path.read_bytes()
    after = before.replace(b"characters:mara", b"characters:MARA") if field == "members" else before.replace(b"secret", b"public")
    assert len(before) == len(after) and before != after
    path.write_bytes(after)
    got = get_index()
    assert got.digest != first.digest
    assert got.groups[GROUP].members == (("characters:MARA",) if field == "members" else ("characters:mara",))
    assert got.groups[GROUP].secrecy == ("public" if field == "secrecy" else "secret")
    assert get_index() == got


@pytest.mark.parametrize("filename", ["deleted.json", "detached.json"])
@pytest.mark.parametrize("raw", [b"{", b"{}", b"[null]", b"[1]", b'["characters/../mara"]', b"\xff"])
def test_warm_hit_strictly_rejects_unreadable_ledgers(case, monkeypatch, filename, raw):
    cid, croot, wroot = case
    _write_group(wroot)
    for name in ("deleted.json", "detached.json"):
        (croot / name).write_text('["lore/saltmarch"]', encoding="utf-8")
    _age_tree(croot)
    _age_tree(wroot)
    _freeze_stats(monkeypatch, croot, wroot)
    assert GROUP in membership.campaign_index(cid).groups
    assert membership._memo_pool
    path = croot / filename
    path.write_bytes(raw)
    with pytest.raises(group_refs.GroupIdentityConflict):
        membership.campaign_index(cid)
    path.write_text('["lore/saltmarch", "characters/mara"]', encoding="utf-8")
    got = membership.campaign_index(cid).groups[GROUP]
    assert got.members == (("pcs:winifred",) if filename == "detached.json" else ("characters:mara", "pcs:winifred"))
    assert "lore/saltmarch" in overlay._strict_ledger(path, visibility=filename == "deleted.json")


@pytest.mark.parametrize("filename", ["deleted.json", "detached.json"])
def test_warm_hit_rejects_ledger_access_failure(case, monkeypatch, filename):
    cid, croot, wroot = case
    _write_group(wroot)
    path = croot / filename
    path.write_text("[]", encoding="utf-8")
    _age_tree(croot)
    _age_tree(wroot)
    _freeze_stats(monkeypatch, croot, wroot)
    membership.campaign_index(cid)
    real = io.open

    def denied(file, *args, **kwargs):
        if os.fspath(file) == os.fspath(path):
            raise PermissionError("fixture access failure")
        return real(file, *args, **kwargs)

    monkeypatch.setattr(io, "open", denied)
    with pytest.raises(group_refs.GroupIdentityConflict):
        membership.campaign_index(cid)
    monkeypatch.setattr(io, "open", real)
    assert GROUP in membership.campaign_index(cid).groups


def test_valid_duplicate_nonactor_asset_library_ledgers_are_retained(case):
    cid, croot, wroot = case
    _write_group(wroot)
    gone = ["lore/saltmarch", "lore/saltmarch", "assets/library/saltmarch.png", "assets/groups/salt-circle/default/map"]
    off = ["locations/saltmarch", "locations/saltmarch"]
    (croot / "deleted.json").write_text(json.dumps(gone), encoding="utf-8")
    (croot / "detached.json").write_text(json.dumps(off), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in (croot / "deleted.json", croot / "detached.json")}
    capture = overlay.group_capture(cid)
    assert capture.deleted == frozenset(gone) and capture.detached == frozenset(off)
    assert GROUP in membership.campaign_index(cid).groups
    assert before == {path.name: path.read_bytes() for path in (croot / "deleted.json", croot / "detached.json")}


@pytest.mark.parametrize("input_kind", ["ledger", "content"])
def test_preflight_compute_mismatch_returns_coupled_uncached_rows(case, monkeypatch, input_kind):
    cid, croot, wroot = case
    path = _write_group(wroot)
    _age_tree(croot)
    _age_tree(wroot)
    real = overlay.group_capture
    captures = []

    def changing_capture(cid):
        if len(captures) == 1:
            if input_kind == "ledger":
                (croot / "detached.json").write_text('["characters/mara"]', encoding="utf-8")
            else:
                path.write_bytes(path.read_bytes().replace(b"pcs:winifred", b"pcs:seraphine"))
        capture = real(cid)
        captures.append(capture)
        return capture

    monkeypatch.setattr(overlay, "group_capture", changing_capture)
    got = membership.campaign_index(cid)
    assert len(captures) == 2  # one preflight, one fresh computation, no retry
    assert not membership._memo_pool
    assert got.groups[GROUP].members == (("pcs:winifred",) if input_kind == "ledger" else ("characters:mara", "pcs:seraphine"))
    assert membership.campaign_index(cid) == got


def test_capture_root_switch_returns_uncached(case, monkeypatch):
    cid, croot, wroot = case
    _write_group(wroot, members="characters:mara")
    other = worlds.world_root(worlds.create_world("Saltmarch"))
    _write_group(other, members="pcs:winifred")
    real = overlay.group_capture
    calls = []

    def switching(cid):
        if len(calls) == 1:
            path = croot / "campaign.md"
            before = path.read_text(encoding="utf-8")
            after = before.replace(f"world: {wroot.name}", f"world: {other.name}")
            assert before != after
            path.write_text(after, encoding="utf-8")
        captured = real(cid)
        calls.append(captured)
        return captured

    monkeypatch.setattr(overlay, "group_capture", switching)
    assert membership.campaign_index(cid).groups[GROUP].members == ("pcs:winifred",)
    assert len(calls) == 2 and not membership._memo_pool
    assert calls[0].roots != calls[1].roots
    assert membership.campaign_index(cid).groups[GROUP].members == ("pcs:winifred",)


@pytest.mark.parametrize("field", ["members", "secrecy"])
def test_concurrent_cache_insertion_cannot_bypass_preflight(tmp_path, monkeypatch, field):
    path = _write_group(tmp_path, members="characters:mara", secrecy="secret")
    original = path.read_bytes()
    changed = original.replace(b"characters:mara", b"characters:MARA") if field == "members" else original.replace(b"secret", b"public")
    assert len(original) == len(changed)
    _age_tree(tmp_path)
    _freeze_stats(monkeypatch, tmp_path)
    ready_b, release_b, ready_a, release_a = (threading.Event() for _ in range(4))
    real_put, real_memo, real_invert = statcache._put, statcache.memo_stamped, membership.index_from_rows
    results, failures, constructions = {}, [], []

    def held_put(pool, key, value, budget):
        if threading.current_thread().name == "reader-b" and key[:2] == ("membership", "world"):
            ready_b.set()
            assert release_b.wait(30)
        return real_put(pool, key, value, budget)

    def held_memo(key, compute, **kwargs):
        if threading.current_thread().name == "reader-a":
            assert not membership._memo_pool  # A inspected an empty pool
            ready_a.set()
            assert release_a.wait(30)
        return real_memo(key, compute, **kwargs)

    def counted_invert(rows):
        constructions.append(threading.current_thread().name)
        return real_invert(rows)

    def reader(label):
        results[label] = membership.world_index(tmp_path)

    # Worker exceptions belong to the parent assertion, not a lost thread.
    monkeypatch.setattr(threading, "excepthook", lambda args: failures.append(args.exc_value))

    monkeypatch.setattr(statcache, "_put", held_put)
    monkeypatch.setattr(statcache, "memo_stamped", held_memo)
    monkeypatch.setattr(membership, "index_from_rows", counted_invert)
    a, b = (threading.Thread(target=reader, args=(label,), name=f"reader-{label}") for label in ("a", "b"))
    try:
        path.write_bytes(changed)
        b.start()
        assert ready_b.wait(30)
        path.write_bytes(original)
        a.start()
        assert ready_a.wait(30)
        release_b.set()
        b.join(30)
        assert not b.is_alive() and membership._memo_pool
        wrapper = next(iter(membership._memo_pool.values()))[1]
        # Its own provenance describes B's bytes, not A's current capture.
        assert wrapper.groups_digest != entities.group_capture(tmp_path).groups_digest
        release_a.set()
        a.join(30)
        assert not a.is_alive() and not failures
        assert constructions.count("reader-a") == 1  # exactly one fresh fallback
        assert results["a"].groups[GROUP].members == ("characters:mara",)
        assert results["a"].groups[GROUP].secrecy == "secret"
        assert results["a"].digest != results["b"].digest
        assert not membership._memo_pool
        assert membership.world_index(tmp_path) == results["a"]
    finally:
        release_a.set()
        release_b.set()
        for thread in (a, b):
            if thread.ident is not None:
                thread.join(30)


def test_same_cid_two_store_roots_do_not_share_results(tmp_path, monkeypatch):
    got = []
    for directory, actor in (("one", "characters:mara"), ("two", "pcs:winifred")):
        monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / directory))
        wid = worlds.create_world("Realm")
        cid = campaigns.create_campaign("Saltmarch", wid)
        wroot, croot = worlds.world_root(wid), overlay.croot_of(cid)
        _write_group(wroot, members=actor, leader="")
        _age_tree(croot)
        _age_tree(wroot)
        got.append((cid, membership.campaign_index(cid)))
    assert got[0][0] == got[1][0]
    assert [index.groups[GROUP].members for _, index in got] == [("characters:mara",), ("pcs:winifred",)]
    assert len(membership._memo_pool) == 2


def test_group_state_and_body_do_not_move_inverse_digest(tmp_path):
    path = _write_group(tmp_path)
    first = membership.world_index(tmp_path)
    before = entities.group_capture(tmp_path)
    state = tmp_path / "groups" / "salt-circle" / "state.md"
    state.parent.mkdir()
    state.write_text("Seraphine keeps the ledger.", encoding="utf-8")
    assert entities.group_capture(tmp_path).groups_digest == before.groups_digest
    path.write_bytes(path.read_bytes() + b"Mara keeps the roster.")
    assert entities.group_capture(tmp_path).groups_digest != before.groups_digest
    assert membership.world_index(tmp_path).digest == first.digest


def test_native_alias_filter_rechecks_without_actor_body_reads(case, monkeypatch):
    cid, croot, wroot = case
    path = _write_group(wroot, members="characters:mara", leader="characters:mara")
    actor = wroot / "characters" / "mara"
    actor.mkdir(parents=True)
    alias = actor.with_name("MARA")
    if not alias.exists() or not alias.samefile(actor):
        pytest.skip("native alias proof requires a case-insensitive fixture directory")
    (croot / "detached.json").write_text('["characters/MARA"]', encoding="utf-8")
    _age_tree(croot)
    _age_tree(wroot)
    _freeze_stats(monkeypatch, croot, wroot)
    real = io.open
    opened = []

    def record_open(file, *args, **kwargs):
        opened.append(Path(file))
        return real(file, *args, **kwargs)

    monkeypatch.setattr(io, "open", record_open)
    assert membership.campaign_index(cid).groups[GROUP].members == ()
    assert membership._memo_pool
    actor.rmdir()
    got = membership.campaign_index(cid).groups[GROUP]
    assert (got.members, got.leader) == (("characters:mara",), "characters:mara")
    assert opened and all("characters" not in file.parts and "pcs" not in file.parts for file in opened)
    assert path.exists()


def test_group_listing_uses_filename_identity_and_universal_newlines(tmp_path):
    path = _write_group(tmp_path)
    path.write_bytes(b"---\r\nname: Salt Circle\r\nid: seraphine\r\nlayer: campaign\r\nmembers: pcs:winifred\r\n---\r\n")
    rows, stamps, digest = entities.group_listing(tmp_path)
    assert rows == [{"name": "Salt Circle", "id": "salt-circle", "members": "pcs:winifred", "layer": "world"}]
    assert stamps is not None and digest == entities.group_capture(tmp_path).groups_digest
    assert membership.world_index(tmp_path).groups[GROUP].members == ("pcs:winifred",)


def test_campaign_readers_never_write_or_take_campaign_locks(case, monkeypatch):
    cid, croot, wroot = case
    _write_group(wroot)
    before = {path: path.read_bytes() for root in (croot, wroot) for path in root.rglob("*") if path.is_file()}

    def forbidden(*args, **kwargs):
        pytest.fail("a membership reader attempted a write or campaign lock")

    monkeypatch.setattr(locks, "campaign_lock", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    assert GROUP in membership.campaign_index(cid).groups
    assert before == {path: path.read_bytes() for root in (croot, wroot) for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("scope", ["world", "campaign"])
def test_unreadable_group_directory_never_returns_partial_index(case, monkeypatch, scope):
    cid, _croot, wroot = case
    _write_group(wroot)
    get_index = (lambda: membership.world_index(wroot)) if scope == "world" else (lambda: membership.campaign_index(cid))
    assert GROUP in get_index().groups
    real_scandir, real_listdir = os.scandir, os.listdir

    def denied_scandir(path):
        if os.fspath(path) == os.fspath(wroot / "groups"):
            raise PermissionError("fixture collection access failure")
        return real_scandir(path)

    def denied_listdir(path):
        if os.fspath(path) == os.fspath(wroot / "groups"):
            raise PermissionError("fixture collection access failure")
        return real_listdir(path)

    # Python 3.11 enumerates through listdir; newer pathlib uses scandir.
    # Deny the OS collection at either boundary, independent of the parser.
    monkeypatch.setattr(os, "scandir", denied_scandir)
    monkeypatch.setattr(os, "listdir", denied_listdir)
    with pytest.raises(PermissionError):
        get_index()


@pytest.mark.parametrize("raw", [b"\xff", None])
def test_warm_group_read_failure_cannot_serve_cached_index(tmp_path, monkeypatch, raw):
    path = _write_group(tmp_path)
    _age_tree(tmp_path)
    _freeze_stats(monkeypatch, tmp_path)
    membership.world_index(tmp_path)
    assert membership._memo_pool
    if raw is not None:
        path.write_bytes(raw)
        with pytest.raises(UnicodeDecodeError):
            membership.world_index(tmp_path)
    else:
        real = io.open

        def denied(file, *args, **kwargs):
            if os.fspath(file) == os.fspath(path):
                raise PermissionError("fixture group access failure")
            return real(file, *args, **kwargs)

        monkeypatch.setattr(io, "open", denied)
        with pytest.raises(PermissionError):
            membership.world_index(tmp_path)


def test_unvouchable_input_is_returned_without_memoizing(tmp_path, monkeypatch):
    _write_group(tmp_path)
    monkeypatch.setattr(statcache, "stamp", lambda path: None)
    assert entities.group_capture(tmp_path).stamps is None
    assert GROUP in membership.world_index(tmp_path).groups
    assert not membership._memo_pool



def test_native_owned_group_shadow_matches_one_group_reader(case):
    cid, croot, wroot = case
    world_dir = wroot / "groups"
    world_dir.mkdir()
    native_tests._enable_case_sensitive(world_dir)
    lower = _write_group(wroot, members="characters:mara")
    upper = lower.with_name("SALT-CIRCLE.md")
    upper.write_bytes(lower.read_bytes().replace(b"characters:mara", b"pcs:winifred"))
    owned = _write_group(croot, members="characters:seraphine", leader="")
    owned_alias = owned.with_name("SALT-CIRCLE.md")
    if not owned_alias.exists() or not owned_alias.samefile(owned):
        pytest.skip("mixed native policy requires an insensitive owned collection")
    _, context = overlay.read_group_membership(cid, "SALT-CIRCLE")
    got = membership.campaign_index(cid)
    assert list(got.groups) == [GROUP]
    assert got.groups[GROUP].members == context.members == ("characters:seraphine",)
    assert overlay.group_capture(cid).sources[0].path == owned


def test_native_group_tombstones_use_world_namespace(case):
    cid, croot, wroot = case
    world_dir = wroot / "groups"
    world_dir.mkdir()
    native_tests._enable_case_sensitive(world_dir)
    lower = _write_group(wroot, members="characters:mara")
    upper = lower.with_name("SALT-CIRCLE.md")
    upper.write_bytes(lower.read_bytes())
    assert not lower.samefile(upper)
    (croot / "deleted.json").write_text('["groups/SALT-CIRCLE"]', encoding="utf-8")
    got = membership.campaign_index(cid)
    assert list(got.groups) == [GROUP]
    assert got.groups[GROUP].members[0] == "characters:mara"



def test_data_directory_switch_during_capture_cannot_mix_stores(tmp_path, monkeypatch):
    roots = []
    for directory, actor in (("one", "pcs:winifred"), ("two", "characters:seraphine")):
        home = tmp_path / directory
        monkeypatch.setenv("GRIMOIRE_HOME", str(home))
        wid = worlds.create_world("Realm")
        cid = campaigns.create_campaign("Saltmarch", wid)
        croot, wroot = overlay.croot_of(cid), worlds.world_root(wid)
        path = _write_group(wroot, members=actor, leader="")
        path.rename(path.with_name("saltmarch.md"))
        if directory == "one":
            _write_group(croot, members="characters:mara", leader="")
        roots.append((home, cid, croot, wroot))
    first, second = roots
    monkeypatch.setenv("GRIMOIRE_HOME", str(first[0]))
    real_view, real_stamp = overlay.view, statcache.stamp
    captures = []
    switched = []

    def view(cid):
        captures.append(1)
        return real_view(cid)

    def stamp(path):
        if len(captures) == 2 and Path(path) == first[2] and not switched:
            switched.append(1)
            monkeypatch.setenv("GRIMOIRE_HOME", str(second[0]))
        return real_stamp(path)

    monkeypatch.setattr(overlay, "view", view)
    monkeypatch.setattr(statcache, "stamp", stamp)
    result = membership.campaign_index(first[1])
    assert result.groups[GROUP].members == ("characters:mara",)
    assert result.groups["groups:saltmarch"].members == ("pcs:winifred",)
    assert membership.campaign_index(second[1]).groups["groups:saltmarch"].members == ("characters:seraphine",)


def test_captured_owned_uppercase_extension_still_overrides_world(case):
    cid, croot, wroot = case
    directory = croot / "groups"
    directory.mkdir()
    native_tests._enable_case_sensitive(directory)
    owned = _write_group(croot, members="characters:mara", leader="")
    owned = owned.rename(owned.with_suffix(".MD"))
    if not owned.match("*.md"):
        pytest.skip("uppercase extension enumeration requires native Windows path matching")
    assert not owned.with_suffix(".md").exists()
    _write_group(wroot, members="pcs:winifred", leader="")
    assert membership.campaign_index(cid).groups[GROUP].members == ("characters:mara",)
