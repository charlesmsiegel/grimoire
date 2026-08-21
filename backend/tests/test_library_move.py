"""Campaign → world movement: promote, push, demote (#52, #53, #60).

The reverse of `sync.incoming`. Everything here is driven by hand at the store
layer against a `GRIMOIRE_HOME` tmp_path; `test_library_routes.py` covers the
HTTP surface.
"""

import json

import pytest

from grimoire.store import campaigns, characters, entities, greetings, overlay, sync, worlds


def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def _world_and_campaign(monkeypatch, tmp_path, *, name="Run"):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign(name, wid)
    return wid, cid


# ---- promote: a campaign-local record becomes library content (#52) --------

def test_promote_puts_the_campaign_record_in_the_world(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    eid = overlay.create_entity(cid, "locations", "Saltmarch", "fog off the flats")

    sync.promote(cid, "locations", eid)

    wroot = worlds.world_root(wid)
    assert entities.read_entity(wroot, "locations", eid)["body"].strip() == "fog off the flats"


def test_promote_leaves_the_promoting_campaign_with_nothing_incoming(monkeypatch, tmp_path):
    # mine == world == base, byte for byte, is the whole trick
    _wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    eid = overlay.create_entity(cid, "locations", "Saltmarch", "fog off the flats")

    sync.promote(cid, "locations", eid)

    assert sync.incoming(cid) == []


def test_a_promoted_record_reaches_a_sibling_campaign_live(monkeypatch, tmp_path):
    # under the overlay a sibling never materialized it, so there is no sync
    # item to accept -- it simply reads through to the world (see the design doc)
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    sibling = campaigns.create_campaign("Other", wid)
    eid = overlay.create_entity(cid, "locations", "Saltmarch", "fog off the flats")

    sync.promote(cid, "locations", eid)

    assert overlay.read_entity(sibling, "locations", eid)["body"].strip() == "fog off the flats"
    assert sync.incoming(sibling) == []


def test_a_world_edit_after_promote_arrives_as_an_ordinary_update(monkeypatch, tmp_path):
    # proves the recorded base really describes the promoted bytes: if it did
    # not, this would come back a conflict (or not at all)
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    eid = overlay.create_entity(cid, "locations", "Saltmarch", "fog off the flats")
    sync.promote(cid, "locations", eid)

    entities.update_entity(worlds.world_root(wid), "locations", eid, body="the tide is out")

    pend = sync.incoming(cid)
    assert [p["status"] for p in pend] == ["update"]
    assert pend[0]["world"]["body"].strip() == "the tide is out"


def test_promote_refuses_when_the_world_already_holds_the_id(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    entities.create_entity(worlds.world_root(wid), "locations", "Saltmarch", "the world's")
    # a campaign record under that same id, made before the world had one
    overlay.create_entity(cid, "locations", "Elsewhere", "mine")
    croot = campaigns.campaign_root(cid)
    (croot / "locations" / "saltmarch.md").write_text(
        (croot / "locations" / "elsewhere.md").read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(sync.PromoteConflictError):
        sync.promote(cid, "locations", "saltmarch")


def test_promote_refuses_a_record_the_campaign_does_not_hold(monkeypatch, tmp_path):
    _wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    with pytest.raises(entities.EntityNotFound):
        sync.promote(cid, "locations", "nothing-here")


def test_promote_refuses_an_inherited_record(monkeypatch, tmp_path):
    # nothing to promote: the world already has it, so this is a push
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    entities.create_entity(worlds.world_root(wid), "locations", "Saltmarch", "v1")
    with pytest.raises(sync.PromoteConflictError):
        sync.promote(cid, "locations", "saltmarch")


def test_promote_reattaches_a_detached_record(monkeypatch, tmp_path):
    # the campaign's copy outlived its world original, so it was detached; once
    # promoted it is the ancestor of a real world record again
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    entities.create_entity(wroot, "locations", "Saltmarch", "v1")
    overlay.update_entity(cid, "locations", "saltmarch", body="mine")   # materializes
    entities.delete_entity(wroot, "locations", "saltmarch")
    overlay.forget_world_record(wroot, "locations", "saltmarch")
    assert "locations/saltmarch" in overlay.detached(cid)

    sync.promote(cid, "locations", "saltmarch")

    assert "locations/saltmarch" not in overlay.detached(cid)
    assert entities.read_entity(wroot, "locations", "saltmarch")["body"].strip() == "mine"
    assert sync.incoming(cid) == []


def test_promote_carries_the_records_assets(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    eid = overlay.create_entity(cid, "locations", "Saltmarch", "fog")
    img = campaigns.campaign_root(cid) / "locations" / eid / "assets" / "default" / "avatar.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"\x89PNG-not-really")

    sync.promote(cid, "locations", eid)

    landed = worlds.world_root(wid) / "locations" / eid / "assets" / "default" / "avatar.png"
    assert landed.read_bytes() == b"\x89PNG-not-really"


def test_promote_retries_cleanly_after_a_crash_between_the_two_writes(monkeypatch, tmp_path):
    # base recorded, world record never landed -- the residue the ordering
    # deliberately chooses. Sync ignores it, and a retry completes it.
    _wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    eid = overlay.create_entity(cid, "locations", "Saltmarch", "fog")
    manifest = campaigns.read_manifest(cid)
    manifest[f"locations/{eid}"] = "0" * 64      # a base for content the world lacks
    campaigns.write_manifest(cid, manifest)

    assert sync.incoming(cid) == []              # skipped, not offered
    sync.promote(cid, "locations", eid)          # and the retry is not refused
    assert sync.incoming(cid) == []


# ---- promote: greetings name a character, so the world needs that too ------

def test_promote_refuses_a_greeting_whose_character_is_campaign_local(monkeypatch, tmp_path):
    _wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    aid, vid = overlay.create_character(cid, "Winifred")
    gid = overlay.create_greeting(cid, "At the gate", aid, vid, "She waits.")

    with pytest.raises(sync.DanglingReferenceError):
        sync.promote(cid, "greetings", gid)


def test_promote_allows_a_greeting_once_its_character_is_in_the_world(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    aid, vid = overlay.create_character(cid, "Winifred")
    gid = overlay.create_greeting(cid, "At the gate", aid, vid, "She waits.")

    sync.promote(cid, "characters", aid)
    sync.promote(cid, "greetings", gid)

    assert greetings.read_greeting(worlds.world_root(wid), gid)["body"].strip() == "She waits."
    assert sync.incoming(cid) == []


# ---- promote: actors (#60) ------------------------------------------------

def test_promote_carries_an_emergent_character_into_the_library(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    aid, _vid = overlay.create_character(cid, "Winifred")

    sync.promote(cid, "characters", aid)

    assert characters.read_character(worlds.world_root(wid), aid)["meta"]["name"] == "Winifred"
    assert sync.incoming(cid) == []


def test_a_promoted_character_then_syncs_like_any_other(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    aid, vid = overlay.create_character(cid, "Winifred")
    sync.promote(cid, "characters", aid)

    wroot = worlds.world_root(wid)
    card = characters.read_card(wroot, aid, vid)
    card["data"]["description"] = "changed in the library"
    characters.update_version(wroot, aid, vid, card)

    assert [p["status"] for p in sync.incoming(cid)] == ["update"]


def test_promote_carries_world_level_sidecars_but_not_campaign_local_ones(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    aid, _vid = overlay.create_character(cid, "Winifred")
    adir = campaigns.campaign_root(cid) / "characters" / aid
    (adir / "tagline.md").write_text("the gatekeeper", encoding="utf-8")
    (adir / "state.md").write_text("campaign-local notes", encoding="utf-8")

    sync.promote(cid, "characters", aid)

    landed = worlds.world_root(wid) / "characters" / aid
    assert (landed / "tagline.md").read_text(encoding="utf-8") == "the gatekeeper"
    assert not (landed / "state.md").exists()


def test_promote_refuses_an_actor_the_world_already_holds(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Winifred")
    with pytest.raises(sync.PromoteConflictError):
        sync.promote(cid, "characters", "winifred")


# ---- push: a campaign override is saved back (#53) -------------------------

def test_push_writes_the_campaign_text_into_the_world(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    entities.create_entity(worlds.world_root(wid), "locations", "Saltmarch", "v1")
    overlay.update_entity(cid, "locations", "saltmarch", body="my better wording")

    sync.push(cid, "locations", "saltmarch")

    assert entities.read_entity(worlds.world_root(wid), "locations",
                                "saltmarch")["body"].strip() == "my better wording"


def test_push_clears_the_override(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    entities.create_entity(worlds.world_root(wid), "locations", "Saltmarch", "v1")
    overlay.update_entity(cid, "locations", "saltmarch", body="mine")

    sync.push(cid, "locations", "saltmarch")

    assert sync.incoming(cid) == []
    assert sync.diverged(cid) == []


def test_a_sibling_holding_its_own_copy_sees_a_push_as_an_update(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    sibling = campaigns.create_campaign("Other", wid)
    entities.create_entity(worlds.world_root(wid), "locations", "Saltmarch", "v1")
    overlay.materialize_entity(sibling, "locations", "saltmarch")
    overlay.update_entity(cid, "locations", "saltmarch", body="mine")

    sync.push(cid, "locations", "saltmarch")

    pend = sync.incoming(sibling)
    assert [p["status"] for p in pend] == ["update"]
    assert pend[0]["world"]["body"].strip() == "mine"


def test_push_refuses_when_the_world_moved_since_the_base(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    entities.create_entity(wroot, "locations", "Saltmarch", "v1")
    overlay.update_entity(cid, "locations", "saltmarch", body="mine")
    entities.update_entity(wroot, "locations", "saltmarch", body="theirs")

    with pytest.raises(sync.PushConflictError):
        sync.push(cid, "locations", "saltmarch")
    assert entities.read_entity(wroot, "locations", "saltmarch")["body"].strip() == "theirs"


def test_a_forced_push_overwrites_the_world_anyway(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    entities.create_entity(wroot, "locations", "Saltmarch", "v1")
    overlay.update_entity(cid, "locations", "saltmarch", body="mine")
    entities.update_entity(wroot, "locations", "saltmarch", body="theirs")

    sync.push(cid, "locations", "saltmarch", force=True)

    assert entities.read_entity(wroot, "locations", "saltmarch")["body"].strip() == "mine"
    assert sync.incoming(cid) == []


def test_push_refuses_a_record_the_campaign_only_inherits(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    entities.create_entity(worlds.world_root(wid), "locations", "Saltmarch", "v1")
    with pytest.raises(sync.NotDivergedError):
        sync.push(cid, "locations", "saltmarch")


def test_push_refuses_a_campaign_local_record(monkeypatch, tmp_path):
    # that is a promote, and saying so beats creating the record by surprise
    _wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    eid = overlay.create_entity(cid, "locations", "Saltmarch", "mine")
    with pytest.raises(sync.NotInLibraryError):
        sync.push(cid, "locations", eid)


def test_push_refuses_a_version_locked_actor(monkeypatch, tmp_path):
    # its base lives in appearances.json, and pushing one means minting a new
    # world version -- a different operation (#53 option B), not this one
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Winifred")
    with pytest.raises(sync.NotPushableError):
        sync.push(cid, "characters", "winifred")


def test_push_refreshes_the_worlds_updated_stamp(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    entities.create_entity(worlds.world_root(wid), "locations", "Saltmarch", "v1")
    overlay.update_entity(cid, "locations", "saltmarch", body="mine")
    before = worlds.read_world(wid)["meta"].get("updated", "")

    sync.push(cid, "locations", "saltmarch")

    assert worlds.read_world(wid)["meta"]["updated"] >= before


# ---- diverged: what a campaign could push ---------------------------------

def test_diverged_lists_an_edited_copy_and_nothing_else(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    entities.create_entity(wroot, "locations", "Saltmarch", "v1")
    entities.create_entity(wroot, "locations", "Elsewhere", "v1")
    overlay.materialize_entity(cid, "locations", "elsewhere")     # copy, unedited
    overlay.update_entity(cid, "locations", "saltmarch", body="mine")

    assert [d["ref"] for d in sync.diverged(cid)] == [{"kind": "locations", "id": "saltmarch"}]


# ---- demote: a library record becomes campaign-local (#52) -----------------

def test_dependents_lists_every_campaign_of_the_world(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    sibling = campaigns.create_campaign("Other", wid)
    entities.create_entity(worlds.world_root(wid), "locations", "Saltmarch", "v1")
    overlay.update_entity(cid, "locations", "saltmarch", body="mine")   # one holds a copy

    got = {d["id"]: d for d in sync.dependents(wid, "locations", "saltmarch")}

    assert set(got) == {cid, sibling}
    assert got[cid]["has_copy"] is True
    assert got[sibling]["has_copy"] is False


def test_dependents_excludes_a_campaign_that_deleted_the_record(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    entities.create_entity(worlds.world_root(wid), "locations", "Saltmarch", "v1")
    overlay.delete_entity(cid, "locations", "saltmarch")   # tombstoned here

    assert sync.dependents(wid, "locations", "saltmarch") == []


def test_demote_with_copy_down_leaves_every_dependent_holding_its_own(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    sibling = campaigns.create_campaign("Other", wid)
    entities.create_entity(worlds.world_root(wid), "locations", "Saltmarch", "v1")

    sync.demote(wid, "locations", "saltmarch", copy_down=True)

    for c in (cid, sibling):
        croot = campaigns.campaign_root(c)
        assert (croot / "locations" / "saltmarch.md").exists()
        assert overlay.read_entity(c, "locations", "saltmarch")["body"].strip() == "v1"


def test_demote_removes_the_world_record(monkeypatch, tmp_path):
    wid, _cid = _world_and_campaign(monkeypatch, tmp_path)
    entities.create_entity(worlds.world_root(wid), "locations", "Saltmarch", "v1")

    sync.demote(wid, "locations", "saltmarch", copy_down=True)

    assert entities.entity_hash(worlds.world_root(wid), "locations", "saltmarch") is None


def test_demote_detaches_the_copies_it_leaves_behind(monkeypatch, tmp_path):
    # the copies share only a slug with whatever claims that id next (#225)
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    entities.create_entity(worlds.world_root(wid), "locations", "Saltmarch", "v1")

    sync.demote(wid, "locations", "saltmarch", copy_down=True)

    assert "locations/saltmarch" in overlay.detached(cid)
    assert "locations/saltmarch" not in campaigns.read_manifest(cid)
    assert sync.incoming(cid) == []


def test_demote_without_copy_down_takes_the_record_away(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    entities.create_entity(worlds.world_root(wid), "locations", "Saltmarch", "v1")

    sync.demote(wid, "locations", "saltmarch", copy_down=False)

    with pytest.raises(entities.EntityNotFound):
        overlay.read_entity(cid, "locations", "saltmarch")


def test_demote_to_one_target_copies_down_only_there(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    sibling = campaigns.create_campaign("Other", wid)
    entities.create_entity(worlds.world_root(wid), "locations", "Saltmarch", "v1")

    sync.demote(wid, "locations", "saltmarch", copy_down=True, target=cid)

    assert overlay.read_entity(cid, "locations", "saltmarch")["body"].strip() == "v1"
    with pytest.raises(entities.EntityNotFound):
        overlay.read_entity(sibling, "locations", "saltmarch")


def test_demote_does_not_hand_a_record_back_to_a_campaign_that_deleted_it(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    entities.create_entity(worlds.world_root(wid), "locations", "Saltmarch", "v1")
    overlay.delete_entity(cid, "locations", "saltmarch")

    sync.demote(wid, "locations", "saltmarch", copy_down=True)

    with pytest.raises(entities.EntityNotFound):
        overlay.read_entity(cid, "locations", "saltmarch")


def test_demote_refuses_an_actor(monkeypatch, tmp_path):
    wid, _cid = _world_and_campaign(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Winifred")
    with pytest.raises(sync.NotDemotableError):
        sync.demote(wid, "characters", "winifred", copy_down=True)


def test_demote_refuses_a_record_the_world_does_not_hold(monkeypatch, tmp_path):
    wid, _cid = _world_and_campaign(monkeypatch, tmp_path)
    with pytest.raises(entities.EntityNotFound):
        sync.demote(wid, "locations", "nothing-here", copy_down=True)


def test_a_demoted_greeting_leaves_the_worlds_plot_map_clean(monkeypatch, tmp_path):
    wid, cid = _world_and_campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    aid, vid = characters.create_character(wroot, "Winifred")
    gid = greetings.create_greeting(wroot, "At the gate", aid, vid, "She waits.")
    other = greetings.create_greeting(wroot, "Later", aid, vid, "After.")
    greetings.set_edges(wroot, other, leads_to=[gid])

    sync.demote(wid, "greetings", gid, copy_down=True)

    assert gid not in json.dumps(greetings.read_plotmap(wroot))
    assert overlay.read_greeting(cid, gid)["body"].strip() == "She waits."
