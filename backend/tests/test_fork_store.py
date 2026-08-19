"""Campaign fork, from now or from an earlier turn (#72) — `store/fork.py`.

The invariants worth checking here are the ones a reader cannot see by opening
the fork: that the *source* is untouched by everything that happens to the
copy, that the copy inherits from its world exactly as the source did rather
than re-resolving anything, and that a retrospective fork takes back what
carries the removed scenes' ids and says so where it cannot.
"""

import pytest

from grimoire.store import (appearances, campaigns, chronicle, entities, fork, journal,
                            overlay, plot, scenes, worlds)


@pytest.fixture
def wid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return worlds.create_world("Realm")


@pytest.fixture
def cid(wid):
    return campaigns.create_campaign("Saltmarch", wid)


def _meta(cid):
    return campaigns.read_campaign(cid)["meta"]


def _sids(cid):
    return sorted(s["id"] for s in scenes.list_scenes(cid))


def _played(cid, title, posts=2):
    """A scene with a transcript, so a cut has something to take."""
    sid = scenes.create_scene(cid, title)
    for i in range(posts):
        scenes.append_message(cid, sid, "user", f"{title} post {i}")
    return sid


# --- forking from now ------------------------------------------------------


def test_the_fork_is_a_second_campaign_with_its_own_id(cid):
    out = fork.fork_campaign(cid, "Saltmarch Redux")
    assert out["id"] != cid
    assert _meta(out["id"])["name"] == "Saltmarch Redux"
    assert campaigns.campaign_exists(cid)          # the source is still there


def test_the_fork_records_the_campaign_it_came_from(cid):
    child = fork.fork_campaign(cid, "Branch")["id"]
    assert _meta(child)[fork.PARENT_KEY] == cid
    assert fork.FORKED_AT_KEY not in _meta(child)  # forked from now, not a turn


def test_the_fork_is_stamped_as_created_now_not_when_its_parent_was(cid):
    """`created` says when this campaign started existing, and the fork started
    existing at the fork. Copying the parent's would date a branch made today to
    whenever the campaign it came from was begun."""
    parent_created = _meta(cid)["created"]
    child = fork.fork_campaign(cid, "Branch")["id"]
    assert _meta(child)["created"] >= parent_created
    assert _meta(child)["updated"] == _meta(child)["created"]


def test_the_fork_does_not_inherit_its_parents_activity_stamp(cid):
    """The high-water mark ranks Recent. Copied, a fork made a second ago would
    sort by when its parent was last played -- for an old campaign, the bottom
    of the shelf."""
    campaigns.touch_quietly(cid)
    assert campaigns.read_activity(cid)
    child = fork.fork_campaign(cid, "Branch")["id"]
    assert campaigns.read_activity(child) == ""


def test_the_whole_campaign_travels(cid):
    """Not an enumeration of parts: whatever the directory holds is what the
    fork holds. Checked through the stores rather than by comparing trees, so
    the assertion is about what the fork can *read*."""
    sid = _played(cid, "Saltmarch")
    chronicle.absorb(cid, {"id": sid, "one_line": "They swore.", "summary": "A long night.",
                           "keywords": [], "cast": [], "location": "", "date": ""})
    plot.set_movement(cid, "the-oath", "The Oath", "open", "They swore it.", sid)
    child = fork.fork_campaign(cid, "Branch")["id"]
    assert _sids(child) == [sid]
    assert [m["content"] for m in scenes.read_scene(child, sid)["messages"]] == \
        [m["content"] for m in scenes.read_scene(cid, sid)["messages"]]
    assert chronicle.read_chronicle(child)[sid]["one_line"] == "They swore."
    assert list(plot.read(child)) == ["the-oath"]


