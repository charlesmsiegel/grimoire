"""GET /campaigns/{cid}/ledger — the continuity ledger view (#117).

One route, three sections, and a tolerance contract: a garbled plot.json or
commitments.json empties its own section and nothing else. The panel is a pure
render of this, so anything it must not crash on has to be answered here.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return TestClient(create_app())


def _campaign(client):
    wid = client.post("/api/worlds", json={"name": "W"}).json()["id"]
    return client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]


def test_unknown_campaign_is_404(client):
    assert client.get("/api/campaigns/nope/ledger").status_code == 404


def test_empty_campaign_returns_three_empty_sections(client):
    cid = _campaign(client)
    assert client.get(f"/api/campaigns/{cid}/ledger").json() == {
        "plot": [], "commitments": [], "chronicle": []}


def test_open_threads_and_commitments_carry_their_scene(client):
    cid = _campaign(client)
    sid = store.scenes.create_scene(cid, "The Pier at Dusk")
    store.plot.set_movement(cid, "the-ledger", "Find the ledger", "advanced",
                            "Winifred named it aloud.", sid)
    store.plot.set_movement(cid, "done", "Settled", "closed", "resolved", sid)
    store.commitments.set_movement(cid, "the-deadline", "Seraphine's midnight deadline",
                                   "threat", "open", "midnight",
                                   "Seraphine gave her until midnight.", sid)
    store.commitments.set_movement(cid, "paid", "The debt", "promise", "fulfilled",
                                   "", "Repaid in full.", sid)
    body = client.get(f"/api/campaigns/{cid}/ledger").json()

    assert [t["id"] for t in body["plot"]] == ["the-ledger"]      # closed thread gone
    assert body["plot"][0]["scene"]["title"] == "The Pier at Dusk"

    assert [c["id"] for c in body["commitments"]] == ["the-deadline"]   # fulfilled gone
    got = body["commitments"][0]
    assert got["kind"] == "threat" and got["status"] == "open" and got["due"] == "midnight"
    assert got["latest_beat"] == "Seraphine gave her until midnight."
    assert got["scene"]["title"] == "The Pier at Dusk"


def test_chronicle_is_newest_first_and_labelled_with_the_scene_title(client):
    cid = _campaign(client)
    first = store.scenes.create_scene(cid, "First Night")
    second = store.scenes.create_scene(cid, "Second Night")
    store.chronicle.absorb(cid, {"id": first, "one_line": "They met.", "date": "2026-07-05"})
    store.chronicle.absorb(cid, {"id": second, "one_line": "They argued.", "date": "2026-07-06"})
    rows = client.get(f"/api/campaigns/{cid}/ledger").json()["chronicle"]
    assert [r["one_line"] for r in rows] == ["They argued.", "They met."]
    assert rows[0]["title"] == "Second Night" and rows[0]["date"] == "2026-07-06"


def test_the_chronicle_section_is_capped(client):
    from grimoire.routes.campaigns import LEDGER_RECENT
    cid = _campaign(client)
    for i in range(LEDGER_RECENT + 5):
        sid = store.scenes.create_scene(cid, f"Night {i}")
        store.chronicle.absorb(cid, {"id": sid, "one_line": f"Beat {i}.", "date": ""})
    assert len(client.get(f"/api/campaigns/{cid}/ledger").json()["chronicle"]) == LEDGER_RECENT


@pytest.mark.parametrize("filename,section", [("plot.json", "plot"),
                                              ("commitments.json", "commitments")])
def test_a_garbled_file_empties_only_its_own_section(client, filename, section):
    cid = _campaign(client)
    sid = store.scenes.create_scene(cid, "Now")
    store.chronicle.absorb(cid, {"id": sid, "one_line": "It happened.", "date": ""})
    store.plot.set_movement(cid, "t", "A thread", "open", "beat", sid)
    store.commitments.set_movement(cid, "c", "A promise", "promise", "open", "", "beat", sid)
    (store.campaigns.campaign_root(cid) / filename).write_text("{ not json", encoding="utf-8")

    r = client.get(f"/api/campaigns/{cid}/ledger")
    assert r.status_code == 200                      # never a 500
    body = r.json()
    assert body[section] == []                       # the broken one is empty
    other = "commitments" if section == "plot" else "plot"
    assert len(body[other]) == 1                     # its neighbour is untouched
    assert len(body["chronicle"]) == 1


def test_a_garbled_chronicle_still_serves_plot_and_commitments(client):
    cid = _campaign(client)
    sid = store.scenes.create_scene(cid, "Now")
    store.plot.set_movement(cid, "t", "A thread", "open", "beat", sid)
    (store.campaigns.campaign_root(cid) / "chronicle.json").write_text("{ nope", encoding="utf-8")
    body = client.get(f"/api/campaigns/{cid}/ledger").json()
    assert [t["id"] for t in body["plot"]] == ["t"]
    assert body["plot"][0]["scene"]["date"] == ""    # the date label degrades, the row survives
    assert body["chronicle"] == []


def test_a_thread_whose_scene_was_deleted_still_lists(client):
    cid = _campaign(client)
    store.plot.set_movement(cid, "t", "A thread", "open", "beat", "0007-gone")
    row = client.get(f"/api/campaigns/{cid}/ledger").json()["plot"][0]
    assert row["scene"] == {"id": "0007-gone", "title": "0007-gone", "date": ""}


@pytest.mark.parametrize("body", ["[]", '{"s1": []}'])
def test_a_chronicle_of_the_wrong_shape_degrades_the_labels(client, body):
    """`read_chronicle` is a bare json.loads, so valid JSON of the wrong shape
    raises nothing and reaches `_scene` as a list. Before the shape check that
    500'd the whole view for any campaign with one open thread."""
    cid = _campaign(client)
    store.plot.set_movement(cid, "t", "A thread", "open", "beat", "s1")
    (store.campaigns.campaign_root(cid) / "chronicle.json").write_text(body, encoding="utf-8")
    r = client.get(f"/api/campaigns/{cid}/ledger")
    assert r.status_code == 200
    assert r.json()["plot"][0]["scene"]["date"] == ""     # the date label degrades


