"""Imported characters speak (Wave 4 audit G1): 026 and 037 imports to 027's briefs and calls.

A V1 actor and a Character Card V3 character are imported through the real importers into a real
store, a chat is opened over them, and 027 builds the speaker's brief and runs the speaker's call
through a scripted provider. The voice evidence in the prompt comes from the import alone: no profile
is authored by hand. Every card and V1 library is synthetic. No paid model is called.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace

import pytest
from card_fixtures import V3, card, encode, land, seed_world
from chat_fixtures import chat_library
from generation_fixtures import Harness, say
from portrayal_fixtures import derived, derives, envelope, manifest
from import_fixtures import ARCHIVE, CAMPAIGN_ID, Env
from store_helpers import owner_handle
from v1_fixture import CAMPAIGN, sha, write

from grimoire.cards import character_payload, parse_card
from grimoire.chats import Launch
from grimoire.core import ControlAssignment, InstanceRef, Knowledge, actor_knowledge, snapshot_actor_view
from grimoire.core.authority import DIRECTOR
from grimoire.core.entity_codec import definition_from_payload
from grimoire.core.identity import ChatRef, LegacyId, SourceRef
from grimoire.core.inference import InferencePolicy
from grimoire.effective import chat_snapshot
from grimoire.generation import (
    NO_VOICE_TEXT, SnapshotVoices, VoiceNote, derive_voice, example_lines, npc_brief, plan_generation_turn, resolve_snapshot_voice,
)
from grimoire.import_v1 import chat_id_of, imported_voices, v1_voice_texts
from grimoire.store import LibraryStore
from grimoire.voice import EXAMPLES_HEADER, MissingVoice, SecretFact, VoiceAnchor, VoiceCatalog, VoiceProfile

SERAPHINE_DIR = "worlds/saltmarch/characters/seraphine"
SERAPHINE_CARD = json.dumps({"spec": "chara_card_v3", "spec_version": "3.0", "data": {
    "name": "Seraphine", "description": "A lamplighter of Saltmarch. She speaks in a hush, as if the lamps listen.",
    "personality": "Patient and wry.", "scenario": "Night on the quay.", "first_mes": "The lamps are lit.",
    "mes_example": "<START>\n{{user}}: Who's there in the dark?\n{{char}}: Only the lamplighter, {{user}}. "
                   "Mind the wick.\n<START>\n{{char}}: The tide keeps its own hours, and so do I.",
    "system_prompt": "Narrate in the present tense.", "alternate_greetings": [], "tags": [],
    "extensions": {}}}, indent=2, sort_keys=True) + "\n"
SERAPHINE_ANCHOR = "---\nid: 0f0f\n---\n\n- Short, hushed sentences.\n- Never raises her voice.\n"
SERAPHINE = InstanceRef(CAMPAIGN_ID, "character", "seraphine")
LINE = "Hush now. The lamps are listening."
EXAMPLE = "Only the lamplighter, you. Mind the wick."
CARD_SOURCE = "card:mara-vell"
MARA_VELL = SourceRef(CARD_SOURCE, "character", "mara-vell")


def knowing_nothing(snap, actor: InstanceRef):
    knowledge = actor_knowledge(Knowledge(actor.campaign_id), actor, as_of=1)
    return snapshot_actor_view(snap.instance(actor), snap, knowledge)


def speak(actor: InstanceRef, brief, reply: str = LINE):
    """Run one 027 turn in which ``actor`` alone speaks, through a scripted provider."""
    controls = [ControlAssignment(actor.campaign_id, actor.instance_id, DIRECTOR)]
    harness = Harness([say(reply)])
    plan = plan_generation_turn(actor.campaign_id, "t1", present=(actor.instance_id,), speakers=(actor.instance_id,),
                                controls=controls, policy=InferencePolicy(max_attempts_per_call=1))
    outcome = harness.runtime.run_turn(harness.meter(plan), [brief], controls=controls)
    [prompt] = harness.prompts()
    return outcome, prompt


# -- V1 (026) -----------------------------------------------------------------------------------------

@pytest.fixture
def v1_env(tmp_path):
    """The two-NPC V1 library with Seraphine's card voiced and her record's voice anchor written."""
    env = Env(tmp_path)
    write(env.v1, f"{SERAPHINE_DIR}/default.json", SERAPHINE_CARD)
    write(env.v1, f"{SERAPHINE_DIR}/voice_anchor.md", SERAPHINE_ANCHOR)
    write(env.v1, f"{CAMPAIGN}/characters/seraphine/default.json", SERAPHINE_CARD)  # the campaign's own copy
    appearances = json.loads((env.v1 / CAMPAIGN / "appearances.json").read_text(encoding="utf-8"))
    appearances["characters/seraphine"]["base"] = sha(SERAPHINE_CARD)
    write(env.v1, f"{CAMPAIGN}/appearances.json", json.dumps(appearances, indent=2, sort_keys=True) + "\n")
    env.rescan()
    env.plan().apply()
    yield env
    env.store.close()


def v1_snapshot(env):
    chat = ChatRef(CAMPAIGN_ID, chat_id_of(f"v1:{ARCHIVE}:{CAMPAIGN}/campaign.md", "00000000000000000000000000000001"))
    return chat_snapshot(env.store.load(), chat).snapshot


def test_an_imported_v1_npc_speaks_with_the_examples_of_its_verbatim_card(v1_env):
    snap = v1_snapshot(v1_env)
    voice = imported_voices(snap).resolve(SERAPHINE)
    seraphine = SourceRef("world:saltmarch", "character", "seraphine.default").at(f"v1-{sha(SERAPHINE_CARD)[:16]}")
    assert voice.portrayal == voice.actor_revision == seraphine
    assert set(voice.provenance.values()) == {"source"}
    # the record's voice_anchor.md is not carried (a listed actor_sidecar): no anchor comes from it
    assert [(a.anchor_id, a.aspect, a.text) for a in voice.anchors] == [
        ("personality-1", "register", "Patient and wry."),
        ("description-2", "habit", "She speaks in a hush, as if the lamps listen.")]
    assert [e.text for e in voice.examples] == [EXAMPLE, "The tide keeps its own hours, and so do I."]
    # the card's voice fields are kept exactly for export; the verbatim card stays in the definition
    assert voice.imported.card_version == LegacyId(ARCHIVE, f"{SERAPHINE_DIR}/default.json")
    assert voice.imported.example_messages == json.loads(SERAPHINE_CARD)["data"]["mes_example"]
    assert voice.imported.anchor_text is None

    brief = npc_brief(knowing_nothing(snap, SERAPHINE), voice, secrets=())
    outcome, prompt = speak(SERAPHINE, brief)
    assert outcome.status == "complete"
    [line] = outcome.lines
    assert (line.actor, line.text, line.voice_profile) == (SERAPHINE, LINE, seraphine)
    assert f"> {EXAMPLE}" in prompt and "- register: Patient and wry." in prompt and EXAMPLES_HEADER in prompt
    # the user's line is not her voice, and the card's private instruction never reaches the prompt
    assert "Who's there" not in prompt and "{{" not in prompt and "present tense" not in prompt
    assert "hushed sentences" not in prompt


def test_a_rerun_over_an_imported_library_changes_nothing_and_it_still_speaks(v1_env):
    """The voice is read from the stored revision, never written into it: a re-run of the same manifest
    finds every target ``present`` (no ``v1_changed``, nothing to write), and a library imported before
    voices existed (the stored bytes are the same) speaks without being imported again."""
    stored = v1_env.store.load()
    revision = SourceRef("world:saltmarch", "character", "seraphine.default").at(f"v1-{sha(SERAPHINE_CARD)[:16]}")
    assert set(stored.index.resolve(revision).envelope.payload["v1"]) == {
        "format", "label", "legacy_id", "record", "sha256", "text", "version"}
    review = v1_env.plan().review()
    assert not [row for row in review["unresolved"] if row["reason"] == "v1_changed"] and review["writes"] == []
    assert imported_voices(v1_snapshot(v1_env)).resolve(SERAPHINE).examples[0].text == EXAMPLE


def test_a_v1_portrayal_needs_the_v1_reader_to_speak(v1_env):
    """027 alone cannot parse the verbatim card: without 026's reader a V1 portrayal derives nothing."""
    snap = v1_snapshot(v1_env)
    with pytest.raises(MissingVoice, match="no_profile"):
        SnapshotVoices(snap).resolve(SERAPHINE)


