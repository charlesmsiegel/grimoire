"""Membership reversal identity is fenced before an owned original disappears."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from grimoire.store import (
    assets,
    atomic,
    campaign_images,
    campaigns,
    entities,
    group_refs,
    journal,
    locks,
    overlay,
    reclassify,
    revision,
    sync,
    undo,
    world_images,
    worlds,
)

from .test_campaign_images_store import _png


@pytest.fixture
def owned(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    eid = entities.create_entity(campaigns.campaign_root(cid), "groups", "Salt Circle")
    with undo.journalled_group_members(cid, eid, bindings=frozenset(), label="Salt Circle -- members"):
        overlay.update_entity(cid, "groups", eid, fields={"members": "characters:mara"})
    row = next(r for r in journal.read(cid) if r.get("field") == "members")
    return {"wid": wid, "cid": cid, "eid": eid, "row": row, "root": campaigns.campaign_root(cid)}


@pytest.mark.parametrize("content", [b"{", b"[]", b'{}', b'{"seq":true,"entries":[]}',
    b'{"seq":0,"entries":{}}', b'{"seq":0,"entries":[7]}', b"\xff"])
def test_lifetime_journal_preflight_refuses_whole_document_corruption(owned, content):
    path = owned["root"] / "journal.json"
    path.write_bytes(content)
    reader = getattr(journal, "require_readable", None)
    assert callable(reader), "strict lifecycle journal reader is missing"
    with pytest.raises(group_refs.GroupIdentityConflict):
        reader(owned["cid"])
    assert path.read_bytes() == content


def test_retirement_is_permanent_across_generic_repoint_and_matching_new_record(owned):
    retire = getattr(journal, "retire_membership_targets", None)
    assert callable(retire), "membership retirement fence is missing"
    retire(owned["cid"], "groups", owned["eid"])
    journal.repoint_records(owned["cid"], {f"groups/{owned['eid']}": f"lore/{owned['eid']}"})
    target = journal.get(owned["cid"], owned["row"]["id"])["undo"]["target"]
    assert target["retired"] is True
    with pytest.raises(undo.UndoConflict):
        undo.read_value(owned["cid"], target)


def test_begin_relocation_suspends_and_only_its_token_can_complete(owned):
    begin = getattr(journal, "begin_membership_relocation", None)
    complete = getattr(journal, "complete_membership_relocation", None)
    assert callable(begin) and callable(complete), "membership relocation fences are missing"
    token = begin(owned["cid"], "groups", owned["eid"], "lore")
    assert isinstance(token, str) and token
    target = journal.get(owned["cid"], owned["row"]["id"])["undo"]["target"]
    assert target["relocating"]["operation"] == token
    with pytest.raises(undo.UndoConflict):
        undo.read_value(owned["cid"], target)
    journal.repoint_records(owned["cid"], {f"groups/{owned['eid']}": f"lore/{owned['eid']}"})
    complete(owned["cid"], "different-operation", "lore", owned["eid"])
    target = journal.get(owned["cid"], owned["row"]["id"])["undo"]["target"]
    assert target["relocating"]["operation"] == token
    complete(owned["cid"], token, "lore", owned["eid"] + "-2")
    target = journal.get(owned["cid"], owned["row"]["id"])["undo"]["target"]
    assert "relocating" not in target
    assert (target["kind"], target["id"]) == ("lore", owned["eid"] + "-2")
    assert target["scope"] == owned["row"]["undo"]["target"]["scope"]
    assert target["bindings"] == [] and target["inherited"] == []


def test_members_undo_follows_original_suffixed_reclassification(owned):
    cid, eid, root, row = owned["cid"], owned["eid"], owned["root"], owned["row"]
    moved = reclassify.campaign_entity(cid, "groups", eid, "lore")
    replacement = entities.create_entity(root, "groups", "Salt Circle", fields={"members": "characters:mara"})
    assert replacement == eid
    with pytest.raises(undo.UndoConflict):
        undo.undo(cid, row["id"])
    back = reclassify.campaign_entity(cid, "lore", moved, "groups")
    assert back != replacement
    reversed_row = undo.undo(cid, row["id"])
    assert reversed_row["undo"]["target"]["id"] == back
    assert "members" not in entities.read_entity(root, "groups", back)["meta"]
    assert entities.read_entity(root, "groups", replacement)["meta"]["members"] == "characters:mara"


def test_failed_relocation_completion_never_targets_old_slug(owned, monkeypatch):
    cid, eid, root, row = owned["cid"], owned["eid"], owned["root"], owned["row"]
    original = atomic.write_text

    def fail_completion(path, text, *args, **kwargs):
        if path == root / "journal.json" and (root / "lore" / f"{eid}.md").exists():
            raise OSError("relocation completion failed")
        return original(path, text, *args, **kwargs)

    monkeypatch.setattr(atomic, "write_text", fail_completion)
    with pytest.raises(OSError, match="completion failed"):
        reclassify.campaign_entity(cid, "groups", eid, "lore")
    monkeypatch.setattr(atomic, "write_text", original)
    assert not (root / "groups" / f"{eid}.md").exists()
    assert (root / "lore" / f"{eid}.md").exists()
    entities.create_entity(root, "groups", "Salt Circle", fields={"members": "characters:mara"})
    with pytest.raises(undo.UndoConflict):
        undo.undo(cid, row["id"])
    assert journal.get(cid, row["id"])["undo"]["target"].get("relocating")


@pytest.mark.parametrize("operation", ["delete", "dematerialize"])
def test_removed_copy_permanently_retires_matching_offers(owned, operation):
    cid, eid, root, row = owned["cid"], owned["eid"], owned["root"], owned["row"]
    if operation == "delete":
        overlay.delete_entity(cid, "groups", eid)
    else:
        overlay.dematerialize(cid, f"groups/{eid}")
    entities.create_entity(root, "groups", "Salt Circle", fields={"members": "characters:mara"})
    with pytest.raises(undo.UndoConflict):
        undo.undo(cid, row["id"])
    assert journal.get(cid, row["id"])["undo"]["target"]["retired"] is True


@pytest.mark.parametrize("operation", ["delete", "dematerialize", "reclassify"])
def test_retirement_preflight_preserves_copy_and_manifest(owned, operation):
    root, cid, eid = owned["root"], owned["cid"], owned["eid"]
    (root / "journal.json").write_bytes(b"{")
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(group_refs.GroupIdentityConflict):
        if operation == "delete":
            overlay.delete_entity(cid, "groups", eid)
        elif operation == "dematerialize":
            overlay.dematerialize(cid, f"groups/{eid}")
        else:
            reclassify.campaign_entity(cid, "groups", eid, "lore")
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_malformed_suspension_cannot_be_treated_as_available(owned):
    target = dict(owned["row"]["undo"]["target"])
    target["relocating"] = {}
    with pytest.raises(undo.UndoConflict):
        undo.read_value(owned["cid"], target)


@pytest.mark.parametrize("operation", ["reclassify", "delete", "dematerialize"])
def test_failed_lifecycle_effects_invalidate_campaign_revision(owned, monkeypatch, operation):
    cid, eid, root = owned["cid"], owned["eid"], owned["root"]
    old = revision.current(cid)
    original = atomic.write_text

    def fail_after_effect(path, text, *args, **kwargs):
        if (path == root / "journal.json"
                and (operation != "reclassify" or (root / "lore" / f"{eid}.md").exists())):
            original(path, text, *args, **kwargs)
            raise OSError("fence may have landed")
        return original(path, text, *args, **kwargs)

    monkeypatch.setattr(atomic, "write_text", fail_after_effect)
    with pytest.raises(OSError, match="fence may have landed"):
        if operation == "reclassify":
            reclassify.campaign_entity(cid, "groups", eid, "lore")
        elif operation == "delete":
            overlay.delete_entity(cid, "groups", eid)
        else:
            overlay.dematerialize(cid, f"groups/{eid}")
    assert revision.current(cid) != old
    with pytest.raises(revision.RevisionMismatchError):
        revision.require(cid, old)


def test_strict_preflight_failure_does_not_stamp_revision(owned):
    cid, root = owned["cid"], owned["root"]
    old = revision.current(cid)
    (root / "journal.json").write_bytes(b"{")
    with pytest.raises(group_refs.GroupIdentityConflict):
        reclassify.campaign_entity(cid, "groups", owned["eid"], "lore")
    assert revision.current(cid) == old


@pytest.mark.parametrize("phase", ["append", "mark"])
@pytest.mark.parametrize("redo", [False, True])
def test_members_undo_failure_after_restore_invalidates_revision(owned, monkeypatch, phase, redo):
    cid, row, root, eid = owned["cid"], owned["row"], owned["root"], owned["eid"]
    if redo:
        row = undo.undo(cid, row["id"])
    previous = journal.read(cid)
    old = revision.current(cid)
    expected = row["undo"]["restore"]["raw"]
    original = journal.append if phase == "append" else journal.mark_undone

    def fail(*args, **kwargs):
        assert locks.holds_campaign(cid)
        raise OSError("history failed after restore")

    monkeypatch.setattr(journal, "append" if phase == "append" else "mark_undone", fail)
    with pytest.raises(OSError, match="history failed"):
        undo.undo(cid, row["id"])
    monkeypatch.setattr(journal, "append" if phase == "append" else "mark_undone", original)
    assert entities.read_entity(root, "groups", eid)["meta"].get("members") == expected
    assert revision.current(cid) != old
    with pytest.raises(revision.RevisionMismatchError):
        revision.require(cid, old)
    current = journal.read(cid)
    assert len(current) == len(previous) + (phase == "mark")
    assert not journal.get(cid, row["id"]).get("undone")


@pytest.mark.parametrize("first_copy", [False, True])
def test_group_write_failure_after_publication_invalidates_revision(owned, monkeypatch, first_copy):
    cid, eid, root = owned["cid"], owned["eid"], owned["root"]
    if first_copy:
        overlay.dematerialize(cid, f"groups/{eid}")
        entities.create_entity(worlds.world_root(owned["wid"]), "groups", "Salt Circle")
    old = revision.current(cid)
    original = atomic.write_text

    def fail(path, text, *args, **kwargs):
        result = original(path, text, *args, **kwargs)
        if path == root / "groups" / f"{eid}.md":
            assert locks.holds_campaign(cid)
            raise OSError("group publication may have landed")
        return result

    monkeypatch.setattr(atomic, "write_text", fail)
    with pytest.raises(OSError, match="publication may have landed"):
        overlay.update_entity(cid, "groups", eid, fields={"members": "pcs:winifred"})
    assert entities.read_entity(root, "groups", eid)["meta"]["members"] == "pcs:winifred"
    assert revision.current(cid) != old


@pytest.mark.parametrize("phase", ["world_move", "suspension", "completion"])
def test_world_move_failure_stamps_every_possibly_changed_campaign(owned, monkeypatch, phase):
    wid, eid, cid = owned["wid"], owned["eid"], owned["cid"]
    wroot = worlds.world_root(wid)
    entities.create_entity(wroot, "groups", "Salt Circle")
    second = campaigns.create_campaign("Saltmarch", wid)
    root = campaigns.campaign_root(second)
    entities.create_entity(root, "groups", "Salt Circle")
    with undo.journalled_group_members(second, eid, bindings=frozenset(), label="Salt Circle -- members"):
        overlay.update_entity(second, "groups", eid, fields={"members": "pcs:winifred"})
    old = {c: revision.current(c) for c in (cid, second)}
    move, write = entities.reclassify, atomic.write_text

    def fail_move(*args, **kwargs):
        landed = move(*args, **kwargs)
        if args[0] == wroot:
            raise OSError("World move landed before follow")
        return landed

    def fail_fence(path, text, *args, **kwargs):
        if phase == "suspension" and path == root / "journal.json":
            write(path, text, *args, **kwargs)
            raise OSError("second suspension may have landed")
        if (phase == "completion" and path == owned["root"] / "journal.json"
                and (owned["root"] / "lore" / f"{eid}.md").exists()):
            raise OSError("first completion failed")
        return write(path, text, *args, **kwargs)

    if phase == "world_move":
        monkeypatch.setattr(entities, "reclassify", fail_move)
    else:
        monkeypatch.setattr(atomic, "write_text", fail_fence)
    with pytest.raises(OSError):
        reclassify.world_entity(wid, "groups", eid, "lore")
    for c in (cid, second):
        assert revision.current(c) != old[c]
        with pytest.raises(revision.RevisionMismatchError):
            revision.require(c, old[c])
    if phase == "suspension":
        assert (wroot / "groups" / f"{eid}.md").exists()
    else:
        assert (wroot / "lore" / f"{eid}.md").exists()


def test_world_lifetime_preflight_preserves_all_roots_on_unreadable_second_journal(owned):
    wid, eid = owned["wid"], owned["eid"]
    entities.create_entity(worlds.world_root(wid), "groups", "Salt Circle")
    other = campaigns.create_campaign("Saltmarch", wid)
    (campaigns.campaign_root(other) / "journal.json").write_bytes(b"{")
    home = owned["root"].parent.parent
    before = {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()}
    with pytest.raises(group_refs.GroupIdentityConflict):
        reclassify.world_entity(wid, "groups", eid, "lore")
    assert {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()} == before


def test_retained_owned_copy_survives_world_rebind(owned):
    other = worlds.create_world("Realm")
    entities.create_entity(worlds.world_root(other), "groups", "Salt Circle", fields={"members": "characters:mara"})
    meta = campaigns.campaign_meta_path(owned["cid"])
    atomic.write_text(meta, meta.read_text(encoding="utf-8").replace(f"world: {owned['wid']}", f"world: {other}"))
    assert campaigns.world_root_of(owned["cid"]) == worlds.world_root(other)
    undo.undo(owned["cid"], owned["row"]["id"])
    assert "members" not in entities.read_entity(owned["root"], "groups", owned["eid"])["meta"]


def test_lifetime_journal_absence_is_empty_but_access_failure_refuses(owned, monkeypatch):
    path = owned["root"] / "journal.json"
    path.unlink()
    journal.require_readable(owned["cid"])
    read = type(path).read_text

    def denied(self, *args, **kwargs):
        if self == path:
            raise PermissionError("journal inaccessible")
        return read(self, *args, **kwargs)

    monkeypatch.setattr(type(path), "read_text", denied)
    with pytest.raises(group_refs.GroupIdentityConflict):
        overlay.delete_entity(owned["cid"], "groups", owned["eid"])
    assert (owned["root"] / "groups" / f"{owned['eid']}.md").exists()


def test_world_move_cannot_capture_an_independent_detached_owned_original(owned):
    cid, eid, wid = owned["cid"], owned["eid"], owned["wid"]
    wroot = worlds.world_root(wid)
    entities.create_entity(wroot, "groups", "Salt Circle")
    overlay.add_detached(cid, f"groups/{eid}")
    before = journal.get(cid, owned["row"]["id"])["undo"]["target"]
    reclassify.world_entity(wid, "groups", eid, "lore")
    assert (owned["root"] / "groups" / f"{eid}.md").exists()
    assert not (owned["root"] / "lore" / f"{eid}.md").exists()
    assert journal.get(cid, owned["row"]["id"])["undo"]["target"] == before
    assert overlay.detached(cid) == {f"groups/{eid}"}
    undo.undo(cid, owned["row"]["id"])
    assert "members" not in entities.read_entity(owned["root"], "groups", eid)["meta"]


def test_members_journal_readback_failure_stamps_landed_edit(owned, monkeypatch):
    cid, eid, root = owned["cid"], owned["eid"], owned["root"]
    old = revision.current(cid)
    read = entities.read_entity

    def denied_after_publication(*args, **kwargs):
        if args[0] == root and args[1:3] == ("groups", eid):
            current = read(*args, **kwargs)
            if current["meta"].get("members") == "pcs:winifred":
                raise PermissionError("readback failed after publication")
        return read(*args, **kwargs)

    monkeypatch.setattr(entities, "read_entity", denied_after_publication)
    with (pytest.raises(undo.UndoConflict),
          undo.journalled_group_members(cid, eid, bindings=frozenset(), label="Salt Circle -- members")):
        overlay.update_entity(cid, "groups", eid, fields={"members": "pcs:winifred"})
    assert read(root, "groups", eid)["meta"]["members"] == "pcs:winifred"
    assert revision.current(cid) != old


@pytest.mark.parametrize("operation", ["take_world", "prune"])
def test_copy_reversion_retires_before_manifest_reservation(owned, monkeypatch, operation):
    cid, eid, root = owned["cid"], owned["eid"], owned["root"]
    wroot = worlds.world_root(owned["wid"])
    entities.create_entity(wroot, "groups", "Salt Circle", fields={"members": "characters:mara"})
    base = entities.entity_hash(wroot, "groups", eid)
    campaigns.write_manifest(cid, {f"groups/{eid}": base if operation == "prune" else "old common version"})
    if operation == "prune":
        mp = campaigns.campaign_meta_path(cid)
        atomic.write_text(mp, mp.read_text(encoding="utf-8").replace("world_copy: overlay", "world_copy: full"))
    # Inspect the actual owning module, which the facade exports but does not
    # late-bind. This asserts retirement BEFORE either reservation or unlink.
    manifest_path = root / "sync.md"
    atomic_write = atomic.write_text

    def check_fence(path, text, *args, **kwargs):
        if path == manifest_path:
            assert journal.get(cid, owned["row"]["id"])["undo"]["target"]["retired"] is True
        return atomic_write(path, text, *args, **kwargs)

    monkeypatch.setattr(atomic, "write_text", check_fence)
    if operation == "prune":
        campaigns.ensure_campaign_slim(cid)
    else:
        sync.accept(cid, [{"kind": "groups", "id": eid}])
    assert not overlay.has_own_copy(cid, "groups", eid)
    entities.create_entity(root, "groups", "Salt Circle", fields={"members": "characters:mara"})
    with pytest.raises(undo.UndoConflict):
        undo.undo(cid, owned["row"]["id"])


def test_take_world_failure_after_unlink_stamps_revision(owned, monkeypatch):
    cid, eid, root = owned["cid"], owned["eid"], owned["root"]
    entities.create_entity(worlds.world_root(owned["wid"]), "groups", "Salt Circle")
    campaigns.write_manifest(cid, {f"groups/{eid}": "old common version"})
    old = revision.current(cid)
    write = atomic.write_text

    def fail(path, text, *args, **kwargs):
        if path == root / "sync.md" and not overlay.has_own_copy(cid, "groups", eid):
            raise OSError("manifest failed after unlink")
        return write(path, text, *args, **kwargs)

    monkeypatch.setattr(atomic, "write_text", fail)
    with pytest.raises(OSError, match="manifest failed"):
        sync.accept(cid, [{"kind": "groups", "id": eid}])
    assert not overlay.has_own_copy(cid, "groups", eid)
    assert revision.current(cid) != old


@pytest.mark.parametrize("marker", ["detached", "pinned"])
def test_take_world_native_alias_cannot_bypass_retained_copy_protection(owned, marker):
    cid, eid, root = owned["cid"], owned["eid"], owned["root"]
    wroot = worlds.world_root(owned["wid"])
    entities.create_entity(wroot, "groups", "Salt Circle", fields={"members": "pcs:winifred"})
    alias = eid.upper()
    try:
        same = (root / "groups" / f"{alias}.md").samefile(root / "groups" / f"{eid}.md")
    except FileNotFoundError:
        same = False
    if not same:
        pytest.skip("native filesystem keeps these identities distinct")
    campaigns.write_manifest(cid, {f"groups/{eid}": "old common version"})
    if marker == "detached":
        overlay.add_detached(cid, f"groups/{eid}")
    else:
        sync.set_pin(cid, "groups", eid, True)
    target = journal.get(cid, owned["row"]["id"])["undo"]["target"]
    original = entities.read_entity(root, "groups", eid)
    sync.accept(cid, [{"kind": "groups", "id": alias}])
    assert entities.read_entity(root, "groups", eid) == original
    assert journal.get(cid, owned["row"]["id"])["undo"]["target"] == target
    assert campaigns.read_manifest(cid)[f"groups/{eid}"] == "old common version"


@pytest.mark.parametrize("eid", [r"..\lore\salt-circle", "../lore/salt-circle", r"..\..\saltmarch-2\groups\salt-circle"])
def test_prepared_inherited_update_rejects_unsafe_id_before_path_effects(owned, eid):
    wroot = worlds.world_root(owned["wid"])
    entities.create_entity(wroot, "lore", "Salt Circle", body="Mara keeps the ledger.")
    root = owned["root"]
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(entities.EntityNotFound):
        overlay.update_entity(owned["cid"], "groups", eid, body="Winifred keeps the ledger.")
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_group_put_rejects_native_backslash_traversal_without_revision(client, owned):
    entities.create_entity(worlds.world_root(owned["wid"]), "lore", "Salt Circle", body="Mara keeps the ledger.")
    result = client.put(f"/api/campaigns/{owned['cid']}/groups/..%5Clore%5Csalt-circle", json={"body": "Winifred keeps the ledger."})
    assert result.status_code == 404
    assert not (owned["root"] / "lore" / "salt-circle.md").exists()


@pytest.mark.parametrize("filename", ["deleted.json", "detached.json"])
def test_image_promotion_preflights_before_copy_or_avatar_swap(owned, filename):
    cid, eid, root = owned["cid"], owned["eid"], owned["root"]
    wroot = worlds.world_root(owned["wid"])
    entities.create_entity(wroot, "groups", "Salt Circle")
    assets.put_image(wroot, eid, "default", "gallery_1", _png(), "png", "groups")
    (root / filename).write_bytes(b"{")
    home = root.parent.parent
    before = {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()}
    with pytest.raises(group_refs.GroupIdentityConflict):
        overlay.promote_image(cid, eid, "default", "gallery_1", "groups")
    assert {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()} == before


def test_world_delete_sweep_uses_only_its_captured_campaign_hold_set(client, owned, monkeypatch):
    wid, eid, cid = owned["wid"], owned["eid"], owned["cid"]
    wroot = worlds.world_root(wid)
    entities.create_entity(wroot, "groups", "Salt Circle")
    delete = entities.delete_entity
    forget = overlay._forget_in_campaign
    visited, created = [], []

    def delete_with_new_dependent(root, kind, rid):
        if root == wroot and kind == "groups" and rid == eid:
            # Future.result propagates worker errors to the controlling test;
            # an exception cannot pass as merely a background-thread warning.
            with ThreadPoolExecutor(max_workers=1) as workers:
                created.append(workers.submit(campaigns.create_campaign, "Realm", wid).result(timeout=10))
        return delete(root, kind, rid)

    def record_scope(campaign, *args):
        visited.append(campaign)
        assert locks.holds_campaign(campaign), "sweep acquired a campaign outside the captured sorted hold"
        return forget(campaign, *args)

    monkeypatch.setattr(entities, "delete_entity", delete_with_new_dependent)
    monkeypatch.setattr(overlay, "_forget_in_campaign", record_scope)
    result = client.delete(f"/api/worlds/{wid}/groups/{eid}")
    assert result.status_code == 200
    assert created and created[0] != cid
    assert visited == [cid]


def test_world_image_sweep_stays_in_captured_lock_set(owned, monkeypatch):

    wid, cid = owned['wid'], owned['cid']
    world_images.put_image(wid, 'map', _png(), 'png')
    directory = world_images.images_dir(wid)
    delete = assets.delete_in
    drop = overlay.drop_library_tombstone
    visited, created = [], []

    def delete_with_new_dependent(root, name, *args, **kwargs):
        if root == directory:
            with ThreadPoolExecutor(max_workers=1) as workers:
                created.append(workers.submit(campaigns.create_campaign, 'Realm', wid).result(timeout=10))
        return delete(root, name, *args, **kwargs)

    def check_drop(campaign, name):
        visited.append(campaign)
        assert locks.holds_campaign(campaign), f'unheld late dependent {campaign}; already holding {cid}'
        return drop(campaign, name)

    monkeypatch.setattr(assets, 'delete_in', delete_with_new_dependent)
    monkeypatch.setattr(overlay, 'drop_library_tombstone', check_drop)
    world_images.delete_image(wid, 'map')
    assert created and created[0] != cid
    assert visited == [cid]


@pytest.mark.parametrize("redo", [False, True])
def test_members_reversal_keeps_bookkeeping_when_postwrite_read_is_unavailable(owned, monkeypatch, redo):
    cid, eid = owned["cid"], owned["eid"]
    original = owned["row"]
    if redo:
        original = undo.undo(cid, original["id"])
    read, write = undo.read_value, undo.write_value
    wrote = []

    def restore(*args, **kwargs):
        result = write(*args, **kwargs)
        wrote.append(True)
        return result

    def unreadable_after_restore(*args, **kwargs):
        if wrote:
            raise OSError("record became unreadable after restoration")
        return read(*args, **kwargs)

    monkeypatch.setattr(undo, "write_value", restore)
    monkeypatch.setattr(undo, "read_value", unreadable_after_restore)
    reversal = undo.undo(cid, original["id"])
    assert reversal["source"] == "undo" and reversal["reverted"] == original["id"]
    assert reversal["undo"] is None and reversal["why"] == undo.UNREADABLE
    assert reversal["after"] == undo.UNREADABLE
    assert journal.get(cid, original["id"])["undone"]
    assert overlay.read_entity(cid, "groups", eid)["meta"].get("members") == (
        "characters:mara" if redo else None)
    with pytest.raises(undo.NotUndoable):
        undo.undo(cid, reversal["id"])



def test_image_promotion_resolves_current_world_inside_hold(owned, monkeypatch):
    cid, eid = owned["cid"], owned["eid"]
    old_root = worlds.world_root(owned["wid"])
    other_wid = worlds.create_world("Saltmarch")
    other_root = worlds.world_root(other_wid)
    for root, color in ((old_root, (10, 20, 30)), (other_root, (30, 20, 10))):
        entities.create_entity(root, "groups", "Salt Circle")
        assets.put_image(root, eid, "default", "gallery_1", _png(color=color), "png", "groups")
    expected = assets.image_path(other_root, eid, "default", "gallery_1", "groups").read_bytes()
    publication_lock = locks.campaign_lock(cid)
    lock_type = type(publication_lock)
    enter = lock_type.__enter__
    changed = []

    def recreate_before_hold(current_lock):
        if current_lock is publication_lock and not changed and not current_lock._is_owned():
            changed.append(True)
            campaigns.delete_campaign(cid)
            assert campaigns.create_campaign("Saltmarch", other_wid) == cid
        return enter(current_lock)

    monkeypatch.setattr(lock_type, "__enter__", recreate_before_hold)
    overlay.promote_image(cid, eid, "default", "gallery_1", "groups")
    avatar = assets.image_path(campaigns.campaign_root(cid), eid, "default", assets.AVATAR, "groups")
    assert avatar is not None and avatar.read_bytes() == expected


@pytest.mark.parametrize("operation", ["group_reclassify", "library_image_delete"])
def test_world_mutation_excludes_rebound_campaign_before_any_ledger_effect(owned, monkeypatch, operation):
    wid, cid, eid = owned["wid"], owned["cid"], owned["eid"]
    entities.create_entity(worlds.world_root(wid), "groups", "Salt Circle")
    other_wid = worlds.create_world("Saltmarch")
    entities.create_entity(worlds.world_root(other_wid), "groups", "Salt Circle")
    if operation == "library_image_delete":
        for world in (wid, other_wid):
            world_images.put_image(world, "map", _png(), "png")
    hold = locks.hold_all
    replacement = {}

    @contextmanager
    def recreate_before_hold(cids, *args, **kwargs):
        if not replacement:
            replacement["started"] = True
            campaigns.delete_campaign(cid)
            assert campaigns.create_campaign("Saltmarch", other_wid) == cid
            root = campaigns.campaign_root(cid)
            if operation == "group_reclassify":
                overlay.materialize_entity(cid, "groups", eid)
                with undo.journalled_group_members(cid, eid, bindings=frozenset(), label="Salt Circle -- members"):
                    overlay.update_entity(cid, "groups", eid, fields={"members": "characters:mara"})
                replacement["row"] = next(r for r in journal.read(cid) if r.get("field") == "members")
                replacement["bytes"] = (root / "journal.json").read_bytes()
            else:
                campaign_images.delete_image(cid, "map")
                replacement["bytes"] = (root / "deleted.json").read_bytes()
        with hold(cids, *args, **kwargs):
            yield

    monkeypatch.setattr(locks, "hold_all", recreate_before_hold)
    if operation == "group_reclassify":
        result = reclassify.world_entity(wid, "groups", eid, "lore")
        assert result["campaigns"] == []
        assert (campaigns.campaign_root(cid) / "journal.json").read_bytes() == replacement["bytes"]
        undo.undo(cid, replacement["row"]["id"])
    else:
        world_images.delete_image(wid, "map")
        assert (campaigns.campaign_root(cid) / "deleted.json").read_bytes() == replacement["bytes"]
        assert "map" in campaign_images.list_hidden(cid)
        assert campaign_images.image_path(cid, "map") is None