def test_the_fork_inherits_from_the_same_world_through_the_overlay(wid, cid):
    """Nothing is re-resolved: the copied `world` line and `sync.md` are what
    the overlay reads, so a record the campaign never materialized still comes
    straight from the world -- and one it did materialize comes from its copy."""
    wroot = worlds.world_root(wid)
    entities.create_entity(wroot, "lore", "The Pact", "world text")
    entities.create_entity(wroot, "locations", "Saltmarch Docks", "world docks")
    overlay.update_entity(cid, "lore", "the-pact", body="campaign text")

    child = fork.fork_campaign(cid, "Branch")["id"]
    assert overlay.read_entity(child, "lore", "the-pact")["body"] == "campaign text"
    assert overlay.read_entity(child, "locations", "saltmarch-docks")["body"] == "world docks"
    assert campaigns.read_manifest(child) == campaigns.read_manifest(cid)


def test_the_forks_version_locks_travel_rather_than_being_re_derived(wid, cid):
    """A branch that picked a different character version than the campaign it
    came from would not be a branch of it."""
    from grimoire.store import characters
    wroot = worlds.world_root(wid)
    aid, vid = characters.create_character(wroot, "Mara")
    sid = scenes.create_scene(cid, "Saltmarch")
    appearances.appear(cid, sid, "characters", aid, vid, "npc")
    child = fork.fork_campaign(cid, "Branch")["id"]
    assert appearances.record(child) == appearances.record(cid)


def test_a_materialized_records_images_travel_with_the_fork(wid, cid):
    """`sync.md` and the world reference are copied, so the overlay resolves the
    fork the way it resolves the source — but a materialized record's image
    bytes live in the campaign tree itself and only travel because the whole
    directory does. The tree-level copy is what this asserts; the overlay
    resolution above is a different claim."""
    from grimoire.store import assets
    wroot = worlds.world_root(wid)
    eid = entities.create_entity(wroot, "locations", "Saltmarch Docks", "world docks")
    overlay.update_entity(cid, "locations", eid, body="campaign docks")
    croot = campaigns.campaign_root(cid)
    assets.put_image(croot, eid, "default", assets.AVATAR, b"pretend-png", "png",
                     base="locations")

    child = fork.fork_campaign(cid, "Branch")["id"]
    copied = campaigns.campaign_root(child) / "locations" / eid / "assets" / "default"
    assert (copied / f"{assets.AVATAR}.png").read_bytes() == b"pretend-png"


def test_a_copy_that_fails_partway_leaves_no_campaign_behind(cid, monkeypatch):
    """`copytree` publishes `campaign.md` partway through, and that file is what
    makes a directory a campaign to `list_campaigns` — so a copy that dies after
    it would otherwise leave a phantom on the shelf under the SOURCE's name,
    with no `parent` to mark it as a copy. The failure is raised, not swallowed;
    what must not survive it is the half-made campaign."""
    _played(cid, "One")

    def die(*a, **kw):
        campaigns.campaign_meta_path("wreck").write_text("---\nname: Saltmarch\n---\n")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(fork.shutil, "copytree", die)
    with pytest.raises(OSError):
        fork.fork_campaign(cid, "Wreck")
    assert [c["id"] for c in campaigns.list_campaigns()] == [cid]
    assert not campaigns.campaign_root("wreck").exists()


def test_losing_the_id_race_does_not_touch_the_campaign_that_won_it(cid, monkeypatch):
    """The nastiest way to write the cleanup above is to let `copytree`'s own
    `FileExistsError` be the guard against the window `uniquify` leaves — which
    puts SOMEBODY ELSE'S campaign, sitting at the id this fork wanted, inside
    the `rmtree` that follows. Claiming the id with a `mkdir` outside the
    cleanup is what makes the lost race cost a request instead of a campaign.

    The race is simulated by having the id already taken when the fork reaches
    the lock, which is exactly the state it leaves behind."""
    other = campaigns.create_campaign("Branch", _meta(cid)["world"])
    _played(other, "A Scene The Winner Owns")
    # `uniquify` would answer `branch-2` now, so the collision has to be made at
    # the moment of the mkdir — the window the lock cannot close.
    monkeypatch.setattr(fork, "uniquify", lambda base, taken: other)

    with pytest.raises(FileExistsError):
        fork.fork_campaign(cid, "Branch")
    assert campaigns.campaign_exists(other)
    assert _sids(other) == ["001--a-scene-the-winner-owns"]
    assert _meta(other)["name"] == "Branch"        # not re-stamped by the loser
    assert fork.PARENT_KEY not in _meta(other)


