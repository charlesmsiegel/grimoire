"""027: each NPC brief holds only that actor's own perspective; the director brief holds no knowledge."""

from __future__ import annotations

from dataclasses import replace

import pytest
from generation_fixtures import BELLAMY, LEDGER, NAMES, ODA, PRIVATE, TRUTH, brief, scene, view, world
from voice_fixtures import CAMPAIGN, PC, SECRETS

from grimoire.core import (
    InstanceRef, Knowledge, Moment, Perception, actor_knowledge, director_knowledge,
)
from grimoire.core.knowledge import Proposition, Report
from grimoire.core.perspective import ActorKnowledge, KnownBelief
from grimoire.voice import SecretFact
from grimoire.generation import (
    DirectorBrief, GenerationError, HeardLine, NpcBrief, director_brief, director_messages, known_ids, npc_brief,
    npc_messages, screen_reply,
)

SECRET_LINE = "Word is, the duke's betrayal came cheap."


def text_of(messages) -> str:
    return "\n".join(content for _role, content in messages)


def test_npc_brief_holds_the_actors_own_perception_and_voice_only():
    _controls, voices = world()
    bellamy = brief(BELLAMY, "Bellamy", voices)
    oda = brief(ODA, "Oda", voices)
    assert PRIVATE in bellamy.system and PRIVATE not in oda.system
    assert TRUTH not in bellamy.system and TRUTH not in oda.system  # director-only truth
    assert "saw-letter" not in bellamy.system and "letter" not in oda.system  # no ids, no other's percept
    assert bellamy.fragment.text in bellamy.system and "Darling" in bellamy.system
    assert "Counting-house" in oda.system and "Darling" not in oda.system


@pytest.mark.parametrize("knowledge, reason", [(None, "no_knowledge"), ("director", "foreign_knowledge"),
                                               ("other", "foreign_knowledge")])
def test_npc_brief_refuses_missing_or_foreign_knowledge(knowledge, reason):
    _controls, voices = world()
    if knowledge is None:
        actor_view = view(ODA, "Oda", knowledge=False)
    elif knowledge == "director":
        actor_view = replace(view(ODA, "Oda"),
                             knowledge=director_knowledge(LEDGER, as_of=1, requested_by="director"))
    else:
        actor_view = replace(view(ODA, "Oda"), knowledge=actor_knowledge(LEDGER, BELLAMY, as_of=1))
    with pytest.raises(GenerationError) as caught:
        npc_brief(actor_view, voices["npc-oda"], secrets=SECRETS)
    assert caught.value.reason == reason


def test_npc_brief_refuses_another_actors_voice():
    _controls, voices = world()
    with pytest.raises(GenerationError) as caught:
        npc_brief(view(ODA, "Oda"), voices["npc-bellamy"], secrets=SECRETS)
    assert caught.value.reason == "voice_mismatch"


def test_a_secret_heard_aloud_before_this_turn_counts_as_learned():
    _controls, voices = world()
    oda = brief(ODA, "Oda", voices, heard=[HeardLine("Wren", SECRET_LINE)])
    assert screen_reply(oda, "The duke's betrayal? Tally it.") is None
    assert screen_reply(brief(ODA, "Oda", voices), "The duke's betrayal? Tally it.") == "unlearned_secret"


def test_a_speaker_label_never_teaches_a_hidden_name():
    _controls, voices = world()
    identity = SecretFact("bellamy-identity", ("Bellamy",))
    secrets = (*SECRETS, identity)
    oda = brief(ODA, "Oda", voices, secrets=secrets, heard=[HeardLine("Bellamy", "Hm.", BELLAMY)])
    turn = [HeardLine("Bellamy", "Coin, darling.", BELLAMY)]
    text = text_of(npc_messages(oda, turn))
    assert "Bellamy" not in text and "Someone: Hm." in text and "Someone: Coin, darling." in text
    assert screen_reply(oda, "Bellamy, you snake.", spoken=turn) == "unlearned_secret"
    named = brief(ODA, "Oda", voices, secrets=secrets, names={BELLAMY: "the minstrel"})
    assert "the minstrel: Coin, darling." in text_of(npc_messages(named, turn))
    controls, _voices = world()
    narration = scene("A hall.", controls, secrets=secrets, heard=[HeardLine("Bellamy", "Hm.", BELLAMY)])
    assert "Bellamy" not in text_of(director_messages(narration, turn))


