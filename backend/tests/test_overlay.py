from pathlib import Path

import pytest

from grimoire.store import (assets, atomic, campaigns, characters, entities, greetings, overlay,
                            pcs, sync, taglines, worlds)


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
