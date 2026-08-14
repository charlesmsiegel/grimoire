"""GET /campaigns/{cid}/timeline — the play timeline (#198).

Every scene in play order, as a card, with the plot beats that landed in it.
The ledger (#117) answers *what is still open*; this answers *what happened, in
order*, and it is the first reader of the beats `plot.set_movement` has been
writing since Phase 1.

Two contracts are the point of this file:

- **It works before the absorb.** `one_line`, `summary` and `done` are written
  by `mark_absorbed`, so most in-progress scenes carry none of them. A scene
  with no chronicle record must still render as a card — titled, dated and
  ordered — rather than vanish or arrive blank.
- **The tolerance the ledger set.** A garbled plot.json costs the beats and not
  the scenes; a garbled chronicle.json costs the summaries and not the cards.
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
    return TestClient(create_app())


def _campaign(client):
    wid = client.post("/api/worlds", json={"name": "W"}).json()["id"]
    return client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]


def _timeline(client, cid):
    r = client.get(f"/api/campaigns/{cid}/timeline")
    assert r.status_code == 200
    return r.json()


def _absorbed(cid, title, one_line, date="", location=""):
    """A scene taken all the way through the absorb, the way the app writes it:
    the chronicle record and the frontmatter mark are two writes, and a reader
    that only consults one of them is reading half the save."""
    sid = store.scenes.create_scene(cid, title)
    store.chronicle.absorb(cid, {"id": sid, "one_line": one_line, "summary": f"{one_line} At length.",
                                 "date": date, "location": location})
    store.scenes.mark_absorbed(cid, sid, one_line, f"{one_line} At length.")
    return sid


# ---- shape and 404 ---------------------------------------------------------

def test_unknown_campaign_is_404(client):
    assert client.get("/api/campaigns/nope/timeline").status_code == 404


def test_an_empty_campaign_has_no_scenes_and_no_threads(client):
    assert _timeline(client, _campaign(client)) == {"scenes": [], "threads": []}


# ---- ordering --------------------------------------------------------------

def test_scenes_come_back_in_play_order_not_recency(client):
    """`list_scenes` sorts newest-updated first, which is right for a rail and
    backwards for a timeline. Scene ids lead with the sequence number precisely
    so filename order is play order (`store/scene_ids.py`), so that is the sort."""
    cid = _campaign(client)
    first = store.scenes.create_scene(cid, "First Light")
    second = store.scenes.create_scene(cid, "The Long Tide")
    third = store.scenes.create_scene(cid, "Verdigris & Ash")
    # Touch the oldest last, so recency and play order disagree.
    store.scenes.mark_absorbed(cid, first, "They met.", "")

    assert [s["id"] for s in _timeline(client, cid)["scenes"]] == [first, second, third]


# ---- the card --------------------------------------------------------------

def test_an_absorbed_scene_carries_its_one_line_summary_date_and_location(client):
    cid = _campaign(client)
    sid = _absorbed(cid, "The Long Tide", "They argued until the tide turned.",
                    date="3 Reaping", location="The Pier")
    (card,) = _timeline(client, cid)["scenes"]
    assert card["id"] == sid and card["title"] == "The Long Tide"
    assert card["one_line"] == "They argued until the tide turned."
    assert card["summary"] == "They argued until the tide turned. At length."
    assert card["date"] == "3 Reaping"
    assert card["location"] == "The Pier"
    assert card["done"] is True


def test_a_scene_that_was_never_absorbed_still_gets_a_card(client):
    """The gotcha the issue names: `one_line`/`summary`/`done` exist only after
    `mark_absorbed`, and a campaign being played is normally one scene ahead of
    its absorb. An unabsorbed scene is the ordinary case, not a degraded one."""
    cid = _campaign(client)
    sid = store.scenes.create_scene(cid, "The Priory Door")
    (card,) = _timeline(client, cid)["scenes"]
    assert card["id"] == sid and card["title"] == "The Priory Door"
    assert card["one_line"] == "" and card["summary"] == "" and card["location"] == ""
    assert card["done"] is False
    assert card["beats"] == []


def test_a_card_with_no_one_line_falls_back_to_its_summary(client):
    """The fallback every other chronicle consumer uses
    (`context.story._story_entries`, `get_ledger`): a save may leave `one_line`
    empty, and a card with only its title is a blank line rather than a scene."""
    cid = _campaign(client)
    sid = store.scenes.create_scene(cid, "Now")
    store.chronicle.absorb(cid, {"id": sid, "one_line": "",
                                 "summary": "They argued until the tide turned."})
    (card,) = _timeline(client, cid)["scenes"]
    assert card["one_line"] == "They argued until the tide turned."


def test_the_scenes_own_moment_dates_the_card_ahead_of_the_chronicles(client):
    """`time_history` is stamped the moment a scene gets a datetime, absorbed or
    not, and its FIRST entry is when the scene opened — which is what dates a
    card. The chronicle's `date` is the scene's LAST moment and only exists post
    absorb, so it is the fallback rather than the source."""
    cid = _campaign(client)
    sid = store.scenes.create_scene(cid, "The Long Tide")
    store.scenes.set_datetime(cid, sid, "2026-07-05")
    sid = store.scenes.list_scenes(cid)[0]["id"]     # the first set renames the scene
    store.scenes.set_datetime(cid, sid, "2026-07-06")
    store.chronicle.absorb(cid, {"id": sid, "one_line": "They argued.", "date": "2026-07-06"})

    (card,) = _timeline(client, cid)["scenes"]
    assert card["date"] == "2026-07-05"


def test_a_dateless_scene_borrows_the_chronicles_date(client):
    cid = _campaign(client)
    sid = store.scenes.create_scene(cid, "Now")
    store.chronicle.absorb(cid, {"id": sid, "one_line": "It happened.", "date": "12 Harvestmoon"})
    (card,) = _timeline(client, cid)["scenes"]
    assert card["date"] == "12 Harvestmoon"


# ---- thread beats ("thread pairs") -----------------------------------------

def test_a_beat_lands_on_the_scene_it_was_recorded_in_carrying_its_thread(client):
    """The join this view exists for. `plot.set_movement` has recorded
    `(scene, text)` per thread since Phase 1 and nothing has ever read them back
    per scene; the pair the issue asks for is that beat beside its thread's
    title and status."""
    cid = _campaign(client)
    early = _absorbed(cid, "The Priory Door", "They met.")
    late = _absorbed(cid, "The Long Tide", "They argued.")
    store.plot.set_movement(cid, "the-sea-wall", "The sea wall", "open",
                            "Winifred named the debt.", early)
    store.plot.set_movement(cid, "the-sea-wall", "The sea wall", "advanced",
                            "Seraphine refused to pay it.", late)

    cards = {s["id"]: s for s in _timeline(client, cid)["scenes"]}
    assert cards[early]["beats"] == [
        {"thread": "the-sea-wall", "title": "The sea wall", "status": "advanced",
         "text": "Winifred named the debt."}]
    assert cards[late]["beats"] == [
        {"thread": "the-sea-wall", "title": "The sea wall", "status": "advanced",
         "text": "Seraphine refused to pay it."}]


def test_a_closed_threads_beats_are_still_history(client):
    """`open_threads` drops a closed thread, which is right for a ledger of what
    is still owed and wrong for a record of what happened: a thread that
    resolved did not un-happen."""
    cid = _campaign(client)
    sid = _absorbed(cid, "Verdigris & Ash", "It ended.")
    store.plot.set_movement(cid, "settled", "The reckoning", "closed", "Paid in full.", sid)
    (card,) = _timeline(client, cid)["scenes"]
    assert [b["thread"] for b in card["beats"]] == ["settled"]
    assert card["beats"][0]["status"] == "closed"


def test_every_thread_with_a_beat_on_a_card_is_offered_as_a_filter(client):
    """The chip roster. Sorted by title so the filter reads as a list of
    threads rather than of slugs."""
    cid = _campaign(client)
    sid = _absorbed(cid, "The Long Tide", "They argued.")
    store.plot.set_movement(cid, "z-thread", "A debt unpaid", "open", "Named.", sid)
    store.plot.set_movement(cid, "a-thread", "The sea wall", "closed", "Mended.", sid)

    assert _timeline(client, cid)["threads"] == [
        {"id": "z-thread", "title": "A debt unpaid", "status": "open"},
        {"id": "a-thread", "title": "The sea wall", "status": "closed"}]


def test_a_thread_whose_beats_all_point_at_deleted_scenes_is_not_offered(client):
    """A beat can only be shown on the card of the scene it names, so an orphan
    has nowhere to land — and a chip that filters to nothing is worse than no
    chip. `plot.repoint_scenes` follows renames, so this is the deleted case."""
    cid = _campaign(client)
    _absorbed(cid, "The Long Tide", "They argued.")
    store.plot.set_movement(cid, "ghost", "A thread", "open", "beat", "0007--gone")
    body = _timeline(client, cid)
    assert body["threads"] == []
    assert [s["beats"] for s in body["scenes"]] == [[]]


# ---- tolerance -------------------------------------------------------------

def test_a_garbled_plot_file_costs_the_beats_and_not_the_scenes(client):
    cid = _campaign(client)
    _absorbed(cid, "The Long Tide", "They argued.", date="3 Reaping")
    (store.campaigns.campaign_root(cid) / "plot.json").write_text("{ not json", encoding="utf-8")

    body = _timeline(client, cid)
    assert body["threads"] == []
    (card,) = body["scenes"]
    assert card["one_line"] == "They argued." and card["beats"] == []


def test_a_garbled_chronicle_costs_the_summaries_and_not_the_cards(client):
    cid = _campaign(client)
    sid = _absorbed(cid, "The Long Tide", "They argued.")
    store.plot.set_movement(cid, "t", "The sea wall", "open", "Named.", sid)
    (store.campaigns.campaign_root(cid) / "chronicle.json").write_text("{ nope", encoding="utf-8")

    (card,) = _timeline(client, cid)["scenes"]
    assert card["title"] == "The Long Tide"
    assert card["one_line"] == "" and card["date"] == ""
    assert [b["text"] for b in card["beats"]] == ["Named."]


@pytest.mark.parametrize("doc", ["[]", '{"s1": []}', '{"s1": "nope"}'])
def test_a_chronicle_of_the_wrong_shape_degrades_the_labels(client, doc):
    """`read_chronicle` is a bare `json.loads`, so valid JSON of the wrong shape
    raises nothing — the correction `get_ledger` needed for the same reason."""
    cid = _campaign(client)
    store.scenes.create_scene(cid, "The Long Tide")
    (store.campaigns.campaign_root(cid) / "chronicle.json").write_text(doc, encoding="utf-8")
    (card,) = _timeline(client, cid)["scenes"]
    assert card["title"] == "The Long Tide" and card["one_line"] == ""


def test_a_thread_with_non_string_fields_still_renders_its_beat(client):
    """plot.json is hand-editable and read by a bare `json.loads`, so an
    object-valued title reaches React, which refuses an object as a child and
    blanks the card around it. The projection is where the types are made true."""
    cid = _campaign(client)
    sid = _absorbed(cid, "The Long Tide", "They argued.")
    (store.campaigns.campaign_root(cid) / "plot.json").write_text(json.dumps(
        {"t": {"title": {}, "status": [], "last_scene": sid,
               "beats": [{"scene": sid, "text": "Named."}]}}), encoding="utf-8")

    (card,) = _timeline(client, cid)["scenes"]
    assert card["beats"] == [{"thread": "t", "title": "t", "status": "open", "text": "Named."}]


@pytest.mark.parametrize("bad", [[], {}, 7, None])
def test_a_beat_whose_scene_is_not_a_string_is_stepped_over(client, bad):
    """An unhashable `scene` is not a missing lookup, it is a raise — and the
    projection runs outside the tolerant read, so it would 500 the whole view
    rather than cost one beat."""
    cid = _campaign(client)
    sid = _absorbed(cid, "The Long Tide", "They argued.")
    (store.campaigns.campaign_root(cid) / "plot.json").write_text(json.dumps(
        {"t": {"title": "A thread", "status": "open", "last_scene": sid,
               "beats": [{"scene": bad, "text": "Lost."}, {"scene": sid, "text": "Kept."}]}}),
        encoding="utf-8")

    (card,) = _timeline(client, cid)["scenes"]
    assert [b["text"] for b in card["beats"]] == ["Kept."]


def test_a_thread_record_that_is_not_a_dict_is_skipped(client):
    cid = _campaign(client)
    sid = _absorbed(cid, "The Long Tide", "They argued.")
    (store.campaigns.campaign_root(cid) / "plot.json").write_text(json.dumps(
        {"broken": [], "good": {"title": "A thread", "status": "open", "last_scene": sid,
                                "beats": [{"scene": sid, "text": "Named."}]}}), encoding="utf-8")
    assert [t["id"] for t in _timeline(client, cid)["threads"]] == ["good"]


def test_the_whole_view_is_read_under_one_campaign_lock(client, monkeypatch):
    """The same reason the ledger and the briefing take it: `put_chronicle`
    records the chronicle and then applies the absorb's plot edits under one
    hold, so an unlocked pair of reads can catch that sequence half done and
    print a scene marked absorbed whose beat has not landed yet."""
    cid = _campaign(client)
    held = {}

    def _watch(name, real):
        def wrapper(*a, **kw):
            held[name] = store.locks.campaign_lock(cid)._is_owned()
            return real(*a, **kw)
        return wrapper

    # `store.scenes.read`, not the `store.scenes` facade: this module binds the
    # submodule, which is the import form the guard requires and the only name
    # a patch can intercept (see `store/__init__.py`'s docstring).
    monkeypatch.setattr(store.scenes.read, "list_scenes",
                        _watch("scenes", store.scenes.read.list_scenes))
    monkeypatch.setattr(store.chronicle, "read_chronicle",
                        _watch("chronicle", store.chronicle.read_chronicle))
    monkeypatch.setattr(store.plot, "read", _watch("plot", store.plot.read))
    assert client.get(f"/api/campaigns/{cid}/timeline").status_code == 200
    assert held == {"scenes": True, "chronicle": True, "plot": True}
