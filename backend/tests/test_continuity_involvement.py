"""`store/continuity/involvement.py` -- which actors a thread or commitment touched.

Briefing's join, generalised (capstone spec §8): over the whole roster rather
than a scene's focus actors, aggregated over a record's merged group, and
tolerant source by source -- a garbled chronicle must not cost the actors the
appearance record still knows, and the reverse.
"""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app
from grimoire.store.campaigns import paths as campaigns_paths
from grimoire.store.continuity import doc, involvement

S1, S2, S3 = "001--saltmarch", "002--tribunal", "003--ferry"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def cid(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    return client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]


def _root(cid):
    return campaigns_paths.campaign_root(cid)


def _chronicle(cid, casts: dict):
    (_root(cid) / "chronicle.json").write_text(json.dumps(
        {sid: {"id": sid, "one_line": "", "cast": cast} for sid, cast in casts.items()}),
        encoding="utf-8")


def _appearances(cid, scenes_by_ref: dict):
    (_root(cid) / "appearances.json").write_text(json.dumps(
        {ref: {"version": "v1", "role": "player" if ref.startswith("pcs/") else "npc",
               "scenes": scenes} for ref, scenes in scenes_by_ref.items()}), encoding="utf-8")


def test_of_unions_roster_and_chronicle(cid):
    _chronicle(cid, {S1: ["characters/mara"]})
    _appearances(cid, {"pcs/seraphine": [S2]})
    store.plot.set_movement(cid, "missing-map", "The missing map", "open", "Stolen.", S1)
    store.plot.set_movement(cid, "missing-map", "", "advanced", "Traced.", S2)
    got = involvement.of(cid, ["thread:missing-map"])
    assert got == {"thread:missing-map": {"actors": ["characters:mara", "pcs:seraphine"],
                                          "scenes": [S1, S2]}}


def test_of_aggregates_alias_group(cid):
    _chronicle(cid, {S1: ["characters/mara"], S2: ["characters/winifred"]})
    store.plot.set_movement(cid, "missing-map", "The missing map", "open", "Stolen.", S1)
    store.plot.set_movement(cid, "lost-chart", "The lost chart", "open", "Lost.", S2)
    doc.put_alias(cid, "thread:missing-map", {"to": "thread:lost-chart"})
    got = involvement.of(cid, ["thread:missing-map", "thread:lost-chart"])
    expected = {"actors": ["characters:mara", "characters:winifred"], "scenes": [S1, S2]}
    assert got == {"thread:missing-map": expected, "thread:lost-chart": expected}


def test_of_includes_last_scene_without_beat(cid):
    _chronicle(cid, {S3: ["characters/mara"]})
    store.plot.set_movement(cid, "missing-map", "The missing map", "advanced", "", S3)
    assert involvement.of(cid, ["thread:missing-map"])["thread:missing-map"] == {
        "actors": ["characters:mara"], "scenes": [S3]}


def test_of_tolerates_garbled_chronicle_and_appearances(cid):
    store.commitments.set_movement(cid, "mara-promise", "Mara's promise", "promise", "open",
                                   None, "She swore.", S1)
    _appearances(cid, {"characters/mara": [S1]})
    (_root(cid) / "chronicle.json").write_text("{ no", encoding="utf-8")
    assert involvement.of(cid, ["commitment:mara-promise"])["commitment:mara-promise"][
        "actors"] == ["characters:mara"]
    _chronicle(cid, {S1: ["characters/winifred", ["not", "a", "ref"]]})
    (_root(cid) / "appearances.json").write_text("{ no", encoding="utf-8")
    assert involvement.of(cid, ["commitment:mara-promise"])["commitment:mara-promise"][
        "actors"] == ["characters:winifred"]


def test_of_garbled_plot_maps_threads_empty_commitments_still_answer(cid):
    _chronicle(cid, {S1: ["characters/mara"]})
    store.commitments.set_movement(cid, "mara-promise", "Mara's promise", "promise", "open",
                                   None, "She swore.", S1)
    (_root(cid) / "plot.json").write_text("{ no", encoding="utf-8")
    got = involvement.of(cid, ["thread:missing-map", "commitment:mara-promise"])
    assert got["thread:missing-map"] == {"actors": [], "scenes": []}
    assert got["commitment:mara-promise"] == {"actors": ["characters:mara"], "scenes": [S1]}


def test_of_unknown_or_bad_ref_is_empty(cid):
    got = involvement.of(cid, ["place:saltmarch", "nocolon", "thread:nobody"])
    assert got == {ref: {"actors": [], "scenes": []}
                   for ref in ("place:saltmarch", "nocolon", "thread:nobody")}


def test_scene_actors_uses_colon_refs(cid):
    _chronicle(cid, {S1: ["characters/mara"]})
    _appearances(cid, {"pcs/seraphine": [S1, S2]})
    assert involvement.scene_actors(cid) == {
        S1: {"characters:mara", "pcs:seraphine"}, S2: {"pcs:seraphine"}}
