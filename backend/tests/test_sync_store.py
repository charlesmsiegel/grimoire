import pytest

from grimoire.store import appearances as ap
from grimoire.store import campaigns, characters, entities, greetings, overlay, sync, worlds


def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def _setup(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    entities.create_entity(worlds.world_root(wid), "locations", "Seraphine", "v1")
    cid = campaigns.create_campaign("Run", wid)
    return wid, cid


def test_clean_campaign_has_no_incoming(monkeypatch, tmp_path):
    _wid, cid = _setup(monkeypatch, tmp_path)
    assert sync.incoming(cid) == []  # base == world, nothing to offer


def test_world_adds_new_entity_is_not_offered(monkeypatch, tmp_path):
    # a brand-new world record was never materialized into the campaign, so it
    # is never a manifest ref: it flows through live via the overlay, no item.
    wid, cid = _setup(monkeypatch, tmp_path)
    entities.create_entity(worlds.world_root(wid), "locations", "Library", "halls")
    assert sync.incoming(cid) == []
    assert overlay.read_entity(cid, "locations", "library")["body"].strip() == "halls"


def test_world_update_unmodified_local_is_update(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    overlay.materialize_entity(cid, "locations", "seraphine")  # unmodified campaign copy
    entities.update_entity(worlds.world_root(wid), "locations", "seraphine", body="v2")
    pend = sync.incoming(cid)
    assert [p["status"] for p in pend] == ["update"]
    assert pend[0]["world"]["body"].strip() == "v2"
    assert pend[0]["mine"]["body"].strip() == "v1"


def test_both_changed_is_conflict(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    overlay.update_entity(cid, "locations", "seraphine", body="my-edit")  # diverge (materializes)
    entities.update_entity(worlds.world_root(wid), "locations", "seraphine", body="world-edit")
    pend = sync.incoming(cid)
    assert [p["status"] for p in pend] == ["conflict"]
    assert pend[0]["world"]["body"].strip() == "world-edit"
    assert pend[0]["mine"]["body"].strip() == "my-edit"


def test_local_only_change_is_not_offered(monkeypatch, tmp_path):
    _wid, cid = _setup(monkeypatch, tmp_path)
    overlay.update_entity(cid, "locations", "seraphine", body="mine")  # diverge (materializes)
    assert sync.incoming(cid) == []  # world unchanged → nothing incoming


def test_accept_dematerializes_and_clears(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    overlay.materialize_entity(cid, "locations", "seraphine")  # unmodified campaign copy
    entities.update_entity(worlds.world_root(wid), "locations", "seraphine", body="v2")
    sync.accept(cid, [{"kind": "locations", "id": "seraphine"}])
    assert sync.incoming(cid) == []
    assert not (campaigns.campaign_root(cid) / "locations" / "seraphine.md").exists()
    assert "locations/seraphine" not in campaigns.read_manifest(cid)
    assert overlay.read_entity(cid, "locations", "seraphine")["body"].strip() == "v2"  # live


def test_accept_never_materializes_new_world_entity(monkeypatch, tmp_path):
    # a brand-new world entity was never offered as incoming (see
    # test_world_adds_new_entity_is_not_offered): accepting its ref anyway is a
    # no-op on the data — there is no campaign copy to make, it stays inherited.
    wid, cid = _setup(monkeypatch, tmp_path)
    entities.create_entity(worlds.world_root(wid), "lore", "Salt Pact", "the pact")
    sync.accept(cid, [{"kind": "lore", "id": "salt-pact"}])
    assert sync.incoming(cid) == []
    assert not (campaigns.campaign_root(cid) / "lore" / "salt-pact.md").exists()
    assert overlay.read_entity(cid, "lore", "salt-pact")["body"].strip() == "the pact"


def test_reject_keeps_mine_and_does_not_renag(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    overlay.update_entity(cid, "locations", "seraphine", body="mine-edit")  # diverge (materializes)
    entities.update_entity(worlds.world_root(wid), "locations", "seraphine", body="v2")
    sync.reject(cid, [{"kind": "locations", "id": "seraphine"}])
    # mine is untouched, and the change is no longer offered
    assert entities.read_entity(croot, "locations", "seraphine")["body"].strip() == "mine-edit"
    assert sync.incoming(cid) == []
    # A FURTHER world change re-surfaces it. Reject advanced base to v2 while the
    # campaign kept mine-edit, so base(v2) != mine(mine-edit): the next world edit
    # is a conflict, not a clean update — accepting it would overwrite what we
    # deliberately kept.
    entities.update_entity(worlds.world_root(wid), "locations", "seraphine", body="v3")
    assert [p["status"] for p in sync.incoming(cid)] == ["conflict"]


def test_reject_new_entity_is_noop_and_stays_overlay_visible(monkeypatch, tmp_path):
    """A brand-new world entity was never offered as incoming (see
    test_world_adds_new_entity_is_not_offered), so rejecting its ref anyway is
    a no-op on the data: no campaign copy is created, and the record stays
    visible through the overlay, live from the world."""
    wid, cid = _setup(monkeypatch, tmp_path)
    entities.create_entity(worlds.world_root(wid), "lore", "Salt Pact", "x")
    sync.reject(cid, [{"kind": "lore", "id": "salt-pact"}])
    assert sync.incoming(cid) == []
    with pytest.raises(entities.EntityNotFound):
        entities.read_entity(campaigns.campaign_root(cid), "lore", "salt-pact")
    assert overlay.read_entity(cid, "lore", "salt-pact")["body"].strip() == "x"


def test_accept_nonpending_is_noop(monkeypatch, tmp_path):
    _wid, cid = _setup(monkeypatch, tmp_path)
    sync.accept(cid, [{"kind": "locations", "id": "ghost"}])  # not in world
    assert sync.incoming(cid) == []


def test_accept_or_reject_already_synced_ref_does_not_bump(monkeypatch, tmp_path):
    from grimoire.store import frontmatter

    _wid, cid = _setup(monkeypatch, tmp_path)
    overlay.materialize_entity(cid, "locations", "seraphine")  # base == world already
    # force a known-old updated timestamp so any spurious touch() is detectable
    mp = campaigns.campaign_meta_path(cid)
    meta, body = frontmatter.parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["updated"] = "2000-01-01T00:00:00Z"
    mp.write_text(frontmatter.dump_frontmatter(meta, body), encoding="utf-8")
    # seraphine is already in sync (base == world): accept/reject are no-ops
    sync.accept(cid, [{"kind": "locations", "id": "seraphine"}])
    sync.reject(cid, [{"kind": "locations", "id": "seraphine"}])
    assert campaigns.read_campaign(cid)["meta"]["updated"] == "2000-01-01T00:00:00Z"


def test_campaigns_for_world_counts(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    overlay.materialize_entity(cid, "locations", "seraphine")  # unmodified campaign copy
    entities.update_entity(worlds.world_root(wid), "locations", "seraphine", body="v2")
    entities.create_entity(worlds.world_root(wid), "lore", "Pact", "p")  # never materialized: no item
    rows = sync.campaigns_for_world(wid)
    assert len(rows) == 1
    assert rows[0]["id"] == cid
    assert rows[0]["pending"] == {"new": 0, "update": 1, "conflict": 0}


def test_incoming_missing_campaign_raises(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    with pytest.raises(campaigns.CampaignNotFound):
        sync.incoming("nope")


def _greeting_world(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    g = greetings.create_greeting(wroot, "Gala", "c", "v", body="Original.")
    greetings.set_edges(wroot, g, leads_to=[])
    cid = campaigns.create_campaign("Run", wid)
    return wid, wroot, cid, g


def test_incoming_world_greeting_edit_is_update(monkeypatch, tmp_path):
    wid, wroot, cid, g = _greeting_world(monkeypatch, tmp_path)
    overlay.materialize_entity(cid, "greetings", g)  # unmodified campaign copy
    greetings.update_greeting(wroot, g, body="Changed.")
    items = {(i["ref"]["kind"], i["ref"]["id"]): i for i in sync.incoming(cid)}
    item = items[("greetings", g)]
    assert item["status"] == "update"
    assert item["world"]["body"] == "Changed."
    sync.accept(cid, [{"kind": "greetings", "id": g}])
    croot = campaigns.campaign_root(cid)
    assert not (croot / "greetings" / f"{g}.md").exists()  # reverted to inherited
    assert overlay.read_greeting(cid, g)["body"] == "Changed."
    assert sync.incoming(cid) == []


def test_incoming_greeting_conflict_and_reject(monkeypatch, tmp_path):
    wid, wroot, cid, g = _greeting_world(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    overlay.update_greeting(cid, g, body="Campaign edit.")  # diverge (materializes)
    greetings.update_greeting(wroot, g, body="World edit.")
    items = {(i["ref"]["kind"], i["ref"]["id"]): i for i in sync.incoming(cid)}
    assert items[("greetings", g)]["status"] == "conflict"
    sync.reject(cid, [{"kind": "greetings", "id": g}])
    assert greetings.read_greeting(croot, g)["body"] == "Campaign edit."
    assert sync.incoming(cid) == []


def test_incoming_plotmap_update_accept(monkeypatch, tmp_path):
    wid, wroot, cid, g = _greeting_world(monkeypatch, tmp_path)
    overlay.materialize_plotmap(cid)  # unmodified campaign copy of the plot map
    g2 = greetings.create_greeting(wroot, "Next", "c", "v")
    greetings.set_edges(wroot, g, leads_to=[g2])
    items = {(i["ref"]["kind"], i["ref"]["id"]): i for i in sync.incoming(cid)}
    assert items[("plotmap", "plotmap")]["status"] == "update"
    assert ("greetings", g2) not in items  # brand-new world greeting: inherited, no item
    sync.accept(cid, [{"kind": "plotmap", "id": "plotmap"}])
    croot = campaigns.campaign_root(cid)
    assert not (croot / "plotmap.json").exists()  # reverted to inherited
    assert greetings.edges_of(overlay.read_plotmap(cid), g)["leads_to"] == [g2]
    assert overlay.read_greeting(cid, g2)["body"] == ""


def _actor_world(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    char_id, _ = characters.create_character(wroot, "Mara", "young")
    characters.create_version(wroot, char_id, "veteran", characters.blank_card("Mara"))
    cid = campaigns.create_campaign("Run", wid)
    return wid, wroot, cid, char_id


def test_unpicked_actor_world_edit_is_update_and_accept_dematerializes(monkeypatch, tmp_path):
    wid, wroot, cid, char_id = _actor_world(monkeypatch, tmp_path)
    overlay.materialize_actor(cid, "characters", char_id)  # unmodified campaign copy
    card = characters.blank_card("Mara")
    card["data"]["description"] = "changed"
    characters.update_version(wroot, char_id, "young", card)
    characters.delete_version(wroot, char_id, "veteran")
    items = {(i["ref"]["kind"], i["ref"]["id"]): i for i in sync.incoming(cid)}
    assert items[("characters", char_id)]["status"] == "update"
    sync.accept(cid, [{"kind": "characters", "id": char_id}])
    croot = campaigns.campaign_root(cid)
    assert not (croot / "characters" / char_id).exists()  # reverted to inherited
    assert characters.read_card(wroot, char_id, "young")["data"]["description"] == "changed"
    assert not (wroot / "characters" / char_id / "veteran.json").exists()  # world-side deletion
    assert sync.incoming(cid) == []


def test_unpicked_actor_conflict_and_reject(monkeypatch, tmp_path):
    wid, wroot, cid, char_id = _actor_world(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    overlay.materialize_actor(cid, "characters", char_id)  # base == original card
    characters.update_version(wroot, char_id, "young", characters.blank_card("W-Mara"))
    characters.update_version(croot, char_id, "young", characters.blank_card("C-Mara"))
    items = {(i["ref"]["kind"], i["ref"]["id"]): i for i in sync.incoming(cid)}
    assert items[("characters", char_id)]["status"] == "conflict"
    sync.reject(cid, [{"kind": "characters", "id": char_id}])
    assert characters.read_card(croot, char_id, "young")["data"]["name"] == "C-Mara"
    assert sync.incoming(cid) == []


def test_new_world_actor_is_not_offered(monkeypatch, tmp_path):
    # a brand-new world actor was never materialized into the campaign: it's
    # inherited, never a manifest ref, so it never shows as incoming.
    wid, wroot, cid, char_id = _actor_world(monkeypatch, tmp_path)
    new_id, _ = characters.create_character(wroot, "Rowan")
    assert sync.incoming(cid) == []
    assert overlay.char_root(cid, new_id) == wroot
    assert characters.read_card(wroot, new_id, "default")["data"]["name"] == "Rowan"


def test_locked_actor_new_world_version_invisible(monkeypatch, tmp_path):
    wid, wroot, cid, char_id = _actor_world(monkeypatch, tmp_path)
    ap.pick_version(cid, "characters", char_id, "young")
    characters.create_version(wroot, char_id, "elder", characters.blank_card("Mara"))
    assert sync.incoming(cid) == []  # only the locked version's own edits would show


def test_incoming_reads_campaign_meta_and_manifest_once(monkeypatch, tmp_path):
    from pathlib import Path

    wid, cid = _setup(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    char_id, _ = characters.create_character(wroot, "Ada")  # exercise the actor passes too
    overlay.materialize_actor(cid, "characters", char_id)
    overlay.materialize_entity(cid, "locations", "seraphine")
    entities.update_entity(wroot, "locations", "seraphine", body="v2")
    characters.update_version(wroot, char_id, "default", characters.blank_card("Ada2"))
    reads: list[str] = []
    orig = Path.read_text

    def counting(self, *args, **kwargs):
        reads.append(self.name)
        return orig(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting)
    pend = sync.incoming(cid)
    assert {p["ref"]["id"] for p in pend} == {"seraphine", char_id}
    assert reads.count("campaign.md") == 1
    assert reads.count("sync.md") == 1
    assert reads.count("appearances.json") <= 1


def test_inherited_world_edit_produces_no_incoming(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    eid = entities.create_entity(wroot, "lore", "L", "v1")
    cid = campaigns.create_campaign("C", wid)
    entities.update_entity(wroot, "lore", eid, body="v2")
    assert sync.incoming(cid) == []
    assert overlay.read_entity(cid, "lore", eid)["body"] == "v2"   # live


def test_materialized_edit_conflicts_and_accept_dematerializes(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    eid = entities.create_entity(wroot, "lore", "L", "v1")
    cid = campaigns.create_campaign("C", wid)
    overlay.update_entity(cid, "lore", eid, body="campaign v1")   # diverge
    entities.update_entity(wroot, "lore", eid, body="world v2")
    items = sync.incoming(cid)
    assert [i["status"] for i in items] == ["conflict"]
    sync.accept(cid, [{"kind": "lore", "id": eid}])
    assert not (campaigns.campaign_root(cid) / "lore" / f"{eid}.md").exists()
    assert f"lore/{eid}" not in campaigns.read_manifest(cid)
    assert overlay.read_entity(cid, "lore", eid)["body"] == "world v2"


def test_reject_keeps_divergence_and_advances_base(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    eid = entities.create_entity(wroot, "lore", "L", "v1")
    cid = campaigns.create_campaign("C", wid)
    overlay.update_entity(cid, "lore", eid, body="campaign v1")
    entities.update_entity(wroot, "lore", eid, body="world v2")
    sync.reject(cid, [{"kind": "lore", "id": eid}])
    assert sync.incoming(cid) == []
    assert overlay.read_entity(cid, "lore", eid)["body"] == "campaign v1"


def test_new_kind_flows_live_and_syncs_updates(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    entities.create_entity(worlds.world_root(wid), "items", "Salt Knife", "v1")
    # brand-new world record: inherited live via the overlay, nothing incoming
    assert sync.incoming(cid) == []
    assert overlay.read_entity(cid, "items", "salt-knife")["body"].strip() == "v1"
    # materialized-then-world-updated: offered as an update, like locations/lore
    overlay.materialize_entity(cid, "items", "salt-knife")
    entities.update_entity(worlds.world_root(wid), "items", "salt-knife", body="v2")
    pend = sync.incoming(cid)
    assert [p["status"] for p in pend] == ["update"]
    assert pend[0]["world"]["body"].strip() == "v2"
