"""GET /campaigns/{cid}/scenes/{sid}/cast/{kind}/{id}/casefile — the play view's
dossier column (2a).

One actor's whole campaign-local record, gathered from the five files the absorb
pass already writes. Nothing here is new information and nothing here costs a
token; the point of the endpoint is that most of these values had no reader
outside a staged review row, which disappears the moment it is approved.

Same tolerance contract as the briefing and the ledger: a garbled file empties
its own block and nothing else, because the same kind of panel renders it.
"""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    # `with`, so the lifespan runs: producing routes hand their work to a
    # runner that lives on it, and a client without one cannot drive a turn.
    with TestClient(create_app()) as c:
        yield c


def _campaign(client):
    wid = client.post("/api/worlds", json={"name": "W"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    return wid, cid


def _pc(wid, name):
    return store.pcs.create_pc(store.worlds.world_root(wid), name, [],
                               persona=store.pcs.blank_persona(name))


def _npc(wid, name):
    return store.characters.create_character(store.worlds.world_root(wid), name)


def _seated(client):
    """A scene with Sister Aud (NPC) and Ferrant Wyle (PC) standing in it."""
    wid, cid = _campaign(client)
    aud, av = _npc(wid, "Sister Aud")
    wyle, wv = _pc(wid, "Ferrant Wyle")
    sid = store.scenes.create_scene(cid, "The Long Tide")
    store.appearances.appear(cid, sid, "characters", aud, av, "npc")
    store.appearances.appear(cid, sid, "pcs", wyle, wv, "player")
    return cid, sid, aud, wyle


def _get(client, cid, sid, kind, aid, expect=200):
    r = client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast/{kind}/{aid}/casefile")
    assert r.status_code == expect, r.text
    return r.json()


# ---- shape and access ------------------------------------------------------

def test_unknown_scene_is_404(client):
    _wid, cid = _campaign(client)
    assert client.get(
        f"/api/campaigns/{cid}/scenes/nope/cast/characters/x/casefile").status_code == 404


def test_unknown_actor_kind_is_404(client):
    cid, sid, _aud, _wyle = _seated(client)
    _get(client, cid, sid, "dragons", "aud", expect=404)


def test_an_actor_who_is_not_in_this_scene_is_404(client):
    """The cast-membership check is access control as much as correctness:
    without it this reads any character's campaign state from a guessed id."""
    wid, cid = _campaign(client)
    elsewhere, _v = _npc(wid, "The Reeve")
    sid = store.scenes.create_scene(cid, "The Long Tide")
    _get(client, cid, sid, "characters", elsewhere, expect=404)


def test_an_actor_with_nothing_recorded_reads_empty_rather_than_missing(client):
    """Every block is present and empty, so the column can say "nothing
    recorded yet" instead of rendering holes."""
    cid, sid, aud, _wyle = _seated(client)
    body = _get(client, cid, sid, "characters", aud)
    assert body["name"] == "Sister Aud"
    assert body["role"] == "npc"
    # Labelled, not left as filenames: a scene id is `001--the-long-tide`.
    assert body["scenes"] == [{"id": sid, "title": "The Long Tide"}]
    assert body["last_seen"] == "The Long Tide"
    assert body["standing"] == "" and body["knows"] == "" and body["suspects"] == ""
    assert body["dossier"] == "" and body["tagline"] == ""
    assert body["feels_toward"] == [] and body["standing_facts"] == []


# ---- the five files --------------------------------------------------------

def test_standing_knows_and_suspects_come_from_state_md(client):
    cid, sid, aud, _wyle = _seated(client)
    store.playstate.write_state(
        store.campaigns.campaign_root(cid), aud,
        store.playstate.compose_body(
            "Guarded. Will not be alone with the Reeve.",
            "The priory's debt.",
            "That Wyle is being paid by someone upriver."))
    body = _get(client, cid, sid, "characters", aud)
    assert body["standing"] == "Guarded. Will not be alone with the Reeve."
    assert body["knows"] == "The priory's debt."
    assert body["suspects"] == "That Wyle is being paid by someone upriver."


def test_the_dossier_paragraph_comes_from_dossier_md(client):
    cid, sid, aud, _wyle = _seated(client)
    store.dossiers.write(store.campaigns.campaign_root(cid), aud,
                         "A novice of the priory who counts the tide.")
    assert _get(client, cid, sid, "characters", aud)["dossier"] == \
        "A novice of the priory who counts the tide."


def test_the_tagline_is_carried_for_someone_never_played(client):
    """The column falls back to it. Once there is a dossier the tagline is the
    guess the dossier replaced, so both are returned and the client chooses."""
    cid, sid, aud, _wyle = _seated(client)
    store.taglines.write(store.campaigns.campaign_root(cid), aud,
                         "A novice who counts the tide.")
    body = _get(client, cid, sid, "characters", aud)
    assert body["tagline"] == "A novice who counts the tide."
    assert body["dossier"] == ""


def test_feelings_are_only_toward_people_in_this_room(client):
    """A meter about someone who is not on stage is not a fact about the scene
    in front of you. The scene's cast is the whole filter."""
    wid, cid = _campaign(client)
    aud, av = _npc(wid, "Sister Aud")
    wyle, wv = _pc(wid, "Ferrant Wyle")
    reeve, rv = _npc(wid, "The Reeve")
    sid = store.scenes.create_scene(cid, "The Long Tide")
    store.appearances.appear(cid, sid, "characters", aud, av, "npc")
    store.appearances.appear(cid, sid, "pcs", wyle, wv, "player")
    store.relationships.set_feeling(cid, f"characters:{aud}", f"pcs:{wyle}",
                                    2, 4, 1, "He asks the right questions.")
    store.relationships.set_feeling(cid, f"characters:{aud}", f"characters:{reeve}",
                                    0, 0, 5, "Not in the room.")
    body = _get(client, cid, sid, "characters", aud)
    assert [f["id"] for f in body["feels_toward"]] == [wyle]
    assert body["feels_toward"][0] == {
        "ref": f"pcs:{wyle}", "kind": "pcs", "id": wyle, "name": "Ferrant Wyle",
        "trust": 2, "affection": 4, "tension": 1, "note": "He asks the right questions."}


def test_a_feeling_is_directional(client):
    """`a->b` is what a holds about b. Reading the other actor must not hand
    back the same row with the names swapped."""
    cid, sid, aud, wyle = _seated(client)
    store.relationships.set_feeling(cid, f"characters:{aud}", f"pcs:{wyle}", 2, 4, 1, "")
    assert _get(client, cid, sid, "pcs", wyle)["feels_toward"] == []
    assert len(_get(client, cid, sid, "characters", aud)["feels_toward"]) == 1


def test_standing_facts_that_name_the_actor_are_carried(client):
    """facts.json records no actors — a fact is a sentence about the world — so
    this is a name match, and it errs toward showing for the reason the briefing
    errs toward flagging."""
    cid, sid, aud, _wyle = _seated(client)
    store.facts.record(cid, "Sister Aud's priory owes the Reeve", "4 Reaping 1183", sid)
    store.facts.record(cid, "The sea wall was built by hand", "4 Reaping 1183", sid)
    body = _get(client, cid, sid, "characters", aud)
    assert [f["text"] for f in body["standing_facts"]] == \
        ["Sister Aud's priory owes the Reeve"]
    # The recording scene is labelled, not left as a filename: same shape the
    # ledger projects, so one renderer serves both.
    assert body["standing_facts"][0]["scene"] == {
        "id": sid, "title": "The Long Tide", "date": "4 Reaping 1183"}


def test_a_retired_fact_is_not_carried(client):
    cid, sid, aud, _wyle = _seated(client)
    fid = store.facts.record(cid, "Sister Aud keeps the tide book", "4 Reaping 1183", sid)
    store.facts.retire(cid, fid, sid)
    assert _get(client, cid, sid, "characters", aud)["standing_facts"] == []


# ---- tolerance -------------------------------------------------------------

def test_a_garbled_relationships_file_empties_its_block_and_nothing_else(client):
    cid, sid, aud, _wyle = _seated(client)
    store.dossiers.write(store.campaigns.campaign_root(cid), aud, "Still readable.")
    (store.campaigns.campaign_root(cid) / "relationships.json").write_text("{ not json")
    body = _get(client, cid, sid, "characters", aud)
    assert body["feels_toward"] == []
    assert body["dossier"] == "Still readable."


def test_a_garbled_facts_file_empties_its_block_and_nothing_else(client):
    cid, sid, aud, _wyle = _seated(client)
    store.dossiers.write(store.campaigns.campaign_root(cid), aud, "Still readable.")
    (store.campaigns.campaign_root(cid) / "facts.json").write_text("[[[")
    body = _get(client, cid, sid, "characters", aud)
    assert body["standing_facts"] == []
    assert body["dossier"] == "Still readable."


def test_a_hand_edited_meter_is_clamped_rather_than_drawn_out_of_range(client):
    """The column draws n filled pips out of five. A 9 would draw four phantom
    ones; a string would throw where React renders it."""
    cid, sid, aud, wyle = _seated(client)
    store.relationships.set_feeling(cid, f"characters:{aud}", f"pcs:{wyle}", 1, 1, 1, "")
    path = store.campaigns.campaign_root(cid) / "relationships.json"
    data = json.loads(path.read_text())
    key = f"characters:{aud}->pcs:{wyle}"
    data["feelings"][key] = {"trust": 9, "affection": -3, "tension": "high", "note": "x"}
    path.write_text(json.dumps(data))
    f = _get(client, cid, sid, "characters", aud)["feels_toward"][0]
    assert (f["trust"], f["affection"], f["tension"]) == (5, 0, 0)


def test_a_hand_edited_note_that_is_not_text_reads_as_no_note(client):
    cid, sid, aud, wyle = _seated(client)
    store.relationships.set_feeling(cid, f"characters:{aud}", f"pcs:{wyle}", 1, 1, 1, "")
    path = store.campaigns.campaign_root(cid) / "relationships.json"
    data = json.loads(path.read_text())
    data["feelings"][f"characters:{aud}->pcs:{wyle}"]["note"] = {"oops": True}
    path.write_text(json.dumps(data))
    assert _get(client, cid, sid, "characters", aud)["feels_toward"][0]["note"] == ""