def test_a_fact_with_no_one_line_falls_back_to_its_summary(client):
    """A chronicle save may leave `one_line` empty; every other consumer falls
    back to `summary`. Without it the panel renders a blank Recent facts row."""
    cid = _campaign(client)
    sid = store.scenes.create_scene(cid, "Now")
    store.chronicle.absorb(cid, {"id": sid, "one_line": "", "date": "",
                                 "summary": "They argued until the tide turned."})
    rows = client.get(f"/api/campaigns/{cid}/ledger").json()["chronicle"]
    assert rows[0]["one_line"] == "They argued until the tide turned."


@pytest.mark.parametrize("bad_id", [[], {}, 7, None])
def test_a_row_whose_scene_id_is_not_a_string_still_lists(client, bad_id):
    """The row's OWN id is the third place a wrong shape arrives, and the one the
    document-level checks do not reach: the projections run outside `_tolerant`,
    so an unhashable `last_scene` reached `scenes_by_id.get` and 500'd."""
    import json
    cid = _campaign(client)
    (store.campaigns.campaign_root(cid) / "commitments.json").write_text(
        json.dumps({"x": {"title": "A promise", "kind": "promise", "status": "open",
                          "due": "", "beats": [], "last_scene": bad_id}}), encoding="utf-8")
    r = client.get(f"/api/campaigns/{cid}/ledger")
    assert r.status_code == 200
    row = r.json()["commitments"][0]
    assert row["title"] == "A promise"                 # the record still lists...
    assert row["scene"] == {"id": "", "title": "", "date": ""}   # ...minus its label


def test_a_chronicle_record_with_a_non_string_id_still_lists(client):
    cid = _campaign(client)
    (store.campaigns.campaign_root(cid) / "chronicle.json").write_text(
        '{"s1": {"id": [], "one_line": "It happened.", "date": "12 Harvestmoon"}}',
        encoding="utf-8")
    rows = client.get(f"/api/campaigns/{cid}/ledger").json()["chronicle"]
    assert rows[0]["one_line"] == "It happened."
    assert rows[0]["id"] == "" and rows[0]["date"] == "12 Harvestmoon"


