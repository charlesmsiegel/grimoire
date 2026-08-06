import shutil
from pathlib import Path

import pytest

from grimoire.store import (appearances, assets, atomic, campaigns, characters, entities,
                            greetings, overlay, pcs, worlds)
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter


def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def _stamp_full(cid: str) -> None:
    """Mark a campaign as pre-overlay full-copy, the state ensure_campaign_slim migrates from."""
    mp = campaigns.campaign_meta_path(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["world_copy"] = "full"
    mp.write_text(dump_frontmatter(meta, body), encoding="utf-8")


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


def test_delete_world_blocked_while_campaigns_reference_it(monkeypatch, tmp_path):
    """Deleting a world that campaigns still reference would strand inherited
    content; the guard blocks the delete and names the offending campaigns."""
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("C", wid)
    with pytest.raises(worlds.WorldInUse) as exc_info:
        worlds.delete_world(wid)
    assert "C" in exc_info.value.names
    assert worlds.world_root(wid).exists()  # world still exists
    campaigns.delete_campaign(cid)
    worlds.delete_world(wid)  # now allowed
    assert not worlds.world_root(wid).exists()


def test_deleting_world_leaves_campaign_metadata_intact(monkeypatch, tmp_path):
    """A thin campaign leans on the world for anything never materialized.
    With the world-deletion guard, this test now verifies the guard in action:
    deletion is blocked while campaigns reference the world."""
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    entities.create_entity(worlds.world_root(wid), "locations", "Seraphine", "Keeper.")
    cid = campaigns.create_campaign("Run", wid)
    # deletion is blocked
    with pytest.raises(worlds.WorldInUse):
        worlds.delete_world(wid)
    assert worlds.world_root(wid).exists()
    # after deleting the campaign, deletion is allowed
    campaigns.delete_campaign(cid)
    worlds.delete_world(wid)
    assert not worlds.world_root(wid).exists()


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
    _stamp_full(cid)
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

    # second run: no-op — the campaign dir tree, manifest, and tombstones are unchanged
    before_files = sorted(p.relative_to(croot) for p in croot.rglob("*") if p.is_file())
    before_manifest = campaigns.read_manifest(cid)
    before_deleted = overlay.deleted(cid)
    campaigns.ensure_campaign_slim(cid)
    after_files = sorted(p.relative_to(croot) for p in croot.rglob("*") if p.is_file())
    assert after_files == before_files
    assert campaigns.read_manifest(cid) == before_manifest
    assert overlay.deleted(cid) == before_deleted


def test_slim_prunes_duplicate_greeting_asset(monkeypatch, tmp_path):
    """A pre-overlay full-copy campaign that copied a greeting AND a
    byte-identical copy of one of its images: slim must prune the redundant
    greeting asset (like it already does for characters/pcs/locations/lore),
    so the campaign copy doesn't permanently shadow world-side edits to that
    image."""
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    aid, vid = characters.create_character(wroot, "Hero")
    g = greetings.create_greeting(wroot, "Gala", aid, vid, body="Hi.")
    greetings.set_edges(wroot, g, leads_to=[])
    assets.put_image(wroot, g, "default", "art_1", b"artbytes", "png", base="greetings")
    cid = campaigns.create_campaign("C", wid)
    croot = campaigns.campaign_root(cid)
    (croot / "greetings").mkdir()
    (croot / "greetings" / f"{g}.md").write_text(
        (wroot / "greetings" / f"{g}.md").read_text(encoding="utf-8"), encoding="utf-8")
    assets.put_image(croot, g, "default", "art_1", b"artbytes", "png", base="greetings")
    manifest = {f"greetings/{g}": entities.entity_hash(wroot, "greetings", g)}
    campaigns.write_manifest(cid, manifest)
    _stamp_full(cid)

    campaigns.ensure_campaign_slim(cid)

    assert assets.image_path(croot, g, "default", "art_1", base="greetings") is None  # pruned
    assert overlay.image_root(cid, g, "default", "art_1", base="greetings") == wroot   # served from world


def test_slim_keeps_focus_when_campaign_avatar_diverges(monkeypatch, tmp_path):
    """A focus sidecar byte-identical to the world's is still not redundant when
    the campaign avatar beside it diverges: overlay.read_focus treats that avatar
    as authoritative, so pruning the sidecar would silently reset the crop to
    center. Slim must keep it."""
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    aid, vid = characters.create_character(wroot, "Hero")
    assets.put_image(wroot, aid, vid, "avatar", b"worldavatar", "png")
    assets.write_focus(wroot, aid, vid, 80)
    cid = campaigns.create_campaign("C", wid)
    croot = campaigns.campaign_root(cid)
    overlay.materialize_actor(cid, "characters", aid)
    assets.put_image(croot, aid, vid, "avatar", b"campaignavatar", "png")   # divergent
    assets.write_focus(croot, aid, vid, 80)                                 # same bytes as world
    campaigns.write_manifest(cid, {f"characters/{aid}": characters.dir_hash(wroot, aid)})
    _stamp_full(cid)

    campaigns.ensure_campaign_slim(cid)

    assert assets.read_focus(croot, aid, vid) == 80   # sidecar survives
    assert overlay.read_focus(cid, aid, vid) == 80    # crop preserved, not reset to center


def test_slim_tombstones_user_deleted_copied_asset(monkeypatch, tmp_path):
    """A pre-overlay full copy had the world avatar copied in; the user deleted
    that copy. Slim must tombstone it so the overlay doesn't resurface the world
    image once world_copy flips to overlay."""
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    aid, vid = characters.create_character(wroot, "Hero")
    assets.put_image(wroot, aid, vid, "avatar", b"\x89PNG\r\n\x1a\nx", "png")
    cid = campaigns.create_campaign("C", wid)
    croot = campaigns.campaign_root(cid)
    shutil.copytree(wroot / "characters" / aid, croot / "characters" / aid)   # full copy
    for p in (croot / "characters" / aid / "assets" / vid).glob("avatar.*"):
        p.unlink()                                                            # user deletes it
    campaigns.write_manifest(cid, {f"characters/{aid}": characters.dir_hash(wroot, aid)})
    _stamp_full(cid)

    campaigns.ensure_campaign_slim(cid)

    assert f"assets/characters/{aid}/{vid}/avatar" in overlay.deleted(cid)
    assert overlay.image_root(cid, aid, vid, "avatar") == croot   # tombstoned, not world
    assert "avatar" not in {i["name"] for i in overlay.list_images(cid, aid, vid)}


def test_slim_keeps_inherited_world_asset_live(monkeypatch, tmp_path):
    """A world asset the campaign never copied (added to an inherited, never-
    materialized actor) must stay live-inherited after slim, not be tombstoned:
    only records the full copy tracked are candidates for deletion tombstones."""
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    aid, vid = characters.create_character(wroot, "Hero")
    assets.put_image(wroot, aid, vid, "avatar", b"\x89PNG\r\n\x1a\nx", "png")
    cid = campaigns.create_campaign("C", wid)   # thin: never copied this actor
    _stamp_full(cid)                            # but stamped legacy full-copy

    campaigns.ensure_campaign_slim(cid)

    assert f"assets/characters/{aid}/{vid}/avatar" not in overlay.deleted(cid)
    assert overlay.image_root(cid, aid, vid, "avatar") == wroot   # still inherited


def test_slim_skips_when_world_missing(monkeypatch, tmp_path):
    wroot, cid, *_ = _fat_campaign(monkeypatch, tmp_path)
    shutil.rmtree(wroot)
    campaigns.ensure_campaign_slim(cid)
    assert campaigns.read_campaign(cid)["meta"]["world_copy"] == "full"  # untouched, retried later


def test_slim_tombstones_user_deleted_plotmap(monkeypatch, tmp_path):
    """A pre-overlay campaign whose user deleted their local plotmap.json copy
    (world still has one) must come out of slim with the deletion preserved —
    not resurrected through the overlay."""
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    aid, vid = characters.create_character(wroot, "Hero")
    g = greetings.create_greeting(wroot, "Gala", aid, vid, body="Hi.")
    greetings.set_edges(wroot, g, leads_to=[])
    cid = campaigns.create_campaign("C", wid)
    croot = campaigns.campaign_root(cid)
    (croot / "plotmap.json").write_text(
        (wroot / "plotmap.json").read_text(encoding="utf-8"), encoding="utf-8")
    campaigns.write_manifest(cid, {"plotmap": greetings.plotmap_hash(wroot)})
    (croot / "plotmap.json").unlink()   # user deleted their campaign-side copy
    _stamp_full(cid)

    campaigns.ensure_campaign_slim(cid)

    assert "plotmap" in overlay.deleted(cid)
    assert overlay.read_plotmap(cid) == {}   # campaign-side (empty) map, not the world's
    assert "plotmap" not in campaigns.read_manifest(cid)


def test_slim_tombstones_user_deleted_actor(monkeypatch, tmp_path):
    """A pre-overlay campaign whose user deleted their local actor copy (world
    still has that actor) must come out of slim with the deletion preserved."""
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    aid, vid = characters.create_character(wroot, "Hero")
    cid = campaigns.create_campaign("C", wid)
    croot = campaigns.campaign_root(cid)
    shutil.copytree(wroot / "characters" / aid, croot / "characters" / aid)
    ref = f"characters/{aid}"
    campaigns.write_manifest(cid, {ref: characters.dir_hash(wroot, aid)})
    shutil.rmtree(croot / "characters" / aid)   # user deleted their campaign-side copy
    _stamp_full(cid)

    campaigns.ensure_campaign_slim(cid)

    assert ref in overlay.deleted(cid)
    assert ref not in campaigns.read_manifest(cid)
    assert aid not in [c["id"] for c in overlay.list_characters(cid)]


def test_slim_keeps_locked_actor_card_and_drops_ref(monkeypatch, tmp_path):
    """A pre-overlay campaign can hold both a leftover full-copy sync.md entry
    for an actor *and* an appearances.json lock for it (the lock was picked
    during play, under the old code that never cleared the sync entry). Slim
    must keep the locked card/version on disk and only drop the manifest ref
    — the lock invariant (appearances.json) owns the base from here on."""
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    aid, vid = characters.create_character(wroot, "Hero")
    characters.create_version(wroot, aid, "grim", characters.blank_card("Hero"))
    cid = campaigns.create_campaign("C", wid)
    croot = campaigns.campaign_root(cid)
    shutil.copytree(wroot / "characters" / aid, croot / "characters" / aid)
    ref = f"characters/{aid}"
    campaigns.write_manifest(cid, {ref: characters.dir_hash(wroot, aid)})
    appearances.appear(cid, "s1", "characters", aid, vid, "npc")   # locks + purges siblings
    # appear() already drops the manifest ref via its modern _lock helper;
    # restore it to look like a legacy campaign whose old sync entry lingered
    campaigns.write_manifest(cid, {ref: characters.dir_hash(wroot, aid)})
    _stamp_full(cid)

    campaigns.ensure_campaign_slim(cid)

    assert (croot / "characters" / aid / "character.md").exists()
    assert (croot / "characters" / aid / f"{vid}.json").exists()
    assert ref not in campaigns.read_manifest(cid)


def test_slim_keeps_diverged_actor_and_ref(monkeypatch, tmp_path):
    """An actor whose campaign copy has diverged from the world (edited
    campaign-side) must survive slim untouched: card kept, manifest ref kept."""
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    aid, vid = characters.create_character(wroot, "Hero")
    cid = campaigns.create_campaign("C", wid)
    croot = campaigns.campaign_root(cid)
    shutil.copytree(wroot / "characters" / aid, croot / "characters" / aid)
    ref = f"characters/{aid}"
    campaigns.write_manifest(cid, {ref: characters.dir_hash(wroot, aid)})
    characters.update_version(croot, aid, vid, characters.blank_card("Hero (edited)"))
    _stamp_full(cid)

    campaigns.ensure_campaign_slim(cid)

    assert (croot / "characters" / aid / "character.md").exists()
    assert (croot / "characters" / aid / f"{vid}.json").exists()
    assert ref in campaigns.read_manifest(cid)


def test_slim_interrupted_mid_prune_does_not_tombstone_what_it_pruned(monkeypatch, tmp_path):
    """#270. Slim used to unlink redundant copies inside the loop and persist
    the pruned manifest only afterwards, so anything short of finishing — a
    crash, a kill, an OSError out of the manifest write — left every copy it
    had already dropped indistinguishable from a record the user had deleted.
    Its own next run then tombstoned them, hiding inherited records from a
    campaign whose only misfortune was being interrupted."""
    _wroot, cid, same, diverged, removed, aid, _vid = _fat_campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)

    class Interrupted(Exception):
        pass

    def boom(*_a, **_kw):
        raise Interrupted
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(campaigns.paths, "write_manifest", boom)
        with pytest.raises(Interrupted):
            campaigns.ensure_campaign_slim(cid)
    # the pruning never started: the manifest still names every ref it did
    assert f"lore/{same}" in campaigns.read_manifest(cid)
    assert (croot / "lore" / f"{same}.md").exists()

    campaigns.ensure_campaign_slim(cid)

    assert f"lore/{same}" not in overlay.deleted(cid)          # not read as a deletion
    assert f"characters/{aid}" not in overlay.deleted(cid)
    assert overlay.read_entity(cid, "lore", same)["body"] == "same"   # still inherited
    assert f"lore/{removed}" in overlay.deleted(cid)           # the real deletion survives
    assert (croot / "lore" / f"{diverged}.md").exists()        # divergence untouched
    assert campaigns.read_campaign(cid)["meta"]["world_copy"] == "overlay"


