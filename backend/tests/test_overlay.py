import logging
from pathlib import Path

import pytest
from grimoire.store import (
    appearances,
    assets,
    atomic,
    campaigns,
    characters,
    entities,
    failsoft,
    greetings,
    groupstate,
    overlay,
    pcs,
    playstate,
    sync,
    taglines,
    voice_anchors,
    worlds,
)
from grimoire.store.campaigns import read as campaigns_read
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter


@pytest.fixture(autouse=True)
def _forget_corruption_warnings():
    """`failsoft` dedupes on module state; tests must not inherit each other's."""
    failsoft._warned.clear()


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


def test_entity_root_resolves_the_layer_a_read_would_answer_from(monkeypatch, tmp_path):
    """The gate the campaign entity image writes take (#373) has to agree with
    `read_entity` about which layer holds the record, or it 404s every entity a
    thin campaign has not materialized -- which is all of them."""
    _wid, wroot, cid, eid = _pair(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)

    _thin(cid, "lore", eid)
    assert overlay.entity_root(cid, "lore", eid) == wroot        # inherited
    overlay.materialize_entity(cid, "lore", eid)
    assert overlay.entity_root(cid, "lore", eid) == croot        # own copy wins

    # campaign-local invention: the world knows nothing about it
    mine = overlay.create_entity(cid, "lore", "The Ledger", "campaign text")
    assert overlay.entity_root(cid, "lore", mine) == croot

    # a record this campaign never had resolves to the world, where the
    # caller's read raises its usual NotFound
    assert overlay.entity_root(cid, "lore", "nobody") == wroot


def test_a_tombstoned_entity_resolves_to_the_campaign_that_disowned_it(monkeypatch, tmp_path):
    """A tombstone has to beat the world's surviving copy here the same way it
    beats it in `read_entity` -- otherwise the gate says yes and art gets filed
    against a record the campaign cannot list, read or delete."""
    _wid, wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    overlay.delete_entity(cid, "lore", eid)
    assert f"lore/{eid}" in overlay.deleted(cid)
    assert entities.read_entity(wroot, "lore", eid)["body"] == "world text"   # world still has it

    croot = campaigns.campaign_root(cid)
    assert overlay.entity_root(cid, "lore", eid) == croot
    with pytest.raises(entities.EntityNotFound):
        entities.require_entity(overlay.entity_root(cid, "lore", eid), "lore", eid)


def test_a_detached_entity_resolves_to_its_own_copy_not_the_slug_s_next_owner(monkeypatch, tmp_path):
    """A spared copy stops sharing an identity with whatever claims the slug
    next. `entity_root` follows the copy, so a write lands on the campaign's
    own record rather than being authorized by a stranger's."""
    _wid, wroot, cid, eid = _pair(monkeypatch, tmp_path)
    overlay.materialize_entity(cid, "lore", eid)
    entities.delete_entity(wroot, "lore", eid)
    overlay.forget_world_record(wroot, "lore", eid)
    assert f"lore/{eid}" in overlay.detached(cid)

    entities.create_entity(wroot, "lore", "The Sword", "a stranger's text")   # same slug
    assert overlay.entity_root(cid, "lore", eid) == campaigns.campaign_root(cid)


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


def test_create_greeting_bakes_char_from_world_only_character(monkeypatch, tmp_path):
    # #137 P1: a thin campaign's greeting may reference a character that only
    # exists in the world -- name resolution must go through char_root
    # (overlay-aware), not the bare campaign root, or baking silently no-ops.
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Seraphine", "default")
    cid = campaigns.create_campaign("C", wid)
    gid = overlay.create_greeting(cid, "Open", "seraphine", "default", body="{{char}} arrives.")
    assert overlay.read_greeting(cid, gid)["body"].strip() == "Seraphine arrives."


