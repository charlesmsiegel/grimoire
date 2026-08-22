import shutil

import pytest

from grimoire.store import appearances as ap
from grimoire.store import (
    assets,
    campaigns,
    characters,
    dossiers,
    overlay,
    pcs,
    scenes,
    taglines,
    worlds,
)
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter


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


def test_leave_removes_scene_but_keeps_appearance_record(monkeypatch, tmp_path):
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "s1", "characters", "seraphine", "corrupted", "npc")
    ap.appear(cid, "s2", "characters", "seraphine", "corrupted", "npc")
    ap.leave(cid, "s1", "characters", "seraphine")
    assert ap.record(cid)["characters/seraphine"]["scenes"] == ["s2"]
    assert ap.scene_cast(cid, "s1") == []
    assert ap.scene_cast(cid, "s2") == [
        {"kind": "characters", "id": "seraphine", "role": "npc", "name": "Seraphine"}]


def test_leave_on_actor_not_cast_is_a_silent_no_op(monkeypatch, tmp_path):
    """Idempotency: a retried DELETE (lost response, double-click) must not fail
    just because the first attempt already landed."""
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.leave(cid, "s1", "characters", "seraphine")  # never cast at all
    assert ap.record(cid) == {}
    ap.appear(cid, "s1", "characters", "seraphine", "corrupted", "npc")
    ap.leave(cid, "s1", "characters", "seraphine")
    ap.leave(cid, "s1", "characters", "seraphine")  # repeat call: still a no-op
    assert ap.record(cid)["characters/seraphine"]["scenes"] == []


def test_leave_narrates_once_scene_has_messages(monkeypatch, tmp_path):
    from grimoire.store import scenes
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    ap.appear(cid, sid, "characters", "seraphine", "corrupted", "npc")
    ap.leave(cid, sid, "characters", "seraphine")
    assert scenes.read_scene(cid, sid)["messages"] == []  # still empty: silent

    ap.appear(cid, sid, "characters", "seraphine", "corrupted", "npc")
    scenes.append_message(cid, sid, "user", "hi")
    ap.leave(cid, sid, "characters", "seraphine")
    assert scenes.read_scene(cid, sid)["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "*Seraphine leaves the scene.*",
         "speaker": scenes.TRANSITION_SPEAKER},
    ]


def test_appear_narrates_first_time_join_into_a_messaged_scene(monkeypatch, tmp_path):
    """The Task 4 case: adding a brand-new character to a scene that's already
    underway (e.g. via the sidebar's new "+ Add" control) must narrate, exactly
    like a rejoin does. This is the fresh-lock branch, not the rejoin branch."""
    from grimoire.store import scenes
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    scenes.append_message(cid, sid, "user", "hi")
    ap.appear(cid, sid, "characters", "seraphine", "corrupted", "npc")  # first-ever lock
    assert scenes.read_scene(cid, sid)["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "*Seraphine joins the scene.*",
         "speaker": scenes.TRANSITION_SPEAKER},
    ]


def test_appear_narrates_join_once_scene_has_messages(monkeypatch, tmp_path):
    from grimoire.store import scenes
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    # fresh lock, empty scene: silent (matches CastPanel's pre-scene setup today)
    ap.appear(cid, sid, "characters", "seraphine", "corrupted", "npc")
    assert scenes.read_scene(cid, sid)["messages"] == []

    scenes.append_message(cid, sid, "user", "hi")
    sid2 = scenes.create_scene(cid, "S2")
    # already-locked actor rejoining a *different*, non-empty scene: narrates
    ap.appear(cid, sid2, "characters", "seraphine", "corrupted", "npc")
    assert scenes.read_scene(cid, sid)["messages"] == [{"role": "user", "content": "hi"}]  # untouched
    scenes.append_message(cid, sid2, "user", "hi")
    ap.leave(cid, sid2, "characters", "seraphine")
    ap.appear(cid, sid2, "characters", "seraphine", "corrupted", "npc")
    assert scenes.read_scene(cid, sid2)["messages"][-1] == \
        {"role": "assistant", "content": "*Seraphine joins the scene.*",
         "speaker": scenes.TRANSITION_SPEAKER}