#: Interruption points the sweep below walks. Must exceed the number of
#: operations a full migration performs -- pinned by
#: `test_the_interruption_sweep_reaches_the_end_of_the_migration`.
SWEEP_POINTS = 20


def _fatter_campaign(monkeypatch, tmp_path):
    """`_fat_campaign` plus every other shape the migration classifies: a plot
    map, a greeting, a PC, a locked actor, a record the campaign owns, and a
    *diverged* actor carrying a byte-identical asset copy — the one shape whose
    absence hid an asset-tombstoning bug from the sweep below (Codex review)."""
    wroot, cid, same, diverged, removed, aid, vid = _fat_campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    dvid, dver = characters.create_character(wroot, "Mara")
    assets.put_image(wroot, dvid, dver, "avatar", b"\x89PNG\r\n\x1a\nmara", "png")
    shutil.copytree(wroot / "characters" / dvid, croot / "characters" / dvid)
    g = greetings.create_greeting(wroot, "Gala", aid, vid, body="Hi.")
    greetings.set_edges(wroot, g, leads_to=[], excludes=[])
    pid, _ = pcs.create_pc(wroot, "Elara", [])
    lid, lvid = characters.create_character(wroot, "Seraphine")
    manifest = campaigns.read_manifest(cid)
    (croot / "greetings").mkdir()
    (croot / "greetings" / f"{g}.md").write_text(
        (wroot / "greetings" / f"{g}.md").read_text(encoding="utf-8"), encoding="utf-8")
    manifest[f"greetings/{g}"] = entities.entity_hash(wroot, "greetings", g)
    shutil.copy(wroot / "plotmap.json", croot / "plotmap.json")
    manifest["plotmap"] = greetings.plotmap_hash(wroot)
    shutil.copytree(wroot / "pcs" / pid, croot / "pcs" / pid)
    manifest[f"pcs/{pid}"] = pcs.dir_hash(wroot, pid)
    shutil.copytree(wroot / "characters" / lid, croot / "characters" / lid)
    manifest[f"characters/{lid}"] = characters.dir_hash(wroot, lid)
    manifest[f"characters/{dvid}"] = characters.dir_hash(wroot, dvid)
    campaigns.write_manifest(cid, manifest)
    appearances.appear(cid, "s1", "characters", lid, lvid, "npc")   # locks, drops its ref
    characters.update_version(croot, dvid, dver, characters.blank_card("Mara (edited)"))
    mine = entities.create_entity(croot, "lore", "Winifred", "Campaign only.")
    _stamp_full(cid)
    return dict(cid=cid, same=same, diverged=diverged, removed=removed, aid=aid,
                g=g, pid=pid, lid=lid, mine=mine, dvid=dvid, dver=dver)


