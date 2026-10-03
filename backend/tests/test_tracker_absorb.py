"""The scene's final tracked state reaches absorb, and a player character gains
a standing `state.md` that absorb can propose edits to.

Two halves. Absorb is told what the tracker recorded as the scene ended, private
values included and marked, because it writes canonical state rather than
speaking for anyone. And the `character_state` edit it already proposes now
accepts a PC id: staged, applied, undone and conflict-checked against
`pcs/<id>/play/state.md`, never against the NPC tree, which is where a PC whose
id matches an NPC's would otherwise read and write somebody else's state.

The state file sits in a `play/` subdirectory, not beside the persona files: a
PC's versions are `<vid>.md` in its own directory, so a `state.md` there would
be read as a version called "state" and would move the PC's sync hash.
"""

from __future__ import annotations

import pytest

import grimoire.store as store
from grimoire import routes
from grimoire.store import (
    absorb,
    appearances,
    campaigns,
    casefile,
    characters,
    journal,
    overlay,
    pcs,
    playstate,
    scenes,
    undo,
    worlds,
)
from grimoire.store.tracker import prompt as tracker_prompt
from tests import review_runs
from tests.llm_fakes import from_entries

ABSORB_JSON = (
    '{"one_line": "They met.", "summary": "A meeting.", "keywords": [],'
    ' "timeline_events": [], "character_state_edits": [], "lore_edits": [],'
    ' "plot_movements": [], "relationship_deltas": [], "bond_changes": [],'
    ' "new_lore": [], "weather_edits": []}')

_EXTRACTION = {"system_contains": "You are absorbing a completed role-play scene"}
_AUDIT = {"system_contains": "You are auditing a completed role-play scene"}
_DOSSIER = {"system_contains": "You are updating a game master's dossier"}
_VOICE = {"system_contains": "You are checking one character's dialogue"}

MARA, SERAPHINE = "characters:mara", "pcs:seraphine"

#: Mara's mood shows; what she hides is known to nobody. Seraphine holds a
#: lantern in plain sight.
STATE = {
    MARA: {"present": True, "fields": {
        "visible_mood": {"value": "fear", "aware": "present"},
        "concealed": {"value": "a forged pass", "aware": []}}},
    SERAPHINE: {"present": True, "fields": {
        "holding": {"value": "a lantern", "aware": "present"}}},
}


def _fake():
    return from_entries([{"when": _EXTRACTION, "reply": ABSORB_JSON},
                         {"when": _DOSSIER, "reply": "Mara is wary."},
                         {"when": _VOICE, "reply": '{"verdict": "in_voice", "note": ""}'},
                         {"when": _AUDIT, "reply": '{"warnings": [], "sheet_deltas": []}'}])


def _tracked_scene(client):
    """Mara as an NPC, Seraphine as the player, one tracked post whose record
    is `STATE`."""
    wid = store.worlds.create_world("Realm")
    root = store.worlds.world_root(wid)
    store.characters.create_character(root, "Mara", "main", store.characters.blank_card("Mara"))
    store.pcs.create_pc(root, "Seraphine", [], persona=store.pcs.blank_persona("Seraphine"))
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "The Quay")
    store.appearances.appear(cid, sid, "characters", "mara", "main", "npc", narrate=False)
    store.appearances.appear(cid, sid, "pcs", "seraphine", "default", "player", narrate=False)
    store.scenes.append_message(cid, sid, "user", "The tide is turning.", post_id="a" * 32)
    ident = store.scenes.ensure_identity(cid, sid)
    keys = store.tracker.walk.ordered_keys(cid, sid)
    assert keys, "the fixture has no tracked post to key a record to"
    for _, key in keys:
        store.tracker.records.save(cid, ident, key, STATE, changed=[],
                                   fields_digest="d", model="m")
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    return cid, sid


def _extraction_user(fake) -> str:
    for req in fake.requests:
        system = "\n".join(m["content"] for m in req["messages"] if m["role"] == "system")
        if _EXTRACTION["system_contains"] in system:
            return "\n".join(m["content"] for m in req["messages"] if m["role"] == "user")
    raise AssertionError("no extraction request was made")


