"""GET /campaigns/{cid}/scenes/{sid}/briefing — the pre-scene briefing (#118).

The per-scene sibling of the continuity ledger (#117): the same open threads and
commitments, narrowed by *who is on stage here*, plus the relationships between
them and the one fact that came immediately before. Same tolerance contract as
the ledger — a garbled file empties its own section and nothing else — because
the same kind of panel renders it.
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
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    return wid, cid


def _pc(wid, name):
    """A PC in the world, ready to be seated as a scene's player."""
    return store.pcs.create_pc(store.worlds.world_root(wid), name, [],
                               persona=store.pcs.blank_persona(name))


def _npc(wid, name):
    return store.characters.create_character(store.worlds.world_root(wid), name)


def _brief(client, cid, sid):
    r = client.get(f"/api/campaigns/{cid}/scenes/{sid}/briefing")
    assert r.status_code == 200
    return r.json()


# ---- shape and 404 ---------------------------------------------------------

def test_unknown_scene_is_404(client):
    _wid, cid = _campaign(client)
    assert client.get(f"/api/campaigns/{cid}/scenes/nope/briefing").status_code == 404


def test_unknown_campaign_is_404(client):
    """The detail, not just the status: a route that was never registered also
    answers 404, so a bare status assertion would pass with the feature deleted.
    A scene path is built from the campaign root, so an unusable campaign id
    surfaces as the scene 404 rather than a 500."""
    r = client.get("/api/campaigns/nope/scenes/s1/briefing")
    assert r.status_code == 404 and r.json()["detail"] == "scene not found"


def test_a_fresh_scene_briefs_empty(client):
    _wid, cid = _campaign(client)
    sid = store.scenes.create_scene(cid, "The Pier at Dusk")
    assert _brief(client, cid, sid) == {
        "focus": [], "plot": [], "commitments": [], "relationships": [],
        "last_time": None}


# ---- what is still open ----------------------------------------------------

def test_open_threads_and_commitments_list_resolved_ones_do_not(client):
    _wid, cid = _campaign(client)
    sid = store.scenes.create_scene(cid, "The Pier at Dusk")
    store.plot.set_movement(cid, "the-ledger", "Find the ledger", "advanced",
                            "Winifred named it aloud.", sid)
    store.plot.set_movement(cid, "done", "Settled", "closed", "resolved", sid)
    store.commitments.set_movement(cid, "the-deadline", "Seraphine's midnight deadline",
                                   "threat", "open", "midnight",
                                   "Seraphine gave her until midnight.", sid)
    store.commitments.set_movement(cid, "paid", "The debt", "promise", "fulfilled",
                                   "", "Repaid in full.", sid)
    body = _brief(client, cid, sid)

    assert [t["id"] for t in body["plot"]] == ["the-ledger"]
    assert body["plot"][0]["latest_beat"] == "Winifred named it aloud."
    assert [c["id"] for c in body["commitments"]] == ["the-deadline"]
    assert body["commitments"][0]["due"] == "midnight"


# ---- the involvement join, which is what makes this a briefing -------------

def test_a_thread_moved_where_the_pc_stood_is_flagged_and_one_that_was_not_is_not(client):
    """The whole point of the view: two open threads, one of which this scene's
    player was on stage for. Both list; only one carries her name."""
    wid, cid = _campaign(client)
    pid, vid = _pc(wid, "Winifred Vance")
    hers = store.scenes.create_scene(cid, "The Pier at Dusk")
    theirs = store.scenes.create_scene(cid, "A Room Elsewhere")
    store.appearances.appear(cid, hers, "pcs", pid, vid, "player")
    now = store.scenes.create_scene(cid, "The Counting House")
    store.appearances.appear(cid, now, "pcs", pid, vid, "player")

    store.plot.set_movement(cid, "the-ledger", "Find the ledger", "open",
                            "She named it aloud.", hers)
    store.plot.set_movement(cid, "the-tide", "The tide turns", "open",
                            "Nobody she knows was there.", theirs)

    body = _brief(client, cid, now)
    assert body["focus"] == ["Winifred Vance"]
    rows = {t["id"]: t["involves"] for t in body["plot"]}
    assert rows == {"the-ledger": ["Winifred Vance"], "the-tide": []}


