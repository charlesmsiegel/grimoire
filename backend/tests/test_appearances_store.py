import pytest

from grimoire.store import appearances as ap
from grimoire.store import assets, campaigns, characters, dossiers, overlay, pcs, scenes, worlds


def _world_with_char(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    card = characters.blank_card("Seraphine")
    card["data"]["description"] = "the drowned keeper"
    characters.create_character(worlds.world_root(wid), "Seraphine", "Corrupted", card)
    cid = campaigns.create_campaign("Run", wid)
    return wid, cid


def test_lock_materializes_card_but_not_assets(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    aid, vid = characters.create_character(wroot, "Hero")
    assets.put_image(wroot, aid, vid, "avatar", b"\x89PNG\r\n\x1a\nx", "png")
    cid = campaigns.create_campaign("C", wid)
    ap.appear(cid, "s1", "characters", aid, vid, "npc")
    d = campaigns.campaign_root(cid) / "characters" / aid
    assert (d / f"{vid}.json").exists()
    assert not (d / "assets").exists()
    # serving still finds the world file
    assert overlay.image_root(cid, aid, vid, "avatar") == wroot


def test_character_appears_locks_version_and_role(monkeypatch, tmp_path):
    wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "the-docks", "characters", "seraphine", "corrupted", "npc")
    mine = characters.read_card(campaigns.campaign_root(cid), "seraphine", "corrupted")
    assert mine["data"]["description"] == "the drowned keeper"
    rec = ap.record(cid)["characters/seraphine"]
    assert rec == {"version": "corrupted", "base": rec["base"], "scenes": ["the-docks"], "role": "npc"}
    assert rec["base"] == characters.card_hash(worlds.world_root(wid), "seraphine", "corrupted")


def test_second_scene_appends_only(monkeypatch, tmp_path):
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "s1", "characters", "seraphine", "corrupted", "npc")
    ap.appear(cid, "s2", "characters", "seraphine", "corrupted", "npc")
    assert ap.record(cid)["characters/seraphine"]["scenes"] == ["s1", "s2"]
    assert ap.scene_cast(cid, "s2") == [
        {"kind": "characters", "id": "seraphine", "role": "npc", "name": "Seraphine"}]


def test_version_or_role_mismatch_rejected(monkeypatch, tmp_path):
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "s1", "characters", "seraphine", "corrupted", "npc")
    with pytest.raises(ap.AppearError):
        ap.appear(cid, "s1", "characters", "seraphine", "default", "npc")   # version differs
    with pytest.raises(ap.AppearError):
        ap.appear(cid, "s1", "characters", "seraphine", "corrupted", "player")  # role differs


def test_pc_appears_as_player(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    pcs.create_pc(worlds.world_root(wid), "Elara", [], persona={"name": "Elara", "pronouns": "she/her",
                                                                "summary": "scholar", "description": "A wanderer."})
    cid = campaigns.create_campaign("Run", wid)
    ap.appear(cid, "s1", "pcs", "elara", "default", "player")
    assert ap.players_in_scene(cid, "s1") == [{"kind": "pcs", "id": "elara", "version": "default"}]
    # the PC version markdown was copied into the campaign
    assert pcs.read_persona(campaigns.campaign_root(cid), "elara", "default")["description"] == "A wanderer."


def test_character_cast_as_player(monkeypatch, tmp_path):
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "s1", "characters", "seraphine", "corrupted", "player")
    assert ap.players_in_scene(cid, "s1") == [{"kind": "characters", "id": "seraphine", "version": "corrupted"}]


