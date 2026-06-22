import pytest

from grimoire.store import appearances as ap
from grimoire.store import campaigns, characters, pcs, worlds


def _world_with_char(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    card = characters.blank_card("Seraphine")
    card["data"]["description"] = "the drowned keeper"
    characters.create_character(worlds.world_root(wid), "Seraphine", "Corrupted", card)
    cid = campaigns.create_campaign("Run", wid)
    return wid, cid


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
    assert ap.scene_cast(cid, "s2") == [{"kind": "characters", "id": "seraphine", "role": "npc"}]


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
