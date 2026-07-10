import json
import shutil

import pytest

from grimoire.store import campaigns, characters, entities, greetings, overlay, pcs, worlds
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


def _strip_to_legacy(cid):
    """Rewind a freshly created campaign to the pre-full-copy on-disk layout."""
    croot = campaigns.campaign_root(cid)
    for sub in ("greetings", "characters", "pcs"):
        if (croot / sub).exists():
            shutil.rmtree(croot / sub)
    (croot / "plotmap.json").unlink(missing_ok=True)
    manifest = {r: h for r, h in campaigns.read_manifest(cid).items()
                if r.split("/")[0] in ("locations", "lore")}
    campaigns.write_manifest(cid, manifest)
    mp = campaigns.campaign_meta_path(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    del meta["world_copy"]
    mp.write_text(dump_frontmatter(meta, body), encoding="utf-8")


def test_ensure_campaign_copy_backfills_legacy(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    char_id, _ = characters.create_character(wroot, "Mara")
    g = greetings.create_greeting(wroot, "Gala", char_id, "default", body="Hi.")
    greetings.set_edges(wroot, g, leads_to=[])
    cid = campaigns.create_campaign("Run", wid)
    _strip_to_legacy(cid)
    campaigns.ensure_campaign_copy(cid)
    croot = campaigns.campaign_root(cid)
    assert (croot / "greetings" / f"{g}.md").exists()
    assert (croot / "plotmap.json").exists()
    assert (croot / "characters" / char_id / "default.json").exists()
    assert campaigns.read_campaign(cid)["meta"]["world_copy"] == "full"
    manifest = campaigns.read_manifest(cid)
    assert manifest["plotmap"] == greetings.plotmap_hash(wroot)
    before = campaigns.read_manifest(cid)
    campaigns.ensure_campaign_copy(cid)  # idempotent: second run changes nothing
    assert campaigns.read_manifest(cid) == before


def test_ensure_campaign_copy_skips_locked_actors(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    char_id, _ = characters.create_character(wroot, "Mara")
    characters.create_version(wroot, char_id, "grim", characters.blank_card("Mara"))
    cid = campaigns.create_campaign("Run", wid)
    _strip_to_legacy(cid)
    # legacy lock: the old appear() copied exactly one version
    croot = campaigns.campaign_root(cid)
    (croot / "characters" / char_id).mkdir(parents=True)
    src = wroot / "characters" / char_id
    for fn in ("character.md", "default.json"):
        (croot / "characters" / char_id / fn).write_text(
            (src / fn).read_text(encoding="utf-8"), encoding="utf-8")
    (croot / "appearances.json").write_text(
        json.dumps({f"characters/{char_id}": {"version": "default", "base": "h",
                                              "scenes": ["s1"], "role": "npc"}}),
        encoding="utf-8")
    campaigns.ensure_campaign_copy(cid)
    assert not (croot / "characters" / char_id / "grim.json").exists()  # no version resurrection
    assert f"characters/{char_id}" not in campaigns.read_manifest(cid)


def test_ensure_campaign_copy_never_clobbers_existing_actor_dirs(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    pid, _ = pcs.create_pc(wroot, "Elara", [])
    cid = campaigns.create_campaign("Run", wid)
    _strip_to_legacy(cid)
    croot = campaigns.campaign_root(cid)
    # legacy campaign-local overlay shadowing the world PC (old CastPanel merge semantics)
    pcs.create_pc(croot, "Elara", [], persona={**pcs.blank_persona("Elara"), "description": "local"})
    campaigns.ensure_campaign_copy(cid)
    assert pcs.read_persona(croot, pid, "default")["description"] == "local"  # not overwritten
    # base recorded anyway: divergence surfaces through sync instead of a silent clobber
    assert campaigns.read_manifest(cid)[f"pcs/{pid}"] == pcs.dir_hash(wroot, pid)
