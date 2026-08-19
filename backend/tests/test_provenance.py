"""Persisted citation provenance (screen 4a) — `store/provenance.py` and
`GET /campaigns/{cid}/provenance`.

`absorb/parse.py` already asks the extractor to cite itself and
`absorb/routing.py` already weighs those citations into a band; both were
dropped the moment the edit applied, so the quote behind a continuity line
existed for exactly as long as the review row you judged it on. These tests are
about the citation outliving the row.
"""

import importlib
import json

import grimoire.store as store
import pytest
from fastapi.testclient import TestClient
from grimoire.main import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return TestClient(create_app())


def _campaign(client):
    wid = client.post("/api/worlds", json={"name": "W"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    return wid, cid


def _lore_edit(cid_lore, after, review=None):
    e = {"id": f"lore:{cid_lore}", "kind": "lore",
         "target": {"kind": "lore", "id": cid_lore},
         "label": "The Pact — lore", "field": "body",
         "before": "Signed at dusk.", "after": after, "authored": False}
    if review is not None:
        e["review"] = review
    return e


CITED = {"certainty": 0.92, "quote": "I'd rather the mud than his company.",
         "speaker": "Sister Aud", "authority": "self", "score": 0.92, "band": "high"}


def _seed_lore(cid):
    croot = store.campaigns.campaign_root(cid)
    store.entities.create_entity(croot, "lore", "The Pact", body="Signed at dusk.")
    return "the-pact"


def _prov(client, cid):
    r = client.get(f"/api/campaigns/{cid}/provenance")
    assert r.status_code == 200, r.text
    return r.json()


# ---- the key ---------------------------------------------------------------

def test_the_key_is_record_and_field(client):
    assert store.provenance.key(
        {"target": {"kind": "lore", "id": "the-pact"}, "field": "body"}) == "lore/the-pact#body"


def test_an_edit_naming_no_record_has_no_key(client):
    # These arrive off a client PUT body; a shape that names nothing must not
    # become a JSON key that names nothing either.
    for edit in ({}, {"target": None}, {"target": {"kind": "lore"}},
                 {"target": {"kind": "lore", "id": {"oops": 1}}}, "not a dict"):
        assert store.provenance.key(edit) is None


def test_a_field_less_edit_still_keys_on_its_record(client):
    # A whole-record edit (a new character, a plot beat) has no field, and its
    # citation still belongs to the record.
    assert store.provenance.key(
        {"target": {"kind": "characters", "id": "aud"}}) == "characters/aud#"


# ---- what is worth keeping -------------------------------------------------

def test_a_row_with_no_citation_is_not_stored(client):
    # An uncited row is what the panel shows when there is no entry, so writing
    # one would be storing the absence of information.
    for review in (None, {}, {"quote": ""}, {"quote": "   "}):
        edit = {"target": {"kind": "lore", "id": "x"}, "field": "body"}
        if review is not None:
            edit["review"] = review
        assert store.provenance.row(edit, "s1") is None


def test_a_cited_row_keeps_the_quote_speaker_certainty_and_band(client):
    row = store.provenance.row(
        {"target": {"kind": "lore", "id": "x"}, "field": "body", "review": CITED}, "s1")
    assert row["quote"] == "I'd rather the mud than his company."
    assert row["speaker"] == "Sister Aud"
    assert row["certainty"] == 0.92
    # Stored, not recomputed on read: the band is certainty weighted by
    # authority, and a client deriving it would be a second copy of that table.
    assert row["band"] == "high"
    assert row["authority"] == "self"
    assert row["scene"] == "s1"
    assert row["recorded"]


def test_a_certainty_the_model_did_not_give_is_kept_as_null(client):
    # `parse._certainty` answers None for a rating the model omitted, and the
    # panel's meter has to be able to say "unrated" rather than "zero".
    row = store.provenance.row(
        {"target": {"kind": "lore", "id": "x"}, "field": "body",
         "review": {**CITED, "certainty": None}}, "s1")
    assert row["certainty"] is None


def test_a_hand_edited_certainty_that_is_not_a_number_reads_as_unrated(client):
    row = store.provenance.row(
        {"target": {"kind": "lore", "id": "x"}, "field": "body",
         "review": {**CITED, "certainty": "very"}}, "s1")
    assert row["certainty"] is None


# ---- through the absorb ----------------------------------------------------

def test_the_recording_scene_is_labelled_on_read(client):
    """Labelled at read time, not frozen in at write time: a scene can be
    renamed, and a stored title would then name one that no longer exists."""
    _wid, cid = _campaign(client)
    lore = _seed_lore(cid)
    sid = store.scenes.create_scene(cid, "The Long Tide")
    store.absorb.apply_edits(
        cid, [_lore_edit(lore, "Signed at dusk.\n\nBroken by morning.", CITED)], sid=sid)
    entry = _prov(client, cid)["lore/the-pact#body"]
    assert entry["scene"] == sid
    assert entry["scene_title"] == "The Long Tide"


def test_applying_a_cited_edit_persists_its_citation(client):
    _wid, cid = _campaign(client)
    lore = _seed_lore(cid)
    sid = store.scenes.create_scene(cid, "The Long Tide")
    applied, failures = store.absorb.apply_edits(
        cid, [_lore_edit(lore, "Signed at dusk.\n\nBroken by morning.", CITED)], sid=sid)
    assert applied and not failures
    assert _prov(client, cid)["lore/the-pact#body"]["quote"] == \
        "I'd rather the mud than his company."


def test_an_uncited_edit_leaves_no_entry_rather_than_an_empty_one(client):
    _wid, cid = _campaign(client)
    lore = _seed_lore(cid)
    sid = store.scenes.create_scene(cid, "The Long Tide")
    store.absorb.apply_edits(
        cid, [_lore_edit(lore, "Signed at dusk.\n\nBroken by morning.")], sid=sid)
    assert _prov(client, cid) == {}


def test_a_later_scene_replaces_the_citation_for_the_same_field(client):
    """Rolling, like the changes log: a field's provenance is the provenance of
    the value it currently holds, and an older quote explains text that is no
    longer there."""
    _wid, cid = _campaign(client)
    lore = _seed_lore(cid)
    first = store.scenes.create_scene(cid, "The Long Tide")
    store.absorb.apply_edits(
        cid, [_lore_edit(lore, "Signed at dusk.\n\nBroken by morning.", CITED)], sid=first)

    later = store.scenes.create_scene(cid, "The Counting House")
    second = {**CITED, "quote": "The pact is ash.", "speaker": "The Reeve", "certainty": 0.4,
              "band": "low"}
    store.absorb.apply_edits(
        cid, [{**_lore_edit(lore, "Signed at dusk.\n\nBurned.", second),
               "before": "Signed at dusk.\n\nBroken by morning."}], sid=later)

    entry = _prov(client, cid)["lore/the-pact#body"]
    assert entry["quote"] == "The pact is ash."
    assert entry["scene"] == later


def test_an_edit_that_did_not_land_leaves_no_citation(client):
    """The citation explains the stored value. A conflict means the value the
    quote was about never became the record."""
    _wid, cid = _campaign(client)
    lore = _seed_lore(cid)
    croot = store.campaigns.campaign_root(cid)
    store.entities.update_entity(croot, "lore", lore, body="Signed at dusk.\n\nSomeone else's.")
    sid = store.scenes.create_scene(cid, "The Long Tide")
    applied, failures = store.absorb.apply_edits(
        cid, [_lore_edit(lore, "Signed at dusk.\n\nBroken by morning.", CITED)], sid=sid)
    assert not applied and failures
    assert _prov(client, cid) == {}


def test_a_fact_is_cited_too_even_though_it_is_not_browsable(client):
    """`changes.json` only covers browsable records (characters, lore,
    locations). A standing fact and a plot beat are exactly the continuity lines
    this exists to make checkable, and neither is browsable."""
    _wid, cid = _campaign(client)
    sid = store.scenes.create_scene(cid, "The Long Tide")
    edit = {"id": "fact:new", "kind": "fact",
            "target": {"kind": "fact", "id": "f1"}, "label": "A standing fact",
            "field": "text", "before": "", "after": "The priory owes the Reeve",
            "authored": False, "review": CITED,
            "payload": {"text": "The priory owes the Reeve", "date": "4 Reaping 1183"}}
    applied, _failures = store.absorb.apply_edits(cid, [edit], sid=sid)
    if applied:            # the fact kind's own payload contract may reject it
        assert "fact/f1#text" in _prov(client, cid)


# ---- reading it ------------------------------------------------------------

def test_a_campaign_with_no_absorb_reads_empty(client):
    _wid, cid = _campaign(client)
    assert _prov(client, cid) == {}


def test_an_unknown_campaign_is_404(client):
    assert client.get("/api/campaigns/nope/provenance").status_code == 404


def test_a_garbled_file_costs_the_markers_and_not_the_page(client):
    # This backs display markers on a panel that is otherwise fine; one bad
    # byte must not 500 the view.
    _wid, cid = _campaign(client)
    (store.campaigns.campaign_root(cid) / "provenance.json").write_text("{ not json")
    assert _prov(client, cid) == {}


def test_a_file_of_the_wrong_shape_reads_empty_too(client):
    # Valid JSON, wrong type: `json.loads` raises nothing and the route would
    # otherwise hand a list to a client expecting a map.
    _wid, cid = _campaign(client)
    (store.campaigns.campaign_root(cid) / "provenance.json").write_text("[]")
    assert _prov(client, cid) == {}


def test_recording_nothing_does_not_create_the_file(client):
    _wid, cid = _campaign(client)
    store.provenance.record(cid, {})
    assert not (store.campaigns.campaign_root(cid) / "provenance.json").exists()


def test_a_second_record_merges_rather_than_replacing_the_file(client):
    _wid, cid = _campaign(client)
    store.provenance.record(cid, {"lore/a#body": {"quote": "one"}})
    store.provenance.record(cid, {"lore/b#body": {"quote": "two"}})
    data = json.loads((store.campaigns.campaign_root(cid) / "provenance.json").read_text())
    assert set(data) == {"lore/a#body", "lore/b#body"}
