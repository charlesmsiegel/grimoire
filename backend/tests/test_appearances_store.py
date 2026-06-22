import pytest

from grimoire.store import appearances as ap
from grimoire.store import campaigns, characters, worlds


def _world_with_char(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    card = characters.blank_card("Seraphine")
    card["data"]["description"] = "the drowned keeper"
    characters.create_character(worlds.world_root(wid), "Seraphine", "Corrupted", card)
    cid = campaigns.create_campaign("Run", wid)
    return wid, cid


def test_appear_locks_copies_and_records(monkeypatch, tmp_path):
    wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "the-docks", "seraphine", "corrupted")
    # the locked card was copied into the campaign
    mine = characters.read_card(campaigns.campaign_root(cid), "seraphine", "corrupted")
    assert mine["data"]["description"] == "the drowned keeper"
    rec = ap.record(cid)["seraphine"]
    assert rec["version"] == "corrupted"
    assert rec["scenes"] == ["the-docks"]
    assert rec["base"] == characters.card_hash(worlds.world_root(wid), "seraphine", "corrupted")


def test_second_scene_appends_only(monkeypatch, tmp_path):
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "the-docks", "seraphine", "corrupted")
    ap.appear(cid, "the-reckoning", "seraphine", "corrupted")
    assert ap.record(cid)["seraphine"]["scenes"] == ["the-docks", "the-reckoning"]
    assert ap.scene_cast(cid, "the-reckoning") == ["seraphine"]


def test_mismatched_version_rejected(monkeypatch, tmp_path):
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "the-docks", "seraphine", "corrupted")
    with pytest.raises(ap.AppearError):
        ap.appear(cid, "the-docks", "seraphine", "default")  # locked to corrupted


def test_appear_missing_world_version(monkeypatch, tmp_path):
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    with pytest.raises(ap.AppearError):
        ap.appear(cid, "the-docks", "seraphine", "ghost")


def test_suggestions_scan_names(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    sera = characters.blank_card("Seraphine")
    sera["data"]["description"] = "She fears the Drowned King above all."
    characters.create_character(wroot, "Seraphine", "default", sera)
    characters.create_character(wroot, "Drowned King", "default", characters.blank_card("Drowned King"))
    characters.create_character(wroot, "Oracle", "default", characters.blank_card("Oracle"))
    cid = campaigns.create_campaign("Run", wid)
    ap.appear(cid, "scene-1", "seraphine", "default")
    sugg = ap.suggestions(cid, "scene-1")
    ids = [s["character"] for s in sugg]
    assert "drowned-king" in ids       # mentioned by seraphine's card
    assert "oracle" not in ids          # not mentioned
    assert "seraphine" not in ids       # already appeared


def test_suggestion_mentioned_by_attributes_only_the_mentioner(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    sera = characters.blank_card("Seraphine")
    sera["data"]["description"] = "She fears the Drowned King."
    characters.create_character(wroot, "Seraphine", "default", sera)
    characters.create_character(wroot, "Oracle", "default", characters.blank_card("Oracle"))  # silent
    characters.create_character(wroot, "Drowned King", "default", characters.blank_card("Drowned King"))
    cid = campaigns.create_campaign("Run", wid)
    ap.appear(cid, "scene-1", "seraphine", "default")
    ap.appear(cid, "scene-1", "oracle", "default")
    sugg = ap.suggestions(cid, "scene-1")
    assert sugg == [{"character": "drowned-king", "name": "Drowned King", "mentioned_by": ["seraphine"]}]