def test_an_imported_v1_npc_with_no_text_at_all_stays_missing_with_a_clear_reason(v1_env):
    """Mara's fixture card is all blank fields: nothing to speak from, so she gets no profile."""
    snap = v1_snapshot(v1_env)
    voices = imported_voices(snap)
    mara = InstanceRef(CAMPAIGN_ID, "character", "mara")
    with pytest.raises(MissingVoice) as missing:
        voices.resolve(mara)
    assert missing.value.reason == NO_VOICE_TEXT
    assert set(voices.gaps.values()) == {NO_VOICE_TEXT}
    with pytest.raises(MissingVoice, match="no_profile"):  # 027's own resolver, over the same catalog
        resolve_snapshot_voice(mara, snap, voices.catalog)


# -- Character Card V3 (037) ---------------------------------------------------------------------------

def card_chat(store: LibraryStore, ref: SourceRef = MARA_VELL):
    started = chat_library(store, owner_handle()).start_chat(Launch("character", ref)).commit()
    return chat_snapshot(store.load(), started.chat).snapshot, started.actors[0]


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed_world(opened)
        yield opened


VOICED = card(V3, mes_example="<START>\n{{user}}: Is the cargo safe?\n{{char}}: Safe as the tide, {{user}}. "
                              "Keep your voice down.\n{{user}}: And the watch?\n{{char}}: *taps the crate* Asleep.\n"
                              "<START>\n{{char}}: We sail when the lamps go out.")