def test_a_heard_secret_the_actor_holds_is_not_a_leak():
    _controls, voices = world()
    ledger = Knowledge(CAMPAIGN, events=LEDGER.events,
                       evidence=[Perception(CAMPAIGN, "overheard", ODA, "betrayal", "hearing",
                                            "Someone muttered about the duke's betrayal", Moment(1), "director",
                                            confirmed=True)])
    oda = npc_brief(replace(view(ODA, "Oda"), knowledge=actor_knowledge(ledger, ODA, as_of=1)),
                    voices["npc-oda"], heard=[HeardLine("Wren", SECRET_LINE)], secrets=SECRETS)
    assert screen_reply(oda, "Aye. The duke's betrayal. Tally it.") is None


def test_known_ids_cover_reports_sent_and_beliefs():
    belief = KnownBelief("b1", "duke-betrayal", "the duke sold the guild", "suspected", Moment(2), (), None)
    knowledge = ActorKnowledge(ODA, 2, (), (), (belief,), ())
    assert known_ids(knowledge) == {"duke-betrayal"}
    oda = npc_brief(replace(view(ODA, "Oda"), knowledge=knowledge), world()[1]["npc-oda"], secrets=SECRETS)
    assert oda.hidden == ()  # known by id
    assert "You suspect: the duke sold the guild" in oda.system
    assert "oda-debts" not in [o.item_id for o in oda.fragment.omitted]  # the example revealing it is kept


def test_knowledge_is_newest_first_within_budget_and_shown_oldest_first():
    _controls, voices = world()
    evidence = [Perception(CAMPAIGN, f"p{n}", ODA, "letter", "sight", f"Clue number {n}", Moment(n), "director",
                           confirmed=True) for n in range(1, 9)]
    ledger = Knowledge(CAMPAIGN, events=LEDGER.events, evidence=evidence)
    oda = npc_brief(replace(view(ODA, "Oda"), knowledge=actor_knowledge(ledger, ODA, as_of=9)),
                    voices["npc-oda"], secrets=SECRETS, knowledge_chars=115)
    shown = [n for n in range(1, 9) if f"Clue number {n}" in oda.system]
    assert shown == [6, 7, 8]
    assert oda.system.index("Clue number 6") < oda.system.index("Clue number 8")


def test_sections_are_bounded_whole_parts_of_the_card():
    _controls, voices = world()
    oda = brief(ODA, "Oda", voices, sections=[("age", "Fifty."), ("history", "x" * 5000)], context_chars=100)
    assert "age: Fifty." in oda.system and "xxxx" not in oda.system


def test_briefs_are_sealed():
    _controls, voices = world()
    real = brief(ODA, "Oda", voices)
    with pytest.raises(GenerationError):
        NpcBrief(real.actor, real.name, real.voice, real.fragment, real.system, (), (), (), ())
    with pytest.raises(GenerationError):
        DirectorBrief(CAMPAIGN, frozenset(), "x", (), (), ())


def test_npc_messages_carry_what_was_said_this_turn():
    _controls, voices = world()
    bellamy = brief(BELLAMY, "Bellamy", voices)
    messages = npc_messages(bellamy, [HeardLine("Oda", "Ledger's shut.")])
    assert messages[0] == ("system", bellamy.system)
    assert "Oda: Ledger's shut." in messages[1][1] and "Bellamy's reply only" in messages[1][1]


# -- screening ------------------------------------------------------------------------------------

def test_screen_rejects_silence_copies_and_unlearned_secrets():
    _controls, voices = world()
    oda = brief(ODA, "Oda", voices)
    scene = tuple(voices.values())
    assert screen_reply(oda, " ... ", voices=scene) == "silent_reply"
    assert screen_reply(oda, "Every ballad needs a villain, my sweet, and tonight", voices=scene) == "copied_example"
    assert screen_reply(oda, SECRET_LINE, voices=scene) == "unlearned_secret"
    assert screen_reply(oda, "Coin first.", voices=scene) is None
    assert screen_reply(oda, "Coin first. " * 200, voices=scene) == "too_long"


def test_a_secret_spoken_earlier_this_turn_is_exposed():
    _controls, voices = world()
    oda = brief(ODA, "Oda", voices)
    assert screen_reply(oda, "The duke's betrayal? Tally it.",
                        spoken=[HeardLine("Bellamy", SECRET_LINE)]) is None


