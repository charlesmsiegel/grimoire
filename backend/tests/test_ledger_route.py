"""GET /campaigns/{cid}/ledger — the continuity ledger view (#117).

One route, six sections, and a tolerance contract: a garbled plot.json,
commitments.json, facts.json or relationships.json empties its own section and
nothing else. The page is a pure render of this, so anything it must not crash
on has to be answered here.

`retired` and `relationships` arrived with screen 4e, and the first is the point
of that screen: a fact that stopped being true, the scene that ended it and the
fact that replaced it have been on disk since #114 and never left the server.
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
    return client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]


def test_unknown_campaign_is_404(client):
    assert client.get("/api/campaigns/nope/ledger").status_code == 404


def test_empty_campaign_returns_six_empty_sections(client):
    cid = _campaign(client)
    assert client.get(f"/api/campaigns/{cid}/ledger").json() == {
        "plot": [], "commitments": [], "facts": [], "retired": [],
        "relationships": [], "chronicle": [],
        # The campaign's staleness threshold rides beside the sections (#103):
        # the panel needs it to say what "40 days untouched" means here.
        "stale_after_days": store.calendars.STALE_AFTER_DAYS}


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
                                              ("commitments.json", "commitments"),
                                              ("facts.json", "facts")])
def test_a_garbled_file_empties_only_its_own_section(client, filename, section):
    cid = _campaign(client)
    sid = store.scenes.create_scene(cid, "Now")
    store.chronicle.absorb(cid, {"id": sid, "one_line": "It happened.", "date": ""})
    store.plot.set_movement(cid, "t", "A thread", "open", "beat", sid)
    store.commitments.set_movement(cid, "c", "A promise", "promise", "open", "", "beat", sid)
    store.facts.record(cid, "The pier is condemned.", "the third night", sid)
    (store.campaigns.campaign_root(cid) / filename).write_text("{ not json", encoding="utf-8")

    r = client.get(f"/api/campaigns/{cid}/ledger")
    assert r.status_code == 200                      # never a 500
    body = r.json()
    assert body[section] == []                       # the broken one is empty
    for other in {"plot", "commitments", "facts"} - {section}:
        assert len(body[other]) == 1                 # its neighbours are untouched
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
                   "scene": {"id": "s1", "title": "s1", "date": ""},
                   # Aged like every other row (#103), and unaged in substance:
                   # this campaign has no clock, so there is no present to
                   # measure from and every number is honestly None.
                   "aging": {"state": "ok", "days_since": None,
                             "days_over": None, "due_in": None}}
    # `scene` and `aging` are the row's two structured fields; the rest is text
    # the panel interpolates, which is what this test is about.
    assert all(isinstance(v, str) for k, v in row.items() if k not in ("scene", "aging"))


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
    # `scene` and `aging` are the two structured fields on a row; everything
    # else is text the panel interpolates, which is what this is about.
    assert all(isinstance(v, str) for k, v in row.items() if k not in ("scene", "aging"))


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


def test_the_three_sections_are_read_under_one_campaign_lock(client, monkeypatch):
    """A save writes these files one after another: `put_chronicle` holds the
    campaign lock while it records the chronicle and then applies the absorb's
    plot and commitment edits. Reading without that lock can catch the sequence
    half done — a new fact beside the still-open commitment the same save
    fulfilled — and the panel keeps the contradiction until something bumps its
    revision."""
    cid = _campaign(client)
    store.commitments.set_movement(cid, "x", "A promise", "promise", "open",
                                   "", "Sworn.", "s1")
    held = {}

    def _watch(name, real):
        def wrapper(*a, **kw):
            held[name] = store.locks.campaign_lock(cid)._is_owned()
            return real(*a, **kw)
        return wrapper

    monkeypatch.setattr(store.scenes, "list_scenes",
                        _watch("scenes", store.scenes.list_scenes))
    monkeypatch.setattr(store.chronicle, "read_chronicle",
                        _watch("chronicle", store.chronicle.read_chronicle))
    monkeypatch.setattr(store.plot, "open_threads",
                        _watch("plot", store.plot.open_threads))
    monkeypatch.setattr(store.commitments, "open_commitments",
                        _watch("commitments", store.commitments.open_commitments))
    assert client.get(f"/api/campaigns/{cid}/ledger").status_code == 200
    assert held == {"scenes": True, "chronicle": True,
                    "plot": True, "commitments": True}


def test_standing_facts_carry_the_scene_that_recorded_them(client):
    """"recorded in", not "last moved in": a fact's text never changes after it
    is written, and a fact that stopped being true is retired off this list
    rather than rewritten — so the scene it carries is its first one, and the
    ledger can be read as dated."""
    cid = _campaign(client)
    sid = store.scenes.create_scene(cid, "The Pier at Dusk")
    store.chronicle.absorb(cid, {"id": sid, "one_line": "They met.", "date": "2026-07-05"})
    gone = store.facts.record(cid, "The pier is open to traffic.", "the first night", sid)
    store.facts.record(cid, "The pier is condemned.", "the third night", sid, supersedes=gone)

    rows = client.get(f"/api/campaigns/{cid}/ledger").json()["facts"]
    assert [f["text"] for f in rows] == ["The pier is condemned."]   # the retired one is gone
    assert rows[0]["date"] == "the third night"
    assert rows[0]["scene"] == {"id": sid, "title": "The Pier at Dusk", "date": "2026-07-05"}


def test_a_fact_whose_scene_is_the_wrong_shape_loses_its_label_not_the_view(client):
    """The projections run outside `_tolerant`, so an unhashable `scene` reaching
    `scenes_by_id.get` would 500 the whole view rather than costing one label."""
    cid = _campaign(client)
    (store.campaigns.campaign_root(cid) / "facts.json").write_text(
        json.dumps({"f1": {"text": "The pier is condemned.", "date": "the third night",
                           "scene": ["nope"], "status": "active"}}), encoding="utf-8")
    r = client.get(f"/api/campaigns/{cid}/ledger")
    assert r.status_code == 200
    (row,) = r.json()["facts"]
    assert row["text"] == "The pier is condemned."
    assert row["scene"] == {"id": "", "title": "", "date": ""}


# ---- the supersession chain (screen 4e) ------------------------------------

def _dated_scene(cid, title, date):
    sid = store.scenes.create_scene(cid, title)
    store.chronicle.absorb(cid, {"id": sid, "one_line": f"{title} happened.", "date": date})
    return sid


def test_a_superseded_fact_leaves_the_server_with_both_of_its_scenes(client):
    """The whole reason this route grew. The retired half of facts.json has been
    written since #114 and no reader could see it, so the ledger could show that
    a truth stands but never that another one stopped — or which fact ended it.

    Two scenes on the row, and neither is decoration: `scene` is where the fact
    was RECORDED, which is the date it keeps on the ledger, and `retired_scene`
    is where it ENDED, which is the only thing on the row saying when it stopped
    being true.
    """
    cid = _campaign(client)
    early = _dated_scene(cid, "The Priory Door", "28 Sowing")
    late = _dated_scene(cid, "The Long Tide", "3 Reaping")
    old = store.facts.record(cid, "Mara speaks of the drowned freely.", "28 Sowing", early)
    new = store.facts.record(cid, "Mara will not speak of the drowned aloud.", "3 Reaping",
                             late, supersedes=old)

    body = client.get(f"/api/campaigns/{cid}/ledger").json()
    assert [f["id"] for f in body["facts"]] == [new]          # standing: unchanged
    (gone,) = body["retired"]
    assert gone["id"] == old
    assert gone["text"] == "Mara speaks of the drowned freely."
    assert gone["superseded_by"] == new
    assert gone["scene"] == {"id": early, "title": "The Priory Door", "date": "28 Sowing"}
    assert gone["retired_scene"] == {"id": late, "title": "The Long Tide", "date": "3 Reaping"}


def test_a_chain_three_deep_reads_end_to_end(client):
    """f1 ← f2 ← f3, with only f3 standing. Each retired record points at the one
    that replaced it, so the view can walk the chain back from the standing fact
    through everything it descends from — which is what a ledger is for."""
    cid = _campaign(client)
    sids = [_dated_scene(cid, f"Night {n}", f"{n} Reaping") for n in (1, 2, 3)]
    f1 = store.facts.record(cid, "The bridge stands.", "", sids[0])
    f2 = store.facts.record(cid, "The bridge is closed.", "", sids[1], supersedes=f1)
    f3 = store.facts.record(cid, "The bridge is rubble.", "", sids[2], supersedes=f2)

    body = client.get(f"/api/campaigns/{cid}/ledger").json()
    assert [f["id"] for f in body["facts"]] == [f3]
    assert [(f["id"], f["superseded_by"]) for f in body["retired"]] == [(f1, f2), (f2, f3)]


def test_a_fact_retired_outright_names_no_replacement(client):
    """Retirement's other shape — it stopped applying with nothing to say in its
    place — and the one the view's SHOW RETIRED toggle is actually for. A blank
    `superseded_by` is what separates the two, so it has to survive the trip."""
    cid = _campaign(client)
    sid = _dated_scene(cid, "The Long Tide", "3 Reaping")
    fid = store.facts.record(cid, "The gate is watched.", "", sid)
    store.facts.retire(cid, fid, sid)

    (row,) = client.get(f"/api/campaigns/{cid}/ledger").json()["retired"]
    assert row["superseded_by"] == ""
    assert row["retired_scene"]["title"] == "The Long Tide"


def test_a_retired_fact_whose_retiring_scene_was_deleted_still_lists(client):
    """Same degradation the standing sections already promise: the label falls
    back to the id and the row survives. A deleted scene must not be able to
    delete the record of what it ended."""
    cid = _campaign(client)
    (store.campaigns.campaign_root(cid) / "facts.json").write_text(json.dumps(
        {"f1": {"text": "The gate is watched.", "date": "", "scene": "0001-gone",
                "status": "retired", "superseded_by": "", "retired_scene": "0009-also-gone"}}),
        encoding="utf-8")
    (row,) = client.get(f"/api/campaigns/{cid}/ledger").json()["retired"]
    assert row["scene"] == {"id": "0001-gone", "title": "0001-gone", "date": ""}
    assert row["retired_scene"] == {"id": "0009-also-gone", "title": "0009-also-gone",
                                    "date": ""}


@pytest.mark.parametrize("bad", [[], {}, 7, None])
def test_a_retiring_scene_of_the_wrong_shape_costs_its_label_not_the_view(client, bad):
    """`retired_scene` is a second scene id per row, and so a second unhashable
    key reaching `scenes_by_id.get`. These projections run outside `_tolerant`,
    so without the coercion this is a 500 rather than a missing label."""
    cid = _campaign(client)
    (store.campaigns.campaign_root(cid) / "facts.json").write_text(json.dumps(
        {"f1": {"text": "The gate is watched.", "date": "", "scene": "s1",
                "status": "retired", "superseded_by": bad, "retired_scene": bad}}),
        encoding="utf-8")
    r = client.get(f"/api/campaigns/{cid}/ledger")
    assert r.status_code == 200
    (row,) = r.json()["retired"]
    assert row["text"] == "The gate is watched."
    assert row["superseded_by"] == ""
    assert row["retired_scene"] == {"id": "", "title": "", "date": ""}


def test_a_garbled_facts_file_empties_both_halves_and_nothing_else(client):
    """The two fact sections are one file, so they fail together — but the
    tolerance is still per section: the four others are untouched."""
    cid = _campaign(client)
    sid = _dated_scene(cid, "Now", "")
    store.plot.set_movement(cid, "t", "A thread", "open", "beat", sid)
    store.commitments.set_movement(cid, "c", "A promise", "promise", "open", "", "beat", sid)
    (store.campaigns.campaign_root(cid) / "facts.json").write_text("{ not json",
                                                                  encoding="utf-8")
    body = client.get(f"/api/campaigns/{cid}/ledger").json()
    assert body["facts"] == [] and body["retired"] == []
    assert len(body["plot"]) == 1 and len(body["commitments"]) == 1
    assert len(body["chronicle"]) == 1


def test_both_halves_of_the_ledger_are_read_under_the_one_lock(client, monkeypatch):
    """`facts.record` retires a fact and files its replacement in ONE write, so a
    pair of unlocked reads is exactly where a chain shows up with both ends
    standing, or with neither — the contradiction this view must never print."""
    cid = _campaign(client)
    held = {}

    def _watch(name, real):
        def wrapper(*a, **kw):
            held[name] = store.locks.campaign_lock(cid)._is_owned()
            return real(*a, **kw)
        return wrapper

    monkeypatch.setattr(store.facts, "active", _watch("active", store.facts.active))
    monkeypatch.setattr(store.facts, "retired", _watch("retired", store.facts.retired))
    monkeypatch.setattr(store.relationships, "read",
                        _watch("relationships", store.relationships.read))
    assert client.get(f"/api/campaigns/{cid}/ledger").status_code == 200
    assert held == {"active": True, "retired": True, "relationships": True}


# ---- relationships ---------------------------------------------------------

def _cast(client):
    """A campaign with two characters the ledger can name."""
    wid = client.post("/api/worlds", json={"name": "W"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    mara, _v = store.characters.create_character(store.worlds.world_root(wid), "Sister Mara")
    reeve, _v2 = store.characters.create_character(store.worlds.world_root(wid), "The Reeve")
    return cid, f"characters:{mara}", f"characters:{reeve}"


def test_feelings_and_bonds_arrive_as_one_named_list(client):
    """Two shapes, one section: a feeling is directed and metered, a bond is
    symmetric and dated. The reader's question is what stands between two
    people, and answering it in two tables makes them read both to find out."""
    cid, mara, reeve = _cast(client)
    sid = _dated_scene(cid, "The Long Tide", "3 Reaping")
    store.relationships.set_feeling(cid, mara, reeve, 1, 0, 4, "He took the money.")
    store.relationships.set_bond(cid, mara, reeve, "kin", sid)

    rows = client.get(f"/api/campaigns/{cid}/ledger").json()["relationships"]
    feeling = next(r for r in rows if r["kind"] == "feeling")
    assert feeling["a_name"] == "Sister Mara" and feeling["b_name"] == "The Reeve"
    assert (feeling["trust"], feeling["affection"], feeling["tension"]) == (1, 0, 4)
    assert feeling["note"] == "He took the money."
    assert feeling["scene"] == {"id": "", "title": "", "date": ""}   # a feeling has no date

    bond = next(r for r in rows if r["kind"] == "bond")
    assert bond["type"] == "kin"
    assert bond["scene"] == {"id": sid, "title": "The Long Tide", "date": "3 Reaping"}


def test_a_meter_is_clamped_and_a_nonsense_one_reads_as_zero(client):
    """The client draws five pips. A hand-edited 9 would draw four that do not
    exist, and a string would draw none of them and take the section down."""
    cid, mara, reeve = _cast(client)
    (store.campaigns.campaign_root(cid) / "relationships.json").write_text(json.dumps(
        {"feelings": {f"{mara}->{reeve}": {"trust": 9, "affection": -3, "tension": "high",
                                           "note": {"nope": 1}}}, "bonds": {}}),
        encoding="utf-8")
    (row,) = client.get(f"/api/campaigns/{cid}/ledger").json()["relationships"]
    assert (row["trust"], row["affection"], row["tension"]) == (5, 0, 0)
    assert row["note"] == ""


def test_an_actor_with_no_readable_card_falls_back_to_its_id(client):
    """A name is the least of what the row says: the meters and the note are
    still true about two actors whose cards this campaign no longer holds."""
    cid = _campaign(client)
    (store.campaigns.campaign_root(cid) / "relationships.json").write_text(json.dumps(
        {"feelings": {"characters:ghost->characters:other": {
            "trust": 2, "affection": 2, "tension": 0, "note": ""}}, "bonds": {}}),
        encoding="utf-8")
    (row,) = client.get(f"/api/campaigns/{cid}/ledger").json()["relationships"]
    assert row["a_name"] == "ghost" and row["b_name"] == "other"


@pytest.mark.parametrize("doc", ["{ not json", "[]", '{"feelings": [], "bonds": 7}'])
def test_a_garbled_relationships_file_empties_only_its_own_section(client, doc):
    """Valid JSON of the wrong shape counts as garbled here for the reason the
    chronicle check gives: `relationships.read` raises nothing for it, so the
    shape is checked where it is used."""
    cid = _campaign(client)
    sid = _dated_scene(cid, "Now", "")
    store.facts.record(cid, "The pier is condemned.", "", sid)
    (store.campaigns.campaign_root(cid) / "relationships.json").write_text(
        doc, encoding="utf-8")
    r = client.get(f"/api/campaigns/{cid}/ledger")
    assert r.status_code == 200
    assert r.json()["relationships"] == []
    assert len(r.json()["facts"]) == 1


def test_a_relationship_record_that_is_not_a_dict_is_skipped(client):
    cid, mara, reeve = _cast(client)
    (store.campaigns.campaign_root(cid) / "relationships.json").write_text(json.dumps(
        {"feelings": {f"{mara}->{reeve}": ["nope"]},
         "bonds": {f"{mara}|{reeve}": {"type": "kin", "since_scene": ""}}}), encoding="utf-8")
    rows = client.get(f"/api/campaigns/{cid}/ledger").json()["relationships"]
    assert [r["kind"] for r in rows] == ["bond"]
