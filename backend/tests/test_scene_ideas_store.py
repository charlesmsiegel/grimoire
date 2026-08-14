"""The scene ledger (#88).

What separates this store from the suggestion machinery beside it is that an
idea outlives the picker that produced it -- and therefore outlives the shape
of the campaign it was saved against. Most of what follows is about that second
half: a saved idea whose character was deleted, whose location was renamed, or
whose calendar moved must still be pickable, minus the reference that went.
"""

import json
import threading

import pytest

from grimoire.store import (appearances, campaigns, overlay, playing, scene_ideas,
                            scenes, suggest, worlds)


def _bare(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("Realm"))


def _campaign(monkeypatch, tmp_path):
    """A campaign with one character and one location to reference."""
    cid = _bare(monkeypatch, tmp_path)
    overlay.create_character(cid, "Mara", "main")
    overlay.create_entity(cid, "locations", "Saltmarch")
    return cid


def _path(cid):
    return campaigns.campaign_root(cid) / "scene_ideas.json"


def test_read_missing_is_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert scene_ideas.read(cid) == {}
    assert scene_ideas.records(cid) == []


def test_add_stores_an_idea(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    lid = scene_ideas.add(cid, "  The creditor  ", "  A debt-collector arrives.  ",
                          ["characters:mara"], "saltmarch", source=scene_ideas.LLM)
    rec = scene_ideas.get(cid, lid)
    assert lid == "the-creditor"
    assert rec["title"] == "The creditor"
    assert rec["premise"] == "A debt-collector arrives."
    assert rec["cast"] == ["characters:mara"]
    assert rec["location"] == "saltmarch"
    assert rec["source"] == "llm"
    assert rec["status"] == "active"
    assert rec["used_scene"] == ""
    assert rec["created"]


def test_the_write_side_validator_drops_what_the_campaign_lacks(monkeypatch, tmp_path):
    """The write-side half of the two-sided validation (the route runs it, so a
    token this campaign never had cannot enter the file at all)."""
    cid = _campaign(monkeypatch, tmp_path)
    refs = suggest.valid_refs(cid, ["characters:mara", "characters:nobody"], "elsewhere")
    assert refs == {"cast": ["characters:mara"], "location": "", "date": ""}


def test_add_gives_two_ideas_of_one_name_distinct_ids(monkeypatch, tmp_path):
    """Same title, different premise: two ideas, not a collision."""
    cid = _campaign(monkeypatch, tmp_path)
    first = scene_ideas.add(cid, "The creditor", "One.")
    second = scene_ideas.add(cid, "The creditor", "Two.")
    assert first != second
    assert scene_ideas.get(cid, first)["premise"] == "One."
    assert scene_ideas.get(cid, second)["premise"] == "Two."


def test_resaving_a_standing_idea_returns_the_one_already_there(monkeypatch, tmp_path):
    """Saving twice is ordinary, not an error: an impatient double-click, a
    retry after a dropped response, the same suggestion coming back from a
    Regenerate. Each one used to file a duplicate under `<slug>-2` that the
    reader then had to dismiss twice."""
    cid = _campaign(monkeypatch, tmp_path)
    first = scene_ideas.add(cid, "The creditor", "A debt-collector arrives.")
    again = scene_ideas.add(cid, " the CREDITOR ", " a debt-collector arrives. ")
    assert again == first
    assert list(scene_ideas.read(cid)) == [first]


def test_the_two_modes_are_two_ideas_even_worded_alike(monkeypatch, tmp_path):
    """They cast different people -- `pcless` decides which player tokens
    survive validation -- so one sentence saved for each is not one idea."""
    cid = _campaign(monkeypatch, tmp_path)
    onscreen = scene_ideas.add(cid, "The creditor", "P")
    offscreen = scene_ideas.add(cid, "The creditor", "P", pcless=True)
    assert onscreen != offscreen


def test_dedupe_does_not_revive_a_used_or_dismissed_idea(monkeypatch, tmp_path):
    """A used idea already became a scene, so saying it again is a genuine
    second use; a dismissed one was explicitly pushed off the list, and
    reviving it through an unrelated Save would undo that without saying so."""
    cid = _campaign(monkeypatch, tmp_path)
    spent = scene_ideas.add(cid, "The creditor", "P")
    scene_ideas.mark_used(cid, spent, "001--s")
    again = scene_ideas.add(cid, "The creditor", "P")
    assert again != spent
    assert scene_ideas.get(cid, spent)["status"] == "used"       # left where it was

    scene_ideas.set_status(cid, again, scene_ideas.DISMISSED)
    third = scene_ideas.add(cid, "The creditor", "P")
    assert third not in (spent, again)
    assert scene_ideas.get(cid, again)["status"] == "dismissed"  # still dismissed


def test_a_titleless_idea_is_named_from_its_premise(monkeypatch, tmp_path):
    """The picker's free-text box has no title field, so "save this" has to name
    the idea for the reader -- and on a word boundary, so it reads as a phrase."""
    cid = _campaign(monkeypatch, tmp_path)
    lid = scene_ideas.add(cid, "", "  back at the marsh house, the morning after "
                                   "the funeral, with nobody left to ask  ")
    assert scene_ideas.get(cid, lid)["title"] == "back at the marsh house, the morning after the funeral,"


def test_an_idea_with_nothing_to_name_it_still_gets_a_title(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    lid = scene_ideas.add(cid, "", "")
    assert scene_ideas.get(cid, lid)["title"] == "Untitled idea"


def test_add_refuses_a_greeting_source(monkeypatch, tmp_path):
    """Greeting entries are composed from played.json. Storing one here would
    duplicate a lifecycle that already exists and immediately drift from it."""
    cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        scene_ideas.add(cid, "Reckoning", "x", source="greeting")


def test_status_transitions_and_restore(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    lid = scene_ideas.add(cid, "The creditor", "A debt-collector arrives.")

    assert scene_ideas.set_status(cid, lid, scene_ideas.DISMISSED)
    assert scene_ideas.get(cid, lid)["status"] == "dismissed"
    assert scene_ideas.set_status(cid, lid, scene_ideas.ACTIVE)      # restore
    assert scene_ideas.get(cid, lid)["status"] == "active"

    assert scene_ideas.mark_used(cid, lid, "001--s")
    rec = scene_ideas.get(cid, lid)
    assert (rec["status"], rec["used_scene"]) == ("used", "001--s")
    assert rec["title"] == "The creditor" and rec["premise"] == "A debt-collector arrives."


def test_restoring_a_used_idea_clears_the_scene_it_became(monkeypatch, tmp_path):
    """An idea back on the list is one nobody has played; the old stamp would
    have the ledger claim a scene it no longer describes."""
    cid = _campaign(monkeypatch, tmp_path)
    lid = scene_ideas.add(cid, "The creditor", "x")
    scene_ideas.mark_used(cid, lid, "001--s")
    scene_ideas.set_status(cid, lid, scene_ideas.ACTIVE)
    assert scene_ideas.get(cid, lid)["used_scene"] == ""


def test_status_of_an_unknown_idea_is_false_not_a_new_record(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert scene_ideas.set_status(cid, "nope", scene_ideas.DISMISSED) is False
    assert scene_ideas.read(cid) == {}


def test_unknown_status_raises(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    lid = scene_ideas.add(cid, "The creditor", "x")
    with pytest.raises(ValueError):
        scene_ideas.set_status(cid, lid, "parked")


def test_stored_ideas_are_newest_first(monkeypatch, tmp_path):
    """The list is read to be picked from, and the ids are slugs rather than a
    counter -- nothing else in the record orders them."""
    cid = _campaign(monkeypatch, tmp_path)
    scene_ideas.add(cid, "First", "x")
    scene_ideas.add(cid, "Second", "y")
    data = scene_ideas.read(cid)
    data["first"]["created"] = "2026-01-01T00:00:00Z"
    data["second"]["created"] = "2026-02-02T00:00:00Z"
    _path(cid).write_text(json.dumps(data), encoding="utf-8")
    assert [i["id"] for i in scene_ideas.records(cid)] == ["second", "first"]


def test_a_reference_that_goes_away_after_the_save_drops_on_read(monkeypatch, tmp_path):
    """The read-side half of the validation, and the reason one pass is not
    enough: an idea is durable and a campaign is not."""
    cid = _campaign(monkeypatch, tmp_path)
    lid = scene_ideas.add(cid, "The creditor", "x", ["characters:mara"], "saltmarch")
    overlay.delete_entity(cid, "locations", "saltmarch")

    stored = scene_ideas.get(cid, lid)
    assert stored["location"] == "saltmarch"         # the file keeps what it was given
    read = suggest.validate_ideas(cid, scene_ideas.records(cid))[0]
    assert read["location"] == ""                    # the read refuses to hand it on
    assert read["cast"] == ["characters:mara"]       # the character is still there


def test_an_offscreen_idea_never_carries_a_player(monkeypatch, tmp_path):
    """`pcless` is to a saved idea what `offscreen` is to a fresh suggestion: an
    offscreen scene is defined by the player's absence. Checked per row, so one
    read can hold ideas of both modes."""
    cid = _bare(monkeypatch, tmp_path)
    overlay.create_character(cid, "Mara", "main")
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", "mara", "main", "player")
    scene_ideas.add(cid, "Alone", "x", ["characters:mara"], pcless=True)
    scene_ideas.add(cid, "Together", "x", ["characters:mara"])

    cast = {i["title"]: i["cast"] for i in suggest.validate_ideas(cid, scene_ideas.records(cid))}
    assert cast == {"Alone": [], "Together": ["characters:mara"]}
    assert suggest.valid_refs(cid, ["characters:mara"], "", offscreen=True)["cast"] == []


def test_a_hand_mangled_record_reads_as_defaults_rather_than_raising(monkeypatch, tmp_path):
    """scene_ideas.json is hand-editable and read by a bare `json.loads`, so
    every field is whatever the file says -- and these are rendered straight
    into React, which refuses an object as a child."""
    cid = _campaign(monkeypatch, tmp_path)
    _path(cid).write_text(json.dumps({
        "broken": {"title": ["not", "a", "string"], "premise": None, "cast": "characters:mara",
                   "location": 7, "source": "smuggled", "status": "Parked", "pcless": "yes"},
        "not-even-a-record": "text",
    }), encoding="utf-8")
    rows = scene_ideas.records(cid)
    assert [r["id"] for r in rows] == ["broken"]
    assert rows[0] == {"id": "broken", "title": "broken", "premise": "", "cast": [],
                       "location": "", "date": "", "pcless": True, "source": "user",
                       "status": "active", "created": "", "used_scene": ""}


def test_a_ledger_of_the_wrong_shape_refuses_to_be_written_over(monkeypatch, tmp_path):
    """Substituting `{}` would publish an empty ledger over whatever the file
    really held -- `facts._read_ledger`'s rule."""
    cid = _campaign(monkeypatch, tmp_path)
    _path(cid).write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        scene_ideas.add(cid, "The creditor", "x")


def test_repoint_follows_a_renamed_scene(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    lid = scene_ideas.add(cid, "The creditor", "x")
    scene_ideas.mark_used(cid, lid, "001--s")
    scene_ideas.repoint_scenes(cid, {"001--s": "001--2026-07-04--s"})
    assert scene_ideas.get(cid, lid)["used_scene"] == "001--2026-07-04--s"


def test_repoint_steps_over_a_garbled_file(monkeypatch, tmp_path):
    """It runs AFTER the scene file was renamed: raising here 500s the rename
    and leaves every store later in the sweep pointing at a dead id."""
    cid = _campaign(monkeypatch, tmp_path)
    _path(cid).write_text("{not json", encoding="utf-8")
    scene_ideas.repoint_scenes(cid, {"a": "b"})   # must not raise
    _path(cid).write_text(json.dumps({"x": {"used_scene": ["a"]}}), encoding="utf-8")
    scene_ideas.repoint_scenes(cid, {"a": "b"})   # nor on an unhashable id


# ---- the composed greeting half ------------------------------------------
def _greeting(cid, name):
    return overlay.create_greeting(cid, name, "mara", "main", "It begins.")


def test_greetings_are_composed_not_stored(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _greeting(cid, "Reckoning")

    assert playing.greeting_ideas(cid) == [
        {"id": "greeting:reckoning", "title": "Reckoning", "premise": "",
         "cast": [], "location": "", "date": "", "pcless": False,
         "source": "greeting", "status": "active", "created": "", "used_scene": ""}]
    assert scene_ideas.read(cid) == {}                     # nothing was written


def test_a_greetings_status_is_derived_from_its_marks(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    for name in ("Reckoning", "Open", "Ashore"):
        _greeting(cid, name)
    playing.mark_greeting(cid, "open", "completed")
    playing.mark_greeting(cid, "ashore", "skipped")

    status = {r["id"]: r["status"] for r in playing.greeting_ideas(cid)}
    assert status == {"greeting:reckoning": "active", "greeting:open": "used",
                      "greeting:ashore": "dismissed"}


def test_a_greeting_nobody_can_start_is_not_an_idea(monkeypatch, tmp_path):
    """Gated behind a plot predecessor: not something to act on, and its gating
    is the plot map's business rather than this ledger's."""
    cid = _campaign(monkeypatch, tmp_path)
    _greeting(cid, "Reckoning")
    _greeting(cid, "After")
    overlay.set_edges(cid, "reckoning", leads_to=["after"])
    assert [r["id"] for r in playing.greeting_ideas(cid)] == ["greeting:reckoning"]


# ---- concurrency: the reason this module is in the lock domain -------------
def test_two_threads_saving_one_idea_file_it_once(monkeypatch, tmp_path):
    """`add` allocates its id from the keys it just read, so an unlocked pair
    can pick the same slug and lose one of the two ideas -- and the dedupe that
    makes a retried save safe is itself a read-modify-write, so unlocked it
    would let both callers miss each other and file two copies.

    Two clients is not hypothetical here: the store is meant to be pointed at a
    synced folder, and a phone and a desktop can have the picker open at once
    (see `store.locks` on what that lock does and does not cover).
    """
    cid = _campaign(monkeypatch, tmp_path)
    ids: dict[str, str] = {}

    def worker(name: str) -> None:
        ids[name] = scene_ideas.add(cid, "The creditor", "A debt-collector arrives.")

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads), "a worker thread never finished"

    assert ids["a"] == ids["b"], ids          # both callers were told the same id
    assert list(scene_ideas.read(cid)) == [ids["a"]]   # and only one record exists


def test_a_concurrent_dismiss_cannot_lose_a_save(monkeypatch, tmp_path):
    """The file is rewritten whole, so a status write and a save that overlap
    would otherwise publish one over the other -- the loser's record simply
    absent from the file it was never in."""
    cid = _campaign(monkeypatch, tmp_path)
    first = scene_ideas.add(cid, "Standing", "x")
    done: list[str] = []

    def dismiss() -> None:
        scene_ideas.set_status(cid, first, scene_ideas.DISMISSED)
        done.append("dismiss")

    def save() -> None:
        done.append(scene_ideas.add(cid, "Fresh", "y"))

    threads = [threading.Thread(target=dismiss), threading.Thread(target=save)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads), "a worker thread never finished"

    data = scene_ideas.read(cid)
    assert sorted(data) == sorted([first, "fresh"])     # neither write was lost
    assert data[first]["status"] == "dismissed"
