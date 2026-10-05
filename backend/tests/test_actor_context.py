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
    lore_fields,
    overlay,
    pins,
    playstate,
    relationships,
    scenes,
    worlds,
)
from grimoire.store.appearances import paths
from grimoire.store.context import actor as actor_context
from grimoire.store.context import semantic


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


def test_assigned_npc_can_answer_a_birthday_question(cast_scene):
    cid, sid = cast_scene
    characters.set_birthdate(campaigns.campaign_root(cid), "mara", "--05-09")
    scenes.append_message(cid, sid, "user", "When is Mara's birthday?")

    messages, _ = context.compose_turn(cid, sid, actor_ref="characters:mara")
    assert "May 9" in str(messages)


def test_assigned_npc_birthdate_lookup_obeys_exclusion(cast_scene):
    cid, sid = cast_scene
    characters.set_birthdate(campaigns.campaign_root(cid), "winifred", "--05-09")
    pins.set_rule(cid, "characters:winifred", pins.EXCLUDE, sid=sid)
    scenes.append_message(cid, sid, "user", "When is Winifred's birthday?")

    _messages, detail = context.compose_turn(cid, sid, actor_ref="characters:mara")
    assert not any(s["id"] == "birthdates" and "May 9" in s["text"]
                   for s in detail["sections"])


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
    text = str(messages)
    assert "Each established NPC owns their speech, actions, physical reactions and decisions" in text
    assert "genuinely new characters" in text
    assert "Do not write any NPC dialogue" not in text
    assert "Do not write additional characters" not in text


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
    monkeypatch.setattr(assembly.effective, "render_threads", lambda *args, **kwargs: ["PRIVATE_PLOT"])
    monkeypatch.setattr(assembly.effective, "render_commitments", lambda *args, **kwargs: ["PRIVATE_COMMITMENT"])
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
def test_reply_format_never_asks_for_script_labels(cast_scene, actor_ref):
    _, individual = context.compose_turn(*cast_scene, actor_ref=actor_ref)
    _, combined = context.compose_turn(*cast_scene)
    individual_format = next(row["text"] for row in individual["sections"]
                             if row["id"] == "response_format")
    combined_format = next(row["text"] for row in combined["sections"]
                           if row["id"] == "response_format")
    # Assigned calls never ask the model to provide a label. A stored roll
    # proposal from before actor-scoped rounds still has no assigned speaker,
    # so its compatibility continuation needs the parser's marker grammar.
    assert "**<Name>:**" not in individual_format
    assert "**Full Name:**" in combined_format


@pytest.mark.parametrize("actor_ref", ["characters:mara", "grimoire"])
def test_response_budget_never_requests_ensemble_blocks(cast_scene, actor_ref):
    _, individual = context.compose_turn(*cast_scene, actor_ref=actor_ref)
    _, combined = context.compose_turn(*cast_scene)
    budget = next(row["text"] for row in individual["sections"] if row["id"] == "response_budget")
    ensemble = next(row["text"] for row in combined["sections"] if row["id"] == "response_budget")
    assert "**Grimoire:**" not in budget
    assert "characters act or speak" not in budget
    assert "**Grimoire:**" not in ensemble


def test_actor_prompt_uses_continuation_ceilings(cast_scene):
    _, detail = context.compose_turn(*cast_scene, actor_ref="characters:mara")
    budget = next(row["text"] for row in detail["sections"] if row["id"] == "response_budget")
    assert "at most 150 words" in budget
    assert "at most 2 paragraphs" in budget
    assert "There is no minimum length" in budget
    assert "blocks_per_speaker" not in budget
    assert "preserve required control blocks" in budget



def test_structured_perception_is_assigned_npc_only(cast_scene):
    for actor in ("characters:mara", "grimoire"):
        messages, _ = context.compose_turn(*cast_scene, actor_ref=actor)
        text = " ".join(" ".join(m["content"] for m in messages).split())
        assert ("Before the roleplay prose, emit one short fenced perception block" in text) is (actor != "grimoire")
        if actor != "grimoire":
            assert "Keep continuity notes as facts for the writer" in text
            assert "does not count toward the prose word budget" in text



