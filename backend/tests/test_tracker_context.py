"""The scene state section: what each prompt is told about the tracked state,
and above all what it is NOT told.

The tracker records who knows what about whom, so the section is filtered per
reader: an assigned NPC sees its own state whole, another character's only
where that character's body gives it away (`aware: "present"`) or where it was
told (a list naming it); the narrator sees everything, with the private values
labelled so it plays them as unspoken. The leak tests below are the point of
this file -- every other assertion is about shape.
"""

from __future__ import annotations

import json
import re

import pytest

from grimoire import routes, store
from grimoire.store import (
    appearances,
    campaigns,
    characters,
    config,
    context,
    pcs,
    pins,
    responses,
    scenes,
    worlds,
)
from grimoire.store.context import assemble, layout
from grimoire.store.tracker import records, walk

from .llm_fakes import FakeLLM, from_entries

pytestmark = pytest.mark.tracker

MARA, WINIFRED, SERAPHINE = "characters:mara", "characters:winifred", "pcs:seraphine"
NARRATED = " (private: never state or imply in narration)"


def _secret(ref: str) -> str:
    return ref + "_PRIVATE"


#: Every character and the PC hold one private value; Mara's lists Winifred,
#: so Winifred has been told it and nobody else has. Each also holds one value
#: anyone present can see.
STATE = {
    MARA: {"present": True, "fields": {
        "visible_mood": {"value": "joy", "aware": "present"},
        "true_mood": {"value": _secret(MARA), "aware": [WINIFRED]}}},
    WINIFRED: {"present": True, "fields": {
        "clothing": {"value": "grey cloak", "aware": "present"},
        "intent": {"value": _secret(WINIFRED), "aware": []}}},
    SERAPHINE: {"present": True, "fields": {
        "holding": {"value": "a lantern", "aware": "present"},
        "concealed": {"value": _secret(SERAPHINE), "aware": []}}},
}