def test_involvement_counts_an_earlier_beat_not_only_the_latest(client):
    """A thread's beats each name their own scene. Joining on `last_scene` alone
    would drop a thread the PC opened and someone else has since advanced."""
    wid, cid = _campaign(client)
    pid, vid = _pc(wid, "Winifred Vance")
    hers = store.scenes.create_scene(cid, "The Pier at Dusk")
    theirs = store.scenes.create_scene(cid, "A Room Elsewhere")
    store.appearances.appear(cid, hers, "pcs", pid, vid, "player")
    now = store.scenes.create_scene(cid, "The Counting House")
    store.appearances.appear(cid, now, "pcs", pid, vid, "player")

    store.plot.set_movement(cid, "the-ledger", "Find the ledger", "open",
                            "She named it aloud.", hers)
    store.plot.set_movement(cid, "the-ledger", "", "advanced",
                            "Someone else moved it on.", theirs)

    row = _brief(client, cid, now)["plot"][0]
    assert row["last_scene"] == theirs           # the latest beat is not hers...
    assert row["involves"] == ["Winifred Vance"]  # ...but an earlier one is


def test_a_pc_who_has_since_left_that_scene_still_counts(client):
    """The appearance record is *current* membership — `leave` drops the scene
    from it — while the chronicle holds the cast absorb recorded at the time.
    "Was she there when this moved" is a question only the snapshot answers."""
    wid, cid = _campaign(client)
    pid, vid = _pc(wid, "Winifred Vance")
    past = store.scenes.create_scene(cid, "The Pier at Dusk")
    store.appearances.appear(cid, past, "pcs", pid, vid, "player")
    store.chronicle.absorb(cid, {"id": past, "one_line": "She named it aloud.",
                                 "cast": [f"pcs/{pid}"], "date": ""})
    store.appearances.leave(cid, past, "pcs", pid)
    now = store.scenes.create_scene(cid, "The Counting House")
    store.appearances.appear(cid, now, "pcs", pid, vid, "player")
    store.plot.set_movement(cid, "the-ledger", "Find the ledger", "open", "beat", past)

    assert _brief(client, cid, now)["plot"][0]["involves"] == ["Winifred Vance"]


def test_a_thread_moved_in_a_scene_that_was_never_absorbed_still_counts(client):
    """The mirror of the case above: no chronicle record exists for a scene that
    was never absorbed, so only the appearance record can answer for it. A
    hand-written thread, or one moved in the scene being briefed, lands here."""
    wid, cid = _campaign(client)
    pid, vid = _pc(wid, "Winifred Vance")
    now = store.scenes.create_scene(cid, "The Counting House")
    store.appearances.appear(cid, now, "pcs", pid, vid, "player")
    store.plot.set_movement(cid, "the-ledger", "Find the ledger", "open", "beat", now)

    assert _brief(client, cid, now)["plot"][0]["involves"] == ["Winifred Vance"]


def test_involving_rows_sort_first(client):
    wid, cid = _campaign(client)
    pid, vid = _pc(wid, "Winifred Vance")
    hers = store.scenes.create_scene(cid, "The Pier at Dusk")
    store.appearances.appear(cid, hers, "pcs", pid, vid, "player")
    now = store.scenes.create_scene(cid, "The Counting House")
    store.appearances.appear(cid, now, "pcs", pid, vid, "player")
    # "aaa" sorts ahead of "zzz" in the store's own ordering, so a briefing that
    # merely passed the ledger's order through would put the unrelated one first.
    store.plot.set_movement(cid, "aaa", "Not hers", "open", "beat", "0009--gone")
    store.plot.set_movement(cid, "zzz", "Hers", "open", "beat", hers)

    assert [t["id"] for t in _brief(client, cid, now)["plot"]] == ["zzz", "aaa"]