def test_perception_requires_source_and_access_evidence(cast_scene):
    messages, _ = context.compose_turn(*cast_scene, actor_ref="characters:mara")
    text = " ".join(" ".join(m["content"] for m in messages).split())
    assert "First find the source evidence" in text
    assert '"kind": "speech or action", "source": "exact excerpt", "access": "why this actor heard or saw it"' in text
    assert "Copying narration into a quote does not make it speech" in text
    assert "Do not invent bedroom sightlines" in text
    assert "keep independently sourced prior knowledge in known" in text
    assert "at most 80 words" in text


def test_perception_rider_switch(cast_scene):
    cid, sid = cast_scene
    on = str(context.compose_turn(cid, sid, actor_ref="characters:mara")[0])
    assert "```perception" in on
    # The tracker is off in this suite, so there is no Scene state section for
    # the rider to point at (`test_tracker_context` has the other half).
    assert "rely on the Scene state section" not in on
    config.write_config(perception_rider="off")
    off = str(context.compose_turn(cid, sid, actor_ref="characters:mara")[0])
    assert "```perception" not in off
    assert "rely on the Scene state section" not in off
    # The rest of the assigned-speaker contract is untouched by the switch.
    assert "# What this actor can perceive" in off and "```handoff" in off


# ---- what a per-character call may see (spec §8) ------------------------------

def _actor_text(cid, sid, actor_ref=None):
    return str(context.compose_turn(cid, sid, actor_ref=actor_ref)[0])


def test_known_by_reaches_named_actor_and_narrator_only(cast_scene):
    # A secret about Mara that only Winifred knows: it activates because Mara is
    # in the room -- on Winifred's call too, where the narrowed cast used to
    # leave Mara out of `present` -- and then only the narrator and Winifred see it.
    cid, sid = cast_scene
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Debt", "MARA_OWES_THE_TIDE",
                           owners="characters:mara", secrecy="secret",
                           fields={"known_by": "characters:winifred"})
    scenes.append_message(cid, sid, "user", "Calm.")
    assert "MARA_OWES_THE_TIDE" in _actor_text(cid, sid)
    assert "MARA_OWES_THE_TIDE" in _actor_text(cid, sid, "grimoire")
    assert "MARA_OWES_THE_TIDE" in _actor_text(cid, sid, "characters:winifred")
    assert "MARA_OWES_THE_TIDE" not in _actor_text(cid, sid, "characters:mara")


def test_location_owned_public_lore_reaches_npc_there(cast_scene):
    cid, sid = cast_scene
    croot = campaigns.campaign_root(cid)
    loc = entities.create_entity(croot, "locations", "Saltmarch Quay", "Grey water.")
    scenes.set_location(cid, sid, loc)
    entities.create_entity(croot, "lore", "Quay Bell", "THE_QUAY_BELL_RINGS_AT_DUSK",
                           owners=f"locations:{loc}")
    scenes.append_message(cid, sid, "user", "Calm.")
    for actor_ref in (None, "characters:mara", "characters:winifred"):
        assert "THE_QUAY_BELL_RINGS_AT_DUSK" in _actor_text(cid, sid, actor_ref), actor_ref


def test_character_owned_lore_still_private_to_owner(cast_scene):
    # Public and owned by Mara, who is present on Winifred's call now -- so it
    # activates there, and the knowledge rule is what keeps it out.
    cid, sid = cast_scene
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Habit", "MARA_HUMS_WHEN_LYING",
                           owners="characters:mara")
    scenes.append_message(cid, sid, "user", "Calm.")
    assert "MARA_HUMS_WHEN_LYING" in _actor_text(cid, sid)
    assert "MARA_HUMS_WHEN_LYING" in _actor_text(cid, sid, "characters:mara")
    assert "MARA_HUMS_WHEN_LYING" not in _actor_text(cid, sid, "characters:winifred")