def test_a_name_that_slugifies_onto_an_existing_campaign_takes_the_next_id(cid):
    out = fork.fork_campaign(cid, "Saltmarch")
    assert out["id"] == f"{cid}-2"
    assert _meta(out["id"])["name"] == "Saltmarch"   # the name is not uniquified


def test_forking_a_fork_names_its_immediate_parent(cid):
    child = fork.fork_campaign(cid, "Branch")["id"]
    grandchild = fork.fork_campaign(child, "Twig")["id"]
    assert _meta(grandchild)[fork.PARENT_KEY] == child


def test_a_fork_of_a_retrospective_fork_starts_from_now_again(cid):
    """`forked_from_scene` describes one fork, not a dynasty: copied forward it
    would label a branch cut from nothing as an approximation of a past state."""
    one = _played(cid, "One")
    _played(cid, "Two")
    child = fork.fork_campaign(cid, "Branch", from_scene=one)["id"]
    assert _meta(child)[fork.FORKED_AT_KEY] == one
    grandchild = fork.fork_campaign(child, "Twig")["id"]
    assert fork.FORKED_AT_KEY not in _meta(grandchild)
    # ...and the grandchild is a whole copy of the child, not of the child's
    # own source: it has the one scene the cut left, not the two before it.
    assert _sids(grandchild) == [one]


# --- the source is never written to ----------------------------------------


def test_playing_the_fork_leaves_the_parent_alone(cid):
    sid = _played(cid, "Saltmarch")
    child = fork.fork_campaign(cid, "Branch")["id"]
    scenes.append_message(child, sid, "user", "a different turn")
    _played(child, "A Scene Only The Branch Has")

    assert [m["content"] for m in scenes.read_scene(cid, sid)["messages"]] == \
        ["Saltmarch post 0", "Saltmarch post 1"]
    assert _sids(cid) == [sid]


def test_playing_the_parent_leaves_the_fork_alone(cid):
    sid = _played(cid, "Saltmarch")
    child = fork.fork_campaign(cid, "Branch")["id"]
    scenes.append_message(cid, sid, "user", "the parent plays on")
    _played(cid, "A Scene Only The Parent Has")

    assert [m["content"] for m in scenes.read_scene(child, sid)["messages"]] == \
        ["Saltmarch post 0", "Saltmarch post 1"]
    assert _sids(child) == [sid]


def test_editing_a_record_in_the_fork_leaves_the_parents_copy_alone(wid, cid):
    entities.create_entity(worlds.world_root(wid), "lore", "The Pact", "world text")
    child = fork.fork_campaign(cid, "Branch")["id"]
    overlay.update_entity(child, "lore", "the-pact", body="branch text")
    assert overlay.read_entity(cid, "lore", "the-pact")["body"] == "world text"


def test_deleting_the_fork_leaves_the_parent_whole(cid):
    sid = _played(cid, "Saltmarch")
    child = fork.fork_campaign(cid, "Branch")["id"]
    campaigns.delete_campaign(child)
    assert campaigns.campaign_exists(cid)
    assert _sids(cid) == [sid]


# --- refusals --------------------------------------------------------------


def test_an_unknown_campaign_is_refused_before_anything_is_created(wid):
    with pytest.raises(campaigns.CampaignNotFound):
        fork.fork_campaign("no-such-campaign", "Branch")
    assert [c["id"] for c in campaigns.list_campaigns()] == []


def test_a_from_scene_that_is_not_one_of_its_scenes_is_refused(cid):
    _played(cid, "Saltmarch")
    with pytest.raises(scenes.SceneNotFound):
        fork.fork_campaign(cid, "Branch", from_scene="0007--nothing")
    assert [c["id"] for c in campaigns.list_campaigns()] == [cid]


# --- forking from an earlier turn ------------------------------------------


@pytest.fixture
def three(cid):
    """Three played scenes, oldest first. Ids are number-first, so this is both
    play order and lexicographic order."""
    return [_played(cid, "One"), _played(cid, "Two"), _played(cid, "Three")]