def test_update_greeting_bakes_char_from_world_only_character(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Seraphine", "default")
    gid = greetings.create_greeting(wroot, "Open", "seraphine", "default", body="Hello.")
    cid = campaigns.create_campaign("C", wid)
    overlay.update_greeting(cid, gid, body="{{char}} returns.")
    assert overlay.read_greeting(cid, gid)["body"].strip() == "Seraphine returns."


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


def test_image_root_honors_parent_record_tombstone(monkeypatch, tmp_path):
    """Deleting an inherited record writes only its <kind>/<id> tombstone; a
    stale image URL for that record must 404, not fall through to the world."""
    _wid, wroot, cid, eid = _pair(monkeypatch, tmp_path)   # world lore entry + thin campaign
    croot = campaigns.campaign_root(cid)
    assets.put_image(wroot, eid, "default", "art", PNG, "png", base="lore")
    assert overlay.image_root(cid, eid, "default", "art", base="lore") == wroot   # inherited
    overlay.delete_entity(cid, "lore", eid)   # writes the lore/<eid> record tombstone
    assert overlay.image_root(cid, eid, "default", "art", base="lore") == croot   # no fallthrough


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


def test_promote_inherited_without_avatar_tombstones_source(monkeypatch, tmp_path):
    """Promoting an inherited image when there's no avatar to swap into its
    slot must tombstone the inherited source, or the world image would still
    show through the overlay next to the promoted avatar (a duplicate)."""
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    assets.put_image(wroot, aid, "default", "gallery_0", PNG, "png")  # world has no avatar here
    overlay.promote_image(cid, aid, "default", "gallery_0")
    croot = campaigns.campaign_root(cid)
    # the promoted content is now the campaign avatar
    assert overlay.image_root(cid, aid, "default", "avatar") == croot
    assert assets.image_path(croot, aid, "default", "avatar").read_bytes() == PNG
    # the inherited gallery slot is tombstoned, not duplicated back in from the world
    assert {i["name"] for i in overlay.list_images(cid, aid, "default")} == {"avatar"}
    assert overlay.image_root(cid, aid, "default", "gallery_0") == croot  # 404, no fallthrough


def test_a_campaign_read_heals_a_stranded_world_image(monkeypatch, tmp_path):
    """The recovery in #253 lives on the directory scan, so a campaign read of an
    inherited image can fire it against the WORLD directory. That is the right
    outcome -- it is the world's own damage, the repair is a rename onto a name
    the UI renders, and every campaign sees the image again -- and the campaign
    layer still wins wherever it has its own file."""
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    d = wroot / "characters" / aid / "assets" / "default"
    d.mkdir(parents=True, exist_ok=True)
    (d / "promote-tmp.png").write_bytes(b"stranded-in-the-world")  # pre-#253 wreckage

    assert {i["name"] for i in overlay.list_images(cid, aid, "default")} == {"avatar"}
    assert not list(d.glob("promote-tmp.*"))  # repaired in place, world-side
    assert (d / "avatar.png").read_bytes() == b"stranded-in-the-world"

    # and a campaign-side avatar still shadows the recovered world one
    assets.put_image(campaigns.campaign_root(cid), aid, "default", "avatar", b"mine", "png")
    assert overlay.image_root(cid, aid, "default", "avatar") == campaigns.campaign_root(cid)


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


def test_image_cache_tokens_come_from_the_same_union_as_the_names(monkeypatch, tmp_path):
    # `?v=` URLs are served immutable, so a token derived from a different root
    # than the name it labels would pin the wrong bytes in the browser cache.
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    assets.put_image(wroot, aid, "default", "avatar", PNG, "png")
    overlay.materialize_actor(cid, "characters", aid)   # cards campaign-side, asset world-side
    world_v = assets.image_version(assets.image_path(wroot, aid, "default", "avatar"))
    default = next(v for v in overlay.read_character(cid, aid)["versions"] if v["id"] == "default")
    assert default["image_v"]["avatar"] == world_v
    listed = next(c for c in overlay.list_characters(cid) if c["id"] == aid)
    assert listed["avatar_v"] == world_v

    # A campaign-side avatar shadows the world's, and the token has to follow.
    assets.put_image(campaigns.campaign_root(cid), aid, "default", "avatar", b"mine-and-longer", "png")
    mine_v = assets.image_version(
        assets.image_path(campaigns.campaign_root(cid), aid, "default", "avatar"))
    assert mine_v != world_v
    listed = next(c for c in overlay.list_characters(cid) if c["id"] == aid)
    assert listed["avatar_v"] == mine_v
    default = next(v for v in overlay.read_character(cid, aid)["versions"] if v["id"] == "default")
    assert default["image_v"]["avatar"] == mine_v


@pytest.mark.parametrize("ext", ["gif", "webp"])
def test_image_tokens_name_the_file_the_server_serves_through_the_union(monkeypatch, tmp_path, ext):
    # Same trap as the world side, one root further out: the token has to come
    # from `image_root` + `image_path`, the pair the serve route resolves with,
    # not from the union listing's order. `?v=` caches immutable for a year.
    import os
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    assets.put_image(wroot, aid, "default", "avatar", PNG, "png")
    overlay.materialize_actor(cid, "characters", aid)   # cards campaign-side, asset world-side
    served = assets.image_path(wroot, aid, "default", "avatar")
    stale = served.with_suffix(f".{ext}")
    stale.write_bytes(b"stale bytes of a different length")
    os.utime(stale, (1, 1))
    served_v = assets.image_version(served)

    listed = next(c for c in overlay.list_characters(cid) if c["id"] == aid)
    assert listed["avatar_v"] == served_v
    default = next(v for v in overlay.read_character(cid, aid)["versions"] if v["id"] == "default")
    assert default["image_v"]["avatar"] == served_v


def _pc_pair(monkeypatch, tmp_path):
    """A world with one two-version PC + a thin campaign on it (see
    `_actor_pair`, which this mirrors for the other actor kind)."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    pid, _ = pcs.create_pc(wroot, "Winifred", [])
    pcs.create_version(wroot, pid, "Older", pcs.blank_persona("Winifred"))
    cid = campaigns.create_campaign("C", wid)
    return wroot, cid, pid


def test_pc_images_union_campaign_wins_and_tombstones(monkeypatch, tmp_path):
    """The asset overlay was already base-parameterised, so #219 needed no new
    resolution rule -- but nothing had ever passed it `pcs`, so nothing proved
    the rules hold there."""
    wroot, cid, pid = _pc_pair(monkeypatch, tmp_path)
    base = pcs.ASSET_BASE
    assets.put_image(wroot, pid, "default", "avatar", PNG, "png", base)
    assets.put_image(wroot, pid, "default", "gallery_1", PNG, "png", base)
    assert {i["name"] for i in overlay.list_images(cid, pid, "default", base)} == {
        "avatar", "gallery_1"}
    assert overlay.image_root(cid, pid, "default", "avatar", base) == wroot

    croot = campaigns.campaign_root(cid)
    assets.put_image(croot, pid, "default", "avatar", PNG + b"2", "png", base)
    assert overlay.image_root(cid, pid, "default", "avatar", base) == croot

    overlay.delete_image(cid, pid, "default", "gallery_1", base)
    assert {i["name"] for i in overlay.list_images(cid, pid, "default", base)} == {"avatar"}
    assert overlay.image_root(cid, pid, "default", "gallery_1", base) == croot   # 404, no fallthrough


def test_read_pc_patches_images_from_union(monkeypatch, tmp_path):
    """`materialize_actor` copies persona files and never assets, so a
    materialized PC still wears the world's avatar -- reading the detail off
    `pc_root` alone would report none."""
    wroot, cid, pid = _pc_pair(monkeypatch, tmp_path)
    assets.put_image(wroot, pid, "default", "avatar", PNG, "png", pcs.ASSET_BASE)
    assets.write_focus(wroot, pid, "default", 70, pcs.ASSET_BASE)
    overlay.materialize_actor(cid, "pcs", pid)   # personas in campaign, assets in world

    detail = overlay.read_pc(cid, pid)
    default = next(v for v in detail["versions"] if v["id"] == "default")
    assert default["images"] == ["avatar"] and default["avatar_focus"] == 70
    listed = next(p for p in overlay.list_pcs(cid) if p["id"] == pid)
    assert (listed["has_avatar"], listed["avatar_focus"]) == (True, 70)


def test_list_pcs_patches_an_inherited_pc_too(monkeypatch, tmp_path):
    """The union has to be taken for inherited rows as well as materialized
    ones -- `pcs.list_pcs` computed these fields against one root apiece, so a
    thin campaign's rows would all have said `has_avatar: False`."""
    wroot, cid, pid = _pc_pair(monkeypatch, tmp_path)
    assets.put_image(wroot, pid, "default", "avatar", PNG, "png", pcs.ASSET_BASE)
    assert not (campaigns.campaign_root(cid) / "pcs" / pid).exists()   # never materialized
    assert next(p for p in overlay.list_pcs(cid) if p["id"] == pid)["has_avatar"] is True


def test_pc_promote_copies_up_and_leaves_the_world_alone(monkeypatch, tmp_path):
    wroot, cid, pid = _pc_pair(monkeypatch, tmp_path)
    base = pcs.ASSET_BASE
    assets.put_image(wroot, pid, "default", "avatar", PNG, "png", base)
    assets.put_image(wroot, pid, "default", "gallery_1", PNG + b"2", "png", base)
    overlay.promote_image(cid, pid, "default", "gallery_1", base)

    croot = campaigns.campaign_root(cid)
    assert assets.image_path(croot, pid, "default", "avatar", base).read_bytes() == PNG + b"2"
    assert assets.image_path(croot, pid, "default", "gallery_1", base).read_bytes() == PNG
    assert assets.image_path(wroot, pid, "default", "avatar", base).read_bytes() == PNG
    assert assets.image_path(wroot, pid, "default", "gallery_1", base).read_bytes() == PNG + b"2"


def test_a_pcs_campaign_art_outlives_dematerialization(monkeypatch, tmp_path):
    """Reverting a PC to inherited drops its persona files and keeps its art.

    That is the per-file overlay's whole contract, and it is worth pinning for
    PCs specifically: `dematerialize_actor` unlinks `*.md` and then rmdirs the
    directory *if it is empty*, so an `assets/` subdirectory is what stops the
    removal taking the campaign's own images with it."""
    wroot, cid, pid = _pc_pair(monkeypatch, tmp_path)
    base = pcs.ASSET_BASE
    assets.put_image(wroot, pid, "default", "avatar", PNG, "png", base)
    overlay.materialize_actor(cid, "pcs", pid)
    croot = campaigns.campaign_root(cid)
    assets.put_image(croot, pid, "default", "avatar", PNG + b"mine", "png", base)

    overlay.dematerialize_actor(cid, "pcs", pid)

    assert overlay.pc_root(cid, pid) == wroot                     # persona reverted
    assert not (croot / "pcs" / pid / "default.md").exists()
    assert overlay.image_root(cid, pid, "default", "avatar", base) == croot   # art kept
    assert overlay.read_pc(cid, pid)["versions"][0]["images"] == ["avatar"]


def test_a_detached_pc_does_not_inherit_a_strangers_image(monkeypatch, tmp_path):
    """`_flat_ref("pcs", pid)` in `detached` governs PC assets too: once the
    world PC is gone and a new one takes its id, the campaign's spared copy
    must not start wearing the newcomer's art."""
    wroot, cid, pid = _pc_pair(monkeypatch, tmp_path)
    overlay.materialize_actor(cid, "pcs", pid)      # the campaign has its own copy
    pcs.delete_pc(wroot, pid)
    overlay.forget_world_record(wroot, "pcs", pid)  # spares the copy, detaches it
    pcs.create_pc(wroot, "Winifred", [])            # a new PC lands on the same id
    assets.put_image(wroot, pid, "default", "avatar", PNG + b"stranger", "png", pcs.ASSET_BASE)

    assert overlay.list_images(cid, pid, "default", pcs.ASSET_BASE) == []
    assert overlay.read_pc(cid, pid)["versions"][0]["images"] == []


# ---- #247: materialization records the sync base before it commits the copy ----

def _at_commit(monkeypatch, cid, target: Path, sibling_dir: Path = None, glob: str = None) -> dict:
    """Capture the state the moment `target` is written. `target` is the write
    that commits a materialization, so this is what a crash immediately before
    it would leave behind: the manifest, and optionally which files in
    `sibling_dir` had already landed."""
    seen: dict = {}
    real = atomic.write_text

    def spy(path, text):
        if Path(path) == target and "manifest" not in seen:
            seen["manifest"] = campaigns.read_manifest(cid)
            if sibling_dir is not None:
                seen["siblings"] = sorted(p.name for p in sibling_dir.glob(glob))
        real(path, text)

    monkeypatch.setattr(atomic, "write_text", spy)
    return seen


def _fail_writing(monkeypatch, target: Path) -> None:
    real = atomic.write_text

    def spy(path, text):
        if Path(path) == target:
            raise OSError("no space left on device")
        real(path, text)

    monkeypatch.setattr(atomic, "write_text", spy)


def _fail_after_writing(monkeypatch, target: Path) -> None:
    """Raise once `target` has landed — the shape of an asynchronous exception
    (Ctrl-C, a worker shutdown) arriving after the commit write returned."""
    real = atomic.write_text

    def spy(path, text):
        real(path, text)
        if Path(path) == target:
            raise KeyboardInterrupt("interrupted after the commit")

    monkeypatch.setattr(atomic, "write_text", spy)


def test_materialize_entity_records_base_before_the_copy(monkeypatch, tmp_path):
    """A copy with no recorded base is invisible to sync forever: every ref
    the engine considers comes from the manifest, so world edits to that
    record are never offered again."""
    _wid, wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    seen = _at_commit(monkeypatch, cid, campaigns.campaign_root(cid) / "lore" / f"{eid}.md")
    overlay.materialize_entity(cid, "lore", eid)
    assert seen["manifest"][f"lore/{eid}"] == entities.entity_hash(wroot, "lore", eid)


def test_materialize_plotmap_records_base_before_the_copy(monkeypatch, tmp_path):
    wroot, cid, _gid = _greeting_pair(monkeypatch, tmp_path)
    seen = _at_commit(monkeypatch, cid, campaigns.campaign_root(cid) / "plotmap.json")
    overlay.materialize_plotmap(cid)
    assert seen["manifest"]["plotmap"] == greetings.plotmap_hash(wroot)


def test_materialize_actor_records_base_before_the_meta_file(monkeypatch, tmp_path):
    """character.md is what makes an actor materialized (`actor_root` keys on
    it), so it is the commit point the base has to precede -- not merely the
    first version file."""
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    d = campaigns.campaign_root(cid) / "characters" / aid
    seen = _at_commit(monkeypatch, cid, d / "character.md", d, "*.json")
    overlay.materialize_actor(cid, "characters", aid)
    assert seen["manifest"][f"characters/{aid}"] == characters.dir_hash(wroot, aid)
    assert seen["siblings"] == ["dark.json", "default.json"]   # every card, too


def test_materialize_pc_writes_every_version_before_the_meta_file(monkeypatch, tmp_path):
    """pc.md is both the commit point and an `*.md` sibling of the version
    files, so it must be excluded from the copy loop -- written mid-loop it
    can commit an actor whose later-sorting versions are still missing."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    pid, _ = pcs.create_pc(wroot, "Mara", [])
    pcs.create_version(wroot, pid, "veteran", pcs.blank_persona("Mara"))   # sorts after pc.md
    cid = campaigns.create_campaign("C", wid)
    d = campaigns.campaign_root(cid) / "pcs" / pid
    seen: dict = {}
    real = atomic.write_text

    def spy(path, text):
        if Path(path) == d / "pc.md":
            seen.setdefault("versions", sorted(p.name for p in d.glob("*.md")))
        real(path, text)

    monkeypatch.setattr(atomic, "write_text", spy)
    overlay.materialize_actor(cid, "pcs", pid)
    assert seen["versions"] == ["default.md", "veteran.md"]


def _stamp_full(cid: str) -> None:
    """Mark a campaign pre-overlay full-copy — the state `ensure_campaign_slim`
    migrates from, and the one where sync.md means the full copy's inventory
    rather than the set of materialized records."""
    mp = campaigns.campaign_meta_path(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["world_copy"] = "full"
    mp.write_text(dump_frontmatter(meta, body), encoding="utf-8")


def test_materialize_entity_reserves_the_base_before_the_copy_before_the_migration(monkeypatch, tmp_path):
    """On a campaign slim has not reached, neither ordering is safe: a ref with
    no copy is a record the pending migration tombstones, and a copy with no ref
    is one it can stop recognizing as residue the moment the world moves. So the
    base is reserved before the copy and recorded after it (#270)."""
    _wid, wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    _stamp_full(cid)
    seen = _at_commit(monkeypatch, cid, campaigns.campaign_root(cid) / "lore" / f"{eid}.md")
    overlay.materialize_entity(cid, "lore", eid)
    assert seen["manifest"][f"lore/{eid}"] == overlay.RESERVED_BASE     # named, not yet described
    assert campaigns.read_manifest(cid)[f"lore/{eid}"] == entities.entity_hash(wroot, "lore", eid)


def test_materialize_actor_reserves_the_base_before_the_meta_before_the_migration(monkeypatch, tmp_path):
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    _stamp_full(cid)
    d = campaigns.campaign_root(cid) / "characters" / aid
    seen = _at_commit(monkeypatch, cid, d / "character.md")
    overlay.materialize_actor(cid, "characters", aid)
    assert seen["manifest"][f"characters/{aid}"] == overlay.RESERVED_BASE
    assert campaigns.read_manifest(cid)[f"characters/{aid}"] == characters.dir_hash(wroot, aid)


def test_an_exception_after_the_copy_lands_redeems_the_reservation(monkeypatch, tmp_path):
    """An asynchronous exception can arrive after the commit write returned.
    The copy is then real, so the reservation describing it has to be redeemed
    — the same reasoning that keeps the base past the migration."""
    _wid, wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    _stamp_full(cid)
    croot = campaigns.campaign_root(cid)
    _fail_after_writing(monkeypatch, croot / "lore" / f"{eid}.md")
    with pytest.raises(KeyboardInterrupt):
        overlay.materialize_entity(cid, "lore", eid)
    assert (croot / "lore" / f"{eid}.md").exists()
    assert campaigns.read_manifest(cid)[f"lore/{eid}"] == entities.entity_hash(wroot, "lore", eid)


def test_a_reservation_with_no_copy_is_dropped_not_tombstoned(monkeypatch, tmp_path):
    """The residue of a kill before the copy landed. Nothing was copied, so
    there is nothing the user can have deleted: the migration drops the ref and
    the record stays inherited."""
    _wid, _wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    campaigns.write_manifest(cid, {f"lore/{eid}": overlay.RESERVED_BASE})
    _stamp_full(cid)

    campaigns.ensure_campaign_slim(cid)

    assert f"lore/{eid}" not in overlay.deleted(cid)
    assert f"lore/{eid}" not in campaigns.read_manifest(cid)
    assert overlay.read_entity(cid, "lore", eid)["body"] == "world text"


def test_a_reservation_with_a_copy_survives_as_something_sync_still_sees(monkeypatch, tmp_path):
    """The residue of a kill after the copy landed, and the reason reserving
    beats swapping the writes: the ref keeps the record in front of sync even
    when the world has moved on since. Swapping would leave a copy no ref names,
    which the migration's sweep stops recognizing as residue the moment the
    world content differs — silent permanent divergence (Codex review)."""
    _wid, wroot, cid, eid = _pair(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    overlay.materialize_entity(cid, "lore", eid)
    campaigns.write_manifest(cid, {f"lore/{eid}": overlay.RESERVED_BASE})   # …killed before redeeming
    _stamp_full(cid)
    entities.update_entity(wroot, "lore", eid, body="world v2")             # world moves meanwhile

    campaigns.ensure_campaign_slim(cid)

    assert f"lore/{eid}" not in overlay.deleted(cid)
    assert (croot / "lore" / f"{eid}.md").exists()
    assert overlay.read_entity(cid, "lore", eid)["body"] == "world text"    # content kept
    assert [p["status"] for p in sync.incoming(cid)] == ["conflict"]        # and still offered


def test_materialize_entity_drops_the_base_again_when_the_copy_fails(monkeypatch, tmp_path):
    """Recording the base first must not strand one: a record that is still
    fully inherited has to carry no base at all."""
    _wid, _wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    _fail_writing(monkeypatch, campaigns.campaign_root(cid) / "lore" / f"{eid}.md")
    with pytest.raises(OSError):
        overlay.materialize_entity(cid, "lore", eid)
    assert f"lore/{eid}" not in campaigns.read_manifest(cid)
    assert overlay.read_entity(cid, "lore", eid)["body"] == "world text"   # still inherited


def test_materialize_actor_drops_the_base_again_when_the_copy_fails(monkeypatch, tmp_path):
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    _fail_writing(monkeypatch, campaigns.campaign_root(cid) / "characters" / aid / "character.md")
    with pytest.raises(OSError):
        overlay.materialize_actor(cid, "characters", aid)
    assert f"characters/{aid}" not in campaigns.read_manifest(cid)
    assert overlay.char_root(cid, aid) == wroot                            # still inherited


def test_failed_copy_restores_a_base_it_overwrote(monkeypatch, tmp_path):
    """Undoing the base means putting back what was there, not deleting it --
    an earlier interrupted attempt can have left one."""
    _wid, _wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    campaigns.write_manifest(cid, {**campaigns.read_manifest(cid), f"lore/{eid}": "older"})
    _fail_writing(monkeypatch, campaigns.campaign_root(cid) / "lore" / f"{eid}.md")
    with pytest.raises(OSError):
        overlay.materialize_entity(cid, "lore", eid)
    assert campaigns.read_manifest(cid)[f"lore/{eid}"] == "older"


def _world_moves_when_hashing(monkeypatch, module, name, edit, after: bool = False):
    """Race simulator: run `edit` on the world around the first time the base
    hash is taken — `after` it is taken when the copy reads the source later,
    before when the copy has already read it. Either way it only fires if the
    base is taken from the live world separately from the bytes being copied,
    which is the bug; deriving both from one read leaves it inert."""
    real = getattr(module, name)

    def racing(*a, **kw):
        monkeypatch.setattr(module, name, real)
        if after:
            got = real(*a, **kw)
            edit()
            return got
        edit()
        return real(*a, **kw)

    monkeypatch.setattr(module, name, racing)


def test_recorded_base_describes_the_bytes_that_were_copied(monkeypatch, tmp_path):
    """A base taken from the world after the copy's source was read can
    describe content the campaign never got. Sync then sees world == base and
    skips the record forever — #247's failure mode by another route."""
    _wid, wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    _world_moves_when_hashing(monkeypatch, entities, "entity_hash",
                              lambda: entities.update_entity(wroot, "lore", eid, body="world v2"))
    overlay.materialize_entity(cid, "lore", eid)
    croot = campaigns.campaign_root(cid)
    assert campaigns.read_manifest(cid)[f"lore/{eid}"] == entities.entity_hash(croot, "lore", eid)


def test_recorded_plotmap_base_describes_the_bytes_that_were_copied(monkeypatch, tmp_path):
    wroot, cid, gid = _greeting_pair(monkeypatch, tmp_path)
    _world_moves_when_hashing(monkeypatch, greetings, "plotmap_hash",
                              lambda: greetings.set_edges(wroot, gid, leads_to=["moved"]))
    overlay.materialize_plotmap(cid)
    croot = campaigns.campaign_root(cid)
    assert campaigns.read_manifest(cid)["plotmap"] == greetings.plotmap_hash(croot)


def test_recorded_actor_base_describes_the_files_that_were_copied(monkeypatch, tmp_path):
    """The actor path hashes a whole directory, so it had the race the other
    way round: base taken first, files read after. A version purged in between
    left the copy and its base describing different actors."""
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    _world_moves_when_hashing(monkeypatch, characters, "dir_hash",
                              lambda: characters.delete_version(wroot, aid, "dark"), after=True)
    overlay.materialize_actor(cid, "characters", aid)
    croot = campaigns.campaign_root(cid)
    assert campaigns.read_manifest(cid)[f"characters/{aid}"] == characters.dir_hash(croot, aid)


def test_interrupt_after_the_copy_lands_keeps_the_base(monkeypatch, tmp_path):
    """The undo must not recreate the bug it exists to prevent. Once the copy
    is on disk the materialization has happened, whatever is raised next."""
    _wid, wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    _fail_after_writing(monkeypatch, campaigns.campaign_root(cid) / "lore" / f"{eid}.md")
    with pytest.raises(KeyboardInterrupt):
        overlay.materialize_entity(cid, "lore", eid)
    assert campaigns.read_manifest(cid)[f"lore/{eid}"] == entities.entity_hash(wroot, "lore", eid)


def test_interrupt_after_the_actor_meta_lands_keeps_the_base(monkeypatch, tmp_path):
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    _fail_after_writing(monkeypatch, campaigns.campaign_root(cid) / "characters" / aid / "character.md")
    with pytest.raises(KeyboardInterrupt):
        overlay.materialize_actor(cid, "characters", aid)
    assert campaigns.read_manifest(cid)[f"characters/{aid}"] == characters.dir_hash(wroot, aid)


def test_retry_after_an_interrupted_actor_copy_drops_stale_versions(monkeypatch, tmp_path):
    """An interrupted copy leaves version files behind with no meta. If the
    world purges a version before the retry, copying over the residue would
    resurrect it -- and the base, taken from the world, would not match."""
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    d = campaigns.campaign_root(cid) / "characters" / aid
    d.mkdir(parents=True, exist_ok=True)
    (d / "dark.json").write_text("stale card\n", encoding="utf-8")   # residue, no character.md
    characters.delete_version(wroot, aid, "dark")                    # world purges it meanwhile
    overlay.materialize_actor(cid, "characters", aid)
    assert not (d / "dark.json").exists()
    assert characters.dir_hash(campaigns.campaign_root(cid), aid) == campaigns.read_manifest(cid)[f"characters/{aid}"]


def test_base_without_a_copy_reads_through_and_rebases(monkeypatch, tmp_path):
    """The window a hard crash can still leave -- base written, copy not.
    It has to be the harmless side: the record stays live-inherited, sync
    offers nothing on it, and materializing again records a fresh base."""
    _wid, wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    campaigns.write_manifest(cid, {**campaigns.read_manifest(cid), f"lore/{eid}": "interrupted"})
    entities.update_entity(wroot, "lore", eid, body="world v2")
    assert overlay.read_entity(cid, "lore", eid)["body"].strip() == "world v2"
    assert sync.incoming(cid) == []
    overlay.materialize_entity(cid, "lore", eid)
    assert campaigns.read_manifest(cid)[f"lore/{eid}"] == entities.entity_hash(wroot, "lore", eid)
    assert sync.incoming(cid) == []


# ---- a corrupt deleted.json still fails soft, but says so ----
#
# Every other fail-soft read in the store degrades toward *less* content, which
# the user notices. This one degrades toward more: an empty tombstone set means
# "nothing was deleted", so deleted records return, inherited from the world.
# The fallback stays -- an unopenable campaign is worse -- but it is reported.

def _corrupt_tombstones(cid, text="{not a list"):
    p = campaigns.campaign_root(cid) / "deleted.json"
    p.write_text(text, encoding="utf-8")
    return p


def test_corrupt_tombstones_still_read_as_empty(monkeypatch, tmp_path, caplog):
    _wid, _wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    overlay.delete_entity(cid, "lore", eid)
    _corrupt_tombstones(cid)
    with caplog.at_level(logging.WARNING):
        assert overlay.deleted(cid) == set()


def test_corrupt_tombstones_warn_with_path_and_consequence(monkeypatch, tmp_path, caplog):
    _wid, _wroot, cid, eid = _pair(monkeypatch, tmp_path)
    p = _corrupt_tombstones(cid)
    with caplog.at_level(logging.WARNING):
        overlay.deleted(cid)
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert str(p) in msg
    assert cid in msg and "reappear" in msg


def test_tombstones_of_the_wrong_json_type_warn(monkeypatch, tmp_path, caplog):
    """`{"lore/x": true}` parses, so only the shape check catches it -- and it
    resurrects exactly as much as a parse error does."""
    _wid, _wroot, cid, _eid = _pair(monkeypatch, tmp_path)
    _corrupt_tombstones(cid, '{"lore/x": true}')
    with caplog.at_level(logging.WARNING):
        assert overlay.deleted(cid) == set()
    assert len(caplog.records) == 1


def test_intact_tombstones_are_silent(monkeypatch, tmp_path, caplog):
    _wid, _wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    with caplog.at_level(logging.WARNING):
        overlay.delete_entity(cid, "lore", eid)
        assert overlay.deleted(cid) == {f"lore/{eid}"}
        overlay.list_entities(cid, "lore")
    assert caplog.records == []


def test_a_listing_over_a_corrupt_file_warns_once(monkeypatch, tmp_path, caplog):
    """`deleted()` runs ~2x per record listed; a warning per call would bury
    the report it exists to make."""
    _wid, wroot, cid, _eid = _pair(monkeypatch, tmp_path)
    for i in range(10):
        entities.create_entity(wroot, "lore", f"Entry {i}")
    _corrupt_tombstones(cid)
    with caplog.at_level(logging.WARNING):
        overlay.list_entities(cid, "lore")
        overlay.list_entities(cid, "lore")
    assert len(caplog.records) == 1


# ---- #225: a world-side delete must not leave campaign state behind ----
#
# Ids are stable for life and are handed out by slug, so a record deleted
# world-side and recreated under the same name gets the same id back. Anything
# a dependent campaign filed *beside* the old record under that id is then
# adopted by the new, unrelated one -- which is how a dead group's Secrets got
# into a live scene's context.

def _dependent(monkeypatch, tmp_path):
    """A world plus one campaign on it, both empty."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid)
    return worlds.world_root(wid), cid, campaigns.campaign_root(cid)


def _delete_world_group(wroot, gid):
    """What the world route does: drop the record, then sweep dependents."""
    entities.delete_entity(wroot, "groups", gid)
    overlay.forget_world_record(wroot, "groups", gid)


def test_recreated_world_group_does_not_inherit_the_dead_ones_state(monkeypatch, tmp_path):
    wroot, _cid, croot = _dependent(monkeypatch, tmp_path)
    gid = entities.create_entity(wroot, "groups", "Salt Circle", "the old circle")
    groupstate.write_state(croot, gid, "## Secrets\nThe abbot is a member.")

    _delete_world_group(wroot, gid)
    assert entities.create_entity(wroot, "groups", "Salt Circle") == gid   # id reused
    assert groupstate.read_state(croot, gid) is None


def test_recreated_world_character_does_not_inherit_the_dead_ones_playstate(monkeypatch, tmp_path):
    wroot, _cid, croot = _dependent(monkeypatch, tmp_path)
    aid, _vid = characters.create_character(wroot, "Winifred")
    playstate.write_state(croot, aid, "## Knows\nWhere the ledger is hidden.")

    characters.delete_character(wroot, aid)
    overlay.forget_world_record(wroot, "characters", aid)
    assert characters.create_character(wroot, "Winifred")[0] == aid        # id reused
    assert playstate.read_state(croot, aid) is None


def test_sweep_spares_a_campaign_that_owns_its_own_copy(monkeypatch, tmp_path):
    """A materialized record survives the world delete, so its state is still
    the state of a record the campaign has."""
    wroot, cid, croot = _dependent(monkeypatch, tmp_path)
    gid = entities.create_entity(wroot, "groups", "Salt Circle", "the old circle")
    overlay.materialize_entity(cid, "groups", gid)
    groupstate.write_state(croot, gid, "## Secrets\nThe abbot is a member.")

    _delete_world_group(wroot, gid)
    assert groupstate.read_state(croot, gid)["secrets"] == "The abbot is a member."


def test_sweep_spares_a_campaign_that_materialized_the_actor(monkeypatch, tmp_path):
    wroot, cid, croot = _dependent(monkeypatch, tmp_path)
    aid, _vid = characters.create_character(wroot, "Winifred")
    overlay.materialize_actor(cid, "characters", aid)
    playstate.write_state(croot, aid, "## Knows\nWhere the ledger is hidden.")

    characters.delete_character(wroot, aid)
    overlay.forget_world_record(wroot, "characters", aid)
    assert playstate.read_state(croot, aid)["knows"] == "Where the ledger is hidden."


def test_sweep_leaves_campaigns_of_other_worlds_alone(monkeypatch, tmp_path):
    """Slugs collide across worlds; only the deleted world's dependents move."""
    wroot, _cid, croot = _dependent(monkeypatch, tmp_path)
    other_wid = worlds.create_world("Saltmarch")
    other = worlds.world_root(other_wid)
    other_croot = campaigns.campaign_root(campaigns.create_campaign("Other", other_wid))
    gid = entities.create_entity(wroot, "groups", "Salt Circle")
    entities.create_entity(other, "groups", "Salt Circle")
    groupstate.write_state(croot, gid, "## Secrets\nours")
    groupstate.write_state(other_croot, gid, "## Secrets\ntheirs")

    _delete_world_group(wroot, gid)
    assert groupstate.read_state(croot, gid) is None
    assert groupstate.read_state(other_croot, gid)["secrets"] == "theirs"


def test_sweep_takes_every_sidecar_filed_beside_the_record(monkeypatch, tmp_path):
    """state.md is the one #225 names, but every campaign-local file in the
    record's directory keys on the same reusable id and re-attaches the same
    way -- the dossier feeds scene context too."""
    wroot, _cid, croot = _dependent(monkeypatch, tmp_path)
    aid, _vid = characters.create_character(wroot, "Winifred")
    d = croot / "characters" / aid
    d.mkdir(parents=True)
    (d / "dossier.md").write_text("standing paragraph\n", encoding="utf-8")
    (d / "voice_drift.md").write_text("drifting\n", encoding="utf-8")

    characters.delete_character(wroot, aid)
    overlay.forget_world_record(wroot, "characters", aid)
    assert not d.exists()


def test_sweep_drops_the_worlds_own_leftover_record_directory(monkeypatch, tmp_path):
    """`entities.delete_entity` unlinks `<kind>/<id>.md` and leaves
    `<kind>/<id>/assets/` -- so the world's own images re-attached too."""
    wroot, _cid, _croot = _dependent(monkeypatch, tmp_path)
    gid = entities.create_entity(wroot, "groups", "Salt Circle")
    assets.put_image(wroot, gid, "default", assets.AVATAR, PNG, "png", base="groups")

    _delete_world_group(wroot, gid)
    assert not (wroot / "groups" / gid).exists()


def test_sweep_ignores_a_kind_that_is_never_inherited(monkeypatch, tmp_path):
    wroot, _cid, croot = _dependent(monkeypatch, tmp_path)
    stray = croot / "scenes" / "s1"
    stray.mkdir(parents=True)
    overlay.forget_world_record(wroot, "scenes", "s1")
    assert stray.exists()


def test_campaign_side_delete_takes_the_record_directory_too(monkeypatch, tmp_path):
    """The campaign-side mirror: a campaign-local record deleted campaign-side
    leaves no id taken, so its leftovers would be adopted by the next record
    that slugs the same way."""
    _wroot, cid, croot = _dependent(monkeypatch, tmp_path)
    gid = overlay.create_entity(cid, "groups", "Salt Circle")
    groupstate.write_state(croot, gid, "## Secrets\nThe abbot is a member.")
    assets.put_image(croot, gid, "default", assets.AVATAR, PNG, "png", base="groups")

    overlay.delete_entity(cid, "groups", gid)
    assert not (croot / "groups" / gid).exists()
    assert overlay.create_entity(cid, "groups", "Salt Circle") == gid
    assert groupstate.read_state(croot, gid) is None


def test_sweep_refuses_an_id_that_could_escape_its_directory(monkeypatch, tmp_path):
    """`kind` and the id both arrive straight off the URL path, and the sweep
    removes a whole DIRECTORY -- `<wroot>/groups/..` is the world itself.

    Checking that the directories still exist would not catch it: `rmtree`
    empties a `..` path and only then fails on the final `rmdir`, so the world
    dir survives an unguarded call with nothing left in it."""
    wroot, _cid, croot = _dependent(monkeypatch, tmp_path)
    gid = entities.create_entity(wroot, "groups", "Salt Circle")   # so `groups/` exists to escape
    for escape in ("..", "../..", "../../campaigns"):
        overlay.forget_world_record(wroot, "groups", escape)
    assert (wroot / "world.md").exists() and (wroot / "groups" / f"{gid}.md").exists()
    assert (croot / "campaign.md").exists()


def test_a_campaign_nobody_can_read_does_not_stop_the_sweep(monkeypatch, tmp_path):
    """One corrupt campaign.md -- of any world -- used to abort the whole sweep,
    so every healthy dependent kept its stale state (Codex review)."""
    wroot, _cid, croot = _dependent(monkeypatch, tmp_path)
    gid = entities.create_entity(wroot, "groups", "Salt Circle")
    groupstate.write_state(croot, gid, "## Secrets\nThe abbot is a member.")
    other = campaigns.campaign_root(campaigns.create_campaign("Other", wroot.name))
    (other / "campaign.md").write_bytes(b"---\nname: Other\n---\n\xff\xfe body")

    entities.delete_entity(wroot, "groups", gid)
    overlay.forget_world_record(wroot, "groups", gid)
    assert groupstate.read_state(croot, gid) is None


def test_a_campaign_whose_world_cannot_be_read_is_left_alone(monkeypatch, tmp_path, caplog):
    """`None` means "we could not tell". The in-use check counts that campaign
    as a user; the sweep must not delete its state on the same guess."""
    wroot, cid, croot = _dependent(monkeypatch, tmp_path)
    gid = entities.create_entity(wroot, "groups", "Salt Circle")
    groupstate.write_state(croot, gid, "## Secrets\nThe abbot is a member.")
    monkeypatch.setattr(campaigns_read, "world_refs", lambda: [(cid, "Run", None)])

    entities.delete_entity(wroot, "groups", gid)
    with caplog.at_level(logging.WARNING):
        overlay.forget_world_record(wroot, "groups", gid)
    assert groupstate.read_state(croot, gid) is not None
    assert any("cannot read which world" in r.message for r in caplog.records)


# ---- Codex review: a spared copy is no longer a copy OF anything ----

def test_spared_copy_is_detached_from_the_deleted_world_record(monkeypatch, tmp_path):
    """The campaign keeps its copy -- but its sync base claims a shared ancestor
    with a record that is gone, so a recreated slug arrived as an `update` and
    accepting it overwrote the copy while its state.md stayed put."""
    wroot, cid, croot = _dependent(monkeypatch, tmp_path)
    gid = entities.create_entity(wroot, "groups", "Salt Circle", "the old circle")
    overlay.materialize_entity(cid, "groups", gid)
    groupstate.write_state(croot, gid, "## Secrets\nThe abbot is a member.")

    entities.delete_entity(wroot, "groups", gid)
    overlay.forget_world_record(wroot, "groups", gid)
    assert f"groups/{gid}" not in campaigns.read_manifest(cid)   # campaign-local now

    entities.create_entity(wroot, "groups", "Salt Circle", "a brand new, unrelated circle")
    assert [p for p in sync.incoming(cid) if p["ref"]["id"] == gid] == []
    assert overlay.read_entity(cid, "groups", gid)["body"] == "the old circle"


def test_spared_actor_copy_is_detached_from_the_deleted_world_record(monkeypatch, tmp_path):
    wroot, cid, croot = _dependent(monkeypatch, tmp_path)
    aid, _vid = characters.create_character(wroot, "Winifred", "default",
                                            {"data": {"name": "Winifred", "description": "the old one"}})
    overlay.materialize_actor(cid, "characters", aid)
    playstate.write_state(croot, aid, "## Knows\nWhere the ledger is hidden.")

    characters.delete_character(wroot, aid)
    overlay.forget_world_record(wroot, "characters", aid)
    assert f"characters/{aid}" not in campaigns.read_manifest(cid)

    characters.create_character(wroot, "Winifred", "default",
                                {"data": {"name": "Winifred", "description": "a DIFFERENT person"}})
    assert [p for p in sync.incoming(cid) if p["ref"]["id"] == aid] == []
    assert overlay.char_root(cid, aid) == croot                  # still the campaign's own


def test_sweep_unwires_a_deleted_greeting_from_a_campaign_plot_map(monkeypatch, tmp_path):
    """`delete_greeting` cleans the WORLD's plot map; a campaign that
    materialized its own keeps a copy `read_plotmap` prefers."""
    wroot, cid, _croot = _dependent(monkeypatch, tmp_path)
    aid, _vid = characters.create_character(wroot, "Winifred")
    doomed = greetings.create_greeting(wroot, "Arrival", aid, "default", "hi")
    other = greetings.create_greeting(wroot, "Departure", aid, "default", "bye")
    greetings.set_edges(wroot, other, leads_to=[doomed], excludes=[])
    overlay.materialize_plotmap(cid)

    greetings.delete_greeting(wroot, doomed)
    overlay.forget_world_record(wroot, "greetings", doomed)
    assert overlay.read_plotmap(cid)[other]["leads_to"] == []


def test_sweep_does_not_fork_a_campaign_onto_its_own_plot_map(monkeypatch, tmp_path):
    """A campaign reading the world's map must keep reading it -- materializing
    one here would fork it off over a delete it had nothing to do with."""
    wroot, cid, croot = _dependent(monkeypatch, tmp_path)
    aid, _vid = characters.create_character(wroot, "Winifred")
    doomed = greetings.create_greeting(wroot, "Arrival", aid, "default", "hi")
    (croot / "plotmap.json").unlink(missing_ok=True)
    campaigns.write_manifest(cid, {k: v for k, v in campaigns.read_manifest(cid).items()
                                   if k != "plotmap"})

    greetings.delete_greeting(wroot, doomed)
    overlay.forget_world_record(wroot, "greetings", doomed)
    assert not (croot / "plotmap.json").exists()


def test_campaign_side_greeting_delete_takes_its_directory_too(monkeypatch, tmp_path):
    """Same rule as `delete_entity`: a campaign-local greeting deleted
    campaign-side takes no tombstone, so its id is free again."""
    _wroot, cid, croot = _dependent(monkeypatch, tmp_path)
    aid, _vid = overlay.create_character(cid, "Winifred")
    gid = overlay.create_greeting(cid, "Arrival", aid, "default", "hello")
    assets.put_image(croot, gid, "default", "embed-1", PNG, "png", base="greetings")

    overlay.delete_greeting(cid, gid)
    assert not (croot / "greetings" / gid).exists()
    assert overlay.create_greeting(cid, "Arrival", aid, "default", "hi") == gid


def test_a_concurrent_recreate_is_not_swept_away(monkeypatch, tmp_path):
    """The id is free the instant the delete returns, so a create can publish a
    new world record before the sweep runs. The sweep must not remove it."""
    wroot, _cid, _croot = _dependent(monkeypatch, tmp_path)
    aid, _vid = characters.create_character(wroot, "Winifred")
    characters.delete_character(wroot, aid)
    again, _v = characters.create_character(wroot, "Winifred")     # lands first
    assert again == aid

    overlay.forget_world_record(wroot, "characters", aid)
    assert characters.read_character(wroot, aid)["meta"]["name"] == "Winifred"


def test_one_unreadable_manifest_does_not_cost_the_other_campaigns(monkeypatch, tmp_path):
    """`_drop_manifest_ref` reads sync.md; that read used to raise through the
    loop, so every LATER dependent kept its stale state (Codex review)."""
    wroot, _cid, _croot = _dependent(monkeypatch, tmp_path)
    gid = entities.create_entity(wroot, "groups", "Salt Circle", "the old circle")
    # "aaa" sorts before "zzz", so the broken one is swept first
    broken = campaigns.campaign_root(campaigns.create_campaign("Aaa", wroot.name))
    healthy = campaigns.campaign_root(campaigns.create_campaign("Zzz", wroot.name))
    for croot in (broken, healthy):
        overlay.materialize_entity(croot.name, "groups", gid)
        groupstate.write_state(croot, gid, "## Secrets\nThe abbot is a member.")
    (broken / "sync.md").write_bytes(b"---\n\xff\xfe: x\n---\n")

    entities.delete_entity(wroot, "groups", gid)
    overlay.forget_world_record(wroot, "groups", gid)             # does not raise
    assert f"groups/{gid}" not in campaigns.read_manifest(healthy.name)


def test_a_leftover_record_directory_reserves_its_slug(monkeypatch, tmp_path):
    """Actors have always keyed uniquify on the directory; flat records keyed
    only on the `.md`, so a new record could be handed an id whose assets and
    sidecars were still sitting there."""
    wroot, _cid, _croot = _dependent(monkeypatch, tmp_path)
    gid = entities.create_entity(wroot, "groups", "Salt Circle")
    assets.put_image(wroot, gid, "default", assets.AVATAR, PNG, "png", base="groups")
    (wroot / "groups" / f"{gid}.md").unlink()                     # hand-deleted; dir remains

    assert entities.create_entity(wroot, "groups", "Salt Circle") == f"{gid}-2"


# ---- Codex review: detaching a spared copy, in every direction the id reaches ----

def _spared(monkeypatch, tmp_path):
    """A campaign that owns a copy of a world character the world then deletes."""
    wroot, cid, croot = _dependent(monkeypatch, tmp_path)
    aid, vid = characters.create_character(wroot, "Winifred", "default",
                                           {"data": {"name": "Winifred", "description": "the old one"}})
    taglines.write(wroot, aid, "The old one.")
    assets.put_image(wroot, aid, vid, assets.AVATAR, PNG, "png")
    overlay.materialize_actor(cid, "characters", aid)
    return wroot, cid, croot, aid, vid


def test_a_spared_copy_is_recorded_as_detached(monkeypatch, tmp_path):
    wroot, cid, _croot, aid, _vid = _spared(monkeypatch, tmp_path)
    characters.delete_character(wroot, aid)
    overlay.forget_world_record(wroot, "characters", aid)
    assert f"characters/{aid}" in overlay.detached(cid)


def test_a_detached_record_takes_nothing_from_the_slug_s_next_owner(monkeypatch, tmp_path):
    """Images, tagline and voice anchor all resolve by id, independently of
    sync.md -- so dropping the base alone left three open doors."""
    wroot, cid, _croot, aid, vid = _spared(monkeypatch, tmp_path)
    characters.delete_character(wroot, aid)
    overlay.forget_world_record(wroot, "characters", aid)

    characters.create_character(wroot, "Winifred", "default",
                                {"data": {"name": "Winifred", "description": "a stranger"}})
    taglines.write(wroot, aid, "A stranger.")
    voice_anchors.write(wroot, aid, "The stranger's voice.")
    assets.put_image(wroot, aid, vid, assets.AVATAR, PNG + b"stranger", "png")

    assert overlay.tagline(cid, aid) == ""
    assert overlay.voice_anchor(cid, aid) == ""
    assert overlay.list_images(cid, aid, vid) == []
    assert overlay.image_root(cid, aid, vid, assets.AVATAR) == campaigns.campaign_root(cid)
    assert overlay.read_focus(cid, aid, vid) is None


def test_a_detached_version_lock_is_not_offered_the_strangers_card(monkeypatch, tmp_path):
    """The lock's base lives in appearances.json, not sync.md, so `accept` used
    to copy an unrelated world card over the locked version."""
    wroot, cid, croot, aid, vid = _spared(monkeypatch, tmp_path)
    appearances.appear(cid, "s1", "characters", aid, vid, "npc")
    playstate.write_state(croot, aid, "## Knows\nWhere the ledger is hidden.")

    characters.delete_character(wroot, aid)
    overlay.forget_world_record(wroot, "characters", aid)
    characters.create_character(wroot, "Winifred", "default",
                                {"data": {"name": "Winifred", "description": "a stranger"}})

    assert [p for p in sync.incoming(cid) if p["ref"]["id"] == aid] == []
    assert characters.read_card(croot, aid, vid)["data"]["description"] == "the old one"
    assert playstate.read_state(croot, aid)["knows"] == "Where the ledger is hidden."


def test_a_detached_record_does_not_promote_a_strangers_image(monkeypatch, tmp_path):
    wroot, cid, croot, aid, vid = _spared(monkeypatch, tmp_path)
    characters.delete_character(wroot, aid)
    overlay.forget_world_record(wroot, "characters", aid)
    characters.create_character(wroot, "Winifred")
    assets.put_image(wroot, aid, vid, "gallery_1", PNG + b"stranger", "png")

    with pytest.raises(FileNotFoundError):
        overlay.promote_image(cid, aid, vid, "gallery_1")
    assert overlay.list_images(cid, aid, vid) == []


def test_the_sweep_clears_asset_tombstones_of_the_record_it_removes(monkeypatch, tmp_path):
    """A per-asset tombstone hides by SLOT, so an avatar the campaign deleted
    for the old record would blank the next record-of-that-name's avatar."""
    wroot, cid, croot = _dependent(monkeypatch, tmp_path)
    aid, vid = characters.create_character(wroot, "Winifred")
    assets.put_image(wroot, aid, vid, assets.AVATAR, PNG, "png")
    overlay.delete_image(cid, aid, vid, assets.AVATAR)
    assert f"assets/characters/{aid}/{vid}/{assets.AVATAR}" in overlay.deleted(cid)

    characters.delete_character(wroot, aid)
    overlay.forget_world_record(wroot, "characters", aid)
    assert f"assets/characters/{aid}/{vid}/{assets.AVATAR}" not in overlay.deleted(cid)

    characters.create_character(wroot, "Winifred")
    assets.put_image(wroot, aid, vid, assets.AVATAR, PNG + b"new", "png")
    assert [i["name"] for i in overlay.list_images(cid, aid, vid)] == [assets.AVATAR]


def test_matching_plot_maps_do_not_become_a_conflict_with_itself(monkeypatch, tmp_path):
    """Both maps get the same edit, so they still match -- but sync.md holds the
    pre-delete hash, which showed as a conflict whose two sides were identical."""
    wroot, cid, croot = _dependent(monkeypatch, tmp_path)
    aid, _vid = characters.create_character(wroot, "Winifred")
    doomed = greetings.create_greeting(wroot, "Arrival", aid, "default", "hi")
    other = greetings.create_greeting(wroot, "Departure", aid, "default", "bye")
    greetings.set_edges(wroot, other, leads_to=[doomed], excludes=[])
    overlay.materialize_plotmap(cid)          # byte-identical to the world's

    greetings.delete_greeting(wroot, doomed)
    overlay.forget_world_record(wroot, "greetings", doomed)
    assert [p for p in sync.incoming(cid) if p["ref"]["kind"] == "plotmap"] == []


def test_a_malformed_campaign_plot_map_does_not_abort_the_sweep(monkeypatch, tmp_path):
    """`json.loads` raises JSONDecodeError -- a ValueError, not a decode error."""
    wroot, cid, croot = _dependent(monkeypatch, tmp_path)
    aid, _vid = characters.create_character(wroot, "Winifred")
    gid = greetings.create_greeting(wroot, "Arrival", aid, "default", "hi")
    overlay.materialize_plotmap(cid)
    (croot / "plotmap.json").write_text("{not json", encoding="utf-8")

    greetings.delete_greeting(wroot, gid)
    overlay.forget_world_record(wroot, "greetings", gid)      # does not raise


def test_a_leftover_greeting_directory_reserves_its_slug(monkeypatch, tmp_path):
    """The sweep is best-effort, so a locked asset can leave the directory."""
    wroot, _cid, _croot = _dependent(monkeypatch, tmp_path)
    aid, _vid = characters.create_character(wroot, "Winifred")
    gid = greetings.create_greeting(wroot, "Arrival", aid, "default", "hi")
    assets.put_image(wroot, gid, "default", "embed-1", PNG, "png", base="greetings")
    (wroot / "greetings" / f"{gid}.md").unlink()

    assert greetings.create_greeting(wroot, "Arrival", aid, "default", "hi") == f"{gid}-2"


def test_a_recreated_slug_stands_the_whole_sweep_down(monkeypatch, tmp_path):
    """Not just the world-side drop: once the id is back, nothing here can tell
    state written for the new record from the dead one's, so it deletes neither
    (and detaches nobody)."""
    wroot, cid, croot = _dependent(monkeypatch, tmp_path)
    gid = entities.create_entity(wroot, "groups", "Salt Circle")
    entities.delete_entity(wroot, "groups", gid)
    entities.create_entity(wroot, "groups", "Salt Circle")     # the race
    groupstate.write_state(croot, gid, "## Secrets\nabout the NEW circle")
    overlay.materialize_entity(cid, "groups", gid)

    overlay.forget_world_record(wroot, "groups", gid)
    assert groupstate.read_state(croot, gid)["secrets"] == "about the NEW circle"
    assert f"groups/{gid}" not in overlay.detached(cid)
    assert f"groups/{gid}" in campaigns.read_manifest(cid)


def test_the_detach_marker_lands_before_the_base_is_dropped(monkeypatch, tmp_path):
    """A crash between the two writes must leave the inert state, not the one
    where sync thinks the record is campaign-local and the resolvers do not."""
    wroot, cid, _croot, aid, _vid = _spared(monkeypatch, tmp_path)
    seen = []
    real = campaigns.write_manifest

    def record_order(c, manifest):
        seen.append(("base", sorted(overlay.detached(c))))
        return real(c, manifest)

    monkeypatch.setattr(campaigns, "write_manifest", record_order)
    monkeypatch.setattr(overlay.campaigns_paths, "write_manifest", record_order)
    characters.delete_character(wroot, aid)
    overlay.forget_world_record(wroot, "characters", aid)

    assert seen and f"characters/{aid}" in seen[0][1]   # detached first


def test_deleting_a_detached_copy_takes_its_detachment_with_it(monkeypatch, tmp_path):
    """The marker describes a record, not a slug -- outliving the record, it
    would hide the next world record of that name's images and sidecars."""
    wroot, cid, croot, aid, vid = _spared(monkeypatch, tmp_path)
    gid = entities.create_entity(wroot, "groups", "Salt Circle")
    overlay.materialize_entity(cid, "groups", gid)
    entities.delete_entity(wroot, "groups", gid)
    overlay.forget_world_record(wroot, "groups", gid)
    assert f"groups/{gid}" in overlay.detached(cid)

    overlay.delete_entity(cid, "groups", gid)
    assert f"groups/{gid}" not in overlay.detached(cid)

    again = entities.create_entity(wroot, "groups", "Salt Circle")
    assets.put_image(wroot, again, "default", assets.AVATAR, PNG, "png", base="groups")
    assert [i["name"] for i in overlay.list_images(cid, again, "default", base="groups")] \
        == [assets.AVATAR]


# ---- Codex review: what "detached" has to mean everywhere the id is read ----

def test_accept_refuses_a_detached_ref_from_the_request_body(monkeypatch, tmp_path):
    """`incoming` filters them; accept/reject take theirs from the caller."""
    wroot, cid, croot, aid, _vid = _spared(monkeypatch, tmp_path)
    characters.delete_character(wroot, aid)
    overlay.forget_world_record(wroot, "characters", aid)
    characters.create_character(wroot, "Winifred", "default",
                                {"data": {"name": "Winifred", "description": "a stranger"}})

    sync.accept(cid, [{"kind": "characters", "id": aid}])       # stale ref, submitted anyway
    assert overlay.char_root(cid, aid) == croot                 # copy still the campaign's
    assert characters.read_card(croot, aid, "default")["data"]["description"] == "the old one"


def test_a_detached_greeting_takes_no_edges_from_an_inherited_plot_map(monkeypatch, tmp_path):
    """The campaign owns the greeting but not a plot map, so it reads the
    world's -- where the recreated slug's edges live."""
    wroot, cid, croot = _dependent(monkeypatch, tmp_path)
    aid, _vid = characters.create_character(wroot, "Winifred")
    gid = greetings.create_greeting(wroot, "Arrival", aid, "default", "hi")
    overlay.update_greeting(cid, gid, body="the campaign's own")   # materializes the greeting
    (croot / "plotmap.json").unlink(missing_ok=True)

    greetings.delete_greeting(wroot, gid)
    overlay.forget_world_record(wroot, "greetings", gid)
    assert f"greetings/{gid}" in overlay.detached(cid)

    again = greetings.create_greeting(wroot, "Arrival", aid, "default", "a stranger")
    other = greetings.create_greeting(wroot, "Departure", aid, "default", "bye")
    greetings.set_edges(wroot, other, leads_to=[again], excludes=[])
    assert again == gid
    plotmap = overlay.read_plotmap(cid)
    assert gid not in plotmap
    assert plotmap[other]["leads_to"] == []


def test_deleting_a_detached_copy_does_not_tombstone_the_stranger(monkeypatch, tmp_path):
    """`in_world` would be true only because of the replacement, and the
    tombstone would hide it from this campaign forever."""
    wroot, cid, croot = _dependent(monkeypatch, tmp_path)
    gid = entities.create_entity(wroot, "groups", "Salt Circle", "the old circle")
    overlay.materialize_entity(cid, "groups", gid)
    entities.delete_entity(wroot, "groups", gid)
    overlay.forget_world_record(wroot, "groups", gid)
    entities.create_entity(wroot, "groups", "Salt Circle", "a stranger")

    overlay.delete_entity(cid, "groups", gid)
    assert f"groups/{gid}" not in overlay.deleted(cid)
    assert overlay.read_entity(cid, "groups", gid)["body"] == "a stranger"   # inherits again


def test_a_campaign_create_will_not_claim_a_leftover_world_directory(monkeypatch, tmp_path):
    """The sweep is best-effort; what it leaves in the WORLD would come through
    the overlay the moment a campaign claimed the same slug."""
    wroot, cid, _croot = _dependent(monkeypatch, tmp_path)
    gid = entities.create_entity(wroot, "groups", "Salt Circle")
    assets.put_image(wroot, gid, "default", assets.AVATAR, PNG, "png", base="groups")
    (wroot / "groups" / f"{gid}.md").unlink()          # record gone, directory stranded

    assert overlay.create_entity(cid, "groups", "Salt Circle") == f"{gid}-2"


def test_the_sweep_rechecks_the_campaign_it_is_about_to_touch(monkeypatch, tmp_path):
    """Campaign ids are reusable: the one enumerated may be a different campaign,
    on a different world, by the time its turn comes."""
    wroot, cid, croot = _dependent(monkeypatch, tmp_path)
    gid = entities.create_entity(wroot, "groups", "Salt Circle")
    groupstate.write_state(croot, gid, "## Secrets\nThe abbot is a member.")
    other_wid = worlds.create_world("Saltmarch")

    real = overlay._forget_in_campaign

    def restage(c, kind, rid, w):
        # the campaign is replaced by one of another world between enumeration
        # and its turn; the id is the same
        meta = campaigns.campaign_meta_path(c)
        meta.write_text(meta.read_text(encoding="utf-8").replace(
            f"world: {wroot.name}", f"world: {other_wid}"), encoding="utf-8")
        return real(c, kind, rid, w)

    monkeypatch.setattr(overlay, "_forget_in_campaign", restage)
    entities.delete_entity(wroot, "groups", gid)
    overlay.forget_world_record(wroot, "groups", gid)
    assert groupstate.read_state(croot, gid) is not None


# ---- Codex review: detachment must not overreach either ----

def test_a_campaigns_own_plot_map_keeps_its_detached_greetings_edges(monkeypatch, tmp_path):
    """Filtering belongs to an INHERITED map. A campaign-local one names the
    campaign's own greetings, and its edges are relationships it authored."""
    wroot, cid, croot = _dependent(monkeypatch, tmp_path)
    aid, _vid = characters.create_character(wroot, "Winifred")
    gid = greetings.create_greeting(wroot, "Arrival", aid, "default", "hi")
    other = greetings.create_greeting(wroot, "Departure", aid, "default", "bye")
    overlay.update_greeting(cid, gid, body="the campaign's own")
    overlay.set_edges(cid, other, leads_to=[gid])          # materializes the campaign map

    greetings.delete_greeting(wroot, gid)
    overlay.forget_world_record(wroot, "greetings", gid)
    assert f"greetings/{gid}" in overlay.detached(cid)
    assert overlay.read_plotmap(cid)[other]["leads_to"] == [gid]   # still the campaign's


def test_reattaching_clears_asset_tombstones_left_while_detached(monkeypatch, tmp_path):
    wroot, cid, croot = _dependent(monkeypatch, tmp_path)
    gid = entities.create_entity(wroot, "groups", "Salt Circle")
    overlay.materialize_entity(cid, "groups", gid)
    entities.delete_entity(wroot, "groups", gid)
    overlay.forget_world_record(wroot, "groups", gid)

    again = entities.create_entity(wroot, "groups", "Salt Circle")
    assets.put_image(wroot, again, "default", assets.AVATAR, PNG, "png", base="groups")
    overlay.delete_image(cid, gid, "default", assets.AVATAR, base="groups")   # hides the slot

    overlay.delete_entity(cid, "groups", gid)             # reattaches to the replacement
    assert [i["name"] for i in overlay.list_images(cid, again, "default", base="groups")] \
        == [assets.AVATAR]


def test_saving_a_voice_anchor_on_a_detached_character_is_not_a_no_op(monkeypatch, tmp_path):
    """The no-op shortcut compares against the INHERITED anchor -- a stranger's,
    once the slug has been recreated, so the save was silently discarded."""
    wroot, cid, croot, aid, _vid = _spared(monkeypatch, tmp_path)
    characters.delete_character(wroot, aid)
    overlay.forget_world_record(wroot, "characters", aid)
    characters.create_character(wroot, "Winifred")
    voice_anchors.write(wroot, aid, "The stranger's voice.")

    overlay.set_voice_anchor(cid, aid, "The stranger's voice.")   # same words, different record
    assert overlay.voice_anchor(cid, aid) == "The stranger's voice."
    assert voice_anchors.read(croot, aid) == "The stranger's voice."   # actually persisted


def test_a_ledger_entry_that_is_not_a_string_does_not_500(monkeypatch, tmp_path):
    """`failsoft` checks the outer type only, so `[1]` reads as a good list and
    every ref.startswith() downstream raised out of a fail-soft read."""
    wroot, cid, croot = _dependent(monkeypatch, tmp_path)
    (croot / "detached.json").write_text("[1, \"groups/salt-circle\"]", encoding="utf-8")
    (croot / "deleted.json").write_text("[2]", encoding="utf-8")

    assert overlay.detached(cid) == {"groups/salt-circle"}
    assert overlay.deleted(cid) == set()
    assert overlay.read_plotmap(cid) == {}                 # the read that used to raise


def test_a_campaign_deleted_mid_sweep_is_skipped_not_raised(monkeypatch, tmp_path):
    """The revalidation reads campaign.md, and the campaign may be gone by then
    -- `CampaignNotFound` is neither an OSError nor a ValueError, so it escaped
    the per-campaign handler and 500'd a completed delete (Codex review)."""
    wroot, cid, croot = _dependent(monkeypatch, tmp_path)
    gid = entities.create_entity(wroot, "groups", "Salt Circle")
    doomed = campaigns.create_campaign("Aaa", wroot.name)   # sorts first, swept first
    groupstate.write_state(croot, gid, "## Secrets\nThe abbot is a member.")

    real = overlay._forget_in_campaign

    def vanish(c, kind, rid, w):
        if c == doomed:
            campaigns.delete_campaign(doomed)
        return real(c, kind, rid, w)

    monkeypatch.setattr(overlay, "_forget_in_campaign", vanish)
    entities.delete_entity(wroot, "groups", gid)
    overlay.forget_world_record(wroot, "groups", gid)       # does not raise
    assert groupstate.read_state(croot, gid) is None        # the survivor was still swept
