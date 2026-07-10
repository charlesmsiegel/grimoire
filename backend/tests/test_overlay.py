import pytest

from grimoire.store import assets, campaigns, characters, entities, greetings, overlay, pcs, taglines, worlds


def _pair(monkeypatch, tmp_path):
    """A world with one lore entry + a (thin, copy-on-write) campaign on it."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    eid = entities.create_entity(wroot, "lore", "The Sword", "world text")
    cid = campaigns.create_campaign("C", wid)
    return wid, wroot, cid, eid


def _thin(cid, kind, eid):
    """No-op on an already-thin campaign; tolerant so callers can still force
    the un-materialized state after a test has explicitly materialized it."""
    (campaigns.campaign_root(cid) / kind / f"{eid}.md").unlink(missing_ok=True)
    manifest = campaigns.read_manifest(cid)
    manifest.pop(f"{kind}/{eid}", None)
    campaigns.write_manifest(cid, manifest)


def test_read_falls_through_to_world_when_not_materialized(monkeypatch, tmp_path):
    _wid, _wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    assert overlay.read_entity(cid, "lore", eid)["body"] == "world text"


def test_materialized_copy_shadows_world(monkeypatch, tmp_path):
    _wid, wroot, cid, eid = _pair(monkeypatch, tmp_path)
    overlay.materialize_entity(cid, "lore", eid)   # campaign gets its own copy
    entities.update_entity(wroot, "lore", eid, body="world v2")
    assert overlay.read_entity(cid, "lore", eid)["body"] == "world text"  # copy wins


def test_list_merges_campaign_wins_and_tombstones_hide(monkeypatch, tmp_path):
    _wid, wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    other = entities.create_entity(wroot, "lore", "World Only")
    local = overlay.create_entity(cid, "lore", "Campaign Only")
    overlay.delete_entity(cid, "lore", other)         # inherited -> tombstone
    ids = [e["id"] for e in overlay.list_entities(cid, "lore")]
    assert eid in ids and local in ids and other not in ids


def test_delete_inherited_does_not_resurrect_on_world_edit(monkeypatch, tmp_path):
    _wid, wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    overlay.delete_entity(cid, "lore", eid)
    entities.update_entity(wroot, "lore", eid, body="edited after delete")
    with pytest.raises(entities.EntityNotFound):
        overlay.read_entity(cid, "lore", eid)
    assert eid not in [e["id"] for e in overlay.list_entities(cid, "lore")]


def test_delete_materialized_tombstones_and_drops_base(monkeypatch, tmp_path):
    _wid, _wroot, cid, eid = _pair(monkeypatch, tmp_path)
    overlay.delete_entity(cid, "lore", eid)   # inherited (never materialized) -> tombstone
    assert f"lore/{eid}" in overlay.deleted(cid)
    assert f"lore/{eid}" not in campaigns.read_manifest(cid)
    with pytest.raises(entities.EntityNotFound):
        overlay.read_entity(cid, "lore", eid)


def test_update_inherited_materializes_and_records_base(monkeypatch, tmp_path):
    _wid, wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    base_before = entities.entity_hash(wroot, "lore", eid)
    overlay.update_entity(cid, "lore", eid, body="campaign text")
    assert (campaigns.campaign_root(cid) / "lore" / f"{eid}.md").exists()
    assert campaigns.read_manifest(cid)[f"lore/{eid}"] == base_before
    assert overlay.read_entity(cid, "lore", eid)["body"] == "campaign text"
    assert entities.read_entity(wroot, "lore", eid)["body"] == "world text"  # world untouched


def test_create_uniquifies_against_world_and_tombstones(monkeypatch, tmp_path):
    _wid, _wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    assert overlay.create_entity(cid, "lore", "The Sword") == f"{eid}-2"
    overlay.delete_entity(cid, "lore", eid)  # tombstone the inherited one
    assert overlay.create_entity(cid, "lore", "The Sword") == f"{eid}-3"


def test_create_entity_stores_sd_prompt(monkeypatch, tmp_path):
    _wid, _wroot, cid, _eid = _pair(monkeypatch, tmp_path)
    lid = overlay.create_entity(cid, "locations", "A Tower", "tower text", sd_prompt="a tall tower")
    assert overlay.read_entity(cid, "locations", lid)["meta"]["sd_prompt"] == "a tall tower"


def _greeting_pair(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    gid = greetings.create_greeting(wroot, "Opening", "hero", "default", "hi {{user}}")
    greetings.set_edges(wroot, gid, leads_to=["next"], excludes=[])
    cid = campaigns.create_campaign("C", wid)
    # already thin from creation; tolerant unlinks in case a test materialized first
    (campaigns.campaign_root(cid) / "greetings" / f"{gid}.md").unlink(missing_ok=True)
    (campaigns.campaign_root(cid) / "plotmap.json").unlink(missing_ok=True)
    manifest = campaigns.read_manifest(cid)
    manifest.pop(f"greetings/{gid}", None)
    manifest.pop("plotmap", None)
    campaigns.write_manifest(cid, manifest)
    return wroot, cid, gid


def test_greeting_and_plotmap_fall_through(monkeypatch, tmp_path):
    wroot, cid, gid = _greeting_pair(monkeypatch, tmp_path)
    assert overlay.read_greeting(cid, gid)["body"] == "hi {{user}}"
    assert overlay.read_plotmap(cid)[gid]["leads_to"] == ["next"]


def test_greeting_update_materializes(monkeypatch, tmp_path):
    wroot, cid, gid = _greeting_pair(monkeypatch, tmp_path)
    overlay.update_greeting(cid, gid, body="campaign body")
    assert overlay.read_greeting(cid, gid)["body"] == "campaign body"
    assert greetings.read_greeting(wroot, gid)["body"] == "hi {{user}}"
    assert f"greetings/{gid}" in campaigns.read_manifest(cid)


def test_set_edges_materializes_plotmap(monkeypatch, tmp_path):
    wroot, cid, gid = _greeting_pair(monkeypatch, tmp_path)
    overlay.set_edges(cid, gid, leads_to=["other"])
    assert overlay.read_plotmap(cid)[gid]["leads_to"] == ["other"]
    assert greetings.read_plotmap(wroot)[gid]["leads_to"] == ["next"]   # world untouched
    assert "plotmap" in campaigns.read_manifest(cid)


def test_delete_inherited_greeting_tombstones_and_cleans_edges(monkeypatch, tmp_path):
    wroot, cid, gid = _greeting_pair(monkeypatch, tmp_path)
    other = greetings.create_greeting(wroot, "Second", "hero", "default", "x")
    greetings.set_edges(wroot, other, leads_to=[gid])
    overlay.delete_greeting(cid, gid)
    assert gid not in [g["id"] for g in overlay.list_greetings(cid)]
    assert gid not in overlay.read_plotmap(cid).get(other, {}).get("leads_to", [])
    assert greetings.read_plotmap(wroot)[other]["leads_to"] == [gid]    # world untouched


def _actor_pair(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    aid, _ = characters.create_character(wroot, "Hero")
    characters.create_version(wroot, aid, "dark", characters.blank_card("Hero"))
    taglines.write(wroot, aid, "A hero of legend.")
    cid = campaigns.create_campaign("C", wid)
    import shutil
    d = campaigns.campaign_root(cid) / "characters" / aid
    if d.exists():   # already thin from creation; only needed pre-Task-6
        shutil.rmtree(d)
    manifest = campaigns.read_manifest(cid)
    manifest.pop(f"characters/{aid}", None)
    campaigns.write_manifest(cid, manifest)
    return wroot, cid, aid


def test_actor_root_falls_through_until_materialized(monkeypatch, tmp_path):
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    assert overlay.char_root(cid, aid) == wroot
    overlay.materialize_actor(cid, "characters", aid)
    assert overlay.char_root(cid, aid) == campaigns.campaign_root(cid)
    detail = characters.read_character(overlay.char_root(cid, aid), aid)
    assert [v["id"] for v in detail["versions"]] == ["dark", "default"]
    assert f"characters/{aid}" in campaigns.read_manifest(cid)
    # assets/sidecars are NOT copied
    assert not (campaigns.campaign_root(cid) / "characters" / aid / "tagline.md").exists()


def test_sidecars_do_not_count_as_materialization(monkeypatch, tmp_path):
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    d = campaigns.campaign_root(cid) / "characters" / aid
    d.mkdir(parents=True)
    (d / "dossier.md").write_text("seen in scene 1\n", encoding="utf-8")
    assert overlay.char_root(cid, aid) == wroot          # still inherited
    assert aid in [c["id"] for c in overlay.list_characters(cid)]   # and not duplicated


def test_list_characters_merges_and_hides_tombstoned(monkeypatch, tmp_path):
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    overlay.add_deleted(cid, f"characters/{aid}")
    assert aid not in [c["id"] for c in overlay.list_characters(cid)]


def test_create_character_uniquifies_against_world(monkeypatch, tmp_path):
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)   # aid == "hero", campaign thinned
    assert overlay.create_character(cid, "Hero")[0] == f"{aid}-2"


def test_tagline_falls_through(monkeypatch, tmp_path):
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    assert overlay.tagline(cid, aid) == "A hero of legend."
    taglines.write(campaigns.campaign_root(cid), aid, "Campaign-specific.")
    assert overlay.tagline(cid, aid) == "Campaign-specific."


def test_dematerialize_keeps_sidecars_and_assets(monkeypatch, tmp_path):
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    overlay.materialize_actor(cid, "characters", aid)
    d = campaigns.campaign_root(cid) / "characters" / aid
    (d / "dossier.md").write_text("standing paragraph\n", encoding="utf-8")
    overlay.dematerialize_actor(cid, "characters", aid)
    assert not (d / "character.md").exists() and not list(d.glob("*.json"))
    assert (d / "dossier.md").exists()
    assert overlay.char_root(cid, aid) == wroot


PNG = b"\x89PNG\r\n\x1a\nfake"


def test_images_union_campaign_wins_and_tombstones(monkeypatch, tmp_path):
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    assets.put_image(wroot, aid, "default", "avatar", PNG, "png")
    assets.put_image(wroot, aid, "default", "gallery_0", PNG, "png")
    names = {i["name"] for i in overlay.list_images(cid, aid, "default")}
    assert names == {"avatar", "gallery_0"}
    assert overlay.image_root(cid, aid, "default", "avatar") == wroot
    croot = campaigns.campaign_root(cid)
    assets.put_image(croot, aid, "default", "avatar", PNG + b"2", "png")
    assert overlay.image_root(cid, aid, "default", "avatar") == croot
    overlay.delete_image(cid, aid, "default", "gallery_0")
    assert {i["name"] for i in overlay.list_images(cid, aid, "default")} == {"avatar"}
    # the tombstone makes serving 404 at the campaign root, not fall through
    assert overlay.image_root(cid, aid, "default", "gallery_0") == croot


def test_focus_world_fallback_until_campaign_avatar(monkeypatch, tmp_path):
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    assets.put_image(wroot, aid, "default", "avatar", PNG, "png")
    assets.write_focus(wroot, aid, "default", 80)
    assert overlay.read_focus(cid, aid, "default") == 80
    assets.put_image(campaigns.campaign_root(cid), aid, "default", "avatar", PNG + b"2", "png")
    assert overlay.read_focus(cid, aid, "default") is None   # new avatar, campaign focus unset


def test_focus_hides_through_tombstoned_avatar(monkeypatch, tmp_path):
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    assets.put_image(wroot, aid, "default", "avatar", PNG, "png")
    assets.write_focus(wroot, aid, "default", 80)
    overlay.delete_image(cid, aid, "default", "avatar")
    assert overlay.read_focus(cid, aid, "default") is None


def test_promote_image_copies_up_and_swaps(monkeypatch, tmp_path):
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    assets.put_image(wroot, aid, "default", "avatar", PNG, "png")
    assets.put_image(wroot, aid, "default", "gallery_0", PNG + b"2", "png")
    overlay.promote_image(cid, aid, "default", "gallery_0")
    croot = campaigns.campaign_root(cid)
    assert overlay.image_root(cid, aid, "default", "avatar") == croot
    assert overlay.image_root(cid, aid, "default", "gallery_0") == croot
    new_avatar = assets.image_path(croot, aid, "default", "avatar")
    new_gallery = assets.image_path(croot, aid, "default", "gallery_0")
    assert new_avatar.read_bytes() == PNG + b"2"
    assert new_gallery.read_bytes() == PNG
    # world untouched
    assert assets.image_path(wroot, aid, "default", "avatar").read_bytes() == PNG
    assert assets.image_path(wroot, aid, "default", "gallery_0").read_bytes() == PNG + b"2"


def test_read_character_patches_images_from_union(monkeypatch, tmp_path):
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    assets.put_image(wroot, aid, "default", "avatar", PNG, "png")
    overlay.materialize_actor(cid, "characters", aid)   # cards in campaign, assets in world
    detail = overlay.read_character(cid, aid)
    default = next(v for v in detail["versions"] if v["id"] == "default")
    assert "avatar" in default["images"]
    listed = next(c for c in overlay.list_characters(cid) if c["id"] == aid)
    assert listed["has_avatar"] is True
    assert listed["tagline"] == "A hero of legend."