def test_an_imported_card_character_speaks_with_its_example_messages(store):
    land(store, encode(VOICED))
    snap, actor = card_chat(store)
    voice = SnapshotVoices(snap).resolve(actor)
    assert voice.portrayal == MARA_VELL.at("r1")
    assert [e.text for e in voice.examples] == [
        "Safe as the tide, you. Keep your voice down.", "*taps the crate* Asleep.", "We sail when the lamps go out."]
    assert [(a.anchor_id, a.aspect) for a in voice.anchors] == [("personality-1", "register")]
    assert voice.imported.format == "chara_card_v3" and voice.imported.thaw() == {"x-test": {"kept": True}}
    assert voice.imported.example_messages == VOICED["data"]["mes_example"]

    brief = npc_brief(knowing_nothing(snap, actor), voice, secrets=())
    outcome, prompt = speak(actor, brief, "The tide waits for no one, friend.")
    assert outcome.status == "complete" and outcome.lines[0].voice_profile == MARA_VELL.at("r1")
    assert "> We sail when the lamps go out." in prompt and "- register: Wry, patient, loyal to her crew." in prompt
    for private in ("Is the cargo safe", "Write in the third person", "Stay in character", "A synthetic test card"):
        assert private not in prompt


def test_a_card_with_no_example_messages_speaks_from_anchors_alone(store):
    land(store, encode(card(V3, mes_example=None)))
    snap, actor = card_chat(store)
    voice = SnapshotVoices(snap).resolve(actor)
    assert voice.examples == () and [a.text for a in voice.anchors] == ["Wry, patient, loyal to her crew."]
    outcome, prompt = speak(actor, npc_brief(knowing_nothing(snap, actor), voice, secrets=()))
    assert outcome.status == "complete" and "- register: Wry, patient, loyal to her crew." in prompt
    assert EXAMPLES_HEADER not in prompt


def test_a_card_with_only_a_description_speaks_from_its_opening(store):
    land(store, encode(card(V3, mes_example=None, personality=None)))
    snap, actor = card_chat(store)
    voices = SnapshotVoices(snap)
    voice = voices.resolve(actor)
    assert [(a.anchor_id, a.aspect, a.text) for a in voice.anchors] == [
        ("description-0", "register", V3["data"]["description"])]
    assert voices.derived[MARA_VELL.at("r1")].notes == (VoiceNote("description", 0, "description_fallback"),)


def test_a_card_with_nothing_usable_stays_missing_with_a_clear_reason(store):
    land(store, encode(card(V3, mes_example="<START>\n{{user}}: Hello?", personality="  ", description=None)))
    snap, actor = card_chat(store)
    voices = SnapshotVoices(snap)
    with pytest.raises(MissingVoice) as missing:
        voices.resolve(actor)
    assert missing.value.reason == NO_VOICE_TEXT and "r1" in str(missing.value)
    assert dict(voices.gaps) == {MARA_VELL.at("r1"): NO_VOICE_TEXT} and len(voices.catalog) == 0