def test_known_by_deleted_actor_reaches_nobody_and_does_not_raise(cast_scene):
    # Seraphine is created and deleted, so `known_by` names an actor that WAS
    # real and is gone. It matches no call; the narrator, who is not filtered
    # by knowledge, still has what activated.
    cid, sid = cast_scene
    wroot = worlds.world_root(campaigns.read_campaign(cid)["meta"]["world"])
    characters.create_character(wroot, "Seraphine", "main", characters.blank_card("Seraphine"))
    characters.delete_character(wroot, "seraphine")
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Ledger", "THE_LEDGER_IS_FORGED",
                           owners="characters:mara", secrecy="secret",
                           fields={"known_by": "characters:seraphine"})
    scenes.append_message(cid, sid, "user", "Calm.")
    assert "THE_LEDGER_IS_FORGED" in _actor_text(cid, sid)
    for actor_ref in ("characters:mara", "characters:winifred"):
        assert "THE_LEDGER_IS_FORGED" not in _actor_text(cid, sid, actor_ref), actor_ref


def test_item_held_by_present_npc_unlocks_owned_lore(cast_scene):
    # Winifred holds the lantern. On Mara's call Winifred is still in the
    # present set, so the lantern is present, its public lore activates, and an
    # object owner shares it with everyone in the room (§8.2).
    cid, sid = cast_scene
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "items", "Lantern", "A brass lantern.", keys="unsaid",
                           fields={"holder": "characters:winifred"})
    entities.create_entity(croot, "lore", "Oil", "THE_LANTERN_BURNS_WHALE_OIL",
                           owners="items:lantern")
    scenes.append_message(cid, sid, "user", "Calm.")
    for actor_ref in (None, "characters:mara", "characters:winifred"):
        assert "THE_LANTERN_BURNS_WHALE_OIL" in _actor_text(cid, sid, actor_ref), actor_ref


def test_secret_current_setting_known_by_reaches_that_npc(cast_scene):
    cid, sid = cast_scene
    croot = campaigns.campaign_root(cid)
    loc = entities.create_entity(croot, "locations", "Saltmarch Vault", "VAULT_BEHIND_THE_CHAPEL",
                                 secrecy="secret", fields={"known_by": "characters:winifred"})
    scenes.set_location(cid, sid, loc)
    scenes.append_message(cid, sid, "user", "Calm.")

    def setting(actor_ref):
        sections = context.compose_turn(cid, sid, actor_ref=actor_ref)[1]["sections"]
        return [s for s in sections if s["id"] == "current_setting" and s["text"].strip()]

    assert setting("characters:winifred")
    assert "VAULT_BEHIND_THE_CHAPEL" in _actor_text(cid, sid, "characters:winifred")
    assert not setting("characters:mara")
    assert "VAULT_BEHIND_THE_CHAPEL" not in _actor_text(cid, sid, "characters:mara")


# ---- an NPC's activation sees only what it knows ------------------------------

def test_unknown_lore_does_not_pull_by_recursion_on_another_npcs_call(cast_scene):
    # Mara's secret names the tidebell. At depth 1 it would pull the tidebell
    # entry -- on Mara's call, and the narrator's, but never on Winifred's,
    # who does not know the secret that mentions it.
    cid, sid = cast_scene
    croot = campaigns.campaign_root(cid)
    config.write_config(lore_recursion_depth="1")
    entities.create_entity(croot, "lore", "Mara's Errand", "Mara rings the tidebell at night.",
                           owners="characters:mara", secrecy="secret")
    entities.create_entity(croot, "lore", "Tidebell", "TIDEBELL_CRACKED_IN_THE_FLOOD",
                           keys="tidebell")
    scenes.append_message(cid, sid, "user", "Calm.")
    assert "TIDEBELL_CRACKED_IN_THE_FLOOD" in _actor_text(cid, sid)
    assert "TIDEBELL_CRACKED_IN_THE_FLOOD" in _actor_text(cid, sid, "characters:mara")
    assert "TIDEBELL_CRACKED_IN_THE_FLOOD" not in _actor_text(cid, sid, "characters:winifred")


def test_unknown_item_confers_no_presence_on_another_npcs_call(cast_scene):
    # A secret charm only Mara knows of. Activating, it is present (§7.2) and
    # unlocks the public lore it owns -- for the calls that know the charm.
    cid, sid = cast_scene
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "items", "Charm", "A bone charm.",
                           owners="characters:mara", secrecy="secret")
    entities.create_entity(croot, "lore", "Charm Ward", "THE_CHARM_WARDS_OFF_GULLS",
                           owners="items:charm")
    scenes.append_message(cid, sid, "user", "Calm.")
    assert "THE_CHARM_WARDS_OFF_GULLS" in _actor_text(cid, sid)
    assert "THE_CHARM_WARDS_OFF_GULLS" in _actor_text(cid, sid, "characters:mara")
    assert "THE_CHARM_WARDS_OFF_GULLS" not in _actor_text(cid, sid, "characters:winifred")