@pytest.mark.parametrize("what", ["write", "unlink"])
@pytest.mark.parametrize("stop_at", range(SWEEP_POINTS))
def test_no_interruption_of_the_migration_tombstones_a_live_record(monkeypatch, tmp_path,
                                                                   stop_at, what):
    """The invariant behind #270, swept: stop the migration at each of its
    writes and each of its unlinks in turn, let it run again, and no record the
    user did not delete may end up tombstoned or become unreadable.

    Both spies are needed. The writes alone miss everything inside a copy drop
    — including `dematerialize_actor`'s two-unlink window, which left a
    character that listed but could not be opened (Codex review)."""
    f = _fatter_campaign(monkeypatch, tmp_path)
    cid = f["cid"]

    class Interrupted(Exception):
        pass

    n = [0]
    real_write, real_unlink = atomic.write_text, Path.unlink

    def counted(kind, real):
        def spy(*args, **kwargs):
            if kind == what and n[0] == stop_at:
                raise Interrupted
            n[0] += 1
            return real(*args, **kwargs)
        return spy

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(atomic, "write_text", counted("write", real_write))
        mp.setattr(Path, "unlink", counted("unlink", real_unlink))
        try:
            campaigns.ensure_campaign_slim(cid)
        except Interrupted:
            pass

    campaigns.ensure_campaign_slim(cid)
    campaigns.ensure_campaign_slim(cid)   # and again: the recovery is idempotent

    dead = overlay.deleted(cid)
    for ref in (f"lore/{f['same']}", f"lore/{f['diverged']}", f"lore/{f['mine']}",
                f"characters/{f['aid']}", f"characters/{f['lid']}", f"pcs/{f['pid']}",
                f"greetings/{f['g']}", f"characters/{f['dvid']}", "plotmap"):
        assert ref not in dead, f"{ref} tombstoned"
    assert not [r for r in dead if r.startswith("assets/")], f"asset tombstoned: {sorted(dead)}"
    assert f"lore/{f['removed']}" in dead                              # the real deletion stands
    # the world's image is still the campaign's, not hidden behind a tombstone
    assert [i["name"] for i in overlay.list_images(cid, f["dvid"], f["dver"])] == ["avatar"]
    # …and every live record still reads, with the content it had
    assert overlay.read_entity(cid, "lore", f["same"])["body"] == "same"
    assert overlay.read_entity(cid, "lore", f["diverged"])["body"] == "campaign text"
    assert overlay.read_entity(cid, "lore", f["mine"])["body"] == "Campaign only."
    assert overlay.read_character(cid, f["aid"])["meta"]["name"] == "Hero"
    assert overlay.read_character(cid, f["lid"])["meta"]["name"] == "Seraphine"
    assert pcs.read_pc(overlay.pc_root(cid, f["pid"]), f["pid"])["meta"]["name"] == "Elara"
    assert overlay.read_greeting(cid, f["g"])["body"].strip() == "Hi."
    assert campaigns.read_campaign(cid)["meta"]["world_copy"] == "overlay"