def test_appear_rejoin_same_scene_is_a_noop_no_duplicate_narration(monkeypatch, tmp_path):
    from grimoire.store import scenes
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    scenes.append_message(cid, sid, "user", "hi")
    ap.appear(cid, sid, "characters", "seraphine", "corrupted", "npc")
    before = scenes.read_scene(cid, sid)["messages"]
    ap.appear(cid, sid, "characters", "seraphine", "corrupted", "npc")  # already in this scene
    assert scenes.read_scene(cid, sid)["messages"] == before  # no second join line


def test_appear_narrate_false_suppresses_narration(monkeypatch, tmp_path):
    """ingest_scene.build_scene's use case: the scene's transcript is written
    first, then cast is registered -- narration must be fully suppressed even
    though the scene already has messages."""
    from grimoire.store import scenes
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    scenes.append_message(cid, sid, "assistant", "*The study is silent.*")
    ap.appear(cid, sid, "characters", "seraphine", "corrupted", "npc", narrate=False)
    assert scenes.read_scene(cid, sid)["messages"] == [
        {"role": "assistant", "content": "*The study is silent.*"}]


def test_appear_and_leave_tolerate_a_scene_id_with_no_backing_file(monkeypatch, tmp_path):
    """Every pre-existing test in this file calls appear()/leave() with a bare
    scene id string ("s1", "the-docks", ...) that scenes.create_scene never
    created -- appear()/leave() must not raise SceneNotFound for those."""
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "no-such-scene", "characters", "seraphine", "corrupted", "npc")
    ap.leave(cid, "no-such-scene", "characters", "seraphine")


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


# ---- source badges: library / emergent / override (#99) ----

def test_actor_source_reads_library_for_an_untouched_world_character(monkeypatch, tmp_path):
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "s1", "characters", "seraphine", "corrupted", "npc")
    assert ap.actor_source(cid, "characters", "seraphine") == "library"
    assert ap.cast_detail(cid, "s1", "characters", "seraphine")["source"] == "library"


def test_actor_source_reads_override_once_the_campaign_card_diverges(monkeypatch, tmp_path):
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "s1", "characters", "seraphine", "corrupted", "npc")
    croot = campaigns.campaign_root(cid)
    card = characters.read_card(croot, "seraphine", "corrupted")
    card["data"]["description"] = "the drowned keeper, and a liar besides"
    characters.update_version(croot, "seraphine", "corrupted", card)
    assert ap.actor_source(cid, "characters", "seraphine") == "override"
    assert ap.cast_detail(cid, "s1", "characters", "seraphine")["source"] == "override"


def test_actor_source_reads_library_again_when_the_edit_is_undone(monkeypatch, tmp_path):
    """The comparison is content, not an edit counter: restoring the locked
    text puts the badge back rather than latching on the first write."""
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "s1", "characters", "seraphine", "corrupted", "npc")
    croot = campaigns.campaign_root(cid)
    card = characters.read_card(croot, "seraphine", "corrupted")
    characters.update_version(croot, "seraphine", "corrupted",
                              {**card, "data": {**card["data"], "description": "changed"}})
    assert ap.actor_source(cid, "characters", "seraphine") == "override"
    characters.update_version(croot, "seraphine", "corrupted", card)
    assert ap.actor_source(cid, "characters", "seraphine") == "library"


def test_actor_source_reads_emergent_for_a_character_the_world_never_had(monkeypatch, tmp_path):
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    aid, vid = overlay.create_character(cid, "Winifred")
    ap.appear(cid, "s1", "characters", aid, vid, "npc")
    assert ap.actor_source(cid, "characters", aid) == "emergent"
    assert ap.cast_detail(cid, "s1", "characters", aid)["source"] == "emergent"


def test_actor_source_reads_emergent_for_a_campaign_local_pc(monkeypatch, tmp_path):
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    pid, vid = overlay.create_pc(cid, "Mara", [])
    ap.appear(cid, "s1", "pcs", pid, vid, "player")
    assert ap.actor_source(cid, "pcs", pid) == "emergent"