def test_unknown_lore_never_takes_a_recall_slot_on_another_npcs_call(cast_scene, monkeypatch):
    # Recall with depth 1, and Mara's private lore scores best. On Winifred's
    # call it is not a candidate at all, so the slot goes to what she knows.
    cid, sid = cast_scene
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "Mara Ledger", "MARA_KEEPS_A_SECOND_LEDGER",
                           keys="unsaid-ledger", owners="characters:mara")
    entities.create_entity(croot, "lore", "Harbour Toll", "THE_HARBOUR_TOLL_DOUBLED",
                           keys="unsaid-toll")
    scores = {"mara-ledger": 0.9, "harbour-toll": 0.5}

    def top_one(candidates, _text):
        ranked = sorted(candidates, key=lambda e: -scores.get(e["id"], 0.0))
        return [(e, scores.get(e["id"], 0.0)) for e in ranked[:1]]

    monkeypatch.setattr(semantic, "recall_scored", top_one)
    scenes.append_message(cid, sid, "user", "Calm.")
    mara = _actor_text(cid, sid, "characters:mara")
    assert "MARA_KEEPS_A_SECOND_LEDGER" in mara
    winifred = _actor_text(cid, sid, "characters:winifred")
    assert "MARA_KEEPS_A_SECOND_LEDGER" not in winifred
    assert "THE_HARBOUR_TOLL_DOUBLED" in winifred


def test_actor_call_activation_result_holds_only_known_entries(cast_scene):
    cid, sid = cast_scene
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Habit", "MARA_HUMS_WHEN_LYING",
                           owners="characters:mara")
    scenes.append_message(cid, sid, "user", "Calm.")
    refs = {h.ref for h in context.assemble._assemble(
        cid, sid, actor_ref="characters:winifred")["wi_result"].keyword}
    assert "lore:habit" not in refs
    refs = {h.ref for h in context.assemble._assemble(
        cid, sid, actor_ref="characters:mara")["wi_result"].keyword}
    assert "lore:habit" in refs


@pytest.mark.parametrize("entry, expected", [
    ({"secrecy": "public"}, True),
    ({"secrecy": "secret"}, False),
    ({"secrecy": "gm-only", "owners": ["characters:mara"]}, False),
    ({"secrecy": "gm-only", "known_by": "characters:mara"}, False),
    ({"secrecy": "secret", "owners": ["characters:mara"]}, True),
    ({"secrecy": "public", "owners": ["characters:winifred"]}, False),
    ({"secrecy": "public", "owners": ["locations:quay", "items:lantern"]}, True),
    ({"secrecy": "public", "owners": ["groups:union", "creatures:gull"]}, True),
    ({"secrecy": "secret", "owners": ["locations:quay"]}, False),
    # Mixed: one owner is an actor, so the entry stays that actor's.
    ({"secrecy": "public", "owners": ["locations:quay", "characters:winifred"]}, False),
    ({"secrecy": "public", "owners": ["lore:other"]}, False),
    # `known_by` replaces the owner rule outright, whatever the secrecy.
    ({"secrecy": "secret", "owners": ["characters:winifred"], "known_by": "characters:mara"}, True),
    ({"secrecy": "public", "owners": ["characters:mara"], "known_by": "characters:winifred"}, False),
    ({"secrecy": "public", "known_by": "pcs:mara, characters:winifred"}, False),
    # A known_by of nothing parseable is unset.
    ({"secrecy": "public", "known_by": "locations:quay"}, True),
])
def test_knows_rule(entry, expected):
    assert actor_context.knows(entry, "characters:mara") is expected


def test_knows_reads_parsed_controls_first():
    entry = {"secrecy": "secret", "owners": ["characters:winifred"], "known_by": "",
             "controls": lore_fields.parse({"known_by": "characters:mara"})}
    assert actor_context.knows(entry, "characters:mara") is True
    assert actor_context.known_entries([entry], "characters:winifred") == []