def test_a_new_revision_gets_its_own_profile_and_never_borrows_the_old_one(store):
    land(store, encode(VOICED))
    first, first_actor = card_chat(store, MARA_VELL.at("r1"))
    changed = card(V3, mes_example="<START>\n{{char}}: Lanterns out. Now.")
    land(store, encode(changed), target=CARD_SOURCE)
    second, second_actor = card_chat(store, MARA_VELL.at("r2"))

    old, new = SnapshotVoices(first), SnapshotVoices(second)
    assert [p.portrayal for p in old.catalog] == [MARA_VELL.at("r1")]
    assert [p.portrayal for p in new.catalog] == [MARA_VELL.at("r2")]
    assert [e.text for e in new.resolve(second_actor).examples] == ["Lanterns out. Now."]
    assert len(old.resolve(first_actor).examples) == 3  # the pinned chat keeps its revision's voice
    with pytest.raises(MissingVoice, match="no_profile"):
        resolve_snapshot_voice(second_actor, second, old.catalog)  # r1's voice is never used for r2


def test_a_world_portrayal_derived_from_an_imported_card_inherits_its_derived_voice(store):
    """The catalog covers the actor's pinned bases too, so 027's derivation walk finds the card's voice."""
    land(store, encode(VOICED))
    modern = SourceRef("world:modern-harbor", "character", "mara", "r1")
    txn = store.transaction()
    txn.put(manifest("world:modern-harbor", derives(modern, MARA_VELL.at("r1"))), None)
    txn.put(envelope(derived(modern, MARA_VELL.at("r1"), name="Mara Vell",
                             sections={"occupation": "Runs a ferry app."})), None)
    txn.commit(owner_handle())
    snap, actor = card_chat(store, modern)
    voice = SnapshotVoices(snap).resolve(actor)
    assert (voice.actor_revision, voice.portrayal) == (modern, MARA_VELL.at("r1"))
    assert voice.examples[-1].text == "We sail when the lamps go out."


def test_an_authored_profile_for_the_same_revision_wins(store):
    land(store, encode(VOICED))
    snap, actor = card_chat(store)
    authored = VoiceProfile(MARA_VELL.at("r1"), (VoiceAnchor("hand", "register", "Hand-written voice."),))
    voices = SnapshotVoices(snap, VoiceCatalog([authored]))
    assert voices.resolve(actor).anchors == authored.anchors and MARA_VELL.at("r1") not in voices.derived


def test_an_imported_example_stating_a_hidden_secret_is_screened_out(store):
    land(store, encode(card(V3, mes_example="<START>\n{{char}}: We run the north channel tonight.\n"
                                            "<START>\n{{char}}: Keep your voice down.")))
    snap, actor = card_chat(store)
    voice = SnapshotVoices(snap).resolve(actor)
    route = SecretFact("route", ("the north channel",))
    brief = npc_brief(knowing_nothing(snap, actor), voice, secrets=(route,))
    assert "north channel" not in brief.system and "> Keep your voice down." in brief.system


# -- caps, truncation and determinism (pure) ------------------------------------------------------------

def definition_of(document: dict, revision: str = "r1"):
    payload, _losses = character_payload(parse_card(document))
    return definition_from_payload(MARA_VELL.at(revision), "grimoire.entity/0", payload)


def test_caps_truncation_and_skips_are_deterministic_and_recorded():
    long_turn = "{{char}}: " + "The tide turns. " * 40  # 640 characters of whole sentences
    turns = ["{{char}}: ...", long_turn, "{{char}}: Line 1.", "{{char}}: Line 1."]
    turns += [f"{{{{char}}}}: Line {n}." for n in range(2, 40)]
    personality = "Patient. " + "word " * 90  # a second sentence of 450 characters
    document = card(V3, mes_example="<START>\n" + "\n".join(turns), personality=personality)
    derived = derive_voice(definition_of(document))
    assert derived == derive_voice(definition_of(document))  # the same revision always derives the same voice
    profile = derived.profile
    assert len(profile.examples) == 32
    assert profile.examples[0].example_id == "example-2" and len(profile.examples[0].text) <= 600
    assert profile.examples[0].text.endswith("The tide turns.")
    assert profile.examples[1].text == "Line 1." and profile.examples[-1].text == "Line 31."
    assert [len(a.text) <= 400 for a in profile.anchors] == [True, True]
    assert VoiceNote("mes_example", 1, "blank") in derived.notes
    assert VoiceNote("mes_example", 2, "truncated") in derived.notes
    assert VoiceNote("mes_example", 4, "duplicate") in derived.notes
    assert [note.index for note in derived.notes if note.reason == "over_cap"] == list(range(35, 43))
    assert VoiceNote("personality", 2, "truncated") in derived.notes