def test_the_interruption_sweep_reaches_the_end_of_the_migration(monkeypatch, tmp_path):
    """`SWEEP_POINTS` is only meaningful if it exceeds the number of operations
    a full migration performs — otherwise the tail of the migration silently
    stops being swept as the fixture grows, with no signal. That tail is where
    the asset-tombstoning bug lived (Codex review)."""
    f = _fatter_campaign(monkeypatch, tmp_path)
    n = [0]
    real_write, real_unlink = atomic.write_text, Path.unlink

    def counted(real):
        def spy(*args, **kwargs):
            n[0] += 1
            return real(*args, **kwargs)
        return spy

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(atomic, "write_text", counted(real_write))
        mp.setattr(Path, "unlink", counted(real_unlink))
        campaigns.ensure_campaign_slim(f["cid"])
    assert 0 < n[0] <= SWEEP_POINTS, f"migration takes {n[0]} operations; widen SWEEP_POINTS"


def test_slim_sweep_keeps_an_untracked_copy_that_differs_from_the_world(monkeypatch, tmp_path):
    """Byte-identity is the only thing between the sweep and a record the
    campaign owns, so it needs a case where the world holds the ref and the
    contents differ — the half `test_slim_sweep_keeps_a_campaign_local_record`
    cannot reach, because its record has no world counterpart at all. Drop the
    comparison and keep only "the world has this ref" and this fails (Codex
    review)."""
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    eid = entities.create_entity(wroot, "lore", "Saltmarch", "World text.")
    aid, ver = characters.create_character(wroot, "Mara")
    cid = campaigns.create_campaign("C", wid)
    croot = campaigns.campaign_root(cid)
    (croot / "lore").mkdir()
    (croot / "lore" / f"{eid}.md").write_text(
        (wroot / "lore" / f"{eid}.md").read_text(encoding="utf-8"), encoding="utf-8")
    entities.update_entity(croot, "lore", eid, body="Campaign text.")
    shutil.copytree(wroot / "characters" / aid, croot / "characters" / aid)
    characters.update_version(croot, aid, ver, characters.blank_card("Mara (edited)"))
    campaigns.write_manifest(cid, {})            # …and nothing names either of them
    _stamp_full(cid)

    campaigns.ensure_campaign_slim(cid)

    assert (croot / "lore" / f"{eid}.md").exists()
    assert (croot / "characters" / aid / "character.md").exists()
    assert overlay.read_entity(cid, "lore", eid)["body"] == "Campaign text."
    card = characters.read_card(overlay.char_root(cid, aid), aid, ver)
    assert card["data"]["name"] == "Mara (edited)"   # the campaign's edit, not the world's card
    assert campaigns.read_manifest(cid) == {}    # kept, and still not adopted