def test_actor_source_reads_emergent_once_the_world_original_is_deleted(monkeypatch, tmp_path):
    """A detached copy shares only a slug with whatever claims the id next, so
    it must not be badged against that stranger (overlay.detached, #225)."""
    wid, cid = _world_with_char(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    ap.appear(cid, "s1", "characters", "seraphine", "corrupted", "npc")
    characters.delete_character(wroot, "seraphine")
    overlay.forget_world_record(wroot, "characters", "seraphine")
    assert "characters/seraphine" in overlay.detached(cid)
    assert ap.actor_source(cid, "characters", "seraphine") == "emergent"
    # and still emergent once a stranger takes the freed slug. The assert on
    # the id is the test: without it a `seraphine-2` would pass this for the
    # trivial reason that nothing claimed the slug at all.
    stranger, _ = characters.create_character(wroot, "Seraphine", "Corrupted",
                                              characters.blank_card("Seraphine"))
    assert stranger == "seraphine"
    assert ap.actor_source(cid, "characters", "seraphine") == "emergent"


def test_actor_source_reads_emergent_when_the_world_directory_is_gone(monkeypatch, tmp_path):
    """A campaign whose world is no longer on disk inherits nothing -- the same
    reading `world_root_of` already gives it -- so every actor in it is its own
    rather than the library's.

    Only reachable by hand: `delete_world` refuses a world a campaign still
    depends on. A restored or hand-managed store gets here anyway, which is
    why `world_root_of` answers a path rather than raising."""
    wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "s1", "characters", "seraphine", "corrupted", "npc")
    shutil.rmtree(worlds.world_root(wid))
    assert ap.actor_source(cid, "characters", "seraphine") == "emergent"
    assert ap.cast_detail(cid, "s1", "characters", "seraphine")["source"] == "emergent"


def test_actor_source_reads_override_for_a_campaign_made_version(monkeypatch, tmp_path):
    """The world has the character but never had this version: the text under
    the lock is the campaign's own, which is what the badge reports."""
    _wid, cid, char_id = _fork(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    overlay.materialize_actor(cid, "characters", char_id)
    characters.create_version(croot, char_id, "wounded", characters.blank_card("Mara"))
    ap.pick_version(cid, "characters", char_id, "wounded")
    assert ap.record(cid)[f"characters/{char_id}"]["base"] == ""
    assert ap.actor_source(cid, "characters", char_id) == "override"


def test_actor_source_refuses_an_actor_that_has_not_appeared(monkeypatch, tmp_path):
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    with pytest.raises(ap.AppearError):
        ap.actor_source(cid, "characters", "seraphine")


def test_actor_source_reads_library_while_a_world_edit_is_still_pending(monkeypatch, tmp_path):
    """Provenance, not sync state: a world-side edit since the lock leaves the
    campaign holding the library's text as it took it. #71 reports the other
    axis."""
    wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "s1", "characters", "seraphine", "corrupted", "npc")
    wroot = worlds.world_root(wid)
    card = characters.read_card(wroot, "seraphine", "corrupted")
    characters.update_version(wroot, "seraphine", "corrupted",
                              {**card, "data": {**card["data"], "description": "rewritten upstream"}})
    assert ap.actor_source(cid, "characters", "seraphine") == "library"


