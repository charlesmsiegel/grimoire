"""Assigned writers receive only evidence attributed to their actor."""
import json

import pytest

from grimoire import model_guidance
from grimoire.store import (
    appearances,
    campaigns,
    characters,
    config,
    context,
    entities,
    overlay,
    playstate,
    relationships,
    scenes,
    worlds,
)
from grimoire.store.appearances import paths


@pytest.fixture
def cast_scene(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    wid = worlds.create_world("Realm")
    root = worlds.world_root(wid)
    for name in ("Mara", "Winifred"):
        card = characters.blank_card(name)
        card["data"]["description"] = name + "_CARD_SECRET"
        card["data"]["mes_example"] = "<START>\n" + name + ": example speech."
        characters.create_character(root, name, "main", card)
    cid = campaigns.create_campaign("Saltmarch", wid)
    sid = scenes.create_scene(cid, "Scene")
    for name in ("mara", "winifred"):
        appearances.appear(cid, sid, "characters", name, "main", "npc", narrate=False)
    return cid, sid


def test_actor_secrets_are_excluded_before_render(cast_scene):
    cid, sid = cast_scene
    scenes.append_message(cid, sid, "user", "PUBLIC_DIALOGUE")
    messages, detail = context.compose_turn(cid, sid, actor_ref="characters:mara",
        eligible_speakers=[{"ref": "characters:winifred", "name": "Winifred"}])
    text = str(messages)
    assert "Mara_CARD_SECRET" in text
    assert "Winifred_CARD_SECRET" not in text
    assert "Winifred: example speech" not in text
    assert "Winifred" in text and "PUBLIC_DIALOGUE" in text
    assert "```handoff" in text
    assert any(row["id"] == "response_actor" for row in detail["sections"])


def test_arrival_and_reentry_bound_observed_history(cast_scene):
    cid, sid = cast_scene
    appearances.leave(cid, sid, "characters", "mara")
    scenes.append_message(cid, sid, "user", "PRIVATE_ABSENCE")
    appearances.appear(cid, sid, "characters", "mara", "main", "npc", narrate=False)
    scenes.append_message(cid, sid, "user", "VISIBLE_AFTER_RETURN")
    messages, _ = context.compose_turn(cid, sid, actor_ref="characters:mara")
    assert "PRIVATE_ABSENCE" not in str(messages)
    assert "VISIBLE_AFTER_RETURN" in str(messages)


def test_legacy_presence_only_current_input(cast_scene):
    cid, sid = cast_scene
    record = paths.record(cid)
    record["characters/mara"].pop("presence", None)
    paths._write(cid, record)
    scenes.append_message(cid, sid, "user", "UNKNOWN_OLD_DIALOGUE")
    scenes.append_message(cid, sid, "user", "CURRENT_INPUT")
    messages, _ = context.compose_turn(cid, sid, actor_ref="characters:mara")
    assert "UNKNOWN_OLD_DIALOGUE" not in str(messages)
    assert "CURRENT_INPUT" in str(messages)


def test_snapshot_roundtrip_freezes_undispatched_profiles(cast_scene, monkeypatch):
    prepared, _ = context.compose_turn(*cast_scene, model="unknown")
    snapshot = json.loads(json.dumps(prepared.snapshot()))
    def forbidden(*args, **kwargs):
        raise AssertionError("Historical context reread")
    monkeypatch.setattr(context.assemble, "_assemble", forbidden)
    restored = model_guidance.PreparedMessages.from_snapshot(snapshot, "glm-5.3")
    assert restored == prepared.for_model("glm-5.3")
    assert restored.for_model("new-model") == prepared.for_model("")
    snapshot["unprofiled"][0][0]["content"] = "mutated"
    assert restored.for_model("new-model") == prepared.for_model("")


def test_private_state_lore_and_directed_relationships(cast_scene):
    cid, sid = cast_scene
    root = campaigns.campaign_root(cid)
    for name in ("mara", "winifred"):
        playstate.write_state(root, name, "## Current state\n" + name + "_STATE_SECRET\n"
                              "## Knows\n" + name + "_KNOWN_SECRET")
        entities.create_entity(root, "lore", name, name + "_LORE_SECRET", owners="characters:" + name,
                               secrecy="secret")
    entities.create_entity(root, "lore", "Public", "SHARED_PUBLIC_FACT")
    entities.create_entity(root, "lore", "Secret", "UNATTRIBUTED_SECRET", secrecy="secret")
    relationships.set_feeling(cid, "characters:mara", "characters:winifred", 0, 0, 0, "OWN_FEELING")
    relationships.set_feeling(cid, "characters:winifred", "characters:mara", 0, 0, 0, "OTHER_FEELING")
    text = str(context.compose_turn(cid, sid, actor_ref="characters:mara")[0])
    for value in ("mara_STATE_SECRET", "mara_KNOWN_SECRET", "mara_LORE_SECRET", "SHARED_PUBLIC_FACT", "OWN_FEELING"):
        assert value in text
    for value in ("winifred_STATE_SECRET", "winifred_KNOWN_SECRET", "winifred_LORE_SECRET", "UNATTRIBUTED_SECRET", "OTHER_FEELING"):
        assert value not in text


def test_actor_checks_and_location_sheets_are_not_other_private_evidence(cast_scene, monkeypatch):
    monkeypatch.setattr(context.assemble.mechanics, "_mechanics", lambda *args: {
        "mechanics_rules": [],
        "mechanics_sheets": [{"ref": "locations:realm", "label": "Location", "type_label": "Place", "lines": ["PRIVATE_LOCATION_SHEET"]}],
        "mechanics_checks": [{"ref": "characters:winifred", "label": "Winifred", "sheet_type": "npc", "checks": [["secret", "PRIVATE_CHECK"]]}],
    })
    text = str(context.compose_turn(*cast_scene, actor_ref="characters:mara")[0])
    assert "PRIVATE_LOCATION_SHEET" not in text
    assert "PRIVATE_CHECK" not in text


def test_complete_examples_and_conflicts_survive_bounded_pressure(cast_scene):
    cid, sid = cast_scene
    overlay.set_voice_anchor(cid, "mara", "Usually terse.")
    config.write_config(context_budget="1")
    messages, detail = context.compose_turn(cid, sid, actor_ref="characters:mara")
    rows = {row["id"]: row for row in detail["sections"]}
    assert "Mara: example speech." in str(messages)
    assert not rows["voice_examples"]["dropped"]
    assert "Usually terse." in rows["voice_anchors"]["text"]
    assert "sources conflict" in rows["voice_policy"]["text"]
    assert "editing the character" in rows["voice_policy"]["text"]


def test_examples_skip_overlong_exchange_without_slicing():
    from grimoire.store.context import actor
    short = "Mara: Is the ledger here?\nWinifred: Yes."
    long = "Mara: " + "entire sentence " * 100
    selected = actor.select_examples("<START>\n" + long + "\n<START>\n" + short, 100, "ledger")
    assert selected == "<START>\n" + short
    assert actor.select_examples(long, 100) == ""


def test_presence_remap_keeps_absent_posts_hidden(cast_scene):
    cid, sid = cast_scene
    scenes.append_message(cid, sid, "user", "VISIBLE_FIRST")
    appearances.leave(cid, sid, "characters", "mara")
    scenes.append_message(cid, sid, "user", "PRIVATE")
    appearances.appear(cid, sid, "characters", "mara", "main", "npc", narrate=False)
    scenes.append_message(cid, sid, "user", "VISIBLE_LAST")
    before = scenes.read_scene(cid, sid)["messages"]
    mapping = {i: i - 1 for i in range(1, len(before))}
    paths.remap_presence(cid, sid, mapping, len(before) - 1)
    from grimoire.store.context import actor
    visible = actor.observed_history(cid, sid, "characters:mara", before[1:])
    assert "PRIVATE" not in str(visible)
    assert "VISIBLE_LAST" in str(visible)


def test_deleted_scene_does_not_leave_a_presence_audience(cast_scene):
    cid, sid = cast_scene
    scenes.delete_scene(cid, sid)
    for record in paths.record(cid).values():
        assert sid not in record.get("presence", {})
        assert sid not in record["scenes"]


def test_narrator_retains_scene_scope_and_director_assignment(cast_scene):
    messages, _ = context.compose_director_turn(*cast_scene, "Observe", actor_ref="grimoire")
    assert "Winifred_CARD_SECRET" in str(messages)
    assert "Mara_CARD_SECRET" in str(messages)
    assert "Do not write any NPC dialogue" in str(messages)


@pytest.mark.parametrize("ref", ["characters:absent", "pcs:seraphine", "", "characters/mara"])
def test_unknown_or_player_actor_is_rejected(cast_scene, ref):
    with pytest.raises(ValueError, match="present NPC"):
        context.compose_turn(*cast_scene, actor_ref=ref)


def test_frozen_steer_survives_snapshot_and_actual_model_fallback(cast_scene):
    prepared, _ = context.compose_turn(*cast_scene)
    steer = {"role": "system", "content": "A slower reply."}
    revised = prepared.with_appended(steer)
    steer["content"] = "mutated"
    restored = model_guidance.PreparedMessages.from_snapshot(revised.snapshot(), "glm-5.3")
    assert restored[-1]["content"] == "A slower reply."
    assert restored.for_model("unknown")[-1]["content"] == "A slower reply."
    assert prepared[-1]["content"] != "A slower reply."
    assert restored.breakdown is None  # original token counts no longer describe this prompt


def test_appended_steer_once_when_profiles_share_payload():
    shared = ([{"role": "system", "content": "Base"}], None)
    prepared = model_guidance.PreparedMessages("", lambda model: shared,
        profiles={"": shared, "same": shared})
    result = prepared.with_appended({"role": "user", "content": "Steer"})
    assert [m["content"] for m in result] == ["Base", "Steer"]
    assert result.for_model("same") == result


def test_presence_rename_retains_absence_boundary(cast_scene):
    cid, sid = cast_scene
    appearances.leave(cid, sid, "characters", "mara")
    scenes.append_message(cid, sid, "user", "PRIVATE_BEFORE_RENAME")
    appearances.appear(cid, sid, "characters", "mara", "main", "npc", narrate=False)
    renamed = scenes.rename_scene(cid, sid, "Later")
    scenes.append_message(cid, renamed, "user", "PUBLIC_AFTER_RENAME")
    messages, _ = context.compose_turn(cid, renamed, actor_ref="characters:mara")
    assert "PRIVATE_BEFORE_RENAME" not in str(messages)
    assert "PUBLIC_AFTER_RENAME" in str(messages)
    assert sid not in paths.record(cid)["characters/mara"]["presence"]


def test_unattributed_campaign_material_never_reaches_writer(cast_scene, monkeypatch):
    assembly = context.assemble
    monkeypatch.setattr(assembly.story, "_story_entries", lambda *args, **kwargs: ["PRIVATE_RECAP"])
    monkeypatch.setattr(assembly.archive, "_archive_entries", lambda *args, **kwargs: ["PRIVATE_ARCHIVE"])
    monkeypatch.setattr(assembly.plot, "render_open", lambda *args, **kwargs: ["PRIVATE_PLOT"])
    monkeypatch.setattr(assembly.commitments, "render_open", lambda *args, **kwargs: ["PRIVATE_COMMITMENT"])
    text = str(context.compose_turn(*cast_scene, actor_ref="characters:mara")[0])
    for value in ("PRIVATE_RECAP", "PRIVATE_ARCHIVE", "PRIVATE_PLOT", "PRIVATE_COMMITMENT"):
        assert value not in text


def test_director_roll_resolution_is_packed_and_frozen(cast_scene):
    appended = (("Roll resolution", "system", "The requested roll succeeded."),)
    messages, detail = context.compose_director_turn(*cast_scene, "Wait for the attempt.",
        actor_ref="characters:mara", appended=appended, model="glm-5.3")
    assert "Winifred_CARD_SECRET" not in str(messages)
    assert messages[-1] == {"role": "system", "content": appended[0][2]}
    assert any(row["label"] == "Roll resolution" for row in detail["sections"])
    assert detail["total_tokens"] == sum(context.count_tokens(m["content"]) for m in messages)
    restored = model_guidance.PreparedMessages.from_snapshot(messages.snapshot(), "unknown")
    assert restored[-1] == messages[-1]
    assert any(m["role"] == "user" and m["content"] == "Wait for the attempt." for m in restored)


@pytest.mark.parametrize("actor_ref", ["characters:mara", "grimoire"])
def test_assigned_reply_format_replaces_the_ensemble_script_contract(cast_scene, actor_ref):
    _, individual = context.compose_turn(*cast_scene, actor_ref=actor_ref)
    _, combined = context.compose_turn(*cast_scene)
    individual_format = next(row["text"] for row in individual["sections"]
                             if row["id"] == "response_format")
    combined_format = next(row["text"] for row in combined["sections"]
                           if row["id"] == "response_format")
    # Ensemble labels are a serialization requirement only in combined mode.
    # Keeping that instruction in an assigned writer's prompt invites it to
    # imitate a whole script despite the one-actor contract appended later.
    assert "**<Name>:**" not in individual_format
    assert "**<Name>:**" in combined_format