def test_slim_does_not_tombstone_a_ref_the_fork_recorded_no_hash_for(monkeypatch, tmp_path):
    """Both historical writers stored `<world hash> or ""`, so a campaign forked
    from a world with no plot map carries `plotmap: ''` and no plotmap.json —
    a ref with no copy, on disk, put there by the fork and not by any
    interruption. Read as a deletion it tombstones the world's plot map out of
    the campaign the moment the world gains one, which is #270's own symptom
    (Codex review)."""
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    aid, vid = characters.create_character(wroot, "Hero")
    cid = campaigns.create_campaign("C", wid)
    campaigns.write_manifest(cid, {"plotmap": ""})   # forked before the world had one
    _stamp_full(cid)
    g = greetings.create_greeting(wroot, "Gala", aid, vid, body="Hi.")
    greetings.set_edges(wroot, g, leads_to=["next"], excludes=[])   # …and now it has

    campaigns.ensure_campaign_slim(cid)

    assert "plotmap" not in overlay.deleted(cid)
    assert overlay.read_plotmap(cid) == greetings.read_plotmap(wroot)


def test_slim_prunes_a_copy_the_manifest_does_not_name(monkeypatch, tmp_path):
    """The other half of #270's ordering: every writer drops the manifest ref
    before the copy while the migration is pending, so an interrupted one
    leaves a copy sync.md does not name. Left alone it would survive as a
    materialized record with no base — readable, but never syncing again and
    never saying so. Slim sweeps it when the world still holds it identically."""
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    eid = entities.create_entity(wroot, "lore", "Saltmarch", "Tides.")
    aid, _vid = characters.create_character(wroot, "Mara")
    cid = campaigns.create_campaign("C", wid)
    croot = campaigns.campaign_root(cid)
    (croot / "lore").mkdir()
    (croot / "lore" / f"{eid}.md").write_text(
        (wroot / "lore" / f"{eid}.md").read_text(encoding="utf-8"), encoding="utf-8")
    shutil.copytree(wroot / "characters" / aid, croot / "characters" / aid)
    campaigns.write_manifest(cid, {})   # …and the ref that named them is already gone
    _stamp_full(cid)

    campaigns.ensure_campaign_slim(cid)

    assert not (croot / "lore" / f"{eid}.md").exists()
    assert not (croot / "characters" / aid / "character.md").exists()
    assert not overlay.deleted(cid)
    assert overlay.read_entity(cid, "lore", eid)["body"] == "Tides."   # inherited again
    assert campaigns.read_manifest(cid) == {}


