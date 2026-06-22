import pytest

from grimoire.store import appearances as ap
from grimoire.store import campaigns, characters, greetings, pcs, playing, scenes, tags, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    return wid, cid, sid


def test_played_roundtrip(monkeypatch, tmp_path):
    _wid, cid, _sid = _campaign(monkeypatch, tmp_path)
    assert playing.read_played(cid) == set()
    playing._mark_played(cid, "g1")
    playing._mark_played(cid, "g1")  # idempotent
    playing._mark_played(cid, "g2")
    assert playing.read_played(cid) == {"g1", "g2"}


def test_player_tags_unions_player_pcs_only(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    for t in ("student", "sailor"):
        tags.add_tag(wroot, t)
    pcs.create_pc(wroot, "Elara", ["student"])
    pcs.create_pc(wroot, "Bryn", ["sailor"])
    ap.appear(cid, sid, "pcs", "elara", "default", "player")
    ap.appear(cid, sid, "pcs", "bryn", "default", "player")
    assert playing.player_tags(cid) == {"student", "sailor"}


def test_available_greetings_end_to_end(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    tags.add_tag(wroot, "vip")
    g = greetings.create_greeting(wroot, "Gala", "c", "v", requires_tags=["vip"])
    assert {x["id"]: x["available"] for x in playing.available_greetings(cid)}[g] is False
    pcs.create_pc(wroot, "Elara", ["vip"])
    ap.appear(cid, sid, "pcs", "elara", "default", "player")
    assert {x["id"]: x["available"] for x in playing.available_greetings(cid)}[g] is True


def test_start_from_greeting_seeds_appears_marks(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    card = characters.blank_card("Seraphine")
    card["data"].update(description="keeper")
    characters.create_character(wroot, "Seraphine", "default", card)
    pcs.create_pc(wroot, "Elara", [])
    ap.appear(cid, sid, "pcs", "elara", "default", "player")
    g = greetings.create_greeting(wroot, "Open", "seraphine", "default",
                                  body="{{char}} greets {{user}}.")
    playing.start_from_greeting(cid, sid, g)
    scene = scenes.read_scene(cid, sid)
    assert scene["messages"][0]["role"] == "assistant"
    assert scene["messages"][0]["content"] == "Seraphine greets Elara."   # tokens substituted
    assert g in playing.read_played(cid)
    assert ap.is_appeared(cid, "characters", "seraphine")
    # second start on a now-nonempty scene -> PlayError
    with pytest.raises(playing.PlayError):
        playing.start_from_greeting(cid, sid, g)


def test_start_unavailable_raises(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    tags.add_tag(wroot, "vip")
    g = greetings.create_greeting(wroot, "Gala", "s", "default", requires_tags=["vip"])
    with pytest.raises(playing.PlayError):
        playing.start_from_greeting(cid, sid, g)
