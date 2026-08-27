"""Reclassification (#119): a generic entity changes kind, and everything that
named it follows -- in the world, and in every campaign that inherits it."""

import json

import pytest

from grimoire.store import (
    campaigns,
    changes,
    entities,
    journal,
    overlay,
    pins,
    provenance,
    reclassify,
    sheets,
    sync,
    worlds,
)
from grimoire.store.context import world_state


def _world(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return wid, worlds.world_root(wid)


def _world_and_campaign(monkeypatch, tmp_path):
    wid, wroot = _world(monkeypatch, tmp_path)
    entities.create_entity(wroot, "lore", "Tidewatch", "A stretch of grey coast.")
    cid = campaigns.create_campaign("Saltmarch", wid)
    return wid, wroot, cid


# ---- world scope ----

def test_world_move_keeps_the_id_and_the_body(monkeypatch, tmp_path):
    wid, wroot = _world(monkeypatch, tmp_path)
    entities.create_entity(wroot, "lore", "Tidewatch", "A stretch of grey coast.")
    out = reclassify.world_entity(wid, "lore", "tidewatch", "locations")
    assert out == {"id": "tidewatch", "campaigns": []}
    assert (entities.read_entity(wroot, "locations", "tidewatch")["body"].strip()
            == "A stretch of grey coast.")


def test_world_move_leaves_a_clean_sync_for_an_inheriting_campaign(monkeypatch, tmp_path):
    # The issue's headline failure: without the campaign sweep the old ref reads
    # as a world-side deletion (skipped) and the new one arrives as `new`, so the
    # campaign ends with a stale copy under the old kind and a duplicate.
    wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    overlay.materialize_entity(cid, "lore", "tidewatch")
    assert reclassify.world_entity(wid, "lore", "tidewatch", "locations")["campaigns"] == [cid]
    assert sync.incoming(cid) == []
    assert [e["id"] for e in overlay.list_entities(cid, "lore")] == []
    assert [e["id"] for e in overlay.list_entities(cid, "locations")] == ["tidewatch"]


def test_world_move_carries_the_campaign_copys_divergence(monkeypatch, tmp_path):
    wid, wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    overlay.update_entity(cid, "lore", "tidewatch", body="mine")   # materializes, diverged
    reclassify.world_entity(wid, "lore", "tidewatch", "locations")
    assert overlay.read_entity(cid, "locations", "tidewatch")["body"].strip() == "mine"
    # still diverged, and still against the same world record: one conflict, not
    # a duplicate pair
    entities.update_entity(wroot, "locations", "tidewatch", body="theirs")
    pend = sync.incoming(cid)
    assert [(p["ref"], p["status"]) for p in pend] == [
        ({"kind": "locations", "id": "tidewatch"}, "conflict")]


def test_world_move_leaves_an_uninvolved_campaign_alone(monkeypatch, tmp_path):
    wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    other_wid = worlds.create_world("Elsewhere")
    entities.create_entity(worlds.world_root(other_wid), "lore", "Tidewatch")
    other = campaigns.create_campaign("Other", other_wid)
    assert reclassify.world_entity(wid, "lore", "tidewatch", "locations")["campaigns"] == [cid]
    assert [e["id"] for e in overlay.list_entities(other, "lore")] == ["tidewatch"]


def test_world_move_follows_a_campaign_tombstone(monkeypatch, tmp_path):
    # The campaign deleted the record; the world then reclassifies it. A
    # tombstone left on the old ref would let the world's copy reappear under
    # its new kind as a record the user had already thrown away.
    wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    overlay.delete_entity(cid, "lore", "tidewatch")
    reclassify.world_entity(wid, "lore", "tidewatch", "locations")
    assert overlay.deleted(cid) == {"locations/tidewatch"}
    assert [e["id"] for e in overlay.list_entities(cid, "locations")] == []
    assert [e["id"] for e in overlay.list_entities(cid, "lore")] == []


def test_world_move_follows_a_detached_campaign_copy(monkeypatch, tmp_path):
    wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    overlay.update_entity(cid, "lore", "tidewatch", body="mine")
    overlay.add_detached(cid, "lore/tidewatch")
    reclassify.world_entity(wid, "lore", "tidewatch", "locations")
    assert overlay.detached(cid) == {"locations/tidewatch"}


def test_world_move_repoints_per_asset_tombstones(monkeypatch, tmp_path):
    wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    overlay.add_deleted(cid, "assets/lore/tidewatch/default/avatar")
    reclassify.world_entity(wid, "lore", "tidewatch", "locations")
    assert overlay.deleted(cid) == {"assets/locations/tidewatch/default/avatar"}


def test_world_move_rewrites_owners_in_the_world(monkeypatch, tmp_path):
    wid, wroot = _world(monkeypatch, tmp_path)
    entities.create_entity(wroot, "locations", "Tidewatch")
    entities.create_entity(wroot, "lore", "Rumour", owners="locations:tidewatch")
    reclassify.world_entity(wid, "locations", "tidewatch", "lore")
    assert entities.read_entity(wroot, "lore", "rumour")["meta"]["owners"] == "lore:tidewatch"


def test_world_move_takes_the_world_sheet_with_it(monkeypatch, tmp_path):
    wid, wroot = _world(monkeypatch, tmp_path)
    entities.create_entity(wroot, "lore", "Tidewatch")
    sheet_dir = wroot / "sheets" / "mod1"
    sheet_dir.mkdir(parents=True)
    (sheet_dir / "lore--tidewatch.json").write_text('{"sheet_type": "place"}', encoding="utf-8")
    reclassify.world_entity(wid, "lore", "tidewatch", "locations")
    assert not (sheet_dir / "lore--tidewatch.json").exists()
    assert json.loads((sheet_dir / "locations--tidewatch.json").read_text()) == {
        "sheet_type": "place"}


def test_world_move_uniquifies_against_the_destination(monkeypatch, tmp_path):
    wid, wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    entities.create_entity(wroot, "locations", "Tidewatch", "the real place")
    out = reclassify.world_entity(wid, "lore", "tidewatch", "locations")
    assert out["id"] == "tidewatch-2"
    assert overlay.read_entity(cid, "locations", "tidewatch")["body"].strip() == "the real place"


def test_world_move_reports_a_campaign_copy_that_could_not_follow(monkeypatch, tmp_path):
    # The campaign already holds a local `locations/tidewatch-2`, which is the id
    # the world record lands on. Its copy cannot be a copy of that, so it becomes
    # campaign-local instead of quietly shadowing a stranger.
    wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    overlay.update_entity(cid, "lore", "tidewatch", body="mine")
    entities.create_entity(worlds.world_root(wid), "locations", "Tidewatch")
    croot = overlay.croot_of(cid)
    (croot / "locations").mkdir(parents=True, exist_ok=True)
    (croot / "locations" / "tidewatch-2.md").write_text(
        "---\nname: Squatter\n---\nnot the same record\n", encoding="utf-8")
    out = reclassify.world_entity(wid, "lore", "tidewatch", "locations")
    assert out["id"] == "tidewatch-2"
    ids = [e["id"] for e in overlay.list_entities(cid, "locations")]
    assert ids == ["tidewatch", "tidewatch-2", "tidewatch-3"]
    assert overlay.read_entity(cid, "locations", "tidewatch-3")["body"].strip() == "mine"
    assert "locations/tidewatch-3" not in campaigns.read_manifest(cid)


# ---- campaign scope ----

def test_campaign_move_materializes_and_hides_the_world_copy(monkeypatch, tmp_path):
    _wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    assert reclassify.campaign_entity(cid, "lore", "tidewatch", "locations") == "tidewatch"
    assert [e["id"] for e in overlay.list_entities(cid, "locations")] == ["tidewatch"]
    assert [e["id"] for e in overlay.list_entities(cid, "lore")] == []
    assert overlay.deleted(cid) == {"lore/tidewatch"}
    # campaign-local now: no base, so no incoming change is ever offered for it
    assert campaigns.read_manifest(cid) == {}
    assert sync.incoming(cid) == []


def test_campaign_move_leaves_the_world_alone(monkeypatch, tmp_path):
    _wid, wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    reclassify.campaign_entity(cid, "lore", "tidewatch", "locations")
    assert [e["id"] for e in entities.list_entities(wroot, "lore")] == ["tidewatch"]
    assert entities.list_entities(wroot, "locations") == []


def test_campaign_move_of_a_local_record_writes_no_tombstone(monkeypatch, tmp_path):
    _wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    overlay.create_entity(cid, "lore", "Saltmarch Rumour")
    reclassify.campaign_entity(cid, "lore", "saltmarch-rumour", "locations")
    assert overlay.deleted(cid) == set()
    assert [e["id"] for e in overlay.list_entities(cid, "locations")] == ["saltmarch-rumour"]


def test_campaign_move_uniquifies_against_what_it_inherits(monkeypatch, tmp_path):
    _wid, wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    entities.create_entity(wroot, "locations", "Tidewatch", "the real place")
    assert reclassify.campaign_entity(cid, "lore", "tidewatch", "locations") == "tidewatch-2"
    assert overlay.read_entity(cid, "locations", "tidewatch")["body"].strip() == "the real place"


def test_campaign_move_rejects_a_record_it_cannot_see(monkeypatch, tmp_path):
    _wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    overlay.delete_entity(cid, "lore", "tidewatch")
    with pytest.raises(entities.EntityNotFound):
        reclassify.campaign_entity(cid, "lore", "tidewatch", "locations")
    with pytest.raises(entities.EntityNotFound):
        reclassify.campaign_entity(cid, "lore", "nobody", "locations")


def test_campaign_move_refuses_same_and_unknown_kinds(monkeypatch, tmp_path):
    _wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    with pytest.raises(entities.SameKindError):
        reclassify.campaign_entity(cid, "lore", "tidewatch", "lore")
    with pytest.raises(entities.UnknownKind):
        reclassify.campaign_entity(cid, "lore", "tidewatch", "characters")


# ---- the ledgers ----

def _ledgered(cid):
    changes.record(cid, "s1", {"lore/tidewatch": [{"field": "body", "before": "a", "after": "b"}]})
    provenance.record(cid, {"lore/tidewatch#body": {"quote": "the coast", "scene": "s1"}})
    pins.set_rule(cid, "lore:tidewatch", pins.PIN, scope=pins.CAMPAIGN)
    journal.append(cid, [{"scene": "s1", "kind": "lore",
                          "ref": {"kind": "lore", "id": "tidewatch"},
                          "field": "body", "label": "Tidewatch -- lore",
                          "undo": {"target": {"w": "entity", "kind": "lore", "id": "tidewatch"},
                                   "restore": "a", "expect": "b"}}])
    sheet_dir = overlay.croot_of(cid) / "sheets"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    (sheet_dir / "lore--tidewatch.json").write_text('{"sheet_type": "place"}', encoding="utf-8")


def _assert_repointed(cid):
    assert list(changes.read(cid)) == ["locations/tidewatch"]
    assert list(provenance.read(cid)) == ["locations/tidewatch#body"]
    assert [r["ref"] for r in pins.read(cid).values()] == ["locations:tidewatch"]
    assert list(pins.read(cid)) == ["*:locations:tidewatch"]
    entry = journal.read(cid)[0]
    assert entry["ref"] == {"kind": "locations", "id": "tidewatch"}
    assert entry["undo"]["target"] == {"w": "entity", "kind": "locations", "id": "tidewatch"}
    croot = overlay.croot_of(cid)
    assert not (croot / "sheets" / "lore--tidewatch.json").exists()
    assert (croot / "sheets" / "locations--tidewatch.json").exists()


def test_campaign_move_repoints_every_ledger(monkeypatch, tmp_path):
    _wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    _ledgered(cid)
    reclassify.campaign_entity(cid, "lore", "tidewatch", "locations")
    _assert_repointed(cid)


def test_world_move_repoints_every_dependent_ledger(monkeypatch, tmp_path):
    wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    _ledgered(cid)
    reclassify.world_entity(wid, "lore", "tidewatch", "locations")
    _assert_repointed(cid)


def test_a_scene_scoped_pin_is_rekeyed_with_its_scene(monkeypatch, tmp_path):
    _wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    pins.set_rule(cid, "lore:tidewatch", pins.EXCLUDE, scope=pins.SCENE, sid="s1")
    reclassify.campaign_entity(cid, "lore", "tidewatch", "locations")
    assert list(pins.read(cid)) == ["s1:locations:tidewatch"]
    assert pins.read(cid)["s1:locations:tidewatch"]["sid"] == "s1"


def test_an_unrelated_ledger_row_is_untouched(monkeypatch, tmp_path):
    _wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    changes.record(cid, "s1", {"lore/other": [{"field": "body", "before": "a", "after": "b"}]})
    provenance.record(cid, {"characters/mara#body": {"quote": "hers", "scene": "s1"}})
    reclassify.campaign_entity(cid, "lore", "tidewatch", "locations")
    assert list(changes.read(cid)) == ["lore/other"]
    assert list(provenance.read(cid)) == ["characters/mara#body"]


def test_the_campaign_sheet_is_not_overwritten_by_the_move(monkeypatch, tmp_path):
    _wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    sheet_dir = overlay.croot_of(cid) / "sheets"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    (sheet_dir / "lore--tidewatch.json").write_text('{"sheet_type": "rumour"}', encoding="utf-8")
    (sheet_dir / "locations--tidewatch.json").write_text('{"sheet_type": "place"}', encoding="utf-8")
    sheets.repoint_records(cid, {"lore/tidewatch": "locations/tidewatch"})
    assert json.loads((sheet_dir / "locations--tidewatch.json").read_text()) == {
        "sheet_type": "place"}
    assert (sheet_dir / "lore--tidewatch.json").exists()


# ---- owners ----

def test_campaign_move_rewrites_owners_it_only_inherits(monkeypatch, tmp_path):
    # The campaign holds no copy of the owning record, so a croot-only sweep
    # would rewrite nothing and leave it gated on a kind that, here, is gone.
    _wid, wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    entities.create_entity(wroot, "lore", "Rumour", owners="lore:tidewatch")
    reclassify.campaign_entity(cid, "lore", "tidewatch", "locations")
    assert (overlay.read_entity(cid, "lore", "rumour")["meta"]["owners"]
            == "locations:tidewatch")
    # ...campaign-side only: the world's record still says what it always did
    assert entities.read_entity(wroot, "lore", "rumour")["meta"]["owners"] == "lore:tidewatch"


def test_campaign_move_leaves_unrelated_owners_unmaterialized(monkeypatch, tmp_path):
    _wid, wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    entities.create_entity(wroot, "lore", "Rumour", owners="characters:mara")
    reclassify.campaign_entity(cid, "lore", "tidewatch", "locations")
    assert not (overlay.croot_of(cid) / "lore" / "rumour.md").exists()


def test_world_move_leaves_a_campaigns_own_copy_of_an_owner_alone(monkeypatch, tmp_path):
    wid, wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    entities.create_entity(wroot, "lore", "Rumour", owners="lore:tidewatch")
    overlay.update_entity(cid, "lore", "rumour", body="mine")   # materialized copy
    reclassify.world_entity(wid, "lore", "tidewatch", "locations")
    assert entities.read_entity(wroot, "lore", "rumour")["meta"]["owners"] == "locations:tidewatch"
    # the campaign's own text is not rewritten under it; the world's edit arrives
    # as an ordinary sync update instead
    assert overlay.read_entity(cid, "lore", "rumour")["meta"]["owners"] == "lore:tidewatch"
    assert [(p["ref"], p["status"]) for p in sync.incoming(cid)] == [
        ({"kind": "lore", "id": "rumour"}, "conflict")]


# ---- the cases the review found ----

def test_a_detached_copy_is_not_tombstoned_on_its_way_out(monkeypatch, tmp_path):
    # `detached` says the world's holder of this id is a STRANGER, so there is
    # nothing of ours to hide from. Asking "would this inherit?" after the
    # repoint has moved the detachment reads it as freshly inheriting and writes
    # a tombstone -- hiding that stranger permanently, for a move that was never
    # about it.
    _wid, wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    overlay.update_entity(cid, "lore", "tidewatch", body="mine")
    # the world record goes, which is what detaches the copy, and the slug is
    # then handed to an unrelated record -- #225's stranger, exactly
    entities.delete_entity(wroot, "lore", "tidewatch")
    overlay.add_detached(cid, "lore/tidewatch")
    assert entities.create_entity(wroot, "lore", "Tidewatch",
                                  "a different place entirely") == "tidewatch"
    reclassify.campaign_entity(cid, "lore", "tidewatch", "locations")
    assert overlay.deleted(cid) == set()
    assert overlay.detached(cid) == {"locations/tidewatch"}
    # the stranger is still visible under its own kind, which is the point
    assert (overlay.read_entity(cid, "lore", "tidewatch")["body"].strip()
            == "a different place entirely")


def test_a_scene_pin_keeps_its_scene_when_the_ref_grows(monkeypatch, tmp_path):
    # `s1:lore:tidewatch` -> `s1:locations:tidewatch`: the two refs are different
    # lengths, so a key trimmed by the wrong one eats part of the scene id.
    _wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    pins.set_rule(cid, "lore:tidewatch", pins.PIN, scope=pins.SCENE, sid="s1")
    reclassify.campaign_entity(cid, "lore", "tidewatch", "locations")
    assert list(pins.read(cid)) == ["s1:locations:tidewatch"]


def test_a_pin_whose_key_disagrees_with_its_ref_is_left_alone(monkeypatch, tmp_path):
    # Hand-edited: rebuilding the key from `scope` would file it under
    # `*:locations:tidewatch` and could delete a real campaign rule there.
    _wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    pins.set_rule(cid, "locations:tidewatch", pins.EXCLUDE, scope=pins.CAMPAIGN)
    data = pins.read(cid)
    data["bogus-key"] = {"ref": "lore:tidewatch", "mode": "pin",
                         "scope": "nonsense", "sid": "", "ttl_posts": 0,
                         "created_posts": 0, "created": ""}
    (overlay.croot_of(cid) / "pins.json").write_text(json.dumps(data), encoding="utf-8")
    reclassify.campaign_entity(cid, "lore", "tidewatch", "locations")
    after = pins.read(cid)
    assert after["bogus-key"]["ref"] == "lore:tidewatch"        # untouched
    assert after["*:locations:tidewatch"]["mode"] == "exclude"  # not clobbered


def test_a_campaign_with_an_unreadable_ledger_still_gets_its_record_moved(
        monkeypatch, tmp_path):
    _wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    sheets_dir = overlay.croot_of(cid) / "sheets"
    sheets_dir.mkdir(parents=True, exist_ok=True)
    (sheets_dir / "lore--tidewatch.json").write_text("{}", encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError("the disk said no")

    monkeypatch.setattr(reclassify.overlay, "rewrite_owner_refs", boom)
    assert reclassify.campaign_entity(cid, "lore", "tidewatch", "locations") == "tidewatch"
    assert [e["id"] for e in overlay.list_entities(cid, "locations")] == ["tidewatch"]


def test_a_campaign_copy_lands_on_the_world_id_even_though_the_world_moved(
        monkeypatch, tmp_path):
    # The world record is ALREADY at `locations/tidewatch` when the copy follows
    # it, so a destination check that consults the world would refuse the only
    # id the copy is allowed to take.
    wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    overlay.update_entity(cid, "lore", "tidewatch", body="mine")
    reclassify.world_entity(wid, "lore", "tidewatch", "locations")
    assert [e["id"] for e in overlay.list_entities(cid, "locations")] == ["tidewatch"]
    assert overlay.read_entity(cid, "locations", "tidewatch")["body"].strip() == "mine"


def test_a_world_record_that_cannot_be_swept_still_reaches_its_campaigns(
        monkeypatch, tmp_path):
    # The campaign sweep is the failure this whole module exists to prevent, so
    # it may not be starved by the cosmetic half that runs before it.
    wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    overlay.materialize_entity(cid, "lore", "tidewatch")

    def boom(*_a, **_k):
        raise OSError("the disk said no")

    monkeypatch.setattr(reclassify.entities, "rewrite_owner_refs", boom)
    out = reclassify.world_entity(wid, "lore", "tidewatch", "locations")
    assert out == {"id": "tidewatch", "campaigns": [cid]}
    assert [e["id"] for e in overlay.list_entities(cid, "locations")] == ["tidewatch"]
    assert sync.incoming(cid) == []


def test_a_keyless_location_that_becomes_lore_starts_activating(monkeypatch, tmp_path):
    # What the feature is FOR. The five kinds differ only in what the context
    # builder does with them: a keyless location surfaces solely as the scene's
    # current setting, and keyless lore is always-on. Correcting the kind has to
    # actually change what reaches the prompt, or the move is bookkeeping.
    _wid, wroot = _world(monkeypatch, tmp_path)
    entities.create_entity(wroot, "locations", "Tidewatch", "A stretch of grey coast.")
    cid = campaigns.create_campaign("Saltmarch", _wid)
    assert world_state._world_info(cid, "nothing to match on")[0] == []
    reclassify.campaign_entity(cid, "locations", "tidewatch", "lore")
    activated = world_state._world_info(cid, "nothing to match on")[0]
    assert [(e["kind"], e["id"]) for e in activated] == [("lore", "tidewatch")]


def test_a_campaign_scope_round_trip_does_not_reclaim_the_tombstoned_slug(
        monkeypatch, tmp_path):
    # Pinned rather than accidental: the first move tombstones the world's
    # `lore/tidewatch`, so the second finds that slug taken. Landing on it
    # anyway would put the record under a ref whose tombstone hides its images.
    _wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    assert reclassify.campaign_entity(cid, "lore", "tidewatch", "locations") == "tidewatch"
    assert reclassify.campaign_entity(cid, "locations", "tidewatch", "lore") == "tidewatch-2"
    assert [e["id"] for e in overlay.list_entities(cid, "lore")] == ["tidewatch-2"]
    assert overlay.list_entities(cid, "locations") == []


def test_a_world_scope_round_trip_gets_its_id_back(monkeypatch, tmp_path):
    # No tombstone is involved when the world moves its own record, so the
    # record comes home under the id it started with.
    wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    overlay.materialize_entity(cid, "lore", "tidewatch")
    assert reclassify.world_entity(wid, "lore", "tidewatch", "locations")["id"] == "tidewatch"
    assert reclassify.world_entity(wid, "locations", "tidewatch", "lore")["id"] == "tidewatch"
    assert [e["id"] for e in overlay.list_entities(cid, "lore")] == ["tidewatch"]
    assert sync.incoming(cid) == []


def test_a_groups_state_rides_along_and_comes_back(monkeypatch, tmp_path):
    # `state.md` lives in the record's own directory, so it moves with it -- and
    # a group that has stopped being a group has no group state to read, which
    # is the honest answer rather than a loss.
    _wid, _wroot = _world(monkeypatch, tmp_path)
    cid = campaigns.create_campaign("Saltmarch", _wid)
    overlay.create_entity(cid, "groups", "The Tidewatch")
    croot = overlay.croot_of(cid)
    (croot / "groups" / "the-tidewatch").mkdir(parents=True, exist_ok=True)
    (croot / "groups" / "the-tidewatch" / "state.md").write_text("secrets", encoding="utf-8")
    reclassify.campaign_entity(cid, "groups", "the-tidewatch", "lore")
    assert (croot / "lore" / "the-tidewatch" / "state.md").read_text() == "secrets"
    reclassify.campaign_entity(cid, "lore", "the-tidewatch", "groups")
    assert (croot / "groups" / "the-tidewatch" / "state.md").read_text() == "secrets"


def test_a_sheet_ref_that_is_not_a_safe_id_moves_nothing(monkeypatch, tmp_path):
    _wid, _wroot = _world(monkeypatch, tmp_path)
    cid = campaigns.create_campaign("Saltmarch", _wid)
    sheet_dir = overlay.croot_of(cid) / "sheets"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    (sheet_dir / "lore--tidewatch.json").write_text("{}", encoding="utf-8")
    sheets.repoint_records(cid, {"lore/tidewatch": "locations/../../escape"})
    assert (sheet_dir / "lore--tidewatch.json").exists()
    assert sorted(p.name for p in sheet_dir.iterdir()) == ["lore--tidewatch.json"]


def test_a_refused_campaign_move_does_not_materialize_the_record(monkeypatch, tmp_path):
    # Validate, then mutate. Both refusals live one call later in
    # `entities.reclassify`, by which point the copy has been made -- so a
    # request that cannot be satisfied would fork the record off its world and
    # then report failure.
    _wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    croot = overlay.croot_of(cid)
    for bad_kind, exc in (("lore", entities.SameKindError), ("characters", entities.UnknownKind)):
        with pytest.raises(exc):
            reclassify.campaign_entity(cid, "lore", "tidewatch", bad_kind)
        assert not (croot / "lore" / "tidewatch.md").exists()
    assert campaigns.read_manifest(cid) == {}


# ---- ref-valued fields (#222) ----
#
# The same two spellings `owners:` has, for the same reason: a reclassify moves
# a record that still exists, so every field naming it has to follow. A DELETE
# deliberately does not -- see `entity_schema`'s docstring, and the two tests
# at the bottom that hold the decision.

def test_world_move_rewrites_ref_fields_in_the_world(monkeypatch, tmp_path):
    wid, wroot = _world(monkeypatch, tmp_path)
    entities.create_entity(wroot, "locations", "Tidewatch")
    entities.create_entity(wroot, "groups", "Saltmarch Watch",
                           fields={"headquarters": "locations:tidewatch"})
    entities.create_entity(wroot, "creatures", "Marsh Wyrm",
                           fields={"habitat": "locations:tidewatch, locations:elsewhere"})
    reclassify.world_entity(wid, "locations", "tidewatch", "lore")
    assert (entities.read_entity(wroot, "groups", "saltmarch-watch")["meta"]["headquarters"]
            == "lore:tidewatch")
    # a multi field keeps its order and its untouched entries
    assert (entities.read_entity(wroot, "creatures", "marsh-wyrm")["meta"]["habitat"]
            == "lore:tidewatch, locations:elsewhere")


def test_ref_rewrite_collapses_a_record_that_named_both_spellings(monkeypatch, tmp_path):
    wid, wroot = _world(monkeypatch, tmp_path)
    entities.create_entity(wroot, "locations", "Tidewatch")
    entities.create_entity(wroot, "creatures", "Marsh Wyrm",
                           fields={"habitat": "locations:tidewatch, lore:tidewatch"})
    reclassify.world_entity(wid, "locations", "tidewatch", "lore")
    assert (entities.read_entity(wroot, "creatures", "marsh-wyrm")["meta"]["habitat"]
            == "lore:tidewatch")


def test_campaign_move_rewrites_ref_fields_it_only_inherits(monkeypatch, tmp_path):
    _wid, wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    entities.create_entity(wroot, "groups", "Saltmarch Watch",
                           fields={"headquarters": "lore:tidewatch"})
    reclassify.campaign_entity(cid, "lore", "tidewatch", "locations")
    assert (overlay.read_entity(cid, "groups", "saltmarch-watch")["meta"]["headquarters"]
            == "locations:tidewatch")
    # campaign-side only: the world's record still says what it always did
    assert (entities.read_entity(wroot, "groups", "saltmarch-watch")["meta"]["headquarters"]
            == "lore:tidewatch")


def test_campaign_move_leaves_unrelated_ref_fields_unmaterialized(monkeypatch, tmp_path):
    _wid, wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    entities.create_entity(wroot, "groups", "Saltmarch Watch",
                           fields={"headquarters": "locations:elsewhere"})
    reclassify.campaign_entity(cid, "lore", "tidewatch", "locations")
    assert not (overlay.croot_of(cid) / "groups" / "saltmarch-watch.md").exists()


def test_a_ref_sweep_that_fails_still_leaves_the_record_moved(monkeypatch, tmp_path):
    # Same trade the `owners:` sweep makes: the record has already moved by the
    # time this runs, so one unreadable file must not 500 a move that happened.
    wid, _wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    overlay.materialize_entity(cid, "lore", "tidewatch")

    def boom(*_a, **_k):
        raise OSError("the disk said no")

    monkeypatch.setattr(reclassify.entities, "rewrite_ref_fields", boom)
    out = reclassify.world_entity(wid, "lore", "tidewatch", "locations")
    assert out == {"id": "tidewatch", "campaigns": [cid]}
    assert [e["id"] for e in overlay.list_entities(cid, "locations")] == ["tidewatch"]


def test_deleting_a_target_leaves_the_ref_dangling_rather_than_scrubbing_it(
        monkeypatch, tmp_path):
    # The decided behaviour (#222), not an oversight: see `entity_schema`'s
    # docstring. A dangling ref says the holder is gone, and comes back by
    # itself if the record is re-created under its old name.
    _wid, wroot = _world(monkeypatch, tmp_path)
    entities.create_entity(wroot, "locations", "Tidewatch")
    entities.create_entity(wroot, "groups", "Saltmarch Watch",
                           fields={"headquarters": "locations:tidewatch"})
    entities.delete_entity(wroot, "locations", "tidewatch")
    assert (entities.read_entity(wroot, "groups", "saltmarch-watch")["meta"]["headquarters"]
            == "locations:tidewatch")
    # ...and re-creating it under the same name reclaims the id, so the ref resolves again
    assert entities.create_entity(wroot, "locations", "Tidewatch") == "tidewatch"


def test_a_campaign_tombstone_does_not_edit_the_worlds_ref(monkeypatch, tmp_path):
    # The scope argument: the delete is one campaign's, the referring record is
    # the world's, and every other campaign shares it.
    _wid, wroot, cid = _world_and_campaign(monkeypatch, tmp_path)
    entities.create_entity(wroot, "groups", "Saltmarch Watch",
                           fields={"headquarters": "lore:tidewatch"})
    overlay.delete_entity(cid, "lore", "tidewatch")
    assert (entities.read_entity(wroot, "groups", "saltmarch-watch")["meta"]["headquarters"]
            == "lore:tidewatch")
    assert not (overlay.croot_of(cid) / "groups" / "saltmarch-watch.md").exists()
