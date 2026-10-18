"""A missing optional record costs its prompt block, never the conversation.

These cases compose through the public entry point: catching a read error in
a gathering helper means little if another consumer of that record still
raises before the prompt can be sent.
"""

import pytest

from grimoire.store import (
    appearances,
    campaigns,
    characters,
    context,
    entities,
    pcs,
    scenes,
    styles,
    worlds,
)


@pytest.fixture
def scene(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Saltmarch", worlds.create_world("Realm"))
    sid = scenes.create_scene(cid, "Mara")
    scenes.append_message(cid, sid, "user", "Mara waits at the pier.")
    return cid, sid


@pytest.mark.parametrize("kind,role", [("characters", "npc"), ("characters", "player"),
                                       ("pcs", "player")])
def test_a_missing_locked_version_drops_only_its_card(scene, kind, role):
    cid, sid = scene
    root = campaigns.campaign_root(cid)
    card = characters.blank_card("Winifred")
    card["data"]["description"] = "Winifred keeps the ledger."
    aid, vid = characters.create_character(root, "Winifred", card=card)
    appearances.appear(cid, sid, "characters", aid, vid, "npc", narrate=False)
    if kind == "pcs":
        aid, vid = pcs.create_pc(root, "Seraphine", [],
                                 persona={"name": "Seraphine", "summary": "Seraphine guards the pier."})
        suffix = "md"
    else:
        card = characters.blank_card("Seraphine")
        card["data"]["description"] = "Seraphine guards the pier."
        aid, vid = characters.create_character(root, "Seraphine", card=card)
        suffix = "json"
    appearances.appear(cid, sid, kind, aid, vid, role, narrate=False)
    assert "Seraphine guards the pier." in str(context.compose_turn(cid, sid)[0])
    # A synced deletion can leave the appearance and container in place while
    # the version file is absent; do not repair those records in the fixture.
    (root / kind / aid / f"{vid}.{suffix}").unlink()
    messages, _ = context.compose_turn(cid, sid)
    assert "Seraphine guards the pier." not in str(messages)
    assert "Winifred keeps the ledger." in str(messages)
    assert {"role": "user", "content": "Mara waits at the pier."} in messages


def test_a_deleted_current_location_does_not_cost_the_turn(scene):
    cid, sid = scene
    root = campaigns.campaign_root(cid)
    eid = entities.create_entity(root, "locations", "Saltmarch", "A lamp lights the pier.")
    scenes.set_location(cid, sid, eid)
    assert "A lamp lights the pier." in str(context.compose_turn(cid, sid)[0])
    entities.delete_entity(root, "locations", eid)
    assert scenes.get_location_history(cid, sid)[-1] == eid
    messages, _ = context.compose_turn(cid, sid)
    assert "A lamp lights the pier." not in str(messages)
    assert {"role": "user", "content": "Mara waits at the pier."} in messages


@pytest.mark.parametrize("failure", [OSError("style became unreadable"),
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "style changed while syncing")])
def test_a_selected_style_that_becomes_unreadable_omits_only_the_style(scene, monkeypatch, failure):
    cid, sid = scene
    style = styles.create_style("Saltmarch", body="Keep the storm audible.")
    scenes.set_response(cid, sid, {"style_id": style})
    assert "Keep the storm audible." in str(context.compose_turn(cid, sid)[0])

    def unreadable(_sid):
        raise failure

    # Resolution can see a readable style before the subsequent body read
    # fails. Fault that read, not the resolver, or the test takes the ordinary
    # unknown-style fallback and never exercises this race.
    monkeypatch.setattr(styles, "read_style", unreadable)
    messages, _ = context.compose_turn(cid, sid)
    assert "Keep the storm audible." not in str(messages)
    assert "# Prose style" not in str(messages)
    assert messages[-1] == {"role": "user", "content": "Mara waits at the pier."}