def test_suggestions_still_scan_character_names(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    sera = characters.blank_card("Seraphine")
    sera["data"]["description"] = "She fears the Drowned King."
    characters.create_character(wroot, "Seraphine", "default", sera)
    characters.create_character(wroot, "Drowned King", "default", characters.blank_card("Drowned King"))
    cid = campaigns.create_campaign("Run", wid)
    ap.appear(cid, "s1", "characters", "seraphine", "default", "npc")
    sugg = ap.suggestions(cid, "s1")
    assert sugg == [{"character": "drowned-king", "name": "Drowned King", "mentioned_by": ["seraphine"]}]


def test_campaign_local_pc_appears_without_world_source(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    croot = campaigns.campaign_root(cid)
    # PC exists only in the campaign (overlay), never in the world
    pcs.create_pc(croot, "Mara", ["rebel"], persona={"name": "Mara", "pronouns": "she/her",
                  "summary": "outlaw", "description": "On the run."})
    ap.appear(cid, "s1", "pcs", "mara", "default", "player")
    assert ap.players_in_scene(cid, "s1") == [{"kind": "pcs", "id": "mara", "version": "default"}]
    assert ap.record(cid)["pcs/mara"]["base"] == ""
    assert pcs.version_hash(worlds.world_root(wid), "mara", "default") is None  # nothing in the world


def test_appear_raises_when_actor_in_neither_world_nor_campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    with pytest.raises(ap.AppearError):
        ap.appear(cid, "s1", "pcs", "ghost", "default", "player")


def test_sync_ignores_campaign_local_pc(monkeypatch, tmp_path):
    from grimoire.store import sync
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    croot = campaigns.campaign_root(cid)
    pcs.create_pc(croot, "Mara", [], persona=pcs.blank_persona("Mara"))
    ap.appear(cid, "s1", "pcs", "mara", "default", "player")
    assert sync.incoming(cid) == []


def test_rename_scene_migrates_cast_end_to_end(monkeypatch, tmp_path):
    """The real bug: renaming a scene changes its id, but the cast lived under the
    old id in appearances.json. scenes.rename_scene must carry the cast across."""
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Old Title")
    ap.appear(cid, sid, "characters", "seraphine", "corrupted", "npc")
    new_sid = scenes.rename_scene(cid, sid, "Bright New Title")
    assert new_sid != sid
    assert ap.scene_cast(cid, new_sid) == [
        {"kind": "characters", "id": "seraphine", "role": "npc", "name": "Seraphine"}]
    assert ap.scene_cast(cid, sid) == []


def test_repoint_scenes_only_touches_matching_id(monkeypatch, tmp_path):
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "a", "characters", "seraphine", "corrupted", "npc")
    ap.appear(cid, "b", "characters", "seraphine", "corrupted", "npc")
    ap.repoint_scenes(cid, {"a": "z"})
    assert ap.record(cid)["characters/seraphine"]["scenes"] == ["z", "b"]


def test_repoint_scenes_noop_when_id_unchanged(monkeypatch, tmp_path):
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "a", "characters", "seraphine", "corrupted", "npc")
    ap.repoint_scenes(cid, {"a": "a"})
    assert ap.record(cid)["characters/seraphine"]["scenes"] == ["a"]


def test_appear_does_not_copy_dossier_into_campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Aese", "main", characters.blank_card("Aese"))
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")

    ap.appear(cid, sid, "characters", "aese", "main", "npc")

    # Dossiers are born campaign-side at absorb, not copied on appearance.
    assert dossiers.read(campaigns.campaign_root(cid), "aese") == ""