def test_only_imported_character_revisions_derive_a_voice():
    plain = replace(definition_of(V3), extras={})
    assert derive_voice(plain) is None
    v1_style = replace(definition_of(V3), description="", extras={"v1": {"legacy_id": "v1:a:x.json", "text": "{}"}})
    assert derive_voice(v1_style) is None  # only 026's reader can read a V1 card
    assert derive_voice(v1_style, (v1_voice_texts,)).reason == NO_VOICE_TEXT


def test_a_v1_card_nested_too_deep_to_parse_speaks_from_its_description():
    deep = '{"data": {"mes_example": "{{char}}: Hi.", "extensions": ' + "[" * 100000 + "]" * 100000 + "}}"
    v1_style = replace(definition_of(V3), extras={"v1": {"legacy_id": "v1:a:x.json", "text": deep}})
    derived = derive_voice(v1_style, (v1_voice_texts,))
    assert VoiceNote("card", 0, "card_unreadable") in derived.notes
    assert [a.anchor_id for a in derived.profile.anchors] == ["description-0"] and not derived.profile.examples


# -- example lines fail closed --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "<START>\n{{char}}: Hello there.\n{{user}}:I have a secret plan.",
    "<START>\n{{char}}: Hello there.\n{{user}}\uff1aI have a secret plan.",
    "<START>\n{{char}}: Hello there.\n**{{user}}:** My PIN is 1234.",
    "<START>\n{{char}}: Hello there.\n{{user}} (whispering): Kill the king tonight.",
    "<START>\n{{char}}: Hello there.\nCaptain of the Guard: Arrest them all.",
    "<START>\n{{char}}: Hello there.\nAnon: my real line",
    "<START>\n{{char}}: Hello there.\n{{user}} leans in and names the king.",
    "<START>\n{{char}}: Hello there. {{user}}: and the vault code.",
    "<START>\n{{char}}: Hello there.\n<USER>: the vault code.",
    "<START>\n{{char}}: Hello there.\n[{{user}}]: The vault code is 4471.",
    '<START>\n{{char}}: Hello there.\n"{{user}}": The vault code is 4471.',
    "<START>\n{{char}}: Hello there.\n[Anon]: my secret",
    "<START>\n{{char}}: Hello there.\n2B: I am an android.",
    "<START>\n{{char}}: Hello there.\n\U0001f5e1\ufe0f Guard: Drop it.",
    "<START>\n{{char}}: Hello there.\nThe Captain of the Guard: Arrest them all.",
    "<START>\n{{char}}: Hello there.\nUser - my secret plan",
    "<START>\n{{char}}: Hello there.\nAnon \u2014 my secret",
    "<START>\n{{char}}: Hello there.\n[Anon] - my secret",
    "<START>\n{{char}}: Hello there.\n> {{user}}: quoted secret",
    "<START>\n{{char}}: Hello there.\nshe whispers to {{user}} the secret",
    "<START>\n{{char}}: Hello there. [{{user}}]: and the vault code.",
    '<START>\n{{char}}: Hello there. "{{user}}" (aside): and the vault code.',
    "<START>\n{{char}}: Hello there.\nAnon\ua789 secret",
    "<START>\n{{char}}: Hello there.\nAnon\u02f8 secret",
    "<START>\n{{char}}: Hello there.\nAnon\ufe13 secret",
    "<START>\n{{char}}: Hello there.\nAnon\ufe55 secret",
    "<START>\n{{char}}: Hello there.\nAnon\u2236 secret",
    "<START>\n{{char}}: Hello there.\nAnon | secret",
    "<START>\n{{char}}: Hello there.\nAnon > secret",
    "<START>\n{{char}}: Hello there.\n\u3010Anon\u3011 secret",
    "<START>\n{{char}}: Hello there.\n\u300cAnon\u300d secret",
    "<START>\n{{char}}: Hello there.\n\uff3bAnon\uff3d secret",
    "<START>\n{{char}}: Hello there.\n[Anon] secret",
    "<START>\n{{char}}: Hello there.\n{{User}}: secret",
    "<START>\n{{char}}: Hello there.\n" + "z " * 40 + "<user> secret",
])
def test_another_speakers_line_never_joins_the_characters_turn(text):
    assert example_lines(text, "Seraphine") == ["Hello there."]