@pytest.fixture
def cast_scene(monkeypatch, tmp_path):
    """Mara and Winifred as NPCs, Seraphine as the player, one tracked post."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    wid = worlds.create_world("Realm")
    root = worlds.world_root(wid)
    for name in ("Mara", "Winifred"):
        characters.create_character(root, name, "main", characters.blank_card(name))
    pcs.create_pc(root, "Seraphine", [], persona=pcs.blank_persona("Seraphine"))
    cid = campaigns.create_campaign("Saltmarch", wid)
    sid = scenes.create_scene(cid, "Scene")
    for name in ("mara", "winifred"):
        appearances.appear(cid, sid, "characters", name, "main", "npc", narrate=False)
    appearances.appear(cid, sid, "pcs", "seraphine", "default", "player", narrate=False)
    scenes.append_message(cid, sid, "user", "The tide is turning.", post_id="a" * 32)
    _seed(cid, sid, STATE)
    return cid, sid


def _seed(cid: str, sid: str, snapshot: dict) -> None:
    """`snapshot` as the record of every tracked post in the scene."""
    ident = scenes.ensure_identity(cid, sid)
    keys = walk.ordered_keys(cid, sid)
    assert keys, "the fixture has no tracked post to key a record to"
    for _, key in keys:
        records.save(cid, ident, key, snapshot, changed=[], fields_digest="d", model="m")


def _text(cid: str, sid: str, actor_ref: str | None) -> str:
    return str(context.compose_turn(cid, sid, actor_ref=actor_ref)[0])


def test_no_actor_prompt_leaks_another_actors_private_value(cast_scene):
    cid, sid = cast_scene
    for ref in (MARA, WINIFRED):
        text = _text(cid, sid, ref)
        assert "# Scene state" in text
        for owner in (MARA, WINIFRED, SERAPHINE):
            allowed = owner == ref or (owner == MARA and ref == WINIFRED)
            assert (_secret(owner) in text) == allowed, (ref, owner)


def test_an_npc_reads_itself_whole_and_the_others_as_perceived(cast_scene):
    cid, sid = cast_scene
    text = _text(cid, sid, WINIFRED)
    block = text[text.index("# Scene state"):]
    assert ("Winifred (you) — Clothing: grey cloak; Intent: "
            + _secret(WINIFRED)) in block
    assert ("Mara (what you can perceive) — Visible mood: joy; True mood: "
            + _secret(MARA) + " (known to you)") in block
    assert "Seraphine (what you can perceive) — Holding: a lantern" in block
    # Its own line first, however the snapshot happens to be ordered.
    assert block.index("Winifred (you)") < block.index("Mara (what")


def test_narrator_sees_all_with_private_label(cast_scene):
    cid, sid = cast_scene
    for actor_ref in ("grimoire", None):
        text = _text(cid, sid, actor_ref)
        for owner in (MARA, WINIFRED, SERAPHINE):
            assert _secret(owner) + NARRATED in text, (actor_ref, owner)
        assert "(you)" not in text and "(what you can perceive)" not in text
        assert "Mara — Visible mood: joy; True mood: " + _secret(MARA) + NARRATED in text


def test_a_character_with_nothing_this_reader_may_see_renders_no_line(cast_scene):
    cid, sid = cast_scene
    hidden = json.loads(json.dumps(STATE))
    del hidden[WINIFRED]["fields"]["clothing"]
    _seed(cid, sid, hidden)
    messages = context.compose_turn(cid, sid, actor_ref=MARA)[0]
    block = _block(messages)
    assert "Mara (you)" in block and "Seraphine (what you can perceive)" in block
    assert "Winifred" not in block
    assert _secret(WINIFRED) not in str(messages)


def test_an_excluded_character_takes_its_state_out_with_it(cast_scene):
    """An exclude is "keep this out of the prompt": the character's card and
    standing state go, and so does what the tracker recorded of them."""
    cid, sid = cast_scene
    pins.set_rule(cid, WINIFRED, pins.EXCLUDE, sid=sid)
    for actor_ref in (MARA, None):
        text = _text(cid, sid, actor_ref)
        assert "# Scene state" in text
        assert "grey cloak" not in text and _secret(WINIFRED) not in text


def test_section_absent_when_tracker_off(cast_scene):
    cid, sid = cast_scene
    config.write_config(tracker="off")
    for actor_ref in (MARA, "grimoire", None):
        text = _text(cid, sid, actor_ref)
        assert "# Scene state" not in text
        assert _secret(MARA) not in text


def test_section_absent_with_no_record(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    wid = worlds.create_world("Realm")
    characters.create_character(worlds.world_root(wid), "Mara", "main",
                                characters.blank_card("Mara"))
    cid = campaigns.create_campaign("Saltmarch", wid)
    sid = scenes.create_scene(cid, "Scene")
    appearances.appear(cid, sid, "characters", "mara", "main", "npc", narrate=False)
    scenes.append_message(cid, sid, "user", "Hello.", post_id="b" * 32)
    assert "# Scene state" not in _text(cid, sid, None)
    assert "# Scene state" not in _text(cid, sid, MARA)


def test_a_garbled_response_ledger_costs_the_section_not_the_turn(cast_scene):
    """The walk reads the response ledger and fails closed on a malformed one
    (right for a prune). A prompt must not: it composes without the section."""
    cid, sid = cast_scene
    responses._path(cid).write_text("not json", encoding="utf-8")
    for actor_ref in (MARA, None):
        text = _text(cid, sid, actor_ref)
        assert "# Scene state" not in text
        assert "The tide is turning." in text


def test_the_opener_carries_no_scene_state(cast_scene):
    cid, sid = cast_scene
    messages, detail = context.compose_opener(cid, sid, "Set the scene.")
    assert "# Scene state" not in str(messages)
    assert all(row["id"] != "tracker_state" for row in detail["sections"])


def test_a_pinned_cast_member_holds_the_section_up():
    cast = [{"kind": "characters", "id": "mara", "role": "npc"}]
    held = assemble._pinned_sections(frozenset({MARA}), cast, [], None)
    assert "tracker_state" in held


def test_saved_layout_keeps_transient_position():
    migrated = layout._migrate([{"id": "transient_state", "enabled": False}])
    assert migrated == [{"id": "tracker_state", "enabled": False}]
    merged = layout.merge(assemble.SECTIONS, [{"id": "transient_state", "enabled": False}])
    assert all(s.id != "tracker_state" for s in merged)


# ---------------------------------------------------------------- the real routes

#: The system prompt that owns a tracker update (templates/tracker/update_system.j2).
TRACKER = {"system_contains": "You maintain the scene state tracker"}
MARA_SAYS = 'Mara answers.\n```handoff\n{"next":null}\n```'
RUN_TIMEOUT = 10.0
_BLOCK = re.compile(r"# Scene state\n.*?(?=\n\n|\Z)", re.DOTALL)


def _flow(client, clothing: str) -> FakeLLM:
    """Every tracker update dresses Mara in `clothing`; every turn is Mara's."""
    llm = from_entries([
        {"when": TRACKER, "reply": json.dumps({"changes": {"Mara": {"clothing": clothing}}})},
        {"when": {}, "reply": MARA_SAYS}])
    client.app.dependency_overrides[routes.get_llm] = lambda: llm
    return llm