def test_the_scenes_after_the_cut_come_off_the_fork(cid, three):
    one, two, three_ = three
    out = fork.fork_campaign(cid, "Branch", from_scene=one)
    assert _sids(out["id"]) == [one]
    assert out["removed_scenes"] == [two, three_]      # reported in play order
    assert out["from_scene"] == one


def test_the_scene_the_fork_is_cut_at_is_kept_whole(cid, three):
    one = three[0]
    child = fork.fork_campaign(cid, "Branch", from_scene=one)["id"]
    assert [m["content"] for m in scenes.read_scene(child, one)["messages"]] == \
        ["One post 0", "One post 1"]


def test_the_parent_keeps_every_scene_the_cut_took_off_the_fork(cid, three):
    fork.fork_campaign(cid, "Branch", from_scene=three[0])
    assert _sids(cid) == sorted(three)


def test_cutting_at_the_newest_scene_removes_nothing(cid, three):
    out = fork.fork_campaign(cid, "Branch", from_scene=three[-1])
    assert out["removed_scenes"] == []
    assert _sids(out["id"]) == sorted(three)
    # ...and still records that this was a cut, not a fork from now: the two
    # are the same tree today and a different tree the moment either is played.
    assert _meta(out["id"])[fork.FORKED_AT_KEY] == three[-1]


def test_a_scene_with_no_transcript_still_comes_off(cid):
    """`cascade.delete_from` refuses an index that removes nothing, which is
    what an unplayed scene looks like. That is "nothing to cut", not a failure."""
    one = _played(cid, "One")
    empty = scenes.create_scene(cid, "Never Played")
    out = fork.fork_campaign(cid, "Branch", from_scene=one)
    assert out["removed_scenes"] == [empty]
    assert out["failed"] == []
    assert _sids(out["id"]) == [one]


def test_the_removed_scenes_chronicle_and_beats_go_with_them(cid, three):
    one, two, _three = three
    for sid, line in ((one, "They met."), (two, "They swore.")):
        chronicle.absorb(cid, {"id": sid, "one_line": line, "summary": line,
                               "keywords": [], "cast": [], "location": "", "date": ""})
        plot.set_movement(cid, f"thread-{sid}", "A Thread", "open", line, sid)

    child = fork.fork_campaign(cid, "Branch", from_scene=one)["id"]
    assert set(chronicle.read_chronicle(child)) == {one}
    # The BEATS the removed scene contributed go; the threads stand. That is
    # `plot.forget_scene`'s own rule -- a beat-less thread is not provably one
    # the removed scene opened, and a sweep may only take what carries the
    # scene's name.
    assert [b["scene"] for b in plot.read(child)[f"thread-{two}"]["beats"]] == []
    assert [b["scene"] for b in plot.read(child)[f"thread-{one}"]["beats"]] == [one]
    # the parent still has both
    assert set(chronicle.read_chronicle(cid)) == {one, two}
    assert [b["scene"] for b in plot.read(cid)[f"thread-{two}"]["beats"]] == [two]


def test_a_write_back_the_removed_scene_landed_is_put_back(wid, cid):
    """The change journal holds the record's actual prior value, so this is a
    restoration rather than a guess -- and the count says how many."""
    from grimoire.store import absorb
    wroot = worlds.world_root(wid)
    entities.create_entity(wroot, "lore", "The Pact", "old body")
    one = _played(cid, "One")
    two = _played(cid, "Two")
    absorb.apply_edits(cid, [{
        "id": "lore:the-pact", "kind": "lore",
        "target": {"kind": "lore", "id": "the-pact"},
        "label": "The Pact — lore", "field": "body",
        "before": "old body", "after": "new body", "authored": False,
    }], two)
    assert overlay.read_entity(cid, "lore", "the-pact")["body"] == "new body"

    out = fork.fork_campaign(cid, "Branch", from_scene=one)
    assert out["records"] == 1 and out["refused"] == [] and out["failed"] == []
    assert overlay.read_entity(out["id"], "lore", "the-pact")["body"] == "old body"
    # the source keeps what its own play wrote
    assert overlay.read_entity(cid, "lore", "the-pact")["body"] == "new body"
    # ...and the WORLD is untouched, which the two assertions above cannot show:
    # the source materialized its own copy at the absorb, so a reversal that
    # wrote through to the world would leave both of them reading exactly as
    # they do here while corrupting every other campaign of that world.
    assert entities.read_entity(wroot, "lore", "the-pact")["body"] == "old body"