def test_slim_sweep_keeps_a_campaign_local_record(monkeypatch, tmp_path):
    """The sweep is keyed on identity with the world, which is what stops it
    taking a record the campaign owns: an edit diverged it, and a record
    created campaign-side has no world counterpart to be identical to."""
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("C", wid)
    croot = campaigns.campaign_root(cid)
    mine = entities.create_entity(croot, "lore", "Winifred", "Campaign only.")
    campaigns.write_manifest(cid, {})
    _stamp_full(cid)

    campaigns.ensure_campaign_slim(cid)

    assert (croot / "lore" / f"{mine}.md").exists()
    assert overlay.read_entity(cid, "lore", mine)["body"] == "Campaign only."
    assert campaigns.read_manifest(cid) == {}   # nor a base: there is no world record to have one against


def test_slim_sweep_keeps_a_locked_actors_card(monkeypatch, tmp_path):
    """A lock drops the actor's manifest ref itself (`appearances.versions._lock`
    calls `_drop_manifest_ref`), so a locked actor's card is genuinely untracked
    by the time the sweep runs — and identical to the world when the lock kept
    the only version. Without the `locked` term the sweep takes exactly the
    files the lock invariant needs, so this must NOT put the ref back: doing so
    was what left the term unexercised (Codex review)."""
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    aid, vid = characters.create_character(wroot, "Hero")
    cid = campaigns.create_campaign("C", wid)
    croot = campaigns.campaign_root(cid)
    shutil.copytree(wroot / "characters" / aid, croot / "characters" / aid)
    campaigns.write_manifest(cid, {f"characters/{aid}": characters.dir_hash(wroot, aid)})
    appearances.appear(cid, "s1", "characters", aid, vid, "npc")   # drops the ref, keeps the card
    assert f"characters/{aid}" not in campaigns.read_manifest(cid)
    _stamp_full(cid)

    campaigns.ensure_campaign_slim(cid)

    assert (croot / "characters" / aid / "character.md").exists()
    assert (croot / "characters" / aid / f"{vid}.json").exists()


