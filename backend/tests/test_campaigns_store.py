import shutil

import pytest

from grimoire.store import assets, campaigns, characters, entities, greetings, overlay, pcs, worlds
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter


def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def test_create_campaign_is_thin(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    entities.create_entity(wroot, "lore", "L", "body")
    characters.create_character(wroot, "Hero")
    cid = campaigns.create_campaign("C", wid)
    root = campaigns.campaign_root(cid)
    assert not (root / "lore").exists()
    assert not (root / "characters").exists()
    assert not (root / "plotmap.json").exists()
    assert campaigns.read_manifest(cid) == {}
    assert campaigns.read_campaign(cid)["meta"]["world_copy"] == "overlay"
    # …but everything is readable through the overlay
    assert overlay.list_entities(cid, "lore")
    assert overlay.list_characters(cid)


def test_create_campaign_does_not_copy_entities_but_overlay_reads_them(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    eid = entities.create_entity(worlds.world_root(wid), "locations", "Seraphine", "Keeper.")
    cid = campaigns.create_campaign("Run One", wid)
    # nothing was copied into the campaign at creation
    with pytest.raises(entities.EntityNotFound):
        entities.read_entity(campaigns.campaign_root(cid), "locations", eid)
    # …but the overlay reads it straight through from the world
    assert overlay.read_entity(cid, "locations", eid)["meta"]["name"] == "Seraphine"
    assert campaigns.read_manifest(cid) == {}


def test_create_campaign_does_not_copy_entity_assets(monkeypatch, tmp_path):
    from grimoire.store import assets
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    eid = entities.create_entity(wroot, "locations", "Warehouse Nine", "Docks.")
    assets.put_image(wroot, eid, "default", assets.AVATAR, b"img", "png", base="locations")
    cid = campaigns.create_campaign("Run One", wid)
    assert assets.image_path(campaigns.campaign_root(cid), eid, "default", assets.AVATAR,
                             base="locations") is None
    # the overlay still serves it from the world
    root = overlay.image_root(cid, eid, "default", assets.AVATAR, base="locations")
    assert root == wroot
    p = assets.image_path(root, eid, "default", assets.AVATAR, base="locations")
    assert p is not None and p.read_bytes() == b"img"


def test_create_against_missing_world_raises(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    with pytest.raises(worlds.WorldNotFound):
        campaigns.create_campaign("X", "no-such-world")


def test_empty_world_makes_empty_campaign(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Empty")
    cid = campaigns.create_campaign("Run", wid)
    # thin creation: nothing materializes up front, so the manifest starts empty
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


def test_deleting_world_leaves_campaign_metadata_intact(monkeypatch, tmp_path):
    """A thin campaign leans on the world for anything never materialized, so
    deleting the world out from under it strands that inherited content —
    a gap a later world-deletion guard closes by blocking the delete outright.
    This only pins what still holds today: the campaign itself survives."""
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    entities.create_entity(worlds.world_root(wid), "locations", "Seraphine", "Keeper.")
    cid = campaigns.create_campaign("Run", wid)
    worlds.delete_world(wid)
    assert campaigns.read_campaign(cid)["meta"]["id"] == cid


def test_manifest_roundtrip_with_slash_keys(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    campaigns.write_manifest(cid, {"characters/a": "deadbeef", "lore/salt-pact": "cafe"})
    assert campaigns.read_manifest(cid) == {"characters/a": "deadbeef", "lore/salt-pact": "cafe"}


def test_create_campaign_is_thin_even_with_a_full_world(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    char_id, _ = characters.create_character(wroot, "Mara")
    characters.create_version(wroot, char_id, "grim", characters.blank_card("Mara"))
    pid, _ = pcs.create_pc(wroot, "Elara", [])
    g = greetings.create_greeting(wroot, "Gala", char_id, "default", body="Hi.")
    greetings.set_edges(wroot, g, leads_to=[])
    cid = campaigns.create_campaign("Run", wid)
    croot = campaigns.campaign_root(cid)
    # nothing was copied at creation, no matter how much the world holds
    assert not (croot / "greetings").exists()
    assert not (croot / "plotmap.json").exists()
    assert not (croot / "characters").exists()
    assert not (croot / "pcs").exists()
    assert campaigns.read_manifest(cid) == {}
    assert campaigns.read_campaign(cid)["meta"]["world_copy"] == "overlay"
    # …but every bit of it is readable through the overlay
    assert overlay.read_greeting(cid, g)["meta"]["name"] == "Gala"
    assert overlay.read_plotmap(cid) == greetings.read_plotmap(wroot)
    detail = overlay.read_character(cid, char_id)
    assert {v["id"] for v in detail["versions"]} == {"default", "grim"}
    assert pid in [p["id"] for p in overlay.list_pcs(cid)]


def _fat_campaign(monkeypatch, tmp_path):
    """A pre-overlay full-copy campaign: build thin, then hand-copy the world
    like the old create_campaign did, stamp world_copy: full. Three lore
    entries cover the slim cases: `same` (redundant copy), `diverged`
    (campaign body differs), `removed` (user deleted the copy, base ref kept)."""
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    same = entities.create_entity(wroot, "lore", "Same", "same")
    diverged = entities.create_entity(wroot, "lore", "Diverged", "world text")
    removed = entities.create_entity(wroot, "lore", "Removed", "removed text")
    aid, vid = characters.create_character(wroot, "Hero")
    assets.put_image(wroot, aid, vid, "avatar", b"\x89PNG\r\n\x1a\nx", "png")
    cid = campaigns.create_campaign("C", wid)
    croot = campaigns.campaign_root(cid)
    manifest = {}
    (croot / "lore").mkdir()
    for xid in (same, diverged, removed):
        (croot / "lore" / f"{xid}.md").write_text(
            (wroot / "lore" / f"{xid}.md").read_text(encoding="utf-8"), encoding="utf-8")
        manifest[f"lore/{xid}"] = entities.entity_hash(wroot, "lore", xid)
    shutil.copytree(wroot / "characters" / aid, croot / "characters" / aid)
    manifest[f"characters/{aid}"] = characters.dir_hash(wroot, aid)
    campaigns.write_manifest(cid, manifest)
    entities.update_entity(croot, "lore", diverged, body="campaign text")
    (croot / "lore" / f"{removed}.md").unlink()
    mp = campaigns.campaign_meta_path(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["world_copy"] = "full"
    mp.write_text(dump_frontmatter(meta, body), encoding="utf-8")
    return wroot, cid, same, diverged, removed, aid, vid


def test_slim_deletes_redundant_keeps_diverged_and_deletions(monkeypatch, tmp_path):
    wroot, cid, same, diverged, removed, aid, vid = _fat_campaign(monkeypatch, tmp_path)
    campaigns.ensure_campaign_slim(cid)
    croot = campaigns.campaign_root(cid)
    assert not (croot / "lore" / f"{same}.md").exists()                 # slimmed
    assert f"lore/{same}" not in campaigns.read_manifest(cid)
    assert overlay.read_entity(cid, "lore", same)["body"] == "same"     # inherited now
    assert (croot / "lore" / f"{diverged}.md").exists()                 # kept
    assert f"lore/{removed}" in overlay.deleted(cid)                    # deletion preserved
    assert not (croot / "characters" / aid).exists() \
        or not (croot / "characters" / aid / "character.md").exists()   # actor dematerialized
    assert overlay.image_root(cid, aid, vid, "avatar") == wroot         # asset dupe pruned
    assert campaigns.read_campaign(cid)["meta"]["world_copy"] == "overlay"
    campaigns.ensure_campaign_slim(cid)                                 # second run: no-op


def test_slim_skips_when_world_missing(monkeypatch, tmp_path):
    wroot, cid, *_ = _fat_campaign(monkeypatch, tmp_path)
    shutil.rmtree(wroot)
    campaigns.ensure_campaign_slim(cid)
    assert campaigns.read_campaign(cid)["meta"]["world_copy"] == "full"  # untouched, retried later
