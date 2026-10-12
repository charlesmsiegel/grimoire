"""Membership copies must not erase the ledgers that establish actor identity."""

import json
import os
import shutil
import subprocess
import threading
from contextlib import contextmanager, suppress
from pathlib import Path

import pytest

from grimoire.routes import characters as character_routes
from grimoire.routes import entities as entity_routes
from grimoire.routes import greetings as greeting_routes
from grimoire.routes import worlds as world_routes
from grimoire.store import (
    appearances,
    atomic,
    campaign_images,
    campaigns,
    characters,
    entities,
    entity_schema,
    greetings,
    group_refs,
    journal,
    locks,
    overlay,
    pcs,
    reclassify,
    sync,
    undo,
    world_images,
    worlds,
)
from grimoire.store.campaigns import lifecycle as campaigns_lifecycle
from grimoire.store.campaigns import paths as campaigns_paths


@pytest.fixture
def case(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    return {"wid": wid, "cid": cid, "world": worlds.world_root(wid),
            "campaign": campaigns.campaign_root(cid)}


@pytest.mark.parametrize("filename,function", [
    ("detached.json", "detached_strict"), ("deleted.json", "deleted_strict")])
@pytest.mark.parametrize("content", [b"{", b"{}", b"null", b'["characters/mara", 7]',
    b'["characters/../mara"]', b'["unknown/mara"]', b"\xff"])
def test_strict_ledgers_refuse_corruption_before_effects(case, filename, function, content):
    path = case["campaign"] / filename
    path.write_bytes(content)
    reader = getattr(overlay, function, None)
    assert callable(reader), "strict ledger reader is missing"
    with pytest.raises(group_refs.GroupIdentityConflict):
        reader(case["cid"])
    assert path.read_bytes() == content


def test_strict_ledgers_preserve_all_supported_marker_shapes(case):
    off = ["characters/mara", "groups/salt-circle", "plotmap/plotmap",
           "locations/mara,seraphine", "characters/mara"]
    gone = ["plotmap", "groups/salt-circle", "assets/library/mara",
            "assets/characters/mara/default/avatar", "assets/groups/salt-circle/default/map"]
    (case["campaign"] / "detached.json").write_text(json.dumps(off), encoding="utf-8")
    (case["campaign"] / "deleted.json").write_text(json.dumps(gone), encoding="utf-8")
    reader = getattr(overlay, "require_identity_ledgers", None)
    assert callable(reader), "strict ledger preflight is missing"
    assert reader(case["cid"]) == (set(off), set(gone))


@pytest.mark.parametrize("filename,function", [
    ("detached.json", "detached_strict"), ("deleted.json", "deleted_strict")])
def test_strict_ledger_only_genuine_absence_is_empty(case, monkeypatch, filename, function):
    reader = getattr(overlay, function, None)
    assert callable(reader), "strict ledger reader is missing"
    assert reader(case["cid"]) == set()
    path = case["campaign"] / filename
    path.write_text("[]", encoding="utf-8")
    original = Path.read_text

    def unreadable(self, *args, **kwargs):
        if self == path:
            raise PermissionError("held identity ledger")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    with pytest.raises(group_refs.GroupIdentityConflict):
        reader(case["cid"])


@pytest.mark.parametrize("filename,operation", [
    ("detached.json", "add_detached"), ("deleted.json", "add_deleted"),
    ("deleted.json", "drop_library"), ("detached.json", "undetach"),
    ("deleted.json", "undetach"), ("detached.json", "repoint"),
    ("deleted.json", "repoint")])
def test_ledger_mutators_refuse_corruption_without_erasing_other_state(case, filename, operation):
    cid, root = case["cid"], case["campaign"]
    (root / "detached.json").write_text('["groups/salt-circle", "characters/mara"]', encoding="utf-8")
    (root / "deleted.json").write_text('["assets/library/mara"]', encoding="utf-8")
    campaigns_paths.write_manifest(cid, {"groups/salt-circle": "old-base"})
    (root / filename).write_bytes(b"{")
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    with pytest.raises(group_refs.GroupIdentityConflict):
        if operation == "add_detached":
            overlay.add_detached(cid, "pcs/winifred")
        elif operation == "add_deleted":
            overlay.add_deleted(cid, "pcs/winifred")
        elif operation == "drop_library":
            overlay.drop_library_tombstone(cid, "mara")
        elif operation == "undetach":
            overlay.undetach(cid, "groups/salt-circle")
        else:
            overlay.repoint_record(cid, "groups/salt-circle", "lore/salt-circle", keep_base=False)
    assert {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()} == before


def test_repoint_and_reattach_preserve_unrelated_markers(case):
    cid, root = case["cid"], case["campaign"]
    (root / "detached.json").write_text('["groups/salt-circle", "characters/mara"]', encoding="utf-8")
    (root / "deleted.json").write_text(json.dumps([
        "groups/salt-circle", "assets/groups/salt-circle/default/map", "assets/library/mara"]), encoding="utf-8")
    overlay.repoint_record(cid, "groups/salt-circle", "lore/salt-circle", keep_base=False)
    overlay.undetach(cid, "lore/salt-circle")
    assert json.loads((root / "detached.json").read_text(encoding="utf-8")) == ["characters/mara"]
    assert set(json.loads((root / "deleted.json").read_text(encoding="utf-8"))) == {
        "lore/salt-circle", "assets/library/mara"}


@pytest.mark.parametrize("writer,filename", [
    ("add_detached", "detached.json"), ("add_deleted", "deleted.json")])
def test_marker_writers_cannot_publish_invalid_new_refs(case, writer, filename):
    with pytest.raises(group_refs.GroupIdentityConflict):
        getattr(overlay, writer)(case["cid"], "unknown/mara")
    assert not (case["campaign"] / filename).exists()


@pytest.mark.parametrize("filename", ["detached.json", "deleted.json"])
@pytest.mark.parametrize("kind", ["characters", "pcs"])
def test_actor_birth_preflights_identity_before_publishing(case, filename, kind):
    root, cid = case["campaign"], case["cid"]
    (root / filename).write_bytes(b"{")
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(group_refs.GroupIdentityConflict):
        if kind == "characters":
            overlay.create_character(cid, "Mara")
        else:
            overlay.create_pc(cid, "Winifred", [])
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    assert not (root / kind / ("mara" if kind == "characters" else "winifred")).exists()


@pytest.mark.parametrize("filename", ["detached.json", "deleted.json"])
@pytest.mark.parametrize("operation", ["delete", "reclassify"])
def test_entity_lifecycle_preflights_before_file_effects(case, filename, operation):
    root, cid = case["campaign"], case["cid"]
    gid = entities.create_entity(root, "groups", "Salt Circle", body="Mara keeps the roster.")
    (root / filename).write_bytes(b"{")
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(group_refs.GroupIdentityConflict):
        if operation == "delete":
            overlay.delete_entity(cid, "groups", gid)
        else:
            reclassify.campaign_entity(cid, "groups", gid, "lore")
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_world_mutation_preflights_every_dependent(case):
    gid = entities.create_entity(case["world"], "groups", "Salt Circle")
    second = campaigns.create_campaign("Saltmarch", case["wid"])
    (campaigns.campaign_root(second) / "detached.json").write_bytes(b"{")
    wrapper = getattr(overlay, "world_identity_change", None)
    assert callable(wrapper), "World lifecycle preflight wrapper is missing"
    path = case["world"] / "groups" / f"{gid}.md"
    before = path.read_bytes()
    with pytest.raises(group_refs.GroupIdentityConflict), wrapper(case["world"]):
        entities.delete_entity(case["world"], "groups", gid)
    assert path.read_bytes() == before
    assert not (case["campaign"] / "groups" / f"{gid}.md").exists()


def test_library_image_delete_preflights_before_bytes_and_description(case):
    root = campaign_images.images_dir(case["cid"])
    root.mkdir(parents=True, exist_ok=True)
    image = root / "mara.png"
    image.write_bytes(b"existing image bytes")
    descriptions = root / "descriptions.json"
    descriptions.write_text('{"mara": "Mara at Saltmarch"}', encoding="utf-8")
    (case["campaign"] / "deleted.json").write_bytes(b"{")
    before = (image.read_bytes(), descriptions.read_bytes())
    with pytest.raises(group_refs.GroupIdentityConflict):
        campaign_images.delete_image(case["cid"], "mara")
    assert (image.read_bytes(), descriptions.read_bytes()) == before


@pytest.mark.parametrize("old,new", [
    ("groups/salt-circle", "unknown/salt-circle"),
    ("unknown/salt-circle", "groups/salt-circle"),
    ("groups/salt-circle", "groups/../salt-circle"),
    ("unknown/salt-circle", "unknown/salt-circle")])
def test_repoint_cannot_publish_invalid_record_refs(case, old, new):
    cid, root = case["cid"], case["campaign"]
    overlay.add_detached(cid, "groups/salt-circle")
    overlay.add_deleted(cid, "assets/groups/salt-circle/default/map")
    campaigns_paths.write_manifest(cid, {"groups/salt-circle": "old-base"})
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    with pytest.raises(group_refs.GroupIdentityConflict):
        overlay.repoint_record(cid, old, new, keep_base=True)
    assert {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()} == before


@pytest.mark.parametrize("filename", ["detached.json", "deleted.json"])
@pytest.mark.parametrize("operation", ["character", "pc", "greeting", "image", "direct_reclassify"])
def test_remaining_campaign_deletes_preflight_before_effects(case, filename, operation):
    cid, root, wroot = case["cid"], case["campaign"], case["world"]
    if operation == "character":
        aid = characters.create_character(root, "Mara")
    elif operation == "pc":
        aid = pcs.create_pc(root, "Winifred", [])
    elif operation == "greeting":
        aid = greetings.create_greeting(wroot, "Saltmarch", "mara", "default")
    elif operation == "image":
        image_dir = root / "assets" / "characters" / "mara" / "default"
        image_dir.mkdir(parents=True)
        (image_dir / "avatar.png").write_bytes(b"existing image bytes")
    else:
        aid = entities.create_entity(wroot, "groups", "Salt Circle")
    (root / filename).write_bytes(b"{")
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(group_refs.GroupIdentityConflict):
        if operation in ("character", "pc"):
            overlay.delete_actor(cid, "characters" if operation == "character" else "pcs", aid)
        elif operation == "greeting":
            overlay.delete_greeting(cid, aid)
        elif operation == "image":
            overlay.delete_image(cid, "mara", "default", "avatar")
        else:
            overlay.reclassify_entity(cid, "groups", aid, "lore")
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("operation", ["group", "character", "pc", "greeting", "image", "demote"])
def test_world_delete_refuses_corrupt_second_dependent_before_any_effect(case, operation):
    wid, wroot = case["wid"], case["world"]
    if operation in ("group", "demote"):
        eid = entities.create_entity(wroot, "groups", "Salt Circle")
    elif operation == "character":
        eid = characters.create_character(wroot, "Mara")
    elif operation == "pc":
        eid = pcs.create_pc(wroot, "Winifred", [])
    elif operation == "greeting":
        eid = greetings.create_greeting(wroot, "Saltmarch", "mara", "default")
    else:
        d = world_images.images_dir(wid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "mara.png").write_bytes(b"existing image bytes")
        (d / "descriptions.json").write_text('{"mara": "Mara at Saltmarch"}', encoding="utf-8")
        eid = "mara"
    second = campaigns.create_campaign("Saltmarch", wid)
    (campaigns.campaign_root(second) / "detached.json").write_bytes(b"{")
    roots = (wroot, case["campaign"], campaigns.campaign_root(second))
    before = [{p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} for root in roots]
    with pytest.raises(group_refs.GroupIdentityConflict):
        if operation == "group":
            entity_routes.delete_world_entity(wid, "groups", eid)
        elif operation == "character":
            character_routes.delete_world_character(wid, eid)
        elif operation == "pc":
            world_routes.delete_world_pc(wid, eid)
        elif operation == "greeting":
            greeting_routes.delete_world_greeting(wid, eid)
        elif operation == "image":
            world_images.delete_image(wid, eid)
        else:
            sync.demote(wid, "groups", eid)
    assert [{p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} for root in roots] == before


@pytest.mark.parametrize("filename", ["detached.json", "deleted.json"])
@pytest.mark.parametrize("operation", ["accept", "reject", "push", "promote", "migrate"])
def test_sync_and_migration_preflight_before_publishing_or_pruning(case, filename, operation):
    cid, root, wroot = case["cid"], case["campaign"], case["world"]
    if operation == "promote":
        eid = entities.create_entity(root, "groups", "Salt Circle")
    else:
        eid = entities.create_entity(wroot, "groups", "Salt Circle", body="Mara keeps the roster.")
        overlay.materialize_entity(cid, "groups", eid)
        entities.update_entity(wroot, "groups", eid, body="Winifred keeps the roster.")
    if operation == "migrate":
        path = root / "campaign.md"
        path.write_text(path.read_text(encoding="utf-8").replace("world_copy: overlay", "world_copy: full"), encoding="utf-8")
    (root / filename).write_bytes(b"{")
    roots = (root, wroot)
    before = [{p.relative_to(r): p.read_bytes() for p in r.rglob("*") if p.is_file()} for r in roots]
    with pytest.raises(group_refs.GroupIdentityConflict):
        if operation in ("accept", "reject"):
            getattr(sync, operation)(cid, [{"kind": "groups", "id": eid}])
        elif operation == "push":
            sync.push(cid, "groups", eid, force=True)
        elif operation == "promote":
            sync.promote(cid, "groups", eid)
        else:
            campaigns.ensure_campaign_slim(cid)
    assert [{p.relative_to(r): p.read_bytes() for p in r.rglob("*") if p.is_file()} for r in roots] == before



def test_demote_preflights_campaigns_that_already_hide_the_record(case):
    eid = entities.create_entity(case["world"], "groups", "Salt Circle")
    second = campaigns.create_campaign("Saltmarch", case["wid"])
    root = campaigns.campaign_root(second)
    overlay.add_deleted(second, f"groups/{eid}")
    (root / "detached.json").write_bytes(b"{")
    path = case["world"] / "groups" / f"{eid}.md"
    before = path.read_bytes()
    with pytest.raises(group_refs.GroupIdentityConflict):
        sync.demote(case["wid"], "groups", eid)
    assert path.read_bytes() == before
    assert not (case["campaign"] / "groups" / f"{eid}.md").exists()


def test_group_delete_and_library_restore_preserve_both_markers(case, monkeypatch):
    cid, root = case["cid"], case["campaign"]
    eid = entities.create_entity(case["world"], "groups", "Salt Circle")
    overlay.add_deleted(cid, "assets/library/mara")
    overlay.add_deleted(cid, "pcs/winifred")
    overlay.add_detached(cid, "characters/mara")
    paused, release, restore_started, restore_wrote = (threading.Event() for _ in range(4))
    original = atomic.write_text
    errors = []

    def observed(path, text, *args, **kwargs):
        if path == root / "deleted.json":
            if threading.current_thread().name == "group-delete":
                paused.set()
                assert release.wait(5), "delete was never released"
            else:
                restore_wrote.set()
        return original(path, text, *args, **kwargs)

    def delete():
        try:
            overlay.delete_entity(cid, "groups", eid)
        except BaseException as exc:  # noqa: BLE001 - propagate worker failures to the test thread
            errors.append(exc)

    def restore():
        restore_started.set()
        try:
            campaign_images.restore_image(cid, "mara")
        except BaseException as exc:  # noqa: BLE001 - propagate worker failures to the test thread
            errors.append(exc)

    monkeypatch.setattr(atomic, "write_text", observed)
    deleting = threading.Thread(target=delete, name="group-delete")
    restoring = threading.Thread(target=restore, name="library-restore")
    deleting.start()
    try:
        assert paused.wait(5), "delete never reached its ledger write"
        restoring.start()
        assert restore_started.wait(5)
        assert not restore_wrote.wait(0.1), "Restore passed the held campaign lock"
    finally:
        release.set()
        deleting.join(5)
        if restoring.ident is not None:
            restoring.join(5)
    assert not deleting.is_alive() and not restoring.is_alive()
    assert not errors
    assert overlay.deleted_strict(cid) == {f"groups/{eid}", "pcs/winifred"}
    assert overlay.detached_strict(cid) == {"characters/mara"}


def test_world_identity_holder_acquires_campaigns_in_sorted_order(case, monkeypatch):
    second = campaigns.create_campaign("Saltmarch", case["wid"])
    cids = [second, case["cid"]]
    requested = []
    original = locks.campaign_lock

    def observed(cid):
        requested.append(cid)
        return original(cid)

    monkeypatch.setattr(overlay, "dependent_campaigns", lambda root: cids)
    monkeypatch.setattr(locks, "campaign_lock", observed)
    with overlay.world_identity_change(case["world"]) as held:
        assert set(held) == set(cids)
    assert requested == sorted(cids)


@pytest.mark.parametrize("filename", ["detached.json", "deleted.json"])
def test_post_world_delete_cleanup_preflights_before_campaign_state_removal(case, filename):
    # The public sweep is also used after a deletion has already landed. It
    # must leave this campaign's state alone when its identity cannot be read.
    d = case["campaign"] / "groups" / "salt-circle"
    d.mkdir(parents=True)
    path = d / "state.md"
    path.write_text("Mara keeps the roster.", encoding="utf-8")
    (case["campaign"] / filename).write_bytes(b"{")
    before = {p.relative_to(case["campaign"]): p.read_bytes() for p in case["campaign"].rglob("*") if p.is_file()}
    with pytest.raises(group_refs.GroupIdentityConflict):
        overlay.forget_world_record(case["world"], "groups", "salt-circle")
    assert {p.relative_to(case["campaign"]): p.read_bytes() for p in case["campaign"].rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("campaign_actor", [False, True])
@pytest.mark.parametrize("identity", ["missing", "detached"])
@pytest.mark.parametrize("operation", ["body", "keys", "materialize", "reclassify", "demote"])
def test_unrelated_save_cannot_bind_present_or_future_campaign_actor(case, campaign_actor, identity, operation):
    cid, root, wroot = case["cid"], case["campaign"], case["world"]
    if identity == "detached":
        characters.create_character(wroot, "Mara")
        overlay.add_detached(cid, "characters/mara")
    if campaign_actor:
        characters.create_character(root, "Mara")
    eid = entities.create_entity(wroot, "groups", "Salt Circle", fields={"members": "characters:mara"})
    original = (wroot / "groups" / f"{eid}.md").read_bytes()
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(group_refs.GroupIdentityConflict) as refused:
        if operation == "body":
            overlay.update_entity(cid, "groups", eid, body="Winifred keeps the roster.")
        elif operation == "keys":
            overlay.update_entity(cid, "groups", eid, keys="Saltmarch")
        elif operation == "materialize":
            overlay.materialize_entity(cid, "groups", eid)
        elif operation == "reclassify":
            reclassify.campaign_entity(cid, "groups", eid, "lore")
        else:
            sync.demote(case["wid"], "groups", eid)
    assert ("members", "characters:mara") in refused.value.slots
    assert not (root / "groups" / f"{eid}.md").exists()
    assert (wroot / "groups" / f"{eid}.md").read_bytes() == original
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_exact_slot_consent_does_not_review_other_member_or_leader(case):
    cid, root, wroot = case["cid"], case["campaign"], case["world"]
    fields = {"members": "characters:mara, pcs:winifred", "leader": "characters:mara"}
    eid = entities.create_entity(wroot, "groups", "Salt Circle", fields=fields)
    original = (wroot / "groups" / f"{eid}.md").read_text(encoding="utf-8")
    with pytest.raises(group_refs.GroupIdentityConflict) as refused:
        overlay.update_entity(cid, "groups", eid, fields=fields,
            reviewed_slots=frozenset({("members", "characters:mara")}))
    assert set(refused.value.slots) == {("members", "pcs:winifred"), ("leader", "characters:mara")}
    assert not (root / "groups" / f"{eid}.md").exists()
    overlay.update_entity(cid, "groups", eid, fields={"members": "characters:mara", "leader": ""},
        reviewed_slots=frozenset({("members", "characters:mara")}))
    assert overlay.read_entity(cid, "groups", eid)["meta"]["members"] == "characters:mara"
    assert campaigns.read_manifest(cid)[f"groups/{eid}"] == entities.content_hash(original)


@pytest.mark.parametrize("fail_publication", [False, True])
def test_clear_collision_publishes_only_final_bytes(case, monkeypatch, fail_publication):
    cid, root, wroot = case["cid"], case["campaign"], case["world"]
    eid = entities.create_entity(wroot, "groups", "Salt Circle", fields={"members": "characters:mara", "leader": "characters:mara"})
    source = wroot / "groups" / f"{eid}.md"
    original_text = source.read_text(encoding="utf-8")
    dst = root / "groups" / f"{eid}.md"
    publications = []
    original = atomic.write_text
    before_manifest = campaigns.read_manifest(cid)

    def observed(path, text, *args, **kwargs):
        if path == dst:
            assert "characters:mara" not in text, "unsafe intermediate copy was attempted"
            publications.append(text)
            if fail_publication:
                raise OSError("before final copy publication")
        return original(path, text, *args, **kwargs)

    monkeypatch.setattr(atomic, "write_text", observed)
    if fail_publication:
        with pytest.raises(OSError, match="before final copy"):
            overlay.update_entity(cid, "groups", eid, fields={"members": "", "leader": ""})
        assert not dst.exists()
        assert campaigns.read_manifest(cid) == before_manifest
    else:
        overlay.update_entity(cid, "groups", eid, fields={"members": "", "leader": ""})
        assert dst.read_text(encoding="utf-8") == publications[0]
        assert campaigns.read_manifest(cid)[f"groups/{eid}"] == entities.content_hash(original_text)
    assert len(publications) == 1
    assert source.read_text(encoding="utf-8") == original_text


@pytest.mark.parametrize("field", ["members", "leader"])
@pytest.mark.parametrize("kind", ["groups", "lore", "items"])
def test_dormant_group_refs_cannot_bypass_copy_guard(case, kind, field):
    eid = entities.create_entity(case["world"], kind, "Salt Circle", fields={field: "characters:mara"})
    with pytest.raises(group_refs.GroupIdentityConflict) as refused:
        overlay.materialize_entity(case["cid"], kind, eid)
    assert (field, "characters:mara") in refused.value.slots
    assert not (case["campaign"] / kind / f"{eid}.md").exists()


def test_legacy_extra_leader_slot_is_also_guarded(case):
    characters.create_character(case["world"], "Seraphine")
    eid = entities.create_entity(case["world"], "groups", "Salt Circle",
        fields={"leader": "characters:seraphine, characters:mara"})
    with pytest.raises(group_refs.GroupIdentityConflict) as refused:
        overlay.materialize_entity(case["cid"], "groups", eid)
    assert refused.value.slots == (("leader", "characters:mara"),)


def test_one_group_context_and_rev_share_source_bytes(case, monkeypatch):
    cid, wroot = case["cid"], case["world"]
    characters.create_character(wroot, "Seraphine")
    eid = entities.create_entity(wroot, "groups", "Salt Circle", fields={
        "members": "characters:mara, characters:seraphine, pcs:winifred", "leader": "characters:mara"})
    overlay.add_detached(cid, "characters/mara")
    path = wroot / "groups" / f"{eid}.md"
    original = Path.read_text
    first = original(path, encoding="utf-8")
    reads = []

    def changed_after_read(self, *args, **kwargs):
        text = original(self, *args, **kwargs)
        if self == path:
            reads.append(text)
            self.write_text(text.replace("characters:seraphine", "pcs:winifred"), encoding="utf-8")
        return text

    monkeypatch.setattr(Path, "read_text", changed_after_read)
    reader = getattr(overlay, "read_group_membership", None)
    assert callable(reader), "same-bytes group reader is missing"
    record, context = reader(cid, eid)
    assert reads == [first]
    assert record["rev"] == entities.content_hash(first)
    assert record["meta"]["members"] == "characters:mara, characters:seraphine, pcs:winifred"
    assert context.layer == "world"
    assert context.members == ("characters:seraphine", "pcs:winifred")
    assert context.leader is None
    assert context.filtered_members == ("characters:mara",)
    assert context.filtered_leader == "characters:mara"
    assert context.unresolved_members == ("characters:mara", "pcs:winifred")
    assert context.unresolved_leader == "characters:mara"


def test_owned_group_refs_remain_authored_and_diagnostics_empty(case):
    eid = entities.create_entity(case["campaign"], "groups", "Salt Circle", fields={
        "members": "characters:mara, legacy text", "leader": "pcs:winifred"})
    (case["campaign"] / "detached.json").write_bytes(b"{")
    reader = getattr(overlay, "read_group_membership", None)
    assert callable(reader), "one-group context reader is missing"
    record, context = reader(case["cid"], eid)
    assert context.layer == "campaign"
    assert context.members == ("characters:mara",)
    assert context.leader == "pcs:winifred"
    assert (context.filtered_members, context.filtered_leader,
            context.unresolved_members, context.unresolved_leader) == ((), None, (), None)
    overlay.update_entity(case["cid"], "groups", eid, body="Winifred keeps the roster.")
    assert overlay.read_entity(case["cid"], "groups", eid)["meta"]["members"] == record["meta"]["members"]


@pytest.mark.parametrize("field", ["members", "leader"])
@pytest.mark.parametrize("kind", ["groups", "lore"])
@pytest.mark.parametrize("operation", ["promote", "push", "force"])
@pytest.mark.parametrize("identity", ["missing", "detached"])
def test_publication_requires_world_actor_even_when_forced(case, field, kind, operation, identity):
    cid, root, wroot = case["cid"], case["campaign"], case["world"]
    if identity == "detached":
        characters.create_character(wroot, "Mara")
        overlay.add_detached(cid, "characters/mara")
    eid = entities.create_entity(root, kind, "Salt Circle", fields={field: "characters:mara"})
    if operation != "promote":
        entities.create_entity(wroot, kind, "Salt Circle", body="Mara keeps the roster.")
        campaigns_paths.write_manifest(cid, {f"{kind}/{eid}": "older common version"})
    roots = (root, wroot)
    before = [{p.relative_to(r): p.read_bytes() for p in r.rglob("*") if p.is_file()} for r in roots]
    with pytest.raises(sync.DanglingReferenceError):
        if operation == "promote":
            sync.promote(cid, kind, eid)
        else:
            sync.push(cid, kind, eid, force=operation == "force")
    assert [{p.relative_to(r): p.read_bytes() for p in r.rglob("*") if p.is_file()} for r in roots] == before


@pytest.mark.parametrize("filename", ["detached.json", "deleted.json"])
def test_asset_copy_preflights_identity_before_any_bytes(case, filename):
    d = case["world"] / "groups" / "salt-circle"
    d.mkdir(parents=True)
    (d / "state.md").write_text("Mara keeps the roster.", encoding="utf-8")
    (case["campaign"] / filename).write_bytes(b"{")
    before = {p.relative_to(case["campaign"]): p.read_bytes() for p in case["campaign"].rglob("*") if p.is_file()}
    with pytest.raises(group_refs.GroupIdentityConflict):
        overlay.copy_record_dir_down(case["cid"], "groups", "salt-circle")
    assert {p.relative_to(case["campaign"]): p.read_bytes() for p in case["campaign"].rglob("*") if p.is_file()} == before


def test_demote_validates_every_first_copy_before_assets_or_text(case):
    characters.create_character(case["world"], "Mara")
    eid = entities.create_entity(case["world"], "groups", "Salt Circle", fields={"members": "characters:mara"})
    d = case["world"] / "groups" / eid
    d.mkdir(parents=True)
    (d / "state.md").write_text("Mara keeps the roster.", encoding="utf-8")
    second = campaigns.create_campaign("Saltmarch", case["wid"])
    overlay.add_detached(second, "characters/mara")
    roots = (case["world"], case["campaign"], campaigns.campaign_root(second))
    before = [{p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} for root in roots]
    with pytest.raises(group_refs.GroupIdentityConflict):
        sync.demote(case["wid"], "groups", eid)
    assert [{p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} for root in roots] == before


def test_demote_copy_and_preflight_use_one_frozen_source(case, monkeypatch):
    characters.create_character(case["world"], "Seraphine")
    eid = entities.create_entity(case["world"], "groups", "Salt Circle", fields={"members": "characters:seraphine"})
    second = campaigns.create_campaign("Saltmarch", case["wid"])
    path = case["world"] / "groups" / f"{eid}.md"
    first = path.read_text(encoding="utf-8")
    original = overlay.require_group_materialization
    reviewed = []

    def changed_after_review(cid, kind, eid, *, source_text=None):
        original(cid, kind, eid, source_text=source_text)
        reviewed.append(source_text)
        if len(reviewed) == 1:
            path.write_text(first.replace("characters:seraphine", "characters:mara"), encoding="utf-8")

    monkeypatch.setattr(overlay, "require_group_materialization", changed_after_review)
    sync.demote(case["wid"], "groups", eid)
    assert reviewed == [first, first]
    for cid in (case["cid"], second):
        assert overlay.record_text(cid, "groups", eid) == first
    assert not path.exists()


def test_new_authored_member_and_valid_world_publication_are_allowed(case):
    characters.create_character(case["world"], "Seraphine")
    pcs.create_pc(case["world"], "Winifred", [])
    eid = entities.create_entity(case["world"], "groups", "Salt Circle", fields={"members": "characters:seraphine"})
    overlay.update_entity(case["cid"], "groups", eid,
        fields={"members": "characters:seraphine, pcs:winifred", "leader": "pcs:winifred"})
    sync.push(case["cid"], "groups", eid)
    assert entities.read_entity(case["world"], "groups", eid)["meta"]["members"] == "characters:seraphine, pcs:winifred"
    # An explicitly new token is authored even before its actor exists; it is
    # not implicit consent for an unrelated retained foreign token.
    other = entities.create_entity(case["world"], "groups", "Salt Circle")
    overlay.update_entity(case["cid"], "groups", other, fields={"members": "characters:mara"})
    assert overlay.read_entity(case["cid"], "groups", other)["meta"]["members"] == "characters:mara"


def test_actor_birth_and_first_copy_share_campaign_hold(case, monkeypatch):
    cid, root, wroot = case["cid"], case["campaign"], case["world"]
    characters.create_character(wroot, "Mara")
    eid = entities.create_entity(wroot, "groups", "Salt Circle", fields={"members": "characters:mara"})
    held, release, started, birth_wrote = (threading.Event() for _ in range(4))
    original = atomic.write_text
    errors, calls = [], []

    def observed(path, text, *args, **kwargs):
        if threading.current_thread().name == "first-copy" and path == root / "groups" / f"{eid}.md":
            held.set()
            assert release.wait(5)
        elif threading.current_thread().name == "actor-birth" and path.name == "character.md":
            birth_wrote.set()
        return original(path, text, *args, **kwargs)

    def copy():
        try:
            overlay.materialize_entity(cid, "groups", eid)
            calls.append("copy")
        except BaseException as exc:  # noqa: BLE001 - propagate worker failures to the test thread
            errors.append(exc)

    def birth():
        started.set()
        try:
            overlay.create_character(cid, "Mara")
            calls.append("birth")
        except BaseException as exc:  # noqa: BLE001 - propagate worker failures to the test thread
            errors.append(exc)

    monkeypatch.setattr(atomic, "write_text", observed)
    copying = threading.Thread(target=copy, name="first-copy")
    birthing = threading.Thread(target=birth, name="actor-birth")
    copying.start()
    try:
        assert held.wait(5)
        birthing.start()
        assert started.wait(5)
        assert not birth_wrote.wait(0.1), "birth published while copy held the campaign"
    finally:
        release.set()
        copying.join(5)
        if birthing.ident is not None:
            birthing.join(5)
    assert not copying.is_alive() and not birthing.is_alive()
    assert not errors
    assert calls == ["copy", "birth"]


def test_first_copy_after_actor_birth_rechecks_detached_identity(case, monkeypatch):
    cid, root, wroot = case["cid"], case["campaign"], case["world"]
    eid = entities.create_entity(wroot, "groups", "Salt Circle", fields={"members": "characters:mara"})
    held, release, started, copy_checked = (threading.Event() for _ in range(4))
    original_write, original_guard = atomic.write_text, overlay._require_group_copy
    errors, calls = [], []

    def observed(path, text, *args, **kwargs):
        if threading.current_thread().name == "actor-birth" and path.name == "character.md":
            held.set()
            assert release.wait(5)
        return original_write(path, text, *args, **kwargs)

    def checked(*args, **kwargs):
        copy_checked.set()
        return original_guard(*args, **kwargs)

    def birth():
        try:
            overlay.create_character(cid, "Mara")
            calls.append("birth")
        except BaseException as exc:  # noqa: BLE001 - propagate worker failures to the test thread
            errors.append(exc)

    def copy():
        started.set()
        try:
            overlay.materialize_entity(cid, "groups", eid)
            calls.append("copy")
        except BaseException as exc:  # noqa: BLE001 - propagate worker failures to the test thread
            errors.append(exc)

    monkeypatch.setattr(atomic, "write_text", observed)
    monkeypatch.setattr(overlay, "_require_group_copy", checked)
    birthing = threading.Thread(target=birth, name="actor-birth")
    copying = threading.Thread(target=copy, name="first-copy")
    birthing.start()
    try:
        assert held.wait(5)
        copying.start()
        assert started.wait(5)
        assert not copy_checked.wait(0.1), "copy validated while birth held the campaign"
    finally:
        release.set()
        birthing.join(5)
        if copying.ident is not None:
            copying.join(5)
    assert not copying.is_alive() and not birthing.is_alive()
    assert calls == ["birth"]
    assert len(errors) == 1 and isinstance(errors[0], group_refs.GroupIdentityConflict)
    assert not (root / "groups" / f"{eid}.md").exists()


@pytest.mark.parametrize("field", ["members", "leader"])
@pytest.mark.parametrize("identity_root", ["campaign", "world"])
def test_native_actor_aliases_cannot_bypass_context_or_copy_consent(case, field, identity_root):
    cid, croot, wroot = case["cid"], case["campaign"], case["world"]
    overlay.create_character(cid, "Mara")
    characters.create_character(wroot, "Mara")
    if identity_root == "world":
        characters.delete_character(croot, "mara")   # marker still names its original identity
    root = croot if identity_root == "campaign" else wroot
    alias, canonical = root / "characters" / "MARA", root / "characters" / "mara"
    if not alias.exists() or not alias.samefile(canonical):
        pytest.skip("native actor directory names are case-sensitive")
    actor = "characters:MARA"
    assert not entity_schema.invalid_values("groups", {field: actor})
    eid = entities.create_entity(wroot, "groups", "Salt Circle", fields={field: actor})
    path = wroot / "groups" / f"{eid}.md"
    before = path.read_bytes()
    _, context = overlay.read_group_membership(cid, eid)
    if field == "members":
        assert context.filtered_members == context.unresolved_members == (actor,)
        assert context.members == ()
    else:
        assert context.filtered_leader == context.unresolved_leader == actor
        assert context.leader is None
    with pytest.raises(group_refs.GroupIdentityConflict) as refused:
        overlay.update_entity(cid, "groups", eid, body="Winifred keeps the roster.")
    assert refused.value.slots == ((field, actor),)
    assert overlay.record_text(cid, "groups", eid) is None
    assert path.read_bytes() == before


@pytest.mark.parametrize("field", ["members", "leader"])
@pytest.mark.parametrize("operation", ["promote", "push", "force"])
def test_native_actor_aliases_cannot_bypass_publication(case, field, operation):
    cid, croot, wroot = case["cid"], case["campaign"], case["world"]
    overlay.create_character(cid, "Mara")
    characters.create_character(wroot, "Mara")
    alias = croot / "characters" / "MARA"
    if not alias.exists() or not alias.samefile(croot / "characters" / "mara"):
        pytest.skip("native actor directory names are case-sensitive")
    eid = entities.create_entity(croot, "groups", "Salt Circle", fields={field: "characters:MARA"})
    if operation != "promote":
        entities.create_entity(wroot, "groups", "Salt Circle")
        campaigns_paths.write_manifest(cid, {f"groups/{eid}": "old common version"})
    roots = (croot, wroot)
    before = [{p.relative_to(r): p.read_bytes() for p in r.rglob("*") if p.is_file()} for r in roots]
    with pytest.raises(sync.DanglingReferenceError):
        if operation == "promote":
            sync.promote(cid, "groups", eid)
        else:
            sync.push(cid, "groups", eid, force=operation == "force")
    assert [{p.relative_to(r): p.read_bytes() for p in r.rglob("*") if p.is_file()} for r in roots] == before


def test_alias_matching_defers_to_filesystem_identity_and_preserves_raw_spelling(case, monkeypatch):
    # Simulate distinct case-sensitive entries rather than guess from the OS.
    # The native alias test above independently proves real Windows behavior.
    matcher = getattr(overlay, "actor_detachments", None)
    assert callable(matcher), "native actor detachment matcher is missing"
    monkeypatch.setattr(Path, "samefile", lambda self, other: False)
    severed = {"characters/mara", "groups/salt-circle"}
    assert matcher(case["campaign"], case["world"], ["characters:MARA", "characters:mara"], severed) == severed


@pytest.mark.parametrize("filename", ["detached.json", "deleted.json"])
def test_world_reclassify_preflights_before_world_move(case, filename):
    eid = entities.create_entity(case["world"], "groups", "Salt Circle")
    overlay.materialize_entity(case["cid"], "groups", eid)
    second = campaigns.create_campaign("Saltmarch", case["wid"])
    overlay.materialize_entity(second, "groups", eid)
    (campaigns.campaign_root(second) / filename).write_bytes(b"{")
    roots = (case["world"], case["campaign"], campaigns.campaign_root(second))
    before = [{p.relative_to(r): p.read_bytes() for p in r.rglob("*") if p.is_file()} for r in roots]
    with pytest.raises(group_refs.GroupIdentityConflict):
        reclassify.world_entity(case["wid"], "groups", eid, "lore")
    assert [{p.relative_to(r): p.read_bytes() for p in r.rglob("*") if p.is_file()} for r in roots] == before



def test_demote_rechecks_copy_after_take_world_before_hold(case, monkeypatch):
    cid, wroot = case["cid"], case["world"]
    eid = entities.create_entity(wroot, "groups", "Salt Circle", body="Seraphine keeps the roster.")
    overlay.materialize_entity(cid, "groups", eid)
    entities.update_entity(wroot, "groups", eid, body="Mara keeps the roster.")
    expected = (wroot / "groups" / f"{eid}.md").read_text(encoding="utf-8")
    hold = locks.hold_all
    changed = []

    @contextmanager
    def accept_before_hold(cids, *args, **kwargs):
        if not changed:
            changed.append(True)
            sync.accept(cid, [{"kind": "groups", "id": eid}])
        with hold(cids, *args, **kwargs):
            yield

    monkeypatch.setattr(locks, "hold_all", accept_before_hold)
    result = sync.demote(case["wid"], "groups", eid)
    assert changed
    assert overlay.record_text(cid, "groups", eid) == expected
    assert result["copied_down"] == [cid]
    assert not (wroot / "groups" / f"{eid}.md").exists()


def test_demote_skips_recreated_campaign_bound_to_another_world(case, monkeypatch):
    cid, wroot = case["cid"], case["world"]
    eid = entities.create_entity(wroot, "groups", "Salt Circle", body="Seraphine keeps the roster.")
    other_wid = worlds.create_world("Saltmarch")
    other_root = worlds.world_root(other_wid)
    entities.create_entity(other_root, "groups", "Salt Circle", body="Winifred keeps the roster.")
    hold = locks.hold_all
    recreated = []
    before = {p.relative_to(other_root): p.read_bytes() for p in other_root.rglob("*") if p.is_file()}

    @contextmanager
    def recreate_before_hold(cids, *args, **kwargs):
        if not recreated:
            campaigns.delete_campaign(cid)
            recreated.append(campaigns.create_campaign("Saltmarch", other_wid))
        with hold(cids, *args, **kwargs):
            yield

    monkeypatch.setattr(locks, "hold_all", recreate_before_hold)
    result = sync.demote(case["wid"], "groups", eid)
    assert recreated == [cid]
    assert result["copied_down"] == result["dependents"] == []
    assert overlay.record_text(cid, "groups", eid) is None
    assert campaigns_paths.read_manifest(cid) == {}
    assert overlay.read_entity(cid, "groups", eid)["body"] == "Winifred keeps the roster."
    assert {p.relative_to(other_root): p.read_bytes() for p in other_root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("force", [False, True])
def test_native_group_alias_cannot_push_detached_original_over_new_world_record(case, force):
    cid, croot, wroot = case["cid"], case["campaign"], case["world"]
    eid = overlay.create_entity(cid, "groups", "Salt Circle", body="Mara keeps the roster.")
    entities.create_entity(wroot, "groups", "Salt Circle", body="Winifred keeps the roster.")
    overlay.add_detached(cid, f"groups/{eid}")  # retained original, unrelated World replacement
    assert f"groups/{eid}" in overlay.detached_strict(cid)
    alias = eid.upper()
    path = croot / "groups" / f"{eid}.md"
    if not (croot / "groups" / f"{alias}.md").exists() or not path.samefile(croot / "groups" / f"{alias}.md"):
        pytest.skip("native record names are case-sensitive")
    campaigns_paths.write_manifest(cid, {f"groups/{alias}": entities.entity_hash(wroot, "groups", eid)})
    roots = (croot, wroot)
    before = [{p.relative_to(r): p.read_bytes() for p in r.rglob("*") if p.is_file()} for r in roots]
    with pytest.raises(sync.NotInLibraryError):
        sync.push(cid, "groups", alias, force=force)
    assert [{p.relative_to(r): p.read_bytes() for p in r.rglob("*") if p.is_file()} for r in roots] == before


@pytest.mark.parametrize("operation", ["read", "preflight", "materialize", "update"])
def test_native_group_alias_cannot_resurrect_tombstoned_world_group(case, operation):
    cid, croot, wroot = case["cid"], case["campaign"], case["world"]
    eid = entities.create_entity(wroot, "groups", "Salt Circle")
    alias = eid.upper()
    if not (wroot / "groups" / f"{alias}.md").exists() or not (wroot / "groups" / f"{alias}.md").samefile(wroot / "groups" / f"{eid}.md"):
        pytest.skip("native record names are case-sensitive")
    overlay.add_deleted(cid, f"groups/{eid}")
    before = {p.relative_to(croot): p.read_bytes() for p in croot.rglob("*") if p.is_file()}
    with pytest.raises(entities.EntityNotFound):
        if operation == "read":
            overlay.read_group_membership(cid, alias)
        elif operation == "preflight":
            overlay.require_group_materialization(cid, "groups", alias)
        elif operation == "materialize":
            overlay.materialize_entity(cid, "groups", alias)
        else:
            overlay.update_entity(cid, "groups", alias, body="Mara keeps the roster.")
    assert {p.relative_to(croot): p.read_bytes() for p in croot.rglob("*") if p.is_file()} == before



@pytest.mark.parametrize("operation", ["push", "promote"])
def test_publication_resolves_world_with_current_campaign_inside_hold(case, monkeypatch, operation):
    cid, wroot = case["cid"], case["world"]
    if operation == "push":
        eid = entities.create_entity(wroot, "groups", "Salt Circle", body="Seraphine keeps the roster.")
        overlay.materialize_entity(cid, "groups", eid)
    else:
        eid = overlay.create_entity(cid, "groups", "Salt Circle", body="Seraphine keeps the roster.")
    other_wid = worlds.create_world("Saltmarch")
    other_root = worlds.world_root(other_wid)
    if operation == "push":
        entities.create_entity(other_root, "groups", "Salt Circle", body="Winifred keeps the roster.")
    path = wroot / "groups" / f"{eid}.md"
    before = path.read_bytes() if path.exists() else None
    migrate = campaigns_lifecycle.ensure_campaign_slim
    publication_lock = locks.campaign_lock(cid)
    lock_type = type(publication_lock)
    enter = lock_type.__enter__
    ready, changed = [], []

    def migrated(campaign):
        result = migrate(campaign)
        ready.append(True)
        return result

    def recreate_before_publication_hold(current_lock):
        if current_lock is publication_lock and ready and not changed and not current_lock._is_owned():
            changed.append(True)
            campaigns.delete_campaign(cid)
            assert campaigns.create_campaign("Saltmarch", other_wid) == cid
            if operation == "push":
                overlay.materialize_entity(cid, "groups", eid)
                overlay.update_entity(cid, "groups", eid, body="Mara keeps the roster.")
            else:
                assert overlay.create_entity(cid, "groups", "Salt Circle", body="Mara keeps the roster.") == eid
        return enter(current_lock)

    monkeypatch.setattr(campaigns_lifecycle, "ensure_campaign_slim", migrated)
    monkeypatch.setattr(lock_type, "__enter__", recreate_before_publication_hold)
    if operation == "push":
        sync.push(cid, "groups", eid, force=True)
    else:
        sync.promote(cid, "groups", eid)
    assert changed
    assert (path.read_bytes() if path.exists() else None) == before
    assert entities.read_entity(other_root, "groups", eid)["body"] == "Mara keeps the roster."



@pytest.mark.parametrize("marker_alias", [False, True])
def test_native_detachment_follows_reclassified_original_before_force_push(case, marker_alias):
    cid, croot, wroot = case["cid"], case["campaign"], case["world"]
    eid = entities.create_entity(wroot, "groups", "Salt Circle", body="Seraphine keeps the roster.")
    overlay.update_entity(cid, "groups", eid, body="Mara keeps the roster.")
    path = croot / "groups" / f"{eid}.md"
    if not (croot / "groups" / f"{eid.upper()}.md").exists() or not path.samefile(croot / "groups" / f"{eid.upper()}.md"):
        pytest.skip("native record names are case-sensitive")
    entity_routes.delete_world_entity(case["wid"], "groups", eid.upper() if marker_alias else eid)
    moved = reclassify.campaign_entity(cid, "groups", eid.upper(), "lore")
    replacement = entities.create_entity(wroot, "lore", "Salt Circle", body="Winifred keeps the roster.")
    before = (wroot / "lore" / f"{replacement}.md").read_bytes()
    with pytest.raises(sync.NotInLibraryError):
        sync.push(cid, "lore", moved, force=True)
    assert (wroot / "lore" / f"{replacement}.md").read_bytes() == before
    assert overlay.detached_strict(cid) == {f"lore/{moved}"}


@pytest.mark.parametrize("fail", [False, True])
def test_native_actor_reattachment_clears_or_restores_proven_markers(case, monkeypatch, fail):
    cid, croot, wroot = case["cid"], case["campaign"], case["world"]
    aid, _ = characters.create_character(wroot, "Mara")
    overlay.materialize_actor(cid, "characters", aid)
    if not (croot / "characters" / aid.upper()).exists() or not (croot / "characters" / aid).samefile(croot / "characters" / aid.upper()):
        pytest.skip("native actor names are case-sensitive")
    character_routes.delete_world_character(case["wid"], aid.upper())
    before = overlay.detached_strict(cid)
    assert before and overlay.record_is_marked(cid, "characters", aid, before)
    if fail:
        write = atomic.write_text
        def refuse_commit(path, text, *args, **kwargs):
            if path == wroot / "characters" / aid / "character.md":
                raise OSError("Mara publication interrupted")
            return write(path, text, *args, **kwargs)
        monkeypatch.setattr(atomic, "write_text", refuse_commit)
        with pytest.raises(OSError, match="publication interrupted"):
            sync.promote(cid, "characters", aid)
        assert overlay.detached_strict(cid) == before
    else:
        sync.promote(cid, "characters", aid)
        assert not overlay.record_is_marked(cid, "characters", aid, overlay.detached_strict(cid))
        eid = entities.create_entity(croot, "groups", "Salt Circle", fields={"members": f"characters:{aid}"})
        sync.promote(cid, "groups", eid)
        assert entities.read_entity(wroot, "groups", eid)["meta"]["members"] == f"characters:{aid}"


@pytest.mark.parametrize("operation", ["world_reclassify", "campaign_delete"])
def test_native_entity_lifecycle_carries_canonical_base_and_asset_slots(case, operation):
    cid, croot, wroot = case["cid"], case["campaign"], case["world"]
    eid = entities.create_entity(wroot, "groups", "Salt Circle", body="Seraphine keeps the roster.")
    overlay.materialize_entity(cid, "groups", eid)
    if not (croot / "groups" / f"{eid.upper()}.md").exists() or not (croot / "groups" / f"{eid}.md").samefile(croot / "groups" / f"{eid.upper()}.md"):
        pytest.skip("native record names are case-sensitive")
    overlay.add_deleted(cid, f"assets/groups/{eid}/default/map")
    if operation == "world_reclassify":
        moved = reclassify.world_entity(case["wid"], "groups", eid.upper(), "lore")["id"]
        assert f"groups/{eid}" not in campaigns_paths.read_manifest(cid)
        assert f"lore/{moved}" in campaigns_paths.read_manifest(cid)
        assert overlay.deleted_strict(cid) == {f"assets/lore/{moved}/default/map"}
        entities.update_entity(wroot, "lore", moved, body="Mara keeps the roster.")
        assert [item["ref"] for item in sync.incoming(cid)] == [{"kind": "lore", "id": moved}]
    else:
        overlay.delete_entity(cid, "groups", eid.upper())
        assert f"groups/{eid}" not in campaigns_paths.read_manifest(cid)
        assert not overlay.list_entities(cid, "groups")
        with pytest.raises(entities.EntityNotFound):
            overlay.read_group_membership(cid, eid)



def test_record_reclassification_keeps_distinct_case_sensitive_identity(case):
    cid, root = case["cid"], case["campaign"]
    (root / "groups").mkdir(exist_ok=True)
    _enable_case_sensitive(root / "groups")
    eid = overlay.create_entity(cid, "groups", "Salt Circle", body="Mara keeps the roster.")
    lower = root / "groups" / f"{eid}.md"
    upper = root / "groups" / f"{eid.upper()}.md"
    overlay.add_detached(cid, f"groups/{eid}")
    if upper.exists() and upper.samefile(lower):
        pytest.skip("native record names are case-insensitive")
    upper.write_text(lower.read_text(encoding="utf-8").replace("Mara", "Winifred"), encoding="utf-8")
    overlay.add_detached(cid, f"groups/{eid.upper()}")
    before = upper.read_bytes()
    moved = reclassify.campaign_entity(cid, "groups", eid, "lore")
    assert upper.read_bytes() == before
    assert overlay.detached_strict(cid) == {f"groups/{eid.upper()}", f"lore/{moved}"}



def test_native_actor_delete_clears_original_markers_before_world_reuse(case):
    cid, croot, wroot = case["cid"], case["campaign"], case["world"]
    aid, _ = overlay.create_character(cid, "Mara")
    if not (croot / "characters" / aid.upper()).exists() or not (croot / "characters" / aid).samefile(croot / "characters" / aid.upper()):
        pytest.skip("native actor names are case-sensitive")
    overlay.add_deleted(cid, f"assets/characters/{aid}/default/map")
    overlay.delete_actor(cid, "characters", aid.upper())
    assert overlay.detached_strict(cid) == set()
    assert overlay.deleted_strict(cid) == set()
    assert characters.create_character(wroot, "Mara")[0] == aid
    assert overlay.read_character(cid, aid)["meta"]["name"] == "Mara"



def _enable_case_sensitive(directory):
    # Only a disposable fixture directory changes policy, never the checkout
    # or library. On Windows this exercises actual different native identities.
    if os.name == "nt":
        result = subprocess.run(["fsutil", "file", "setCaseSensitiveInfo", str(directory), "enable"],
                                capture_output=True, timeout=10, check=False)
        if result.returncode:
            pytest.skip("native case-sensitive fixture directory is unavailable")


def test_native_marker_capture_obeys_selected_source_namespace(case):
    cid, root, world = case["cid"], case["campaign"], case["world"]
    eid = entities.create_entity(world, "groups", "Salt Circle", body="Seraphine keeps the roster.")
    lower_world = world / "groups" / f"{eid}.md"
    upper_world = world / "groups" / f"{eid.upper()}.md"
    if not upper_world.exists() or not lower_world.samefile(upper_world):
        pytest.skip("mixed native policies require a case-insensitive World root")
    (root / "groups").mkdir(exist_ok=True)
    _enable_case_sensitive(root / "groups")
    overlay.materialize_entity(cid, "groups", eid)
    lower = root / "groups" / f"{eid}.md"
    upper = root / "groups" / f"{eid.upper()}.md"
    if upper.exists():
        pytest.skip("native case-sensitive fixture directory is unavailable")
    upper.write_text(lower.read_text(encoding="utf-8").replace("Seraphine", "Winifred"), encoding="utf-8")
    assert not upper.samefile(lower)
    overlay.add_detached(cid, f"groups/{eid.upper()}")
    before = upper.read_bytes()
    reclassify.campaign_entity(cid, "groups", eid, "lore")
    assert upper.read_bytes() == before
    assert f"groups/{eid.upper()}" in overlay.detached_strict(cid)
    world_before = lower_world.read_bytes()
    with pytest.raises(sync.NotInLibraryError):
        sync.push(cid, "groups", eid.upper(), force=True)
    assert lower_world.read_bytes() == world_before



@pytest.mark.parametrize("kind,bad", [("greetings", "../groups/salt-circle"),
    ("greetings", "..\\groups\\salt-circle"), ("characters", "../characters/mara"),
    ("characters", "..\\characters\\mara")])
def test_delete_refuses_unsafe_id_before_native_resolution(case, kind, bad):
    cid, root = case["cid"], case["campaign"]
    entities.create_entity(root, "groups", "Salt Circle")
    overlay.create_greeting(cid, "Salt Circle", "", "")
    overlay.create_character(cid, "Mara")
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    refusal = greetings.GreetingNotFound if kind == "greetings" else characters.CharacterNotFound
    with pytest.raises(refusal):
        if kind == "greetings":
            overlay.delete_greeting(cid, bad)
        else:
            overlay.delete_actor(cid, kind, bad)
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("operation", ["delete", "reclassify"])
def test_inherited_alias_mutation_does_not_reselect_an_owned_original(case, operation):
    cid, root, world = case["cid"], case["campaign"], case["world"]
    eid = entities.create_entity(world, "groups", "Salt Circle", body="Seraphine keeps the roster.")
    world_alias = world / "groups" / f"{eid.upper()}.md"
    if not world_alias.exists():
        pytest.skip("mixed native policies require a case-insensitive World root")
    (root / "groups").mkdir(exist_ok=True)
    _enable_case_sensitive(root / "groups")
    overlay.update_entity(cid, "groups", eid, body="Mara keeps the roster.")
    lower = root / "groups" / f"{eid}.md"
    assert not (root / "groups" / f"{eid.upper()}.md").exists()
    before = lower.read_bytes()
    if operation == "delete":
        overlay.delete_entity(cid, "groups", eid.upper())
    else:
        moved = reclassify.campaign_entity(cid, "groups", eid.upper(), "lore")
        assert entities.read_entity(root, "lore", moved)["body"] == "Seraphine keeps the roster."
    assert lower.read_bytes() == before


@pytest.mark.parametrize("lock_alias", [False, True])
def test_native_actor_promotion_keeps_version_locked_sync_flow(case, lock_alias):
    cid, root, world = case["cid"], case["campaign"], case["world"]
    aid, vid = characters.create_character(world, "Mara")
    if lock_alias:
        overlay.materialize_actor(cid, "characters", aid)
        if not (root / "characters" / aid.upper()).exists():
            pytest.skip("native actor names are case-sensitive")   # MARA cannot be locked
    locked_id = aid.upper() if lock_alias else aid
    appearances.pick_version(cid, "characters", locked_id, vid)
    if not (root / "characters" / aid.upper()).exists():
        pytest.skip("native actor names are case-sensitive")
    character_routes.delete_world_character(case["wid"], aid)
    meta = root / "characters" / aid / "character.md"
    before = meta.read_bytes()
    if lock_alias:
        # A different saved spelling is a repairable identity boundary, not
        # permission to change historical role keys or dematerialize the actor.
        roots = (root, world)
        files = [{p.relative_to(r): p.read_bytes() for p in r.rglob("*") if p.is_file()} for r in roots]
        with pytest.raises(group_refs.GroupIdentityConflict, match=r"version lock.*alias"):
            sync.promote(cid, "characters", aid.upper())
        assert [{p.relative_to(r): p.read_bytes() for p in r.rglob("*") if p.is_file()} for r in roots] == files
        return
    sync.promote(cid, "characters", aid.upper())
    assert f"characters/{aid.upper()}" not in campaigns_paths.read_manifest(cid)
    card = characters.read_card(world, aid, vid)
    card["data"]["description"] = "Mara keeps the roster."
    characters.update_version(world, aid, vid, card)
    incoming = sync.incoming(cid)
    assert len(incoming) == 1 and incoming[0]["ref"] == {"kind": "characters", "id": locked_id}
    assert incoming[0]["world"]["version"] == vid
    sync.accept(cid, [incoming[0]["ref"]])
    assert meta.read_bytes() == before
    assert appearances.locked_version(cid, "characters", locked_id) == vid
    assert overlay.read_character(cid, aid)["versions"][0]["card"]["data"]["description"] == "Mara keeps the roster."



def test_inverse_native_policy_preserves_world_tombstone_identity(case):
    cid, root, world = case["cid"], case["campaign"], case["world"]
    (world / "groups").mkdir(exist_ok=True)
    _enable_case_sensitive(world / "groups")
    eid = entities.create_entity(world, "groups", "Salt Circle", body="Seraphine keeps the roster.")
    lower = world / "groups" / f"{eid}.md"
    upper = world / "groups" / f"{eid.upper()}.md"
    if upper.exists():
        pytest.skip("native case-sensitive World directory is unavailable")
    upper.write_text(lower.read_text(encoding="utf-8").replace("Seraphine", "Winifred"), encoding="utf-8")
    overlay.delete_entity(cid, "groups", eid.upper())
    overlay.materialize_entity(cid, "groups", eid)
    owned_lower = root / "groups" / f"{eid}.md"
    owned_upper = root / "groups" / f"{eid.upper()}.md"
    if not owned_upper.exists() or not owned_lower.samefile(owned_upper):
        pytest.skip("inverse policies require a case-insensitive campaign directory")
    upper_before = upper.read_bytes()
    reclassify.campaign_entity(cid, "groups", eid, "lore")
    assert upper.read_bytes() == upper_before
    assert overlay.deleted_strict(cid) == {f"groups/{eid}", f"groups/{eid.upper()}"}
    assert not overlay.list_entities(cid, "groups")


@pytest.mark.parametrize("field", ["members", "leader"])
@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("case_sensitive_side", ["world", "campaign"])
def test_publication_preserves_actor_across_native_namespaces(case, field, force, case_sensitive_side):
    cid, root, world = case["cid"], case["campaign"], case["world"]
    sensitive = world if case_sensitive_side == "world" else root
    (sensitive / "characters").mkdir(exist_ok=True)
    _enable_case_sensitive(sensitive / "characters")
    aid, _ = characters.create_character(world, "Mara")
    overlay.materialize_actor(cid, "characters", aid)
    lower, upper = sensitive / "characters" / aid, sensitive / "characters" / aid.upper()
    if upper.exists():
        pytest.skip("native case-sensitive source directory is unavailable")
    shutil.copytree(lower, upper)
    meta = upper / "character.md"
    meta.write_text(meta.read_text(encoding="utf-8").replace("Mara", "Winifred"), encoding="utf-8")
    insensitive = root if case_sensitive_side == "world" else world
    if not (insensitive / "characters" / aid.upper()).exists() or not (insensitive / "characters" / aid).samefile(insensitive / "characters" / aid.upper()):
        pytest.skip("mixed policies require a case-insensitive counterpart")
    assert not lower.samefile(upper)
    assert overlay.detached_strict(cid) == set()
    assert overlay.read_character(cid, aid.upper())["meta"]["name"] != characters.read_character(world, aid.upper())["meta"]["name"]
    eid = entities.create_entity(world, "groups", "Salt Circle")
    overlay.update_entity(cid, "groups", eid, fields={field: f"characters:{aid.upper()}"})
    before = {p.relative_to(world): p.read_bytes() for p in world.rglob("*") if p.is_file()}
    with pytest.raises(sync.DanglingReferenceError):
        sync.push(cid, "groups", eid, force=force)
    assert {p.relative_to(world): p.read_bytes() for p in world.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("operation", ["reclassify", "delete", "demote"])
def test_world_operation_refuses_distinct_owned_original_before_effects(case, operation):
    cid, root, world = case["cid"], case["campaign"], case["world"]
    (world / "groups").mkdir(exist_ok=True)
    _enable_case_sensitive(world / "groups")
    eid = entities.create_entity(world, "groups", "Salt Circle")
    lower, upper = world / "groups" / f"{eid}.md", world / "groups" / f"{eid.upper()}.md"
    if upper.exists():
        pytest.skip("native case-sensitive World directory is unavailable")
    upper.write_text(lower.read_text(encoding="utf-8") + "Winifred keeps this group.\n", encoding="utf-8")
    overlay.materialize_entity(cid, "groups", eid)
    owned = root / "groups" / f"{eid}.md"
    alias = root / "groups" / f"{eid.upper()}.md"
    if not alias.exists() or not owned.samefile(alias):
        pytest.skip("mixed policies require a case-insensitive campaign")
    with undo.journalled_group_members(cid, eid, bindings=frozenset(), label="Salt Circle -- members"):
        overlay.update_entity(cid, "groups", eid, fields={"members": "characters:mara"})
    row = next(r for r in journal.read(cid) if r.get("field") == "members")
    target = row["undo"]["target"]
    before = {str(p): p.read_bytes() for r in (root, world) for p in r.rglob("*") if p.is_file()}
    with pytest.raises(group_refs.GroupIdentityConflict):
        if operation == "reclassify":
            reclassify.world_entity(case["wid"], "groups", eid.upper(), "lore")
        elif operation == "delete":
            entity_routes.delete_world_entity(case["wid"], "groups", eid.upper())
        else:
            sync.demote(case["wid"], "groups", eid.upper())
    assert {str(p): p.read_bytes() for r in (root, world) for p in r.rglob("*") if p.is_file()} == before
    assert undo.read_value(cid, target)


@pytest.mark.parametrize("operation", ["push", "accept", "reject"])
def test_flat_sync_refuses_alias_that_selects_another_world_original(case, operation):
    cid, root, world = case["cid"], case["campaign"], case["world"]
    (world / "groups").mkdir(exist_ok=True)
    _enable_case_sensitive(world / "groups")
    eid = entities.create_entity(world, "groups", "Salt Circle", body="Seraphine keeps this group.")
    lower, upper = world / "groups" / f"{eid}.md", world / "groups" / f"{eid.upper()}.md"
    if upper.exists():
        pytest.skip("native case-sensitive World directory is unavailable")
    upper.write_text(lower.read_text(encoding="utf-8").replace("Seraphine", "Winifred"), encoding="utf-8")
    overlay.materialize_entity(cid, "groups", eid)
    if not (root / "groups" / f"{eid.upper()}.md").exists():
        pytest.skip("mixed policies require a case-insensitive campaign")
    overlay.update_entity(cid, "groups", eid, body="Mara keeps this group.")
    before = {str(p): p.read_bytes() for r in (root, world) for p in r.rglob("*") if p.is_file()}
    with pytest.raises(group_refs.GroupIdentityConflict):
        if operation == "push":
            sync.push(cid, "groups", eid.upper(), force=True)
        else:
            getattr(sync, operation)(cid, [{"kind": "groups", "id": eid.upper()}])
    assert {str(p): p.read_bytes() for r in (root, world) for p in r.rglob("*") if p.is_file()} == before


def test_flat_promotion_uses_the_canonical_owned_source_identity(case):
    cid, root, world = case["cid"], case["campaign"], case["world"]
    (world / "groups").mkdir(exist_ok=True)
    _enable_case_sensitive(world / "groups")
    eid = entities.create_entity(root, "groups", "Salt Circle")
    owned, alias = root / "groups" / f"{eid}.md", root / "groups" / f"{eid.upper()}.md"
    if not alias.exists() or not owned.samefile(alias):
        pytest.skip("native case-insensitive owned source is unavailable")
    sync.promote(cid, "groups", eid.upper())
    assert (world / "groups" / f"{eid}.md").read_bytes() == owned.read_bytes()
    assert not (world / "groups" / f"{eid.upper()}.md").exists()
    assert set(campaigns.read_manifest(cid)) == {f"groups/{eid}"}


def _mixed_actor_adoption(case, sensitive_side, field="members"):
    cid, root, world = case["cid"], case["campaign"], case["world"]
    sensitive = world if sensitive_side == "world" else root
    (sensitive / "characters").mkdir(exist_ok=True)
    _enable_case_sensitive(sensitive / "characters")
    aid, _ = characters.create_character(world, "Mara")
    overlay.materialize_actor(cid, "characters", aid)
    lower, upper = sensitive / "characters" / aid, sensitive / "characters" / aid.upper()
    if upper.exists():
        pytest.skip("native case-sensitive source is unavailable")
    shutil.copytree(lower, upper)
    upper_meta = upper / "character.md"
    upper_meta.write_text(upper_meta.read_text(encoding="utf-8").replace("Mara", "Winifred"), encoding="utf-8")
    assert not lower.samefile(upper)
    insensitive = root if sensitive_side == "world" else world
    if not (insensitive / "characters" / aid.upper()).exists() or not (insensitive / "characters" / aid).samefile(insensitive / "characters" / aid.upper()):
        pytest.skip("mixed native policies require a case-insensitive counterpart")
    assert overlay.detached_strict(cid) == set()
    assert overlay.read_character(cid, aid.upper())["meta"]["name"] != characters.read_character(world, aid.upper())["meta"]["name"]
    eid = entities.create_entity(world, "groups", "Salt Circle", fields={field: f"characters:{aid.upper()}"})
    return cid, eid

@pytest.mark.parametrize("sensitive_side", ["world", "campaign"])
@pytest.mark.parametrize("field", ["members", "leader"])
def test_adoption_context_identifies_native_mismatch(case, sensitive_side, field):
    cid, eid = _mixed_actor_adoption(case, sensitive_side, field)
    _, context = overlay.read_group_membership(cid, eid)
    found = context.unresolved_members if field == "members" else (context.unresolved_leader,)
    assert "characters:MARA" in found

@pytest.mark.parametrize("sensitive_side", ["world", "campaign"])
@pytest.mark.parametrize("field", ["members", "leader"])
def test_body_copy_refuses_native_actor_rebind(case, sensitive_side, field):
    cid, eid = _mixed_actor_adoption(case, sensitive_side, field)
    roots = (case["campaign"], case["world"])
    before = {str(p): p.read_bytes() for r in roots for p in r.rglob("*") if p.is_file()}
    with pytest.raises(group_refs.GroupIdentityConflict):
        overlay.update_entity(cid, "groups", eid, body="Seraphine keeps the roster.")
    assert {str(p): p.read_bytes() for r in roots for p in r.rglob("*") if p.is_file()} == before

@pytest.mark.parametrize("sensitive_side", ["world", "campaign"])
def test_clear_then_undo_does_not_adopt_native_actor_alias(case, sensitive_side):
    cid, eid = _mixed_actor_adoption(case, sensitive_side)
    with undo.journalled_group_members(cid, eid, bindings=frozenset(), label="Salt Circle -- members"):
        overlay.update_entity(cid, "groups", eid, fields={"members": ""})
    row = next(r for r in journal.read(cid) if r.get("field") == "members")
    with suppress(undo.UndoConflict):
        undo.undo(cid, row["id"])
    assert "characters:MARA" not in group_refs.parse_members(overlay.read_entity(cid, "groups", eid)["meta"])

@pytest.mark.parametrize("sensitive_side", ["world", "campaign"])
@pytest.mark.parametrize("field", ["members", "leader"])
def test_canonical_first_copy_control(case, sensitive_side, field):
    cid, eid = _mixed_actor_adoption(case, sensitive_side, field)
    entities.update_entity(case["world"], "groups", eid, fields={field: "characters:mara"})
    _, context = overlay.read_group_membership(cid, eid)
    assert context.unresolved_members == () and context.unresolved_leader is None
    overlay.update_entity(cid, "groups", eid, body="Seraphine keeps the roster.")
    assert overlay.read_entity(cid, "groups", eid)["meta"][field] == "characters:mara"

@pytest.mark.parametrize("sensitive_side", ["world", "campaign"])
@pytest.mark.parametrize("field", ["members", "leader"])
def test_cross_namespace_copy_requires_exact_field_consent(case, sensitive_side, field):
    cid, eid = _mixed_actor_adoption(case, sensitive_side, field)
    wrong = "leader" if field == "members" else "members"
    with pytest.raises(group_refs.GroupIdentityConflict):
        overlay.update_entity(cid, "groups", eid, body="Seraphine keeps the roster.",
            reviewed_slots=frozenset({(wrong, "characters:MARA")}))
    overlay.update_entity(cid, "groups", eid, body="Seraphine keeps the roster.",
        reviewed_slots=frozenset({(field, "characters:MARA")}))
    assert overlay.read_entity(cid, "groups", eid)["meta"][field] == "characters:MARA"
    assert f"groups/{eid}" in campaigns.read_manifest(cid)


@pytest.mark.parametrize("sensitive_side", ["world", "campaign"])
@pytest.mark.parametrize("field", ["members", "leader"])
def test_cross_namespace_copy_down_refuses_without_consent(case, sensitive_side, field):
    _cid, eid = _mixed_actor_adoption(case, sensitive_side, field)
    roots = (case["campaign"], case["world"])
    before = {str(p): p.read_bytes() for root in roots for p in root.rglob("*") if p.is_file()}
    with pytest.raises(group_refs.GroupIdentityConflict):
        sync.demote(case["wid"], "groups", eid)
    assert {str(p): p.read_bytes() for root in roots for p in root.rglob("*") if p.is_file()} == before