@pytest.mark.tracker
def test_absorb_prompt_carries_final_tracked_state(client):
    cid, sid = _tracked_scene(client)
    fake = _fake()
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    assert review_runs.absorb(client, cid, sid).status_code == 200
    user = _extraction_user(fake)
    assert "Final tracked state (as the scene ended; private values marked):" in user
    assert "- Mara: Visible mood: fear; Concealed: a forged pass (private)" in user
    assert "- Seraphine: Holding: a lantern" in user
    # In the head, ahead of the transcript it summarises.
    assert user.index("Final tracked state") < user.index("The tide is turning.")


@pytest.mark.tracker
def test_absorb_keeps_a_departed_characters_last_tracked_state(client):
    """Mara left after the last tracked post: her record still says present,
    and her cast membership is gone. Absorb reads the whole scene, so her last
    state stays in, marked as having left -- not dropped as a stranger."""
    cid, sid = _tracked_scene(client)
    store.appearances.leave(cid, sid, "characters", "mara")
    fake = _fake()
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    assert review_runs.absorb(client, cid, sid).status_code == 200
    user = _extraction_user(fake)
    assert ("- Mara (left the scene): Visible mood: fear; Concealed: a forged pass (private)"
            in user)
    assert "- Seraphine: Holding: a lantern" in user


def test_absorb_prompt_has_no_tracked_state_when_the_tracker_is_off(client):
    """The suite's default is the tracker off: records on disk are not read."""
    cid, sid = _tracked_scene(client)
    fake = _fake()
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    assert review_runs.absorb(client, cid, sid).status_code == 200
    assert "Final tracked state" not in _extraction_user(fake)


@pytest.mark.tracker
def test_a_tracker_read_that_fails_costs_the_section_not_the_absorb(client, monkeypatch):
    cid, sid = _tracked_scene(client)

    def broken(*_a, **_k):
        raise ValueError("malformed responses.json")
    monkeypatch.setattr(store.tracker.walk, "current", broken)
    fake = _fake()
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    assert review_runs.absorb(client, cid, sid).status_code == 200
    assert "Final tracked state" not in _extraction_user(fake)


def test_the_tracked_block_renders_nothing_when_empty():
    bare = absorb.build_prompt("**You:** hi", {})[1]["content"]
    assert absorb.build_prompt("**You:** hi", {}, tracked_snapshot=[])[1]["content"] == bare
    assert absorb.build_prompt("**You:** hi", {}, tracked_snapshot=None)[1]["content"] == bare


def test_the_system_prompt_admits_pc_ids_for_current_state_only():
    system = absorb.build_prompt("**You:** hi", {})[0]["content"]
    assert '"pcs/<id>"' in system and "Final tracked state" in system


# ---- the player character's state file ----------------------------------------

@pytest.fixture
def home(monkeypatch, tmp_path):
    """A campaign whose scene has Mara (NPC) and Seraphine (the player) cast."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    root = worlds.world_root(wid)
    characters.create_character(root, "Mara", "main", characters.blank_card("Mara"))
    pcs.create_pc(root, "Seraphine", [], persona=pcs.blank_persona("Seraphine"))
    cid = campaigns.create_campaign("Saltmarch", wid)
    sid = scenes.create_scene(cid, "The Quay")
    appearances.appear(cid, sid, "characters", "mara", "main", "npc", narrate=False)
    appearances.appear(cid, sid, "pcs", "seraphine", "default", "player", narrate=False)
    return cid, sid, campaigns.campaign_root(cid)


def test_playstate_paths_by_kind(home):
    _, _, croot = home
    assert playstate.state_path(croot, "mara") == croot / "characters" / "mara" / "state.md"
    assert (playstate.state_path(croot, "seraphine", "pcs")
            == croot / "pcs" / "seraphine" / "play" / "state.md")
    with pytest.raises(ValueError):
        playstate.state_path(croot, "mara", "groups")
    with pytest.raises(ValueError):
        playstate.read_state(croot, "mara", kind="locations")
    with pytest.raises(ValueError):
        playstate.write_state(croot, "mara", "x", kind="")


def test_a_pc_state_is_not_a_version_and_does_not_move_the_sync_hash(home):
    cid, _, croot = home
    root = overlay.pc_root(cid, "seraphine")
    versions = [v["id"] for v in pcs.read_pc(root, "seraphine")["versions"]]
    digest = pcs.dir_hash(root, "seraphine")
    playstate.write_state(croot, "seraphine", "Sprained wrist.", kind="pcs")
    root = overlay.pc_root(cid, "seraphine")
    assert [v["id"] for v in pcs.read_pc(root, "seraphine")["versions"]] == versions
    assert pcs.dir_hash(root, "seraphine") == digest
    # ...and it is not the NPC tree's file.
    assert playstate.read_state(croot, "seraphine") is None


def test_materializing_a_pc_keeps_its_state(monkeypatch, tmp_path):
    """A PC inherited from the world can have state before the campaign copies
    it; the copy must not take the state with it."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    pcs.create_pc(worlds.world_root(wid), "Seraphine", [])
    cid = campaigns.create_campaign("Saltmarch", wid)
    croot = campaigns.campaign_root(cid)
    playstate.write_state(croot, "seraphine", "Sprained wrist.", kind="pcs")
    overlay.materialize_actor(cid, "pcs", "seraphine")
    assert playstate.read_state(croot, "seraphine", "pcs")["current_state"] == "Sprained wrist."
    assert [v["id"] for v in pcs.read_pc(croot, "seraphine")["versions"]] == ["default"]