def test_commitments_carry_the_same_flag(client):
    wid, cid = _campaign(client)
    pid, vid = _pc(wid, "Winifred Vance")
    hers = store.scenes.create_scene(cid, "The Pier at Dusk")
    store.appearances.appear(cid, hers, "pcs", pid, vid, "player")
    now = store.scenes.create_scene(cid, "The Counting House")
    store.appearances.appear(cid, now, "pcs", pid, vid, "player")
    store.commitments.set_movement(cid, "the-deadline", "Midnight", "threat", "open",
                                   "midnight", "Sworn in front of her.", hers)

    assert _brief(client, cid, now)["commitments"][0]["involves"] == ["Winifred Vance"]


def test_focus_widens_to_the_whole_cast_in_an_offscreen_scene(client):
    """An offscreen scene (pcless) seats only NPCs, so a flag computed against
    role=player would be empty exactly where the director most needs to know
    which threads the characters on stage are carrying."""
    wid, cid = _campaign(client)
    aid, vid = _npc(wid, "Seraphine")
    past = store.scenes.create_scene(cid, "The Pier at Dusk")
    store.appearances.appear(cid, past, "characters", aid, vid, "npc")
    now = store.scenes.create_scene(cid, "The Counting House", pcless=True)
    store.appearances.appear(cid, now, "characters", aid, vid, "npc")
    store.plot.set_movement(cid, "the-ledger", "Find the ledger", "open", "beat", past)

    body = _brief(client, cid, now)
    assert body["focus"] == ["Seraphine"]
    assert body["plot"][0]["involves"] == ["Seraphine"]


def test_an_ordinary_scene_with_no_player_yet_widens_to_nobody(client):
    """The widening is keyed on the scene's own `pcless` flag, not on "no players
    came back". An ordinary scene has no players *yet* while it is being set up,
    and briefly none again if its player is removed — keying on the empty list
    made `involves` mean one thing before the PC was seated and another after,
    with the NPC's threads losing their flags the moment she arrived."""
    wid, cid = _campaign(client)
    aid, vid = _npc(wid, "Seraphine")
    past = store.scenes.create_scene(cid, "The Pier at Dusk")
    store.appearances.appear(cid, past, "characters", aid, vid, "npc")
    now = store.scenes.create_scene(cid, "The Counting House")     # NOT pcless
    store.appearances.appear(cid, now, "characters", aid, vid, "npc")
    store.plot.set_movement(cid, "the-ledger", "Find the ledger", "open", "beat", past)

    body = _brief(client, cid, now)
    assert body["focus"] == []                          # nobody to be about yet
    assert [t["id"] for t in body["plot"]] == ["the-ledger"]   # the row still lists
    assert body["plot"][0]["involves"] == []


def test_players_alone_are_the_focus_when_the_scene_has_one(client):
    """The fallback above must not widen a scene that *does* have a player: an
    NPC's thread is not "yours"."""
    wid, cid = _campaign(client)
    pid, vid = _pc(wid, "Winifred Vance")
    aid, avid = _npc(wid, "Seraphine")
    past = store.scenes.create_scene(cid, "The Pier at Dusk")
    store.appearances.appear(cid, past, "characters", aid, avid, "npc")
    now = store.scenes.create_scene(cid, "The Counting House")
    store.appearances.appear(cid, now, "pcs", pid, vid, "player")
    store.appearances.appear(cid, now, "characters", aid, avid, "npc")
    store.plot.set_movement(cid, "the-ledger", "Find the ledger", "open", "beat", past)

    body = _brief(client, cid, now)
    assert body["focus"] == ["Winifred Vance"]
    assert body["plot"][0]["involves"] == []


# ---- the other two sections ------------------------------------------------

def test_relationships_render_for_the_present_cast(client):
    wid, cid = _campaign(client)
    pid, vid = _pc(wid, "Winifred Vance")
    aid, avid = _npc(wid, "Seraphine")
    sid = store.scenes.create_scene(cid, "The Counting House")
    store.appearances.appear(cid, sid, "pcs", pid, vid, "player")
    store.appearances.appear(cid, sid, "characters", aid, avid, "npc")
    store.relationships.set_feeling(cid, f"pcs:{pid}", f"characters:{aid}",
                                    2, -1, 3, "owes her a ledger")

    lines = _brief(client, cid, sid)["relationships"]
    assert any("Winifred Vance" in line and "Seraphine" in line for line in lines)