@pytest.mark.parametrize("text, expected", [
    ("<START>\n{{user}}: Hm?\n{{char}}:answer without a space", ["answer without a space"]),
    ("<START>\n**{{char}}:** Bold label.", ["Bold label."]),
    ("<START>\n{{char}} (quietly): Go.\n*She smiles.*", ["Go. *She smiles.*"]),
    ('<START>\n[{{char}}]: Bracketed.\n"Seraphine": Quoted.', ["Bracketed.", "Quoted."]),
    ("<START>\n{{char}}: A long thought runs on, past the first sixty-four characters: still mine.",
     ["A long thought runs on, past the first sixty-four characters: still mine."]),
    ("<START>\n**Seraphine:** By name.\n{{char}}\uff1aFull width.", ["By name.", "Full width."]),
    # a line of her own shaped like a label is dropped too: the fail-closed price
    ("<START>\n{{char}}: Listen.\nRemember: the tide waits.", ["Listen."]),
    ("<START>\n{{char}}: Go.\n*{{char}} smiles.*", ["Go."]),
    ("<START>\n{{char}}: Hello.\nThe tide waits for no one.", ["Hello. The tide waits for no one."]),
    ("<START>\n\u3010{{char}}\u3011\uff1a\u9759\u304b\u306b\u3002", ["\u9759\u304b\u306b\u3002"]),
    # the inherent limit: an unlabelled line with no separator cannot be told from her own
    ("<START>\n{{char}}: Hello.\nI tell you the code", ["Hello. I tell you the code"]),
])
def test_the_characters_own_turns_are_kept_with_their_labels_removed(text, expected):
    assert example_lines(text, "Seraphine") == expected


@pytest.mark.parametrize("text", [
    "<START>\n" + " " * 65536 + "x",
    "<START>\n{{char}}: hi\n<a>" + " " * 65536 + "x",
    "<START>\n{{char}}: hi {{user}}" + " " * 65536 + ": x",
    "<START>\n" + "*" * 65536 + "{{char}}" + " " * 65536 + ":",
])
def test_a_huge_line_costs_no_more_than_a_short_one(text):
    started = time.perf_counter()
    lines = example_lines(text, "S")
    assert time.perf_counter() - started < 0.5
    assert all(len(line) <= 2 * 600 for line in lines)
    long = definition_of(card(V3, mes_example=text, personality=" " * 65536 + "Wry.",
                              description="A " + " " * 65536 + "voice."))
    started = time.perf_counter()
    derive_voice(long)
    assert time.perf_counter() - started < 0.5


# -- derived portrayals over a derived card voice -------------------------------------------------------

def derived_chat(store, **layer):
    land(store, encode(VOICED))
    modern = SourceRef("world:modern-harbor", "character", "mara", "r1")
    txn = store.transaction()
    txn.put(manifest("world:modern-harbor", derives(modern, MARA_VELL.at("r1"))), None)
    txn.put(envelope(derived(modern, MARA_VELL.at("r1"), name="Mara Vell", **layer)), None)
    txn.commit(owner_handle())
    snap, actor = card_chat(store, modern)
    return modern, SnapshotVoices(snap).resolve(actor)


def test_a_layer_that_clears_the_personality_never_brings_it_back(store):
    modern, voice = derived_chat(store, clear_sections=["personality"])
    assert voice.portrayal == modern  # its own voice, from its effective text
    assert not any("Wry" in anchor.text for anchor in voice.anchors)
    assert voice.examples[-1].text == "We sail when the lamps go out."  # the examples are not a layer's to clear


def test_a_layer_that_replaces_the_personality_speaks_with_its_own(store):
    modern, voice = derived_chat(store, sections={"personality": "Cold and curt."})
    assert voice.portrayal == modern
    assert [(a.anchor_id, a.text) for a in voice.anchors] == [("personality-1", "Cold and curt.")]


def test_a_layer_that_replaces_the_description_derives_from_its_own(store):
    modern, voice = derived_chat(store, description="A ferry pilot. She talks in clipped radio code.")
    assert voice.portrayal == modern
    assert ("description-2", "She talks in clipped radio code.") in [(a.anchor_id, a.text) for a in voice.anchors]
