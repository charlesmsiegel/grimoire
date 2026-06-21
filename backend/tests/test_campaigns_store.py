import pytest

from grimoire.store import campaigns, entities, worlds


def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def test_copy_on_create_copies_entities_and_writes_manifest(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    eid = entities.create_entity(worlds.world_root(wid), "characters", "Seraphine", "Keeper.")
    cid = campaigns.create_campaign("Run One", wid)
    # the entity was copied into the campaign verbatim
    copied = entities.read_entity(campaigns.campaign_root(cid), "characters", eid)
    assert copied["meta"]["name"] == "Seraphine"
    # the manifest base hash matches the world's current hash
    manifest = campaigns.read_manifest(cid)
    assert manifest["characters/seraphine"] == entities.entity_hash(worlds.world_root(wid), "characters", eid)


def test_create_against_missing_world_raises(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    with pytest.raises(worlds.WorldNotFound):
        campaigns.create_campaign("X", "no-such-world")


def test_empty_world_makes_empty_campaign(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Empty")
    cid = campaigns.create_campaign("Run", wid)
    assert campaigns.read_manifest(cid) == {}
    assert (campaigns.campaign_root(cid) / "scenes").exists()


def test_list_read_rename_delete(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Old", wid)
    assert campaigns.list_campaigns()[0]["world"] == wid
    campaigns.rename_campaign(cid, "New")
    assert campaigns.read_campaign(cid)["meta"]["name"] == "New"  # id unchanged
    campaigns.delete_campaign(cid)
    assert campaigns.list_campaigns() == []
    with pytest.raises(campaigns.CampaignNotFound):
        campaigns.read_campaign(cid)


def test_deleting_world_leaves_campaign_copy_intact(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    entities.create_entity(worlds.world_root(wid), "characters", "Seraphine", "Keeper.")
    cid = campaigns.create_campaign("Run", wid)
    worlds.delete_world(wid)
    # the campaign and its copied entity survive
    assert campaigns.read_campaign(cid)["meta"]["id"] == cid
    assert entities.read_entity(campaigns.campaign_root(cid), "characters", "seraphine")["body"].strip() == "Keeper."


def test_manifest_roundtrip_with_slash_keys(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    campaigns.write_manifest(cid, {"characters/a": "deadbeef", "lore/salt-pact": "cafe"})
    assert campaigns.read_manifest(cid) == {"characters/a": "deadbeef", "lore/salt-pact": "cafe"}