# -- director brief -------------------------------------------------------------------------------

def test_director_brief_holds_authored_material_and_names_player_characters():
    controls, _voices = world()
    narration = scene("A guild dinner in a flooded hall.", controls, heard=[HeardLine("Wren", "I raise my cup.")])
    assert narration.players == frozenset({PC})
    text = text_of(director_messages(narration))
    assert "A guild dinner in a flooded hall." in text and "Wren: I raise my cup." in text
    assert "The player characters are: Wren. Never write their words, thoughts, feelings or decisions" in text
    assert screen_reply(narration, "Rain hammers the shutters.") is None
    assert screen_reply(narration, "Somewhere, the duke sold the guild.") == "unlearned_secret"


def test_director_brief_has_no_knowledge_parameter():
    with pytest.raises(TypeError):
        director_brief("A hall.", campaign_id=CAMPAIGN, present=(), controls=(), names={}, secrets=SECRETS,
                       knowledge=LEDGER)  # type: ignore[call-arg]


def test_director_brief_takes_player_characters_from_control_and_needs_their_names():
    controls, _voices = world()
    assert scene("A hall.", controls, present=("npc-oda",)).players == frozenset()
    for names in ({}, {PC: "  "}, {PC: "W" * 201}):
        with pytest.raises(GenerationError) as caught:
            scene("A hall.", controls, names=names)
        assert caught.value.reason == "bad_players"
    assert "Wren" in scene("A hall.", controls, names={**NAMES, "npc-oda": "Oda"}).system


@pytest.mark.parametrize("scenario, reason", [("   ", "bad_scenario"), ("x" * 5000, "scenario_too_long")])
def test_director_brief_needs_bounded_scenario_text(scenario, reason):
    with pytest.raises(GenerationError) as caught:
        scene(scenario, world()[0])
    assert caught.value.reason == reason


def test_heard_lines_are_validated():
    with pytest.raises(GenerationError):
        HeardLine("Wren", "  ")
    with pytest.raises(GenerationError):
        scene("A hall.", world()[0], heard=["Wren: hi"])


def test_npc_brief_needs_explicit_secrets():
    _controls, voices = world()
    with pytest.raises(TypeError):
        npc_brief(view(ODA, "Oda"), voices["npc-oda"])  # type: ignore[call-arg]


def test_names_label_who_told_whom_without_ids():
    _controls, voices = world()
    stranger = InstanceRef(CAMPAIGN, "character", "npc-stranger")
    belief = KnownBelief("b1", "rumour", "the vault is empty", "believed", Moment(1), (), None)
    knowledge = ActorKnowledge(ODA, 1, (), (), (belief,), ())
    oda = npc_brief(replace(view(ODA, "Oda"), knowledge=knowledge), voices["npc-oda"], secrets=SECRETS,
                    names={stranger: "The stranger"})
    assert "You believe: the vault is empty" in oda.system and "rumour" not in oda.system


def test_a_hidden_name_never_labels_knowledge_or_unlocks_the_screen():
    _controls, voices = world()
    secrets = (*SECRETS, SecretFact("bellamy-identity", ("Bellamy",)))
    ledger = Knowledge(CAMPAIGN, propositions=[Proposition(CAMPAIGN, "rumor", "The wine is watered", Moment(1),
                                                           "director")],
                       evidence=[Report(CAMPAIGN, "told-oda", ODA, "rumor", "speech", Moment(1), "director", True,
                                        sender=BELLAMY, attributed_to=BELLAMY),
                                 Report(CAMPAIGN, "oda-told", BELLAMY, "rumor", "speech", Moment(2), "director",
                                        True, sender=ODA, attributed_to=ODA, addressed=True, sent_at=Moment(2))])
    knowledge = actor_knowledge(ledger, ODA, as_of=2)
    assert knowledge.evidence and knowledge.sent
    oda = npc_brief(replace(view(ODA, "Oda"), knowledge=knowledge), voices["npc-oda"], secrets=secrets,
                    names={BELLAMY: "Bellamy"})
    turn = [HeardLine("Bellamy", "Coin, darling.", BELLAMY)]
    text = text_of(npc_messages(oda, turn))
    assert "Bellamy" not in text
    assert "Someone told you: The wine is watered" in text and "You told Someone: The wine is watered" in text
    assert screen_reply(oda, "Bellamy, you snake.", spoken=turn) == "unlearned_secret"