def test_player_names_and_scene_cast_names(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    pid, pvid = pcs.create_pc(wroot, "Elara Vane", [])
    char_id, cvid = characters.create_character(wroot, "Seraphine Vale")
    ap.appear(cid, sid, "pcs", pid, pvid, "player")
    ap.appear(cid, sid, "characters", char_id, cvid, "npc")
    assert ap.player_names(cid, sid) == ["Elara Vane"]
    cast = ap.scene_cast(cid, sid)
    assert {a["id"]: a["name"] for a in cast} == {pid: "Elara Vane", char_id: "Seraphine Vale"}


def test_player_names_empty_when_no_players(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    assert ap.player_names(cid, sid) == []


def test_suggestions_reflect_live_world_after_character_deleted(monkeypatch, tmp_path):
    """Rowan is never appeared/materialized in the campaign, so a thin campaign has
    no snapshot of them to fall back on: deleting Rowan from the world removes them
    from suggestions too. (Under the old full-copy campaigns, a stale campaign copy
    of Rowan would have survived the world-side deletion; that no longer applies.)"""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    card = characters.blank_card("Mara")
    card["data"]["description"] = "Mara knows Rowan."
    characters.create_character(wroot, "Mara", "default", card)
    characters.create_character(wroot, "Rowan", "default", characters.blank_card("Rowan"))
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    ap.appear(cid, sid, "characters", "mara", "default", "npc")
    characters.delete_character(wroot, "rowan")  # world diverges after the fork
    got = ap.suggestions(cid, sid)
    assert got == []  # Rowan was never materialized; gone from the world means gone


def _fork(monkeypatch, tmp_path, versions=("young", "veteran")):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    char_id, _ = characters.create_character(wroot, "Mara", versions[0])
    for v in versions[1:]:
        characters.create_version(wroot, char_id, v, characters.blank_card("Mara"))
    cid = campaigns.create_campaign("Run", wid)
    return wid, cid, char_id


def test_pick_version_purges_and_locks(monkeypatch, tmp_path):
    wid, cid, char_id = _fork(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ap.pick_version(cid, "characters", char_id, "veteran")
    assert ap.locked_version(cid, "characters", char_id) == "veteran"
    assert not (croot / "characters" / char_id / "young.json").exists()
    assert (croot / "characters" / char_id / "veteran.json").exists()
    assert characters.read_character(croot, char_id)["meta"]["default_version"] == "veteran"
    assert f"characters/{char_id}" not in campaigns.read_manifest(cid)
    rec = ap.record(cid)[f"characters/{char_id}"]
    assert rec["scenes"] == [] and rec["role"] == "npc"
    with pytest.raises(ap.AppearError):
        ap.pick_version(cid, "characters", char_id, "young")  # already locked


def test_pick_version_unknown_version_raises(monkeypatch, tmp_path):
    wid, cid, char_id = _fork(monkeypatch, tmp_path)
    with pytest.raises(ap.AppearError):
        ap.pick_version(cid, "characters", char_id, "bogus")


def test_lazy_appear_picks_and_purges(monkeypatch, tmp_path):
    wid, cid, char_id = _fork(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    ap.appear(cid, sid, "characters", char_id, "young", "npc")
    croot = campaigns.campaign_root(cid)
    assert not (croot / "characters" / char_id / "veteran.json").exists()
    assert f"characters/{char_id}" not in campaigns.read_manifest(cid)
    assert ap.record(cid)[f"characters/{char_id}"]["scenes"] == [sid]


def test_appear_after_pick_adds_scene(monkeypatch, tmp_path):
    wid, cid, char_id = _fork(monkeypatch, tmp_path)
    ap.pick_version(cid, "characters", char_id, "veteran")
    sid = scenes.create_scene(cid, "S")
    ap.appear(cid, sid, "characters", char_id, "veteran", "npc")
    assert ap.record(cid)[f"characters/{char_id}"]["scenes"] == [sid]


def test_import_version_replaces_pick(monkeypatch, tmp_path):
    wid, cid, char_id = _fork(monkeypatch, tmp_path)
    ap.pick_version(cid, "characters", char_id, "young")
    ap.import_version(cid, "characters", char_id, "veteran")
    croot = campaigns.campaign_root(cid)
    assert ap.locked_version(cid, "characters", char_id) == "veteran"
    assert (croot / "characters" / char_id / "veteran.json").exists()
    assert not (croot / "characters" / char_id / "young.json").exists()
    assert characters.read_character(croot, char_id)["meta"]["default_version"] == "veteran"
    wroot = worlds.world_root(wid)
    assert ap.record(cid)[f"characters/{char_id}"]["base"] == \
        characters.card_hash(wroot, char_id, "veteran")


def test_import_version_requires_lock(monkeypatch, tmp_path):
    wid, cid, char_id = _fork(monkeypatch, tmp_path)
    with pytest.raises(ap.AppearError):
        ap.import_version(cid, "characters", char_id, "veteran")


def test_import_version_unknown_world_version(monkeypatch, tmp_path):
    wid, cid, char_id = _fork(monkeypatch, tmp_path)
    ap.pick_version(cid, "characters", char_id, "young")
    with pytest.raises(ap.AppearError):
        ap.import_version(cid, "characters", char_id, "bogus")


def test_pick_version_pcs_purges_and_keeps_meta(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    pid, _ = pcs.create_pc(wroot, "Elara", [], "young")
    pcs.create_version(wroot, pid, "older", pcs.blank_persona("Elara"))
    cid = campaigns.create_campaign("Run", wid)
    croot = campaigns.campaign_root(cid)
    overlay.materialize_actor(cid, "pcs", pid)   # both versions land in the campaign
    pcs.set_tags(croot, pid, ["campaign-tag"])  # campaign-side meta edit survives the pick
    ap.pick_version(cid, "pcs", pid, "older")
    assert ap.locked_version(cid, "pcs", pid) == "older"
    assert not (croot / "pcs" / pid / "young.md").exists()
    assert (croot / "pcs" / pid / "pc.md").exists()          # meta never purged (*.md glob guard)
    meta = pcs.read_pc(croot, pid)["meta"]
    assert meta["default_version"] == "older"
    assert meta["tags"] == ["campaign-tag"]
    assert f"pcs/{pid}" not in campaigns.read_manifest(cid)
