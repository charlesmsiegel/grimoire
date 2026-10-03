"""The tracker's update prompt: what the model is shown, and how a newcomer's
material is read. Rendering is pure; `newcomer` is the only part that reads a
store, and it answers an empty string rather than raising."""

import json

import pytest

from grimoire.store import appearances as ap
from grimoire.store import campaigns, characters, overlay, pcs, playstate, routing, scenes, worlds
from grimoire.store.campaigns import paths as campaigns_paths
from grimoire.store.tracker import fields, prompt

ROSTER = {"characters:mara": "Mara", "characters:winifred": "Winifred"}
PRESENT = {"characters:mara", "characters:winifred"}


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    sid = scenes.create_scene(cid, "Quay")
    return wid, cid, sid


def test_prompt_lists_fields_values_markers_and_post(home):
    prior = {"characters:mara": {"present": True, "fields": {"clothing": {"value": "grey cloak", "aware": "present", "set_by": "user"},
                                                              "concealed": {"value": "a letter", "aware": ["characters:winifred"]}}}}
    msgs = prompt.build_messages(list(fields.DEFAULT_FIELDS), prior, ROSTER, PRESENT,
                                 [{"ref": "characters:winifred", "name": "Winifred", "description": "A tall archivist.", "state": ""}],
                                 [{"speaker": "Mara", "content": "Earlier."}], {"speaker": "Winifred", "content": "She enters."})
    user = msgs[1]["content"]
    assert "grey cloak (user-set)" in user and "known to: Winifred" in user
    assert "Winifred (new)" in user and "A tall archivist." in user
    assert user.rstrip().endswith("Winifred: She enters.")
    assert "visible_mood (enum: admiration, amusement" in user


def test_messages_are_a_system_user_pair_and_the_system_text_is_pinned(home):
    msgs = prompt.build_messages(list(fields.DEFAULT_FIELDS), {}, ROSTER, PRESENT, [], [],
                                 {"speaker": "Mara", "content": "Hello."})
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[0]["content"].startswith("You maintain the scene state tracker")
    assert '{"changes": {}}' in msgs[0]["content"]


def test_off_fields_absent_absent_characters_skipped_and_private_marked(home):
    flds = [dict(f, off=True) if f["key"] == "pose" else f for f in fields.DEFAULT_FIELDS]
    prior = {"characters:mara": {"present": True, "fields": {
                 "concealed": {"value": "a letter", "aware": []},
                 "condition": {"value": ["soaked", "limping"], "aware": "present"}}},
             "characters:winifred": {"present": False, "fields": {
                 "clothing": {"value": "a green coat", "aware": "present"}}}}
    user = prompt.build_messages(flds, prior, ROSTER, {"characters:mara"}, [], [],
                                 {"speaker": "Mara", "content": "Hi."})[1]["content"]
    assert "pose (" not in user
    assert "green coat" not in user
    assert "concealed: a letter (private)" in user
    assert "condition: soaked, limping" in user
    assert "# New characters" not in user
    assert "# Earlier posts" in user


def test_newcomer_for_a_missing_actor_is_empty_strings_not_an_error(home):
    _, cid, _ = home
    for ref in ("characters:nobody", "pcs:nobody"):
        got = prompt.newcomer(cid, ref)
        assert got["ref"] == ref and got["description"] == "" and got["state"] == ""


def test_newcomer_reads_a_card_a_persona_and_the_standing_state(home):
    wid, cid, sid = home
    wroot = worlds.world_root(wid)
    card = characters.blank_card("Winifred")
    card["data"].update(description="A tall archivist.")
    wcid, _ = characters.create_character(wroot, "Winifred", "default", card)
    pid, _ = pcs.create_pc(wroot, "Mara", [])
    ap.appear(cid, sid, "characters", wcid, "default", "npc")
    ap.appear(cid, sid, "pcs", pid, "default", "player")
    playstate.write_state(campaigns_paths.campaign_root(cid), wcid, "## Current state\nTired.")
    got = prompt.newcomer(cid, f"characters:{wcid}")
    assert got == {"ref": f"characters:{wcid}", "name": "Winifred",
                   "description": "A tall archivist.", "state": "Tired."}
    got = prompt.newcomer(cid, f"pcs:{pid}")
    assert got["name"] == "Mara" and got["state"] == ""


@pytest.mark.parametrize("card", [{"name": "X"}, {"data": "oops"}, {"data": []}])
def test_newcomer_survives_a_card_with_no_usable_data(home, card):
    wid, cid, sid = home
    wcid, vid = characters.create_character(worlds.world_root(wid), "Winifred", "default",
                                            characters.blank_card("Winifred"))
    ap.appear(cid, sid, "characters", wcid, "default", "npc")
    overlay.materialize_actor(cid, "characters", wcid)
    path = characters.require_version(overlay.char_root(cid, wcid), wcid, vid)
    path.write_text(json.dumps(card), encoding="utf-8")
    got = prompt.newcomer(cid, f"characters:{wcid}")
    assert got["description"] == "" and got["ref"] == f"characters:{wcid}"


def test_newcomer_pc_reads_the_campaign_copy_not_the_world_one(home):
    wid, cid, sid = home
    persona = pcs.blank_persona("Mara")
    persona.update(summary="World summary.", description="World description.")
    pid, vid = pcs.create_pc(worlds.world_root(wid), "Mara", [], "default", persona)
    ap.appear(cid, sid, "pcs", pid, "default", "player")
    overlay.materialize_actor(cid, "pcs", pid)
    persona.update(summary="Campaign summary.", description="Campaign description.")
    pcs.require_version(overlay.pc_root(cid, pid), pid, vid).write_text(
        pcs._dump_persona(persona), encoding="utf-8")
    got = prompt.newcomer(cid, f"pcs:{pid}")
    assert got["description"] == "Campaign summary.\n\nCampaign description."


def test_newcomer_pc_that_only_the_campaign_has(home):
    _, cid, sid = home
    persona = pcs.blank_persona("Seraphine")
    persona.update(summary="Keeper.", description="Keeps the tide ledger.")
    pid, _ = overlay.create_pc(cid, "Seraphine", [], "default", persona)
    ap.appear(cid, sid, "pcs", pid, "default", "player")
    got = prompt.newcomer(cid, f"pcs:{pid}")
    assert got["name"] == "Seraphine" and "Keeps the tide ledger." in got["description"]


def test_routing_knows_the_task():
    assert routing.TASK_ROUTE["tracker-update"] == "tracker"


def test_a_field_whose_label_says_more_than_its_key_shows_the_label(home):
    """A custom field's label is what the person wrote it to mean; a key is an
    identifier. The model has to see both: the key to answer under, the label
    to know what it is answering about."""
    flds = [*fields.DEFAULT_FIELDS,
            {"key": "grudge", "label": "Old debt owed to Winifred", "type": "text",
             "aware": "self", "hint": ""}]
    msgs = prompt.build_messages(flds, {}, ROSTER, PRESENT, [], [],
                                 {"speaker": "Mara", "content": "Hello."})
    user = msgs[1]["content"]
    assert 'grudge "Old debt owed to Winifred" (text) [private by default]\n' in user
    # A built-in's label only restates its key, and is left out.
    assert "visible_mood (enum: admiration" in user and '"Visible mood"' not in user
    assert "by its key" in msgs[0]["content"]
