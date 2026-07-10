import pytest

from grimoire.store import campaigns, characters, entities, greetings, overlay, pcs, taglines, worlds


def _pair(monkeypatch, tmp_path):
    """A world with one lore entry + a campaign on it (campaigns are still full
    copies at this task — tests delete the copy to simulate a thin campaign)."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    eid = entities.create_entity(wroot, "lore", "The Sword", "world text")
    cid = campaigns.create_campaign("C", wid)
    return wid, wroot, cid, eid


def _thin(cid, kind, eid):
    (campaigns.campaign_root(cid) / kind / f"{eid}.md").unlink()
    manifest = campaigns.read_manifest(cid)
    manifest.pop(f"{kind}/{eid}", None)
    campaigns.write_manifest(cid, manifest)


def test_read_falls_through_to_world_when_not_materialized(monkeypatch, tmp_path):
    _wid, _wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    assert overlay.read_entity(cid, "lore", eid)["body"] == "world text"


def test_materialized_copy_shadows_world(monkeypatch, tmp_path):
    _wid, wroot, cid, eid = _pair(monkeypatch, tmp_path)
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
    overlay.delete_entity(cid, "lore", eid)   # copy exists (full-copy campaign)
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


def _greeting_pair(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    gid = greetings.create_greeting(wroot, "Opening", "hero", "default", "hi {{user}}")
    greetings.set_edges(wroot, gid, leads_to=["next"], excludes=[])
    cid = campaigns.create_campaign("C", wid)
    # thin: strip the copies so fallthrough is exercised before Task 6 lands
    (campaigns.campaign_root(cid) / "greetings" / f"{gid}.md").unlink()
    (campaigns.campaign_root(cid) / "plotmap.json").unlink()
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
    shutil.rmtree(campaigns.campaign_root(cid) / "characters" / aid)
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
