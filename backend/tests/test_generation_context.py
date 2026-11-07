"""The 027/031 seam: briefs rendered from 031's packs, for an NPC and for the scene-level director.

The Saltmarch cast (``context_fixtures``) shares one world-less chat with a lorebook and a scenario.
Mara's brief takes her own pack; the director's takes lore triggered by table-visible text. A pack
built over another selection (the lorebook detached, or re-pinned to a revision that makes an
entry director-only) is refused. Every fixture is synthetic, and the one model call is a scripted
fake.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from generation_fixtures import DIRECTOR_POLICY, Harness, say
from voice_fixtures import CAMPAIGN, PC

import context_fixtures as f
from context_fixtures import LORE, LORE_SCHEMA, LoreRecord, actor, card, entry, instance, knowledge, snapshot
from grimoire.context import Budget, ContextPack, director_lore, pack_actor_context, pack_player_context
from grimoire.core import (
    ControlAssignment, DefinitionIndex, SourceDescriptor, actor_knowledge, snapshot_actor_view,
)
from grimoire.core.authority import DIRECTOR
from grimoire.core.entity import EntityInstance
from grimoire.core.facets import Agent, FacetBundle, Physical
from grimoire.core.identity import InstanceRef
from grimoire.core.perspective import ActorKnowledge
from grimoire.core.snapshot import EffectiveSnapshot, SelectedSource, SourceSelection
from grimoire.generation import (
    GenerationError, HeardLine, director_brief, director_messages, npc_brief, npc_messages, resolve_snapshot_voice,
    screen_reply,
)
from grimoire.voice import SecretFact, VoiceAnchor, VoiceBinding, VoiceCatalog, VoiceExample, VoiceProfile

#: The table's visible transcript and what Mara perceived of it: the ferry and the lighthouse.
SCENE = "The ferry is late, and the lighthouse is dark again."
#: Text no NPC but the duke may see, and text nobody may.
HIDDEN = ("SECRET-TOKEN", "PLOT-TOKEN", "DIRECTOR-TOKEN", "UNCLASSIFIED-TOKEN", "SCENARIO-TOKEN",
          "UNCONFIRMED-TOKEN", "BESTIARY-TOKEN")
#: Director-side markers. ``crossing`` is in common lore Mara's pack gives her; she learned it from
#: no record, so only the pack can expose it.
SECRETS = (SecretFact("plot", ("PLOT-TOKEN",)), SecretFact("debt", ("DIRECTOR-TOKEN",)),
           SecretFact("crossing", ("crosses the marsh at dusk",)))
VOICES = VoiceCatalog([VoiceProfile(card(n), (VoiceAnchor("cadence", "cadence", f"{n.title()} speaks plainly."),),
                                    (VoiceExample("e1", f"Mind the quay, {n} says."),)) for n in f.NPCS])
#: The lorebook's next revision: the ferry entry is the director's now.
LORE_R2 = LORE.at("r2")
#: A campaign original, with no card: its pack's only selected inputs are the lore bindings.
PELL = InstanceRef(f.C, "character", "pell-1")
PELL_INSTANCE = EntityInstance(PELL, local_name="Pell", added=FacetBundle((Physical(), Agent(), f.VOICE)))


def view(who: str, snap, *, as_of: int = 9):
    return snapshot_actor_view(instance(who), snap, actor_knowledge(knowledge(), actor(who), as_of=as_of))


def pack(who: str, snap, *, as_of: int = 9, budget=Budget(2000), **kwargs) -> ContextPack:
    options = {"scene_text": SCENE, "readers": f.READERS, "unpack": f.unpack, **kwargs}
    return pack_actor_context(snap, knowledge(), actor(who), as_of=as_of, budget=budget, **options)


def mara(context=None, *, snap=None, **kwargs):
    snap = snap or snapshot()
    options = {"context": context, "snapshot": snap, **kwargs}
    return npc_brief(view("mara", snap), resolve_snapshot_voice(actor("mara"), snap, VOICES), secrets=SECRETS,
                     **options)


def repinned(*, lore: bool = True, cast=f.EVERYONE) -> EffectiveSnapshot:
    """The chat after the lorebook is re-pinned to r2, where the ferry is director-only; Pell is captured."""
    reclassified = [{**e, "audience": "director"} if e["id"] == "ferry" else e for e in f.LORE_ENTRIES]
    definitions = [f.definition(n) for n in f.EVERYONE] + [
        LoreRecord(LORE, "Saltmarch lore", LORE_SCHEMA, {"entries": f.LORE_ENTRIES}),
        LoreRecord(LORE_R2, "Saltmarch lore", LORE_SCHEMA, {"entries": reclassified}),
        LoreRecord(f.SCENARIO, "Flood night", LORE_SCHEMA, {"entries": f.SCENARIO_ENTRIES})]
    index = DefinitionIndex([SourceDescriptor(f.CAST, owner="human:owner")], definitions, heads={LORE.unpinned(): "r2"})
    sources = [SelectedSource(card(n), "character", actor=actor(n)) for n in cast]
    if lore:
        sources += [SelectedSource(LORE_R2, "lorebook"), SelectedSource(f.SCENARIO, "scenario")]
    return EffectiveSnapshot(index, SourceSelection(None, tuple(sources)), instances=with_pell(cast))


def with_pell(cast=f.EVERYONE):
    return [(instance(n), None) for n in cast] + [(PELL_INSTANCE, None)]


def first_chat(*, lore: bool = True) -> EffectiveSnapshot:
    """The chat as first started (lorebook r1), with the campaign original Pell captured too."""
    return EffectiveSnapshot(f.index(), f.selection(lore=lore), instances=with_pell())


def pell(context, snap):
    binding = VoiceBinding(f.C, PELL.instance_id, anchors=(VoiceAnchor("cadence", "cadence", "Pell drawls."),))
    known = actor_knowledge(knowledge(), PELL, as_of=9)
    return npc_brief(snapshot_actor_view(snap.instance(PELL), snap, known),
                     resolve_snapshot_voice(PELL, snap, VOICES, binding), secrets=SECRETS, context=context,
                     snapshot=snap)


def refused(call) -> str:
    with pytest.raises(GenerationError) as caught:
        call()
    return caught.value.reason


# -- an NPC brief from its own pack ---------------------------------------------------------------

def test_an_npc_brief_from_its_pack_carries_common_lore_and_its_own_knowledge_only():
    snap = snapshot()
    brief = mara(pack("mara", snap, exclude_sections=()), snap=snap)
    text = "\n".join(content for _role, content in npc_messages(brief))
    assert "What anyone here may know:\n- Tides here turn twice a day.\n- The ferry crosses the marsh at dusk." in text
    assert "You suspect: the duke paid off the harbor watch" in text and "occupation: Mara works" in text
    assert not any(token in text for token in HIDDEN), text
    assert "the ferry pulling out after dark" not in text  # Anya's perception, not Mara's
    assert "speaks in short sentences" not in text  # the card's voice section, even when the pack kept it
    assert "Mara speaks plainly." in text  # the voice reaches the prompt only through the fragment


def test_the_pack_replaces_the_briefs_own_card_and_knowledge_within_its_budget():
    snap = snapshot()
    small = Budget(120, {"lore": 20})
    built = pack("mara", snap, budget=small)
    brief = mara(built, snap=snap)
    assert [i.origin.name for i in built.lane("lore")] == ["tides"] and "ferry crosses" not in brief.system
    assert all(line in brief.system for line in built.lines()[1:])  # all but the name line, which 027 words
    assert "You are Mara." not in brief.system and "oldest first" not in brief.system


def test_a_pack_built_for_anyone_or_anything_else_is_refused():
    snap = snapshot()
    own = pack("mara", snap)
    assert refused(lambda: mara(pack("anya", snap), snap=snap)) == "foreign_context"
    assert refused(lambda: mara(director_lore(snap, campaign_id=f.C, table_text=SCENE, budget=Budget(400),
                                              readers=f.READERS, unpack=f.unpack), snap=snap)) == "foreign_context"
    assert refused(lambda: mara(pack("mara", snap, as_of=8), snap=snap)) == "context_mismatch"
    secret_lore = replace(own.lane("lore")[0], audience="director")
    assert refused(lambda: mara(replace(own, items=(*own.items, secret_lore)), snap=snap)) == "foreign_context"
    assert refused(lambda: mara(replace(own, items=own.items[1:]), snap=snap)) == "bad_context"
    assert refused(lambda: mara(own.render(), snap=snap)) == "bad_context"
    # a pack packed for a player character's controller is that player's view, never an NPC's brief
    played = pack_player_context(snap, knowledge(), actor("mara"), controller="human:ana",
                                 controls=[ControlAssignment(f.C, actor("mara").instance_id, "human:ana")], as_of=9,
                                 budget=Budget(2000), scene_text=SCENE, readers=f.READERS, unpack=f.unpack)
    assert refused(lambda: mara(played, snap=snap)) == "foreign_context"
    assert refused(lambda: mara(replace(own, controller="human:ana"), snap=snap)) == "foreign_context"
    assert refused(lambda: mara(own, snap=snap, sections=[("age", "Forty.")])) == "bad_sections"
    assert refused(lambda: mara(own, snap=snap, snapshot=None)) == "bad_context"  # no selection to check against
    # The view must be the given snapshot's: here Mara is not even captured in it.
    assert refused(lambda: mara(own, snap=snap, snapshot=snapshot(cast=()))) == "context_mismatch"


def test_a_view_from_another_snapshot_than_the_one_given_is_refused():
    renamed = EntityInstance(actor("mara"), card("mara"), local_name="Captain Mara")
    others = [(instance(n), None) for n in f.EVERYONE if n != "mara"]
    elsewhere = EffectiveSnapshot(f.index(), f.selection(), instances=[(renamed, None), *others])
    snap = snapshot()
    # The bindings are the same, so only the view tells that the brief is not for ``snap``.
    captain = snapshot_actor_view(renamed, elsewhere, actor_knowledge(knowledge(), actor("mara"), as_of=9))
    assert captain.name == "Captain Mara"
    assert refused(lambda: npc_brief(captain, resolve_snapshot_voice(actor("mara"), elsewhere, VOICES),
                                     secrets=SECRETS, context=pack("mara", snap), snapshot=snap)) == "context_mismatch"


def test_a_pack_from_before_the_lorebook_was_detached_is_refused():
    before, after = snapshot(), snapshot(lore=False)
    stale = pack("mara", before)
    assert refused(lambda: mara(stale, snap=after)) == "context_mismatch"
    fresh = mara(pack("mara", after), snap=after)
    assert "ferry crosses the marsh" not in fresh.system
    assert screen_reply(fresh, "The ferry crosses the marsh at dusk.") == "unlearned_secret"


def test_a_pack_from_before_a_repin_that_made_an_entry_director_only_is_refused():
    before, after = snapshot(), repinned()
    stale = pack("mara", before)
    assert [i.origin.name for i in stale.lane("lore")] == ["tides", "ferry", "lighthouse"]
    assert refused(lambda: mara(stale, snap=after)) == "context_mismatch"
    fresh = pack("mara", after)
    assert [i.origin.name for i in fresh.lane("lore")] == ["tides", "lighthouse"]
    brief = mara(fresh, snap=after)
    assert "ferry crosses the marsh" not in brief.system
    assert screen_reply(brief, "The ferry crosses the marsh at dusk.") == "unlearned_secret"


def edited_in_place(*, entries=None, mara_card=None) -> EffectiveSnapshot:
    """The first chat over an index rebuilt after hand edits: the same references, maybe new content."""
    definitions = [mara_card if n == "mara" and mara_card is not None else f.definition(n) for n in f.EVERYONE]
    definitions += [LoreRecord(LORE, "Saltmarch lore", LORE_SCHEMA, {"entries": entries or f.LORE_ENTRIES}),
                    LoreRecord(f.SCENARIO, "Flood night", LORE_SCHEMA, {"entries": f.SCENARIO_ENTRIES})]
    index = DefinitionIndex([SourceDescriptor(f.CAST, owner="human:owner")], definitions)
    return EffectiveSnapshot(index, f.selection(), instances=[(instance(n), None) for n in f.EVERYONE])


def test_a_pack_from_before_a_same_reference_edit_is_refused_even_without_stored_etags():
    stale = pack("mara", snapshot())
    assert "Mara works the" in mara(stale, snap=edited_in_place()).system  # equal content: still accepted
    reclassified = [{**e, "audience": "director"} if e["id"] == "ferry" else e for e in f.LORE_ENTRIES]
    smuggler = replace(f.definition("mara"), description="A Saltmarch smuggler called Mara.")
    for after in (edited_in_place(entries=reclassified), edited_in_place(mara_card=smuggler)):
        assert refused(lambda: mara(stale, snap=after)) == "context_mismatch"
    current = lore(SCENE)
    assert "- The ferry crosses" in narration(current, snap=edited_in_place()).system
    assert refused(lambda: narration(current, snap=edited_in_place(entries=reclassified))) == "context_mismatch"


def test_a_pack_from_a_projection_that_has_since_lost_records_is_refused():
    snap = snapshot()
    stale = pack("mara", snap)
    assert "You suspect: the duke paid off the harbor watch" in stale.lines()
    emptied = ActorKnowledge(actor("mara"), 9, (), (), (), ())  # the same moment, every record quarantined
    voice = resolve_snapshot_voice(actor("mara"), snap, VOICES)
    assert refused(lambda: npc_brief(snapshot_actor_view(instance("mara"), snap, emptied), voice, secrets=SECRETS,
                                     context=stale, snapshot=snap)) == "context_mismatch"


def test_a_campaign_originals_stale_pack_is_refused_though_it_has_no_card():
    before = first_chat()
    stale = pack_actor_context(before, knowledge(), PELL, as_of=9, budget=Budget(2000), scene_text=SCENE,
                               readers=f.READERS, unpack=f.unpack)
    assert stale.items[0].origin.source is None and "ferry crosses" in stale.render()
    assert "The ferry crosses the marsh at dusk." in pell(stale, before).system
    for after in (first_chat(lore=False), repinned()):
        assert refused(lambda: pell(stale, after)) == "context_mismatch"


def test_screening_treats_what_the_pack_gave_the_actor_as_exposed():
    snap = snapshot()
    with_pack, without = mara(pack("mara", snap), snap=snap), mara(snap=snap)
    quoted = "Aye, the ferry crosses the marsh at dusk."
    assert screen_reply(without, quoted) == "unlearned_secret"
    assert screen_reply(with_pack, quoted) is None
    assert screen_reply(with_pack, "DIRECTOR-TOKEN, they say.") == "unlearned_secret"  # never in her pack


#: Mara's brief without a pack, word for word: 027's own rendering of her card, voice and knowledge.
NO_PACK_SYSTEM = (
    "You are Mara, a character in a shared story. Write only Mara's next contribution: what Mara says and does, "
    "in Mara's own voice. Never write for any other character, and never describe what anyone else thinks, feels "
    "or decides.\n\n"
    "Voice of Mara. Write only Mara's own words.\n"
    "- cadence: Mara speaks plainly.\n"
    "Style examples (reference only; never repeat them verbatim):\n"
    "> Mind the quay, mara says.\n\n"
    "Who you are:\n"
    "A Saltmarch local called Mara.\n\n"
    "What you know, oldest first:\n"
    "- Someone told you: Anya and Tavi are cousins\n"
    "- Someone told you: the duke paid off the harbor watch\n"
    "- Someone told you: Anya keeps the old charts\n"
    "- You suspect: the duke paid off the harbor watch"
)


def test_without_a_pack_the_brief_is_unchanged():
    snap = snapshot()
    brief = mara(snap=snap)
    assert brief == mara(None, snap=snap, snapshot=None)
    assert brief.system == NO_PACK_SYSTEM


# -- the director's lore ----------------------------------------------------------------------------

def scenario_only(**kwargs):
    """A chat with no characters at all: a lorebook and a scenario."""
    return snapshot(cast=(), **kwargs)


def lore(table_text: str = SCENE, budget=Budget(400), *, snap=None, **kwargs):
    """The director's lore for the scenario-only chat, which is a chat of the harness's campaign."""
    options = {"readers": f.READERS, "unpack": f.unpack, "campaign_id": CAMPAIGN, **kwargs}
    return director_lore(snap or scenario_only(), table_text=table_text, budget=budget, **options)


CONTROLS = [ControlAssignment(CAMPAIGN, PC, "human:alice")]


def narration(packed, *, snap=None, **kwargs):
    options = {"snapshot": snap or scenario_only(), "secrets": SECRETS, **kwargs}
    return director_brief("A flooded quay at night.", campaign_id=CAMPAIGN, present=(PC,), controls=CONTROLS,
                          names={PC: "Wren"}, lore=packed, **options)


def test_a_scenario_only_director_brief_gets_lore_the_table_triggered_within_budget():
    quiet = lore("Nobody says anything of note.")
    assert [(i.origin.name, i.audience) for i in quiet.items] == [("tides", "common"), ("opening", "director")]
    assert dict(quiet.untriggered) == {LORE: 5}  # common and director entries alike: the director's to know
    loud = lore(SCENE + " Somebody mentions the duke and the marsh.")
    assert [(i.origin.name, i.audience) for i in loud.items] == [
        ("tides", "common"), ("opening", "director"), ("duke-plan", "director"), ("ferry", "common"),
        ("lighthouse", "common"), ("unclassified", "director")]
    assert loud == lore(SCENE + " Somebody mentions the duke and the marsh.")  # deterministic
    capped = lore(SCENE, budget=Budget(400, {"lore": 30}))
    assert capped.tokens <= 30 and {o.reason for o in capped.omitted} == {"lane_cap"}
    assert "BESTIARY-TOKEN" not in loud.render()  # the world's bestiary is not bound
    text = "\n".join(content for _role, content in director_messages(narration(loud)))
    assert "What anyone here may know:\n- Tides here turn twice a day.\n- The ferry crosses" in text
    assert "never state it outright:\n- SCENARIO-TOKEN" in text and "DIRECTOR-TOKEN" in text


def test_the_director_may_repeat_common_lore_but_never_state_director_lore():
    brief = narration(lore(SCENE + " The duke is late."))
    assert screen_reply(brief, "Mist rises; the ferry crosses the marsh at dusk.") is None
    assert screen_reply(brief, "Everyone knows: DIRECTOR-TOKEN.") == "unlearned_secret"
    assert screen_reply(narration(None), "Mist rises; the ferry crosses the marsh at dusk.") == "unlearned_secret"


def test_director_lore_is_hidden_like_a_secret_even_when_no_secret_names_it():
    brief = narration(lore(), secrets=())  # no SecretFact names the scenario's opening
    for stated in ("SCENARIO-TOKEN the flood comes tonight.", "Hush. Scenario-token the flood comes tonight!"):
        assert screen_reply(brief, stated) == "unlearned_secret", stated
    assert screen_reply(brief, "The flood comes, they say.") is None  # a paraphrase is not a marker
    assert screen_reply(brief, "Tides here turn twice a day.") is None  # common lore is the table's
    heard = narration(lore(), secrets=(), heard=[HeardLine("Wren", "SCENARIO-TOKEN the flood comes tonight")])
    assert screen_reply(heard, "SCENARIO-TOKEN the flood comes tonight.") is None  # the table said it first


def director_only(*texts: str, keys=()) -> EffectiveSnapshot:
    """A chat whose only binding is a lorebook of director entries: constant ones, or keyed by ``keys``."""
    entries = [entry(f"d{n}", text, *keys, constant=not keys, audience="director") for n, text in enumerate(texts)]
    index = DefinitionIndex([SourceDescriptor(f.CAST, owner="human:owner")],
                            [LoreRecord(LORE, "Saltmarch lore", LORE_SCHEMA, {"entries": entries})])
    return EffectiveSnapshot(index, SourceSelection(None, (SelectedSource(LORE, "lorebook"),)))


def directed(*texts: str, table_text: str = SCENE, keys=()):
    snap = director_only(*texts, keys=keys)
    return narration(lore(table_text, snap=snap), snap=snap, secrets=())


def test_each_telling_sentence_of_a_director_entry_is_screened_word_for_word():
    brief = directed("The ferryman is the duke's son. Few know. He hides it well, they say.")
    assert screen_reply(brief, "Mist. The ferryman is the duke's son.") == "unlearned_secret"
    assert screen_reply(brief, "He hides it well, they say") == "unlearned_secret"
    assert screen_reply(brief, "The ferryman is a son of the duke.") is None  # a paraphrase is not caught
    assert screen_reply(brief, "Few know.") is None and brief.unscreened == ()


@pytest.mark.parametrize("entry_text", [
    '"The duke sold the ferry to the guild." "The ferryman is secretly his oldest son."',
    "\u201cThe duke sold the ferry to the guild.\u201d \u2018The ferryman is secretly his oldest son.\u2019",
    "\u00abThe duke sold the ferry to the guild!\u00bb The ferryman is secretly his oldest son\u2026",
    "The duke sold the ferry to the guild\u2026 The ferryman is secretly his oldest son.",
])
def test_a_sentence_ending_inside_closing_quotes_is_screened_on_its_own(entry_text):
    brief = directed(entry_text)
    assert brief.unscreened == ()
    assert screen_reply(brief, "The duke sold the ferry to the guild.") == "unlearned_secret"
    assert screen_reply(brief, "Rumour has it the ferryman is secretly his oldest son.") == "unlearned_secret"
    assert screen_reply(brief, "The duke sold the ferry.") is None  # a part of a sentence is not the sentence


def test_a_short_director_sentence_is_ordinary_prose_and_never_halts_narration():
    brief = directed("It is late. The duke sold the ferry to the guild.")
    assert screen_reply(brief, "It is late, and rain drums on the quay.") is None  # four words: not screened
    assert screen_reply(brief, "Word is the duke sold the ferry to the guild.") == "unlearned_secret"


def test_a_director_entry_with_no_telling_sentence_is_unscreened_and_named_to_the_caller():
    brief = directed("Rain.", "...!!! ok", "The duke sold the ferry to the guild.")
    assert screen_reply(brief, "Rain lashes the quay.") is None and screen_reply(brief, "ok then") is None
    assert brief.unscreened == ((LORE, "d0"), (LORE, "d1"))
    assert "- Rain." in brief.system  # still shown to the narrator, as guidance


def test_director_sentences_split_at_full_width_stops_but_not_at_abbreviations():
    unspaced = directed("公爵はギルドに借金がある。灯台はもう暗い。")
    assert screen_reply(unspaced, "公爵はギルドに借金がある。") == "unlearned_secret"
    assert screen_reply(unspaced, "灯台はもう暗い。") is None  # about three words: too short to screen
    titled = directed("Mr. Vale sold the ferry. Rain. The duke owes the guild a fortune.")
    assert screen_reply(titled, "Mr. Vale sold the ferry at dawn.") == "unlearned_secret"  # "Mr." ends nothing
    assert screen_reply(titled, "The duke owes the guild a fortune.") == "unlearned_secret"  # "Rain." hides nothing


def test_what_the_table_said_when_it_triggered_director_lore_is_not_hidden():
    entry_text = "The duke is late again. He sold the ferry to the guild."
    said = directed(entry_text, table_text="Where is the duke? The duke is late again.", keys=("duke",))
    assert said.unscreened == () and screen_reply(said, "The duke is late again.") is None
    assert screen_reply(said, "He sold the ferry to the guild.") == "unlearned_secret"
    unsaid = directed(entry_text, table_text="Where is the duke?", keys=("duke",))
    assert screen_reply(unsaid, "The duke is late again.") == "unlearned_secret"


def test_director_lore_for_another_campaign_or_selection_is_refused():
    current = lore(SCENE)
    assert refused(lambda: director_brief("A quay.", campaign_id="zz-other", present=(), controls=(), names={},
                                          secrets=SECRETS, lore=current, snapshot=scenario_only())) == "foreign_context"
    assert refused(lambda: narration(current, snap=scenario_only(lore=False))) == "context_mismatch"  # detached
    assert refused(lambda: narration(current, snapshot=None)) == "bad_lore"
    after = repinned(cast=())
    assert refused(lambda: narration(current, snap=after)) == "context_mismatch"
    fresh = narration(lore(SCENE, snap=after), snap=after, secrets=())
    assert "never state it outright:\n- SCENARIO-TOKEN" in fresh.system and "- The ferry crosses" in fresh.system
    assert screen_reply(fresh, "The ferry crosses the marsh at dusk.") == "unlearned_secret"  # r2 made it hidden


def test_an_actors_pack_is_never_the_directors_lore():
    assert refused(lambda: narration(pack("mara", snapshot()))) == "bad_lore"


def test_a_director_lore_request_is_checked():
    for call, reason in [(lambda: lore(budget=400), "invalid_budget"), (lambda: lore(table_text=3), "invalid_scene_text"),
                         (lambda: lore(campaign_id="Not A Campaign"), "invalid_campaign"),
                         (lambda: lore(measurement="guessed"), "invalid_measurement"),
                         (lambda: lore(count="tokens"), "invalid_count"), (lambda: lore(unpack=3), "invalid_unpack")]:
        with pytest.raises(ValueError) as caught:
            call()
        assert caught.value.reason == reason
    with pytest.raises(TypeError):
        director_lore("snapshot", campaign_id=CAMPAIGN, table_text="", budget=Budget(10))


def test_a_scenario_only_turn_sends_the_triggered_lore_to_the_one_director_call():
    harness = Harness([say("Fog wraps the quay; the ferry has not come.")])
    controls = [*CONTROLS, ControlAssignment(CAMPAIGN, "npc-oda", DIRECTOR)]
    plan = harness.plan(controls, present=(PC,), speakers=(), director_reply=True, policy=DIRECTOR_POLICY)
    outcome = harness.runtime.run_turn(harness.meter(plan), [], controls=controls, director=narration(lore()))
    assert outcome.status == "complete"
    [prompt] = harness.prompts()
    assert "The ferry crosses the marsh at dusk." in prompt and "SCENARIO-TOKEN" in prompt