def test_create_campaign_writes_the_default_climate(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    from grimoire.store import campaign_climate, climates
    worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Chronicle", "realm",
                                    climate=climates.FALLBACK_ID)
    assert campaign_climate.read_default(cid) == climates.FALLBACK_ID


def test_create_campaign_defaults_the_climate_when_omitted(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    from grimoire.store import campaign_climate, climates
    worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Chronicle", "realm")
    assert campaign_climate.read_default(cid) == climates.FALLBACK_ID


def test_create_campaign_rejects_an_unknown_climate_before_creating_anything(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    from grimoire.store import climates
    worlds.create_world("Realm")
    # list_campaigns() returns dicts, so compare ids rather than the rows.
    before = {c["id"] for c in campaigns.list_campaigns()}
    with pytest.raises(climates.ClimateError):
        campaigns.create_campaign("Saltmarch Chronicle", "realm", climate="no-such-climate")
    assert {c["id"] for c in campaigns.list_campaigns()} == before


def test_the_written_default_is_what_the_resolver_reads_back(monkeypatch, tmp_path):
    # The file is only worth writing if settings.resolve honours it; this pins
    # the two halves together rather than trusting the shape of the JSON.
    home(monkeypatch, tmp_path)
    from grimoire.store import climates
    from grimoire.store.weather import settings
    worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Chronicle", "realm",
                                    climate=climates.FALLBACK_ID)
    assert settings.resolve(cid, None)["climate"]["id"] == climates.FALLBACK_ID