def test_the_reversal_is_reported_when_it_cannot_be_made(wid, cid):
    """A record the removed scene *created* has no reversal -- `store/undo.py`
    declines it. The fork says so instead of deleting the record or claiming
    the branch is clean."""
    from grimoire.store import absorb
    one = _played(cid, "One")
    two = _played(cid, "Two")
    absorb.apply_edits(cid, [{
        "id": "new_lore:omen", "kind": "new_lore",
        "target": {"kind": "lore", "id": ""}, "label": "The Omen — new lore",
        "field": "body", "before": "", "after": "A bad sign.",
        "payload": {"name": "The Omen", "body": "A bad sign."}, "authored": False,
    }], two)
    created = list(entities.list_entities(campaigns.campaign_root(cid), "lore"))
    assert created, "fixture created no lore entry"

    out = fork.fork_campaign(cid, "Branch", from_scene=one)
    assert out["records"] == 0
    assert [r["label"] for r in out["refused"]] == ["The Omen — new lore"]
    assert "deleting it" in out["refused"][0]["reason"]
    assert out["failed"] == []
    # the record the removed scene created still stands on the fork
    assert list(entities.list_entities(campaigns.campaign_root(out["id"]), "lore")) == created


def test_a_fork_from_now_reports_the_same_keys_as_a_cut_one(cid):
    """A client reads one shape either way, so an absent key can never be
    mistaken for a report that was cut short."""
    _played(cid, "One")
    assert set(fork.fork_campaign(cid, "Branch")) == \
        set(fork.fork_campaign(cid, "Other", from_scene=_sids(cid)[0]))


def test_the_reversals_the_cut_performed_are_journalled_on_the_fork(wid, cid):
    """Reversals are recorded, not erased. `undo.undo` appends a row for the
    reversal it performs, so the fork's history says the branch was cut — which
    is the record anything reading that history later has to work from."""
    from grimoire.store import absorb
    entities.create_entity(worlds.world_root(wid), "lore", "The Pact", "old body")
    one = _played(cid, "One")
    two = _played(cid, "Two")
    absorb.apply_edits(cid, [{
        "id": "lore:the-pact", "kind": "lore",
        "target": {"kind": "lore", "id": "the-pact"},
        "label": "The Pact — lore", "field": "body",
        "before": "old body", "after": "new body", "authored": False,
    }], two)

    child = fork.fork_campaign(cid, "Branch", from_scene=one)["id"]
    sources = [e.get("source") for e in journal.read(child)]
    assert "absorb" in sources, sources    # the write the removed scene landed
    assert "undo" in sources, sources      # and the cut putting it back
    # The source's own journal has only the write: nothing was reversed there.
    assert [e.get("source") for e in journal.read(cid)] == ["absorb"]


# --- lineage in the listing ------------------------------------------------


def test_the_listing_carries_the_lineage(cid, three):
    child = fork.fork_campaign(cid, "Branch", from_scene=three[0])["id"]
    rows = {c["id"]: c for c in campaigns.list_campaigns()}
    assert rows[cid]["parent"] == "" and rows[cid]["forked_from_scene"] == ""
    assert rows[child]["parent"] == cid
    assert rows[child]["forked_from_scene"] == three[0]


def test_a_fork_whose_parent_was_deleted_lists_as_a_root(cid):
    """Nothing repairs the link, and nothing needs to: a `parent` naming a
    campaign that is gone finds no row to hang under, which is the only answer
    a deleted parent leaves."""
    child = fork.fork_campaign(cid, "Branch")["id"]
    campaigns.delete_campaign(cid)
    rows = {c["id"]: c for c in campaigns.list_campaigns()}
    assert rows[child]["parent"] == cid
    assert cid not in rows