def test_relationships_stay_empty_for_an_actor_who_is_not_on_stage(client):
    wid, cid = _campaign(client)
    pid, vid = _pc(wid, "Winifred Vance")
    aid, _avid = _npc(wid, "Seraphine")
    sid = store.scenes.create_scene(cid, "The Counting House")
    store.appearances.appear(cid, sid, "pcs", pid, vid, "player")
    store.relationships.set_feeling(cid, f"pcs:{pid}", f"characters:{aid}",
                                    2, -1, 3, "owes her a ledger")
    assert _brief(client, cid, sid)["relationships"] == []


def test_last_time_is_the_newest_fact_before_this_scene(client):
    _wid, cid = _campaign(client)
    first = store.scenes.create_scene(cid, "First Night")
    second = store.scenes.create_scene(cid, "Second Night")
    now = store.scenes.create_scene(cid, "Third Night")
    store.chronicle.absorb(cid, {"id": first, "one_line": "They met.", "date": "5 Harvestmoon"})
    store.chronicle.absorb(cid, {"id": second, "one_line": "They argued.", "date": "6 Harvestmoon"})

    assert _brief(client, cid, now)["last_time"] == {
        "id": second, "one_line": "They argued.", "title": "Second Night",
        "date": "6 Harvestmoon"}


def test_last_time_skips_this_scene_and_everything_after_it(client):
    """Re-opening an absorbed scene must brief what came *before* it, not hand
    back its own summary — and not a later scene's, either."""
    _wid, cid = _campaign(client)
    first = store.scenes.create_scene(cid, "First Night")
    now = store.scenes.create_scene(cid, "Second Night")
    later = store.scenes.create_scene(cid, "Third Night")
    for sid, line in ((first, "They met."), (now, "They argued."), (later, "They parted.")):
        store.chronicle.absorb(cid, {"id": sid, "one_line": line, "date": ""})

    assert _brief(client, cid, now)["last_time"]["one_line"] == "They met."


def test_last_time_skips_a_scene_absorbed_with_nothing_written_down(client):
    """One slot: spending it on a record with neither `one_line` nor `summary`
    would hide the real fact sitting right behind it."""
    _wid, cid = _campaign(client)
    first = store.scenes.create_scene(cid, "First Night")
    blank = store.scenes.create_scene(cid, "Second Night")
    now = store.scenes.create_scene(cid, "Third Night")
    store.chronicle.absorb(cid, {"id": first, "one_line": "They met.", "date": ""})
    store.chronicle.absorb(cid, {"id": blank, "one_line": "", "summary": "", "date": ""})

    assert _brief(client, cid, now)["last_time"]["one_line"] == "They met."


def test_last_time_falls_back_to_the_summary(client):
    _wid, cid = _campaign(client)
    first = store.scenes.create_scene(cid, "First Night")
    now = store.scenes.create_scene(cid, "Second Night")
    store.chronicle.absorb(cid, {"id": first, "one_line": "", "date": "",
                                 "summary": "They argued until the tide turned."})
    assert _brief(client, cid, now)["last_time"]["one_line"] == "They argued until the tide turned."


# ---- tolerance: the same contract the ledger keeps -------------------------

@pytest.mark.parametrize("filename,section", [("plot.json", "plot"),
                                              ("commitments.json", "commitments")])
def test_a_garbled_file_empties_only_its_own_section(client, filename, section):
    _wid, cid = _campaign(client)
    past = store.scenes.create_scene(cid, "The Pier at Dusk")
    now = store.scenes.create_scene(cid, "The Counting House")
    store.chronicle.absorb(cid, {"id": past, "one_line": "It happened.", "date": ""})
    store.plot.set_movement(cid, "t", "A thread", "open", "beat", past)
    store.commitments.set_movement(cid, "c", "A promise", "promise", "open", "", "beat", past)
    (store.campaigns.campaign_root(cid) / filename).write_text("{ not json", encoding="utf-8")

    body = _brief(client, cid, now)
    assert body[section] == []
    other = "commitments" if section == "plot" else "plot"
    assert len(body[other]) == 1
    assert body["last_time"]["one_line"] == "It happened."