def test_actor_source_reads_library_then_override_for_a_pc(monkeypatch, tmp_path):
    """The PC half of the comparison is a different hash function
    (`pcs.version_hash`) reached through a different existence check, so it
    gets its own pass through all of it rather than riding the character's."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    pid, vid = pcs.create_pc(wroot, "Elara", [])
    cid = campaigns.create_campaign("Run", wid)
    ap.appear(cid, "s1", "pcs", pid, vid, "player")
    assert ap.actor_source(cid, "pcs", pid) == "library"
    assert ap.cast_detail(cid, "s1", "pcs", pid)["source"] == "library"

    croot = campaigns.campaign_root(cid)
    persona = pcs.read_persona(croot, pid, vid)
    pcs.update_version(croot, pid, vid, {**persona, "description": "On the run, and lying about it."})
    assert ap.actor_source(cid, "pcs", pid) == "override"
    assert ap.cast_detail(cid, "s1", "pcs", pid)["source"] == "override"


def test_actor_source_reads_override_when_the_campaign_copy_is_gone(monkeypatch, tmp_path):
    """A campaign-side delete does not sweep the appearance record, so the
    record can outlive the card it names. With nothing to hash, the badge must
    not claim the card is the library's -- it cannot read the card at all."""
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "s1", "characters", "seraphine", "corrupted", "npc")
    characters.delete_character(campaigns.campaign_root(cid), "seraphine")
    assert ap.actor_source(cid, "characters", "seraphine") == "override"


def test_actor_source_reads_emergent_for_a_campaign_whose_world_ref_is_blank(monkeypatch, tmp_path):
    """The other half of "no world": not a missing directory but a reference
    that names none. `world_root_of` answers a path *below campaign.md*, a
    regular file, so the library lookup stats through a non-directory rather
    than through nothing -- a different failure inside `Path.exists` and worth
    its own pass."""
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "s1", "characters", "seraphine", "corrupted", "npc")
    mp = campaigns.campaign_root(cid) / "campaign.md"
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    mp.write_text(dump_frontmatter({**meta, "world": ""}, body), encoding="utf-8")
    assert not campaigns.world_root_of(cid).is_dir()
    assert ap.actor_source(cid, "characters", "seraphine") == "emergent"


def test_actor_source_stays_emergent_when_the_world_later_takes_the_slug(monkeypatch, tmp_path):
    """A campaign-invented character keeps her own identity when a world
    character is later created under the same slug (Codex review, #225).

    `overlay.create_character` allocates against the world as it stands, which
    is what makes a world record claiming that id *afterwards* a stranger --
    the same position a spared copy is in once its world original is deleted.
    Reading her as that stranger's, diverged, is the exact mistake `detached`
    exists to prevent, arrived at from the other direction."""
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    aid, vid = overlay.create_character(cid, "Winifred")
    ap.appear(cid, "s1", "characters", aid, vid, "npc")
    assert ap.actor_source(cid, "characters", aid) == "emergent"

    wroot = worlds.world_root(campaigns.read_campaign(cid)["meta"]["world"])
    stranger, _ = characters.create_character(
        wroot, "Winifred", "default",
        {"data": {"name": "Winifred", "description": "someone else entirely"}})
    assert stranger == aid                       # the slug really did collide
    assert ap.actor_source(cid, "characters", aid) == "emergent"


def test_a_campaign_made_actor_takes_nothing_from_the_slugs_later_owner(monkeypatch, tmp_path):
    """The half of the same bug that has nothing to do with badges: a campaign
    character invented before the world had that slug used to inherit the
    stranger's avatar and tagline the moment the world claimed it."""
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    aid, vid = overlay.create_character(cid, "Winifred")
    wroot = worlds.world_root(campaigns.read_campaign(cid)["meta"]["world"])
    characters.create_character(wroot, "Winifred", "default",
                                {"data": {"name": "Winifred", "description": "someone else"}})
    assets.put_image(wroot, aid, "default", "avatar", b"\x89PNG\r\n\x1a\nx", "png")
    taglines.write(wroot, aid, "the stranger's tagline")
    assert overlay.list_images(cid, aid, vid) == []
    assert overlay.tagline(cid, aid) == ""


def test_a_campaign_made_pc_is_its_own_from_birth(monkeypatch, tmp_path):
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    pid, vid = overlay.create_pc(cid, "Mara", [])
    ap.appear(cid, "s1", "pcs", pid, vid, "player")
    wroot = worlds.world_root(campaigns.read_campaign(cid)["meta"]["world"])
    stranger, _ = pcs.create_pc(wroot, "Mara", [])
    assert stranger == pid
    assert ap.actor_source(cid, "pcs", pid) == "emergent"