def test_a_record_with_non_string_fields_still_renders_as_a_row(client):
    """`open_commitments` completes on a record with an object-valued title, so
    the route's tolerant boundary never fires and the object reaches
    `LedgerPanel`, where React refuses to render it and the panel goes blank.
    The projection is where the types have to be made true."""
    import json
    cid = _campaign(client)
    (store.campaigns.campaign_root(cid) / "commitments.json").write_text(json.dumps(
        {"x": {"title": {}, "kind": [], "status": "open", "due": {"a": 1},
               "beats": [{"scene": "s1", "text": ["nope"]}], "last_scene": "s1"}}),
        encoding="utf-8")
    row = client.get(f"/api/campaigns/{cid}/ledger").json()["commitments"][0]
    assert row == {"id": "x", "title": "x", "kind": "promise", "status": "open",
                   "due": "", "last_scene": "s1", "latest_beat": "",
                   "scene": {"id": "s1", "title": "s1", "date": ""}}
    assert all(isinstance(v, str) for k, v in row.items() if k != "scene")


def test_a_thread_with_non_string_fields_still_renders_as_a_row(client):
    """Same contract as the commitment above, on the section that reaches the
    same panel through a different store. `open_threads` is `plot.py`'s
    projection and does not coerce, so the route does."""
    import json
    cid = _campaign(client)
    (store.campaigns.campaign_root(cid) / "plot.json").write_text(json.dumps(
        {"t": {"title": {}, "status": [], "beats": [{"scene": "s1", "text": "x"}],
               "last_scene": "s1"}}), encoding="utf-8")
    row = client.get(f"/api/campaigns/{cid}/ledger").json()["plot"][0]
    assert row["id"] == "t" and row["title"] == "t" and row["status"] == "open"
    assert all(isinstance(v, str) for k, v in row.items() if k != "scene")


def test_one_unsortable_chronicle_id_does_not_cost_the_other_facts(client):
    """The recent slice is taken by sorting on the record ids, and comparing a
    list to a string raises — inside `_tolerant`, which empties the WHOLE
    section. One hand-edited row must not take every good fact with it."""
    import json
    cid = _campaign(client)
    (store.campaigns.campaign_root(cid) / "chronicle.json").write_text(json.dumps({
        "s1": {"id": "s1", "one_line": "First.", "date": ""},
        "s2": {"id": [], "one_line": "Broken.", "date": ""},
        "s3": {"id": "s3", "one_line": "Third.", "date": ""},
    }), encoding="utf-8")
    rows = client.get(f"/api/campaigns/{cid}/ledger").json()["chronicle"]
    # Newest first, the unsortable row sorting as "" and so landing oldest.
    assert [r["one_line"] for r in rows] == ["Third.", "First.", "Broken."]


def test_a_scene_label_with_a_non_string_date_still_renders(client):
    """`sceneNote` interpolates the label's own `date`, so a hand-edited
    chronicle entry reaches the panel as `[object Object]`. The recent-facts
    projection coerces its own copy; this nested one feeds the plot and
    commitment sections and was missed."""
    import json
    cid = _campaign(client)
    store.commitments.set_movement(cid, "x", "A promise", "promise", "open",
                                   "", "Sworn.", "s1")
    (store.campaigns.campaign_root(cid) / "chronicle.json").write_text(
        json.dumps({"s1": {"id": "s1", "one_line": "It happened.", "date": {"y": 1}}}),
        encoding="utf-8")
    body = client.get(f"/api/campaigns/{cid}/ledger").json()
    assert body["commitments"][0]["scene"]["date"] == ""
    assert all(isinstance(v, str) for v in body["commitments"][0]["scene"].values())


def test_a_record_that_is_not_a_dict_is_skipped(client):
    import json
    cid = _campaign(client)
    (store.campaigns.campaign_root(cid) / "commitments.json").write_text(
        json.dumps({"broken": [], "good": {"title": "A promise", "kind": "promise",
                                           "status": "open", "beats": [], "last_scene": ""}}),
        encoding="utf-8")
    rows = client.get(f"/api/campaigns/{cid}/ledger").json()["commitments"]
    assert [r["id"] for r in rows] == ["good"]
