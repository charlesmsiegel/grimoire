"""The 027/031 seam: briefs rendered from 031's packs, for an NPC and for the scene-level director.

The Saltmarch cast (``context_fixtures``) shares one world-less chat with a lorebook and a scenario.
Mara's brief takes her own pack; the director's takes lore triggered by table-visible text. Every
fixture is synthetic, and the one model call is a scripted fake.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from generation_fixtures import DIRECTOR_POLICY, Harness, say
from voice_fixtures import CAMPAIGN, PC

import context_fixtures as f
from context_fixtures import LORE, actor, card, instance, knowledge, snapshot
from grimoire.context import Budget, ContextPack, director_lore, pack_actor_context
from grimoire.core import ControlAssignment, SourceRef, actor_knowledge, snapshot_actor_view
from grimoire.core.authority import DIRECTOR
from grimoire.generation import (
    GenerationError, director_brief, director_messages, npc_brief, npc_messages, resolve_snapshot_voice,
    screen_reply,
)
from grimoire.voice import SecretFact, VoiceAnchor, VoiceCatalog, VoiceExample, VoiceProfile

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


def view(who: str, snap, *, as_of: int = 9):
    return snapshot_actor_view(instance(who), snap, actor_knowledge(knowledge(), actor(who), as_of=as_of))


def pack(who: str, snap, *, as_of: int = 9, budget=Budget(2000), **kwargs) -> ContextPack:
    options = {"scene_text": SCENE, "readers": f.READERS, "unpack": f.unpack, **kwargs}
    return pack_actor_context(snap, knowledge(), actor(who), as_of=as_of, budget=budget, **options)


def mara(context=None, *, snap=None, **kwargs):
    snap = snap or snapshot()
    return npc_brief(view("mara", snap), resolve_snapshot_voice(actor("mara"), snap, VOICES), secrets=SECRETS,
                     context=context, **kwargs)


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
    assert refused(lambda: mara(director_lore(snap, table_text=SCENE, budget=Budget(400), readers=f.READERS,
                                              unpack=f.unpack), snap=snap)) == "foreign_context"
    assert refused(lambda: mara(pack("mara", snap, as_of=8), snap=snap)) == "context_mismatch"
    other = replace(own.items[0].origin, source=SourceRef("world:saltmarch", "character", "mara", "r1"))
    assert refused(lambda: mara(replace(own, items=(replace(own.items[0], origin=other), *own.items[1:])),
                                snap=snap)) == "context_mismatch"  # built over another selection's Mara
    secret_lore = replace(own.lane("lore")[0], audience="director")
    assert refused(lambda: mara(replace(own, items=(*own.items, secret_lore)), snap=snap)) == "foreign_context"
    assert refused(lambda: mara(replace(own, items=own.items[1:]), snap=snap)) == "bad_context"
    assert refused(lambda: mara(own.render(), snap=snap)) == "bad_context"
    assert refused(lambda: mara(own, snap=snap, sections=[("age", "Forty.")])) == "bad_sections"


def test_screening_treats_what_the_pack_gave_the_actor_as_exposed():
    snap = snapshot()
    with_pack, without = mara(pack("mara", snap), snap=snap), mara(snap=snap)
    quoted = "Aye, the ferry crosses the marsh at dusk."
    assert screen_reply(without, quoted) == "unlearned_secret"
    assert screen_reply(with_pack, quoted) is None
    assert screen_reply(with_pack, "DIRECTOR-TOKEN, they say.") == "unlearned_secret"  # never in her pack


def test_without_a_pack_the_brief_is_unchanged():
    snap = snapshot()
    assert mara(None, snap=snap) == mara(snap=snap)
    assert "What anyone here may know" not in mara(snap=snap).system


# -- the director's lore ----------------------------------------------------------------------------

def scenario_only(**kwargs):
    """A chat with no characters at all: a lorebook and a scenario."""
    return snapshot(cast=(), **kwargs)


def lore(table_text: str = SCENE, budget=Budget(400), **kwargs):
    options = {"readers": f.READERS, "unpack": f.unpack, **kwargs}
    return director_lore(scenario_only(), table_text=table_text, budget=budget, **options)


CONTROLS = [ControlAssignment(CAMPAIGN, PC, "human:alice")]


def narration(packed, **kwargs):
    return director_brief("A flooded quay at night.", campaign_id=CAMPAIGN, present=(PC,), controls=CONTROLS,
                          names={PC: "Wren"}, secrets=SECRETS, lore=packed, **kwargs)


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


def test_an_actors_pack_is_never_the_directors_lore():
    assert refused(lambda: narration(pack("mara", snapshot()))) == "bad_lore"


def test_a_director_lore_request_is_checked():
    for call, reason in [(lambda: lore(budget=400), "invalid_budget"), (lambda: lore(table_text=3), "invalid_scene_text"),
                         (lambda: lore(measurement="guessed"), "invalid_measurement"),
                         (lambda: lore(count="tokens"), "invalid_count"), (lambda: lore(unpack=3), "invalid_unpack")]:
        with pytest.raises(ValueError) as caught:
            call()
        assert caught.value.reason == reason
    with pytest.raises(TypeError):
        director_lore("snapshot", table_text="", budget=Budget(10))


def test_a_scenario_only_turn_sends_the_triggered_lore_to_the_one_director_call():
    harness = Harness([say("Fog wraps the quay; the ferry has not come.")])
    controls = [*CONTROLS, ControlAssignment(CAMPAIGN, "npc-oda", DIRECTOR)]
    plan = harness.plan(controls, present=(PC,), speakers=(), director_reply=True, policy=DIRECTOR_POLICY)
    outcome = harness.runtime.run_turn(harness.meter(plan), [], controls=controls, director=narration(lore()))
    assert outcome.status == "complete"
    [prompt] = harness.prompts()
    assert "The ferry crosses the marsh at dusk." in prompt and "SCENARIO-TOKEN" in prompt