def _pc_edit(cid, sid, text="Sprained wrist."):
    parsed = {"character_state_edits": [{"id": "pcs/seraphine", "current_state": text}]}
    edits = absorb.materialize(cid, sid, parsed)
    return next(e for e in edits if e["target"] == {"kind": "pcs", "id": "seraphine"})


def test_pc_state_edit_is_staged_and_applied(home):
    cid, sid, croot = home
    e = _pc_edit(cid, sid)
    assert e["id"] == "character_state:pcs:seraphine"
    assert e["kind"] == "character_state"
    assert e["label"] == "Seraphine — current state"
    assert e["before"] == "" and e["after"] == "Sprained wrist."
    applied, failures = absorb.apply_edits(cid, [e], sid)
    assert failures == [] and applied
    assert playstate.read_state(croot, "seraphine", "pcs")["current_state"] == "Sprained wrist."
    assert playstate.read_state(croot, "seraphine") is None


def test_the_changes_listing_keeps_a_pc_state_row(home):
    """`/changes` names each record it lists and drops one it cannot name (a
    record deleted since). A PC is a record it must be able to name."""
    from grimoire.routes import campaigns as campaign_routes

    cid, sid, _croot = home
    absorb.apply_edits(cid, [_pc_edit(cid, sid)], sid)
    out = campaign_routes.get_changes(cid)
    rows = [r for r in out if r["ref"] == {"kind": "pcs", "id": "seraphine"}]
    assert len(rows) == 1 and rows[0]["name"] == "Seraphine"


def test_a_pc_state_edit_carries_no_knows_or_suspects(home):
    cid, sid, _croot = home
    parsed = {"character_state_edits": [{"id": "pcs:seraphine", "current_state": "Soaked.",
                                         "knows": "the pass is forged", "suspects": "Mara"}]}
    e = next(e for e in absorb.materialize(cid, sid, parsed)
             if e["target"]["kind"] == "pcs")
    assert e["after"] == "Soaked."


def test_a_pc_id_that_names_no_pc_is_dropped(home):
    cid, sid, croot = home
    parsed = {"character_state_edits": [{"id": "pcs/winifred", "current_state": "Lost."}]}
    assert absorb.materialize(cid, sid, parsed) == []
    assert not (croot / "characters" / "winifred").exists()


def test_a_pc_state_edit_is_judged_against_the_pc_state(home):
    cid, sid, croot = home
    playstate.write_state(croot, "seraphine", "Bruised.", kind="pcs")
    playstate.write_state(croot, "mara", "Unrelated NPC state.")
    e = _pc_edit(cid, sid)
    assert e["before"] == "Bruised."
    assert absorb.current_value(cid, e) == "Bruised."
    _applied, failures = absorb.apply_edits(cid, [e], sid)
    assert failures == []
    playstate.write_state(croot, "seraphine", "Moved on.", kind="pcs")
    assert absorb.current_value(cid, e) == "Moved on."