def test_a_garbled_chronicle_still_serves_the_other_sections(client):
    _wid, cid = _campaign(client)
    past = store.scenes.create_scene(cid, "The Pier at Dusk")
    now = store.scenes.create_scene(cid, "The Counting House")
    store.plot.set_movement(cid, "t", "A thread", "open", "beat", past)
    (store.campaigns.campaign_root(cid) / "chronicle.json").write_text("{ nope", encoding="utf-8")

    body = _brief(client, cid, now)
    assert [t["id"] for t in body["plot"]] == ["t"]
    assert body["last_time"] is None


def test_a_malformed_appearance_record_costs_only_the_flag(client):
    """appearances.json is what the involvement join reads back through, and a
    record missing a field takes the WHOLE roster read down with it — so the
    flag has to fail to empty while the rows it decorates still list.

    The broken record names a different scene on purpose. One in *this* scene
    reaches `players_in_scene` from `scenes.read_scene`, which has no tolerance
    of its own, so the route 404/500s before this module is ever called — a
    pre-existing gap in the scene read, not this view's to close."""
    wid, cid = _campaign(client)
    pid, vid = _pc(wid, "Winifred Vance")
    past = store.scenes.create_scene(cid, "The Pier at Dusk")
    now = store.scenes.create_scene(cid, "The Counting House")
    store.appearances.appear(cid, now, "pcs", pid, vid, "player")
    store.plot.set_movement(cid, "t", "A thread", "open", "beat", past)

    path = store.campaigns.campaign_root(cid) / "appearances.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["characters/ghost"] = {"role": "npc", "scenes": [past]}   # no `version`
    path.write_text(json.dumps(record), encoding="utf-8")

    body = _brief(client, cid, now)
    assert body["focus"] == ["Winifred Vance"]       # this scene's own cast still reads
    assert [t["id"] for t in body["plot"]] == ["t"]  # the row survives...
    assert body["plot"][0]["involves"] == []         # ...unflagged, the join having failed


def test_a_garbled_relationships_file_costs_only_its_lines(client):
    wid, cid = _campaign(client)
    pid, vid = _pc(wid, "Winifred Vance")
    sid = store.scenes.create_scene(cid, "The Counting House")
    store.appearances.appear(cid, sid, "pcs", pid, vid, "player")
    store.plot.set_movement(cid, "t", "A thread", "open", "beat", sid)
    (store.campaigns.campaign_root(cid) / "relationships.json").write_text("{ nope", encoding="utf-8")

    body = _brief(client, cid, sid)
    assert body["relationships"] == []
    assert [t["id"] for t in body["plot"]] == ["t"]


@pytest.mark.parametrize("shape", ["[]", '{"s1": []}'])
def test_a_chronicle_of_the_wrong_shape_does_not_500(client, shape):
    """`read_chronicle` is a bare json.loads, so valid JSON of the wrong shape
    raises nothing and arrives as a list. The involvement join indexes it."""
    _wid, cid = _campaign(client)
    sid = store.scenes.create_scene(cid, "The Counting House")
    store.plot.set_movement(cid, "t", "A thread", "open", "beat", "s1")
    (store.campaigns.campaign_root(cid) / "chronicle.json").write_text(shape, encoding="utf-8")

    body = _brief(client, cid, sid)
    assert [t["id"] for t in body["plot"]] == ["t"]
    assert body["last_time"] is None


def test_a_thread_with_non_string_fields_still_renders_as_a_row(client):
    """Same hazard as the ledger's: the read SUCCEEDS, so the tolerant boundary
    never fires, and an object-valued title reaches React as a child and blanks
    the panel. The projection is where the types are made true."""
    _wid, cid = _campaign(client)
    sid = store.scenes.create_scene(cid, "The Counting House")
    (store.campaigns.campaign_root(cid) / "plot.json").write_text(json.dumps(
        {"t": {"title": {}, "status": [], "beats": [{"scene": "s1", "text": ["nope"]}],
               "last_scene": {"a": 1}}}), encoding="utf-8")

    row = _brief(client, cid, sid)["plot"][0]
    assert row["id"] == "t" and row["title"] == "t" and row["status"] == "open"
    assert row["last_scene"] == "" and row["latest_beat"] == ""
    assert all(isinstance(v, str) for k, v in row.items() if k != "involves")