def _scene(client) -> tuple[str, str]:
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-test"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Harbour")
    for name in ("Mara", "Winifred"):
        actor = client.post(f"/api/campaigns/{cid}/characters",
                            json={"name": name}).json()["character"]
        r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"id": actor})
        assert r.status_code == 200, r.text
    return cid, sid


def _send(client, cid, sid, text):
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                    json={"content": text, "speaker_ref": MARA})
    assert r.status_code == 200, r.text
    assert '"error"' not in r.text, r.text


def _settle(client, cid, sid) -> None:
    ident = store.scenes.scene_identity(cid, sid)
    for run in client.app.state.runs.for_subject(("scene", cid, ident)):
        if run.cls == "background" and run.kind == "tracker-update":
            assert run.terminal.wait(timeout=RUN_TIMEOUT), "a tracker update never finished"


def _played(client, clothing: str) -> tuple[str, str, str, FakeLLM]:
    """Two tracked turns, settled: the second was composed with the state the
    first left behind, so its frozen prompt carries a block."""
    llm = _flow(client, clothing)
    cid, sid = _scene(client)
    _send(client, cid, sid, "Mara, the tide is turning.")
    _settle(client, cid, sid)
    _send(client, cid, sid, "Mara, the boats are in.")
    _settle(client, cid, sid)
    rid = store.scenes.read_scene(cid, sid)["messages"][-1]["response_id"]
    return cid, sid, rid, llm


def _turn_requests(llm: FakeLLM) -> list[list[dict]]:
    return [req["messages"] for req in llm.requests
            if not any(TRACKER["system_contains"] in m.get("content", "")
                       for m in req["messages"] if m.get("role") == "system")]


def _block(messages) -> str:
    system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
    found = _BLOCK.search(system)
    assert found, "no scene state block in the prompt"
    return found.group(0)


def test_frozen_prompt_carries_the_state_block(client):
    cid, sid, rid, llm = _played(client, "STATE_ONE cloak")
    frozen = responses.get(cid, sid, rid, private=True)["snapshot"]
    block = _block(frozen["unprofiled"][0])
    assert "Mara (you) — Clothing: STATE_ONE cloak" in block
    # The block frozen is the block that was sent.
    assert _block(_turn_requests(llm)[-1]) == block


def test_a_reroll_replays_the_state_it_was_first_composed_with(client):
    """A reroll does not recompose: it replays the frozen prompt, so it answers
    from the state that stood before the post it replaces -- not from whatever
    the tracker has recorded since."""
    cid, sid, rid, llm = _played(client, "STATE_ONE cloak")
    frozen = _block(responses.get(cid, sid, rid, private=True)["snapshot"]["unprofiled"][0])
    changed = {MARA: {"present": True, "fields": {
        "clothing": {"value": "STATE_TWO coat", "aware": "present"}}}}
    _seed(cid, sid, changed)
    assert "STATE_TWO coat" in str(context.compose_turn(cid, sid, actor_ref=MARA)[0])
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}/regenerate", json={})
    assert '"error"' not in r.text, r.text
    _settle(client, cid, sid)
    sent = _block(_turn_requests(llm)[-1])
    assert sent == frozen
    assert "STATE_ONE cloak" in sent and "STATE_TWO" not in sent