def test_undoing_a_pc_state_edit(home):
    cid, sid, croot = home
    playstate.write_state(croot, "seraphine", "Bruised.", kind="pcs")
    absorb.apply_edits(cid, [_pc_edit(cid, sid)], sid)
    assert playstate.read_state(croot, "seraphine", "pcs")["current_state"] == "Sprained wrist."
    entry = journal.read(cid)[0]
    assert entry["undo"] is not None, entry
    undo.undo(cid, entry["id"])
    assert playstate.read_state(croot, "seraphine", "pcs")["current_state"] == "Bruised."
    assert not playstate.state_path(croot, "seraphine").exists()


def test_absorb_can_clear_a_pc_state_that_no_longer_holds(home):
    """A PC's state.md is written by absorb and nothing else, so an absorb that
    says the state is over (an explicit empty `current_state`) has to be able
    to stage that -- dropped as an empty result, the stale state would stand
    for good. Undo puts it back."""
    cid, sid, croot = home
    playstate.write_state(croot, "seraphine", "Sprained wrist.", kind="pcs")
    e = _pc_edit(cid, sid, text="")
    assert e["before"] == "Sprained wrist." and e["after"] == ""
    applied, failures = absorb.apply_edits(cid, [e], sid)
    assert failures == [] and applied
    st = playstate.read_state(croot, "seraphine", "pcs")
    assert st is None or st["current_state"] == ""
    assert absorb.state_snapshot(cid, sid).get("Seraphine") is None
    assert casefile.build(cid, sid, "pcs", "seraphine")["standing"] == ""
    entry = journal.read(cid)[0]
    assert entry["undo"] is not None, entry
    undo.undo(cid, entry["id"])
    assert playstate.read_state(croot, "seraphine", "pcs")["current_state"] == "Sprained wrist."


def test_absorb_leaves_a_pc_state_alone_when_it_says_nothing_about_it(home):
    """Clearing takes an explicit empty value. An entry that omits the current
    state, or clears one that was never set, stages nothing."""
    cid, sid, croot = home
    parsed = {"character_state_edits": [{"id": "pcs/seraphine"}]}
    assert absorb.materialize(cid, sid, parsed) == []
    parsed = {"character_state_edits": [{"id": "pcs/seraphine", "current_state": ""}]}
    assert absorb.materialize(cid, sid, parsed) == []
    playstate.write_state(croot, "seraphine", "Sprained wrist.", kind="pcs")
    parsed = {"character_state_edits": [{"id": "pcs/seraphine"}]}
    assert absorb.materialize(cid, sid, parsed) == []


def test_undo_will_not_conjure_play_state_for_a_deleted_pc(home):
    cid, sid, croot = home
    absorb.apply_edits(cid, [_pc_edit(cid, sid)], sid)
    entry = journal.read(cid)[0]
    overlay.delete_actor(cid, "pcs", "seraphine")
    with pytest.raises(undo.UndoConflict):
        undo.undo(cid, entry["id"])
    assert not playstate.state_path(croot, "seraphine", "pcs").exists()


def test_next_scene_newcomer_reads_pc_state(home):
    cid, _, croot = home
    playstate.write_state(croot, "seraphine", "## Current state\nSprained wrist.", kind="pcs")
    assert tracker_prompt.newcomer(cid, "pcs:seraphine")["state"] == "Sprained wrist."


def test_the_casefile_reads_a_pc_state_from_the_pc(home):
    cid, sid, croot = home
    playstate.write_state(croot, "seraphine", "Sprained wrist.", kind="pcs")
    assert casefile.build(cid, sid, "pcs", "seraphine")["standing"] == "Sprained wrist."


def test_absorb_shows_the_pc_standing_state(home):
    cid, sid, croot = home
    playstate.write_state(croot, "seraphine", "Sprained wrist.", kind="pcs")
    playstate.write_state(croot, "mara", "Wary.")
    snap = absorb.state_snapshot(cid, sid)
    assert snap["Seraphine"] == "Sprained wrist."
    assert snap["Mara"] == "Wary."