def test_a_thread_whose_beats_are_the_wrong_shape_still_lists(client):
    """`beats` is hand-editable: an object where a list belongs, and a beat whose
    `scene` is unhashable, both reach the involvement join's set arithmetic."""
    _wid, cid = _campaign(client)
    sid = store.scenes.create_scene(cid, "The Counting House")
    (store.campaigns.campaign_root(cid) / "plot.json").write_text(json.dumps({
        "mapping": {"title": "A thread", "status": "open", "beats": {}, "last_scene": "s1"},
        "unhashable": {"title": "Another", "status": "open",
                       "beats": [{"scene": [], "text": "x"}], "last_scene": "s1"},
    }), encoding="utf-8")

    assert {t["id"] for t in _brief(client, cid, sid)["plot"]} == {"mapping", "unhashable"}


def test_one_malformed_cast_entry_does_not_unflag_every_row(client):
    """`cast` is hand-editable and the involvement join tests membership of it
    against a dict, so a list-valued entry is unhashable and `in` RAISES rather
    than missing. That raise reached the tolerant wrapper, which threw away the
    history already collected from appearances.json — one bad record unflagging
    every row in the view."""
    wid, cid = _campaign(client)
    pid, vid = _pc(wid, "Winifred Vance")
    past = store.scenes.create_scene(cid, "The Pier at Dusk")
    store.appearances.appear(cid, past, "pcs", pid, vid, "player")
    now = store.scenes.create_scene(cid, "The Counting House")
    store.appearances.appear(cid, now, "pcs", pid, vid, "player")
    store.plot.set_movement(cid, "the-ledger", "Find the ledger", "open", "beat", past)
    (store.campaigns.campaign_root(cid) / "chronicle.json").write_text(json.dumps(
        {past: {"id": past, "one_line": "It happened.", "date": "",
                "cast": [[], {"a": 1}, f"pcs/{pid}"]}}), encoding="utf-8")

    body = _brief(client, cid, now)
    assert body["plot"][0]["involves"] == ["Winifred Vance"]
    assert body["last_time"]["one_line"] == "It happened."


def test_a_record_that_is_not_a_dict_is_skipped(client):
    _wid, cid = _campaign(client)
    sid = store.scenes.create_scene(cid, "The Counting House")
    (store.campaigns.campaign_root(cid) / "plot.json").write_text(json.dumps(
        {"broken": [], "good": {"title": "A thread", "status": "open",
                                "beats": [], "last_scene": ""}}), encoding="utf-8")
    assert [t["id"] for t in _brief(client, cid, sid)["plot"]] == ["good"]


def test_every_section_is_read_under_one_campaign_lock(client, monkeypatch):
    """The ledger's argument (#117), and it binds harder here: a save writes the
    chronicle and then the absorb's plot and commitment edits under one hold, so
    an unlocked briefing can report a fact beside the commitment that same save
    resolved — and this one is read at the moment a scene opens, which is
    precisely when the previous scene's save has just run."""
    _wid, cid = _campaign(client)
    sid = store.scenes.create_scene(cid, "The Counting House")
    store.commitments.set_movement(cid, "x", "A promise", "promise", "open",
                                   "", "Sworn.", "s1")
    held = {}

    def _watch(name, real):
        def wrapper(*a, **kw):
            held[name] = store.locks.campaign_lock(cid)._is_owned()
            return real(*a, **kw)
        return wrapper

    monkeypatch.setattr(store.chronicle, "read_chronicle",
                        _watch("chronicle", store.chronicle.read_chronicle))
    monkeypatch.setattr(store.plot, "open_threads",
                        _watch("plot", store.plot.open_threads))
    monkeypatch.setattr(store.commitments, "open_commitments",
                        _watch("commitments", store.commitments.open_commitments))
    monkeypatch.setattr(store.relationships, "render_present",
                        _watch("relationships", store.relationships.render_present))

    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/briefing").status_code == 200
    assert held == {"chronicle": True, "plot": True,
                    "commitments": True, "relationships": True}
